package com.omnipad.client.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.omnipad.client.R
import com.omnipad.client.data.Settings
import com.omnipad.client.network.ConnectionState
import com.omnipad.client.network.Keyboard
import com.omnipad.client.network.MouseClick
import com.omnipad.client.network.MouseMove
import com.omnipad.client.network.OmniPadMessage
import com.omnipad.client.network.Scroll
import com.omnipad.client.ui.components.ConnectionStatusPill
import com.omnipad.client.ui.components.KeyCapGridRow
import com.omnipad.client.ui.components.KeyCapSpec
import com.omnipad.client.ui.input.TextInputTracker
import com.omnipad.client.ui.util.Haptics
import com.omnipad.client.ui.util.rememberHaptics

/** 横屏时控制面板占的宽度。 */
private val LANDSCAPE_PANEL_WIDTH = 300.dp

private val ModifierSetSaver = listSaver<Set<String>, String>(
    save = { it.toList() },
    restore = { it.toSet() },
)

/**
 * 触控板页。
 *
 * ## 布局原则：触控板优先
 *
 * 上一版把触控板放在一个可滚动 `Column` 的最底部、固定 320dp 高，上面压着 5 排按钮。
 * 在 411×823dp 的屏幕上刚好卡在最底边，横屏时干脆整块在屏幕外 —— 主角被挤没了。
 *
 * 现在触控板拿 `weight(1f)` 抢剩余空间，控制面板是固定高度的标签页，两者不再互相
 * 争地；横屏时改成左右分栏，把纵向空间全留给触控板。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TouchpadScreen(
    connectionState: ConnectionState,
    reconnectAttempt: Int,
    latencyMs: Int?,
    settings: Settings,
    onSendMessage: (OmniPadMessage) -> Unit,
    onDisconnect: () -> Unit,
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val haptics = rememberHaptics(settings.hapticsEnabled)
    val tracker = remember { TextInputTracker() }
    val view = LocalView.current

    // 修饰键的锁定状态要跨旋转保留：否则转屏时电脑上会留下一个按住的 Ctrl
    var activeModifiers by rememberSaveable(stateSaver = ModifierSetSaver) {
        mutableStateOf(emptySet())
    }
    var heldMouseButton by rememberSaveable { mutableStateOf<String?>(null) }
    var confirmDisconnect by remember { mutableStateOf(false) }

    /** 把在电脑上按住的键全部松开。断开、返回、旋转销毁时都要做。 */
    fun releaseHeldKeys() {
        releaseModifiers(activeModifiers).forEach(onSendMessage)
        heldMouseButton?.let { onSendMessage(MouseClick(it, "up")) }
        activeModifiers = emptySet()
        heldMouseButton = null
    }

    DisposableEffect(Unit) {
        onDispose { releaseHeldKeys() }
    }

    // 保持屏幕常亮：用手机当键盘打字时，屏幕不该因为没碰手机而熄灭
    DisposableEffect(settings.keepScreenOn) {
        view.keepScreenOn = settings.keepScreenOn
        onDispose { view.keepScreenOn = false }
    }

    fun pressKey(key: String) {
        haptics.tick()
        keyPress(key).forEach(onSendMessage)
    }

    fun toggleModifier(key: String) {
        haptics.tick()
        if (key in activeModifiers) {
            activeModifiers = activeModifiers - key
            onSendMessage(Keyboard(key, "up"))
        } else {
            activeModifiers = activeModifiers + key
            onSendMessage(Keyboard(key, "down"))
        }
    }

    /**
     * 鼠标键是「按下并保持」的开关，用于拖拽选中、拖动窗口这类操作。
     *
     * 切到另一个键时**必须先松开上一个**：上一版直接覆盖状态，导致先按下的那个键
     * 在 Windows 侧永远处于按下状态，直到用户手动再点一次。
     */
    fun toggleMouseButton(button: String) {
        haptics.click()
        val held = heldMouseButton
        if (held == button) {
            heldMouseButton = null
            onSendMessage(MouseClick(button, "up"))
        } else {
            held?.let { onSendMessage(MouseClick(it, "up")) }
            heldMouseButton = button
            onSendMessage(MouseClick(button, "down"))
        }
    }

    // 每次重组都重新构造：闭包要读到最新的 activeModifiers，remember 会捕获旧值
    val actions = PanelActions(
        pressKey = { pressKey(it) },
        toggleModifier = { toggleModifier(it) },
        sendCombo = { keys ->
            haptics.tick()
            comboSequence(keys).forEach(onSendMessage)
        },
        send = onSendMessage,
        haptics = haptics,
    )

    Scaffold(
        modifier = modifier.fillMaxSize(),
        containerColor = MaterialTheme.colorScheme.background,
        // 自己管内边距：TopAppBar 负责顶部，下面只补横向和底部
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                title = {
                    ConnectionStatusPill(
                        state = connectionState,
                        reconnectAttempt = reconnectAttempt,
                        latencyMs = latencyMs,
                    )
                },
                actions = {
                    IconButton(onClick = onOpenSettings) {
                        Icon(
                            imageVector = Icons.Default.Settings,
                            contentDescription = stringResource(R.string.session_settings),
                        )
                    }
                    IconButton(onClick = { confirmDisconnect = true }) {
                        Icon(
                            imageVector = Icons.Default.PowerSettingsNew,
                            contentDescription = stringResource(R.string.session_disconnect),
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                // safeDrawing 的底部已经取过「导航栏 / 输入法」的较大者，
                // 不需要再叠一次 imePadding
                .windowInsetsPadding(
                    WindowInsets.safeDrawing.only(
                        WindowInsetsSides.Horizontal + WindowInsetsSides.Bottom
                    )
                ),
        ) {
            if (connectionState != ConnectionState.CONNECTED) {
                SessionBanner(
                    state = connectionState,
                    reconnectAttempt = reconnectAttempt,
                    onBack = onDisconnect,
                )
            }

            BoxWithConstraints(modifier = Modifier.fillMaxWidth().weight(1f)) {
                // 按实际可用空间判断横竖，而不是读 Configuration ——
                // 折叠屏、分屏、平板上的窗口比例都能正确响应
                if (maxWidth > maxHeight) {
                    Row(modifier = Modifier.fillMaxSize()) {
                        PadArea(
                            settings = settings,
                            haptics = haptics,
                            heldMouseButton = heldMouseButton,
                            onSendMessage = onSendMessage,
                            onToggleMouseButton = { toggleMouseButton(it) },
                            modifier = Modifier.weight(1f).fillMaxHeight(),
                        )
                        ControlPanel(
                            activeModifiers = activeModifiers,
                            actions = actions,
                            tracker = tracker,
                            contentHeight = null,
                            modifier = Modifier.width(LANDSCAPE_PANEL_WIDTH).fillMaxHeight(),
                        )
                    }
                } else {
                    Column(modifier = Modifier.fillMaxSize()) {
                        PadArea(
                            settings = settings,
                            haptics = haptics,
                            heldMouseButton = heldMouseButton,
                            onSendMessage = onSendMessage,
                            onToggleMouseButton = { toggleMouseButton(it) },
                            modifier = Modifier.weight(1f),
                        )
                        ControlPanel(
                            activeModifiers = activeModifiers,
                            actions = actions,
                            tracker = tracker,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }
        }
    }

    if (confirmDisconnect) {
        AlertDialog(
            onDismissRequest = { confirmDisconnect = false },
            title = { Text(stringResource(R.string.session_disconnect_confirm_title)) },
            text = { Text(stringResource(R.string.session_disconnect_confirm_message)) },
            confirmButton = {
                TextButton(onClick = {
                    confirmDisconnect = false
                    // 断开前先松开按住的键，否则电脑那边会一直保持按下
                    releaseHeldKeys()
                    tracker.reset()
                    onDisconnect()
                }) {
                    Text(
                        text = stringResource(R.string.session_disconnect),
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmDisconnect = false }) {
                    Text(stringResource(R.string.action_cancel))
                }
            },
        )
    }
}

/** 触控板 + 鼠标键条。竖屏与横屏共用。 */
@Composable
private fun PadArea(
    settings: Settings,
    haptics: Haptics,
    heldMouseButton: String?,
    onSendMessage: (OmniPadMessage) -> Unit,
    onToggleMouseButton: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        TouchpadSurface(
            pointerSensitivity = settings.pointerSensitivity,
            scrollSensitivity = settings.scrollSensitivity,
            haptics = haptics,
            onPointerMove = { dx, dy -> onSendMessage(MouseMove(dx, dy)) },
            onButtonClick = { button -> onSendMessage(MouseClick(button, "click")) },
            onScroll = { delta -> onSendMessage(Scroll(delta)) },
            modifier = Modifier.weight(1f).fillMaxWidth(),
        )

        // 鼠标键紧贴触控板下沿：拇指从板上滑下来就能按到，不用横跨整个屏幕
        KeyCapGridRow(
            items = listOf(
                mouseButtonSpec(
                    button = "left",
                    label = stringResource(R.string.mouse_left),
                    heldLabel = stringResource(R.string.mouse_left_held),
                    held = heldMouseButton,
                    onToggle = onToggleMouseButton,
                ),
                mouseButtonSpec(
                    button = "middle",
                    label = stringResource(R.string.mouse_middle),
                    heldLabel = stringResource(R.string.mouse_middle_held),
                    held = heldMouseButton,
                    onToggle = onToggleMouseButton,
                ),
                mouseButtonSpec(
                    button = "right",
                    label = stringResource(R.string.mouse_right),
                    heldLabel = stringResource(R.string.mouse_right_held),
                    held = heldMouseButton,
                    onToggle = onToggleMouseButton,
                ),
            ),
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun mouseButtonSpec(
    button: String,
    label: String,
    heldLabel: String,
    held: String?,
    onToggle: (String) -> Unit,
): KeyCapSpec {
    val isHeld = held == button
    return KeyCapSpec(
        label = label,
        active = isHeld,
        stateDescription = if (isHeld) heldLabel else null,
        onClick = { onToggle(button) },
    )
}

/**
 * 会话中断提示条。
 *
 * 这是「重连时不要把人踢回连接页」的落点：触控板还在原位，只是上方多一条提示，
 * 网络抖一下不会让手感和正在输入的内容全部丢失。
 */
@Composable
private fun SessionBanner(
    state: ConnectionState,
    reconnectAttempt: Int,
    onBack: () -> Unit,
) {
    val reconnecting = state == ConnectionState.RECONNECTING
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = if (reconnecting) {
            MaterialTheme.colorScheme.secondaryContainer
        } else {
            MaterialTheme.colorScheme.errorContainer
        },
        contentColor = if (reconnecting) {
            MaterialTheme.colorScheme.onSecondaryContainer
        } else {
            MaterialTheme.colorScheme.onErrorContainer
        },
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (reconnecting) {
                CircularProgressIndicator(
                    modifier = Modifier.size(16.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onSecondaryContainer,
                )
            }
            Text(
                text = if (reconnecting) {
                    stringResource(R.string.session_reconnecting, reconnectAttempt)
                } else {
                    stringResource(R.string.session_disconnected)
                },
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onBack) {
                Text(stringResource(R.string.session_back))
            }
        }
    }
}
