package com.omnipad.client.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
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
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.omnipad.client.network.Keyboard
import com.omnipad.client.network.MouseClick
import com.omnipad.client.network.MouseMove
import com.omnipad.client.network.OmniPadMessage
import com.omnipad.client.network.Scroll
import com.omnipad.client.network.TextInput

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TouchpadScreen(
    onDisconnect: () -> Unit,
    onSendMessage: (OmniPadMessage) -> Unit,
    modifier: Modifier = Modifier,
) {
    var isPressed by remember { mutableStateOf(false) }
    var textInput by remember { mutableStateOf("") }
    val focusManager = LocalFocusManager.current
    val scrollState = rememberScrollState()
    val dragAccumX = remember { mutableIntStateOf(0) }
    val dragAccumY = remember { mutableIntStateOf(0) }
    val scrollAccum = remember { mutableIntStateOf(0) }

    LaunchedEffect(Unit) {
        while (true) {
            kotlinx.coroutines.delay(16)
            val ax = dragAccumX.intValue
            val ay = dragAccumY.intValue
            if (ax != 0 || ay != 0) {
                dragAccumX.intValue = 0
                dragAccumY.intValue = 0
                onSendMessage(MouseMove(ax, ay))
            }
            val sc = scrollAccum.intValue
            if (sc != 0) {
                scrollAccum.intValue = 0
                onSendMessage(Scroll(if (sc > 0) 1 else -1))
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
                            "左键" to { onSendMessage(MouseClick("left", "click")) },
                            "右键" to { onSendMessage(MouseClick("right", "click")) },
                            "中键" to { onSendMessage(MouseClick("middle", "click")) },
                    ),
                    modifier = Modifier.weight(1f),
                )

                Spacer(Modifier.width(8.dp))

                ButtonGroup(
                    items = listOf(
                            "△" to { onSendMessage(Scroll(1)) },
                            "▽" to { onSendMessage(Scroll(-1)) },
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
                    placeholder = { Text("输入文字发送到电脑") },
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
                listOf("Enter", "Tab", "Esc", "退格").forEach { label ->
                    val key = when (label) {
                        "退格" -> "backspace"
                        "Esc" -> "escape"
                        else -> label.lowercase()
                    }
                    FilledIconButton(
                        onClick = { onSendMessage(Keyboard(key, "press")) },
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
                listOf("Ctrl", "Shift", "Alt", "Win").forEach { key ->
                    FilledIconButton(
                        onClick = { onSendMessage(Keyboard(key.lowercase(), "press")) },
                        modifier = Modifier.weight(1f).height(48.dp),
                        shape = MaterialTheme.shapes.small,
                        colors = IconButtonDefaults.filledIconButtonColors(
                            containerColor = MaterialTheme.colorScheme.surfaceVariant,
                            contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    ) {
                        Text(key, style = MaterialTheme.typography.labelLarge)
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
                        onClick = { onSendMessage(Keyboard(key, "press")) },
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
                    .pointerInput(Unit) {
                        detectTapGestures(
                            onTap = {
                            onSendMessage(MouseClick("left", "click"))
                        },
                            onLongPress = {
                                onSendMessage(MouseClick("right", "click"))
                            },
                        )
                    }
                    .pointerInput(Unit) {
                        detectDragGestures(
                            onDragStart = { isPressed = true },
                            onDragEnd = { isPressed = false },
                            onDragCancel = { isPressed = false },
                            onDrag = { change, dragAmount ->
                                change.consume()
                                dragAccumX.intValue += dragAmount.x.toInt()
                                dragAccumY.intValue += dragAmount.y.toInt()
                            },
                        )
                    }
                    .pointerInput(Unit) {
                        detectTransformGestures { _, pan, _, _ ->
                            val delta = -pan.y.toInt()
                            if (delta != 0) {
                                scrollAccum.intValue += delta
                            }
                        }
                    },
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = if (isPressed) "松开以停止拖动" else "触摸板",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = "点击=左键 长按=右键 拖动=移动 双指=滚动",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                    )
                }
            }

            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
private fun ButtonGroup(
    items: List<Pair<String, () -> Unit>>,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items.forEach { (label, onClick) ->
            val circle = items.size <= 3
            FilledIconButton(
                onClick = onClick,
                modifier = Modifier.weight(1f).height(48.dp),
                shape = if (circle) CircleShape else MaterialTheme.shapes.small,
            ) {
                Text(label, style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
