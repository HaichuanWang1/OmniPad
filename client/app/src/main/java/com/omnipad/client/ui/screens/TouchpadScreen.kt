package com.omnipad.client.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import kotlinx.coroutines.withTimeoutOrNull
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Send

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.omnipad.client.R
import com.omnipad.client.network.Keyboard
import com.omnipad.client.network.MouseClick
import com.omnipad.client.network.MouseMove
import com.omnipad.client.network.OmniPadMessage
import com.omnipad.client.network.Scroll
import com.omnipad.client.network.TextInput
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.sqrt

/** 双指纵向位移换算成滚轮格数的除数。 */
private const val SCROLL_DIVISOR = 3f

/**
 * 触摸板的手势阶段。
 *
 * 全部由同一个 `pointerInput` 状态机驱动：判定阶段先决定这次触摸属于哪一类，
 * 执行阶段只做对应的事。这样各手势之间不会互相抢事件。
 */
private enum class TouchMode { TAP, LONG_PRESS, DRAG, SCROLL }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TouchpadScreen(
    onDisconnect: () -> Unit,
    onSendMessage: (OmniPadMessage) -> Unit,
    autoDisconnect: Boolean,
    onToggleAutoDisconnect: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var isPressed by remember { mutableStateOf(false) }
    var textInput by remember { mutableStateOf("") }
    val focusManager = LocalFocusManager.current
    val scrollState = rememberScrollState()
    val dragAccumX = remember { AtomicInteger(0) }
    val dragAccumY = remember { AtomicInteger(0) }
    val scrollAccum = remember { AtomicInteger(0) }
    var activeModifiers by remember { mutableStateOf(setOf<String>()) }
    var heldMouseButton by remember { mutableStateOf<String?>(null) }
    val modifierOrder = listOf("ctrl", "shift", "alt", "win")

    fun sendKeyWithModifiers(key: String, action: String) {
        val sorted = modifierOrder.filter { it in activeModifiers }
        sorted.forEach { onSendMessage(Keyboard(it, "down")) }
        onSendMessage(Keyboard(key, action))
        sorted.reversed().forEach { onSendMessage(Keyboard(it, "up")) }
    }

    LaunchedEffect(Unit) {
        while (true) {
            kotlinx.coroutines.delay(16)
            val ax = dragAccumX.getAndSet(0)
            val ay = dragAccumY.getAndSet(0)
            if (ax != 0 || ay != 0) {
                onSendMessage(MouseMove(ax, ay))
            }
            val sc = scrollAccum.getAndSet(0)
            if (sc != 0) {
                onSendMessage(Scroll(sc))
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("OmniPad") },
                navigationIcon = {
                    IconButton(onClick = onDisconnect) {
                        Icon(
                            imageVector = Icons.Default.ArrowBack,
                            contentDescription = "Disconnect",
                        )
                    }
                },
                actions = {
                    Switch(
                        checked = autoDisconnect,
                        onCheckedChange = { onToggleAutoDisconnect() },
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = MaterialTheme.colorScheme.primary,
                            checkedTrackColor = MaterialTheme.colorScheme.primaryContainer,
                        ),
                    )
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
    ) { padding ->
        Column(
            modifier = modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.background)
                .padding(padding)
                .padding(horizontal = 16.dp)
                .verticalScroll(scrollState),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Spacer(Modifier.height(4.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                ButtonGroup(
                    items = listOf(
                        ButtonSpec(stringResource(R.string.mouse_left), "left") {
                            val held = heldMouseButton
                            if (held == "left") {
                                heldMouseButton = null
                                onSendMessage(MouseClick("left", "up"))
                            } else {
                                heldMouseButton = "left"
                                onSendMessage(MouseClick("left", "down"))
                            }
                        },
                        ButtonSpec(stringResource(R.string.mouse_right), "right") {
                            val held = heldMouseButton
                            if (held == "right") {
                                heldMouseButton = null
                                onSendMessage(MouseClick("right", "up"))
                            } else {
                                heldMouseButton = "right"
                                onSendMessage(MouseClick("right", "down"))
                            }
                        },
                        ButtonSpec(stringResource(R.string.mouse_middle), "middle") {
                            val held = heldMouseButton
                            if (held == "middle") {
                                heldMouseButton = null
                                onSendMessage(MouseClick("middle", "up"))
                            } else {
                                heldMouseButton = "middle"
                                onSendMessage(MouseClick("middle", "down"))
                            }
                        },
                    ),
                    heldKey = heldMouseButton,
                    modifier = Modifier.weight(1f),
                )

                Spacer(Modifier.width(8.dp))

                ButtonGroup(
                    items = listOf(
                        ButtonSpec("△") { onSendMessage(Scroll(1)) },
                        ButtonSpec("▽") { onSendMessage(Scroll(-1)) },
                    ),
                    modifier = Modifier.width(120.dp),
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = textInput,
                    onValueChange = { textInput = it },
                    placeholder = { Text(stringResource(R.string.touchpad_text_placeholder)) },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                    shape = MaterialTheme.shapes.medium,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = MaterialTheme.colorScheme.primary,
                        unfocusedBorderColor = MaterialTheme.colorScheme.outlineVariant,
                    ),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(
                        onSend = {
                            if (textInput.isNotBlank()) {
                                onSendMessage(TextInput(textInput))
                                textInput = ""
                                focusManager.clearFocus()
                            }
                        },
                    ),
                )
                FilledIconButton(
                    onClick = {
                        if (textInput.isNotBlank()) {
                            onSendMessage(TextInput(textInput))
                            textInput = ""
                            focusManager.clearFocus()
                        }
                    },
                    modifier = Modifier.size(56.dp),
                    shape = MaterialTheme.shapes.medium,
                ) {
                    Icon(
                        imageVector = Icons.Default.Send,
                        contentDescription = "Send",
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                val backspaceLabel = stringResource(R.string.touchpad_backspace)
                listOf("Enter", "Tab", "Esc", backspaceLabel).forEach { label ->
                    val key = when (label) {
                        backspaceLabel -> "backspace"
                        "Esc" -> "escape"
                        else -> label.lowercase()
                    }
                    FilledIconButton(
                        onClick = { sendKeyWithModifiers(key, "press") },
                        modifier = Modifier.weight(1f).height(48.dp),
                        shape = MaterialTheme.shapes.small,
                        colors = IconButtonDefaults.filledIconButtonColors(
                            containerColor = MaterialTheme.colorScheme.surfaceVariant,
                            contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    ) {
                        Text(label, style = MaterialTheme.typography.labelLarge)
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf("Ctrl", "Shift", "Alt", "Win").forEach { label ->
                    val key = label.lowercase()
                    val isActive = key in activeModifiers
                    FilledIconButton(
                        onClick = {
                            if (isActive) {
                                activeModifiers = activeModifiers - key
                                onSendMessage(Keyboard(key, "up"))
                            } else {
                                activeModifiers = activeModifiers + key
                                onSendMessage(Keyboard(key, "down"))
                            }
                        },
                        modifier = Modifier.weight(1f).height(48.dp),
                        shape = MaterialTheme.shapes.small,
                        colors = if (isActive) {
                            IconButtonDefaults.filledIconButtonColors(
                                containerColor = MaterialTheme.colorScheme.primary,
                                contentColor = MaterialTheme.colorScheme.onPrimary,
                            )
                        } else {
                            IconButtonDefaults.filledIconButtonColors(
                                containerColor = MaterialTheme.colorScheme.surfaceVariant,
                                contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        },
                    ) {
                        Text(label, style = MaterialTheme.typography.labelLarge)
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf("↑", "↓", "←", "→").forEach { label ->
                    val key = when (label) {
                        "↑" -> "up"; "↓" -> "down"; "←" -> "left"; "→" -> "right"
                        else -> label.lowercase()
                    }
                    FilledIconButton(
                        onClick = { sendKeyWithModifiers(key, "press") },
                        modifier = Modifier.weight(1f).height(48.dp),
                        shape = MaterialTheme.shapes.small,
                        colors = IconButtonDefaults.filledIconButtonColors(
                            containerColor = MaterialTheme.colorScheme.surfaceVariant,
                            contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    ) {
                        Text(label, style = MaterialTheme.typography.titleMedium)
                    }
                }
            }

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(320.dp)
                    .clip(MaterialTheme.shapes.large)
                    .background(
                        if (isPressed) {
                            MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.4f)
                        } else {
                            MaterialTheme.colorScheme.surfaceVariant
                        },
                    )
                    .border(
                        width = 1.dp,
                        color = if (isPressed) {
                            MaterialTheme.colorScheme.primary
                        } else {
                            MaterialTheme.colorScheme.outlineVariant
                        },
                        shape = MaterialTheme.shapes.large,
                    )
                    // 单一手势状态机：点击 / 长按 / 拖动 / 双指滚动都在这里判定。
                    // 原先三个独立的 pointerInput 会互相抢事件，「拖动被点击吃掉」
                    // 「双指滚动误触发」都是这么来的。
                    .pointerInput(Unit) {
                        awaitEachGesture {
                            val down = awaitFirstDown(requireUnconsumed = false)
                            // 用平台阈值，尊重系统的无障碍与手感设置
                            val longPressTimeout = viewConfiguration.longPressTimeoutMillis
                            val touchSlop = viewConfiguration.touchSlop

                            // null 表示还没判定出来
                            var decided: TouchMode? = null
                            var lastPos = down.position
                            var accumulated = Offset.Zero
                            var scrollLastY = 0f

                            // 判定阶段：静置超时即长按；否则等移动超阈值或第二根手指。
                            withTimeoutOrNull(longPressTimeout) {
                                while (decided == null) {
                                    val event = awaitPointerEvent()
                                    val pressed = event.changes.filter { it.pressed }
                                    when {
                                        pressed.isEmpty() -> decided = TouchMode.TAP

                                        pressed.size >= 2 -> {
                                            decided = TouchMode.SCROLL
                                            scrollLastY = pressed
                                                .map { it.position.y }.average().toFloat()
                                        }

                                        else -> {
                                            val change = event.changes
                                                .firstOrNull { it.id == down.id }
                                                ?: pressed.first()
                                            accumulated += change.position - lastPos
                                            lastPos = change.position
                                            if (accumulated.getDistance() > touchSlop) {
                                                decided = TouchMode.DRAG
                                            }
                                        }
                                    }
                                }
                            }
                            // 静置超过长按阈值，判定为长按
                            val mode = decided ?: TouchMode.LONG_PRESS

                            // 执行阶段：只做判定结果对应的那一件事。
                            when (mode) {
                                TouchMode.TAP ->
                                    onSendMessage(MouseClick("left", "click"))

                                TouchMode.LONG_PRESS -> {
                                    onSendMessage(MouseClick("right", "click"))
                                    // 吃掉后续事件直到抬起，避免抬手时又被判成点击
                                    while (true) {
                                        val event = awaitPointerEvent()
                                        if (event.changes.none { it.pressed }) break
                                    }
                                }

                                TouchMode.DRAG -> {
                                    isPressed = true
                                    // 判定阶段已经积累的位移不能丢
                                    if (accumulated != Offset.Zero) {
                                        dragAccumX.addAndGet(accumulated.x.toInt())
                                        dragAccumY.addAndGet(accumulated.y.toInt())
                                    }
                                    while (true) {
                                        val event = awaitPointerEvent()
                                        val change = event.changes
                                            .firstOrNull { it.id == down.id }
                                        if (change == null || !change.pressed) break
                                        val delta = change.position - lastPos
                                        lastPos = change.position
                                        dragAccumX.addAndGet(delta.x.toInt())
                                        dragAccumY.addAndGet(delta.y.toInt())
                                        change.consume()
                                    }
                                    isPressed = false
                                }

                                TouchMode.SCROLL -> {
                                    while (true) {
                                        val event = awaitPointerEvent()
                                        val pressed = event.changes.filter { it.pressed }
                                        if (pressed.size < 2) break
                                        val avgY = pressed
                                            .map { it.position.y }.average().toFloat()
                                        val delta = ((scrollLastY - avgY) / SCROLL_DIVISOR).toInt()
                                        if (delta != 0) scrollAccum.addAndGet(delta)
                                        scrollLastY = avgY
                                        pressed.forEach { it.consume() }
                                    }
                                }
                            }
                        }
                    },
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = stringResource(
                            if (isPressed) R.string.touchpad_dragging
                            else R.string.touchpad_idle
                        ),
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = stringResource(R.string.touchpad_hint),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                    )
                }
            }

            Spacer(Modifier.height(8.dp))
        }
    }
}

/**
 * 一个按钮的描述。
 *
 * [key] 是协议里的按键标识（如 "left"），与显示文案解耦 —— 原先靠显示文案
 * 反查按键，文案一旦被翻译或改动就会失效。
 */
private data class ButtonSpec(
    val label: String,
    val key: String? = null,
    val onClick: () -> Unit,
)

@Composable
private fun ButtonGroup(
    items: List<ButtonSpec>,
    modifier: Modifier = Modifier,
    heldKey: String? = null,
) {
    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items.forEach { spec ->
            val circle = items.size <= 3
            val isHeld = spec.key != null && spec.key == heldKey
            FilledIconButton(
                onClick = spec.onClick,
                modifier = Modifier.weight(1f).height(48.dp),
                shape = if (circle) CircleShape else MaterialTheme.shapes.small,
                colors = if (isHeld) {
                    IconButtonDefaults.filledIconButtonColors(
                        containerColor = MaterialTheme.colorScheme.primary,
                        contentColor = MaterialTheme.colorScheme.onPrimary,
                    )
                } else {
                    IconButtonDefaults.filledIconButtonColors()
                },
            ) {
                Text(
                    spec.label,
                    style = MaterialTheme.typography.labelLarge,
                )
            }
        }
    }
}
