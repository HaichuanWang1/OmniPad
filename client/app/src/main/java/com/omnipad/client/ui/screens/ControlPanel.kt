package com.omnipad.client.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.omnipad.client.R
import com.omnipad.client.network.Keyboard
import com.omnipad.client.network.OmniPadMessage
import com.omnipad.client.ui.components.KeyCapGridRow
import com.omnipad.client.ui.components.KeyCapSection
import com.omnipad.client.ui.components.KeyCapSpec
import com.omnipad.client.ui.input.TextInputTracker
import com.omnipad.client.ui.util.Haptics

/** 修饰键的发送顺序，固定下来保证组合键的两端对称。 */
private val MODIFIER_ORDER = listOf("ctrl", "shift", "alt", "win")

/**
 * 面板里的动作集合。
 *
 * 打包成一个对象是为了避免每个子面板都摊开五六个回调参数。
 */
class PanelActions(
    val pressKey: (String) -> Unit,
    val toggleModifier: (String) -> Unit,
    val sendCombo: (List<String>) -> Unit,
    val send: (OmniPadMessage) -> Unit,
    val haptics: Haptics,
)

/**
 * 触控板下方的控制面板。
 *
 * 上一版把修饰键、功能键、方向键、文字输入全部平铺成 6 排按钮堆在触控板上方，
 * 结果是主角（触控板）被挤到屏幕最下方、还得滚动才能够到。这里改成固定高度的
 * 标签页面板：三个面板共用同一块空间，谁也不挤压触控板。
 */
@Composable
fun ControlPanel(
    activeModifiers: Set<String>,
    actions: PanelActions,
    tracker: TextInputTracker,
    modifier: Modifier = Modifier,
    /** 面板内容高度；传 null 表示撑满可用空间（横屏时用）。 */
    contentHeight: Dp? = PANEL_HEIGHT,
) {
    var tab by remember { mutableStateOf(0) }

    val tabs = listOf(
        stringResource(R.string.panel_tab_keyboard),
        stringResource(R.string.panel_tab_keys),
        stringResource(R.string.panel_tab_shortcuts),
    )

    Surface(
        modifier = modifier,
        color = MaterialTheme.colorScheme.surface,
        contentColor = MaterialTheme.colorScheme.onSurface,
        // 和触控板一样比背景高一层。横屏时面板是右侧独立一栏，与背景同色会显得
        // 内容在空白的页面上漂浮；加一点色调后它才读得出「一块面板」。
        tonalElevation = 2.dp,
    ) {
        Column(
            modifier = if (contentHeight == null) Modifier.fillMaxHeight() else Modifier
        ) {
            TabRow(
                selectedTabIndex = tab,
                containerColor = MaterialTheme.colorScheme.surface,
                contentColor = MaterialTheme.colorScheme.primary,
            ) {
                tabs.forEachIndexed { index, title ->
                    Tab(
                        selected = tab == index,
                        onClick = {
                            actions.haptics.tick()
                            tab = index
                        },
                        text = { Text(title, style = MaterialTheme.typography.labelLarge) },
                    )
                }
            }

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .then(
                        if (contentHeight != null) {
                            Modifier.height(contentHeight)
                        } else {
                            Modifier.weight(1f)
                        }
                    )
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 12.dp, vertical = 10.dp),
            ) {
                when (tab) {
                    0 -> KeyboardPanel(tracker, actions)
                    1 -> KeysPanel(activeModifiers, actions)
                    else -> ShortcutsPanel(actions)
                }
            }
        }
    }
}

/** 面板固定高度。触控板拿到的是剩余空间，因此这个值直接决定触控板的最小高度。 */
val PANEL_HEIGHT = 188.dp

// ---------------------------------------------------------------- 键盘

@Composable
private fun KeyboardPanel(tracker: TextInputTracker, actions: PanelActions) {
    var field by remember { mutableStateOf(TextFieldValue("")) }

    fun sendEnter() {
        tracker.onEnter().forEach(actions.send)
        field = TextFieldValue("")
        actions.haptics.tick()
    }

    fun clear() {
        tracker.onClear().forEach(actions.send)
        field = TextFieldValue("")
        actions.haptics.tick()
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // 用 decorationBox 把输入框画成一个紧凑的 Surface：
            // 这样整个 Surface 都是输入框的触控区域，而不是只有文字那一行。
            BasicTextField(
                value = field,
                onValueChange = { updated ->
                    // 合成中的内容不算已提交，tracker 会把它排除掉 ——
                    // 否则中文输入时电脑上会先冒出一串拼音
                    val composing = updated.composition?.takeIf { !it.collapsed }
                    tracker.onTextChanged(
                        text = updated.text,
                        composingStart = composing?.start ?: -1,
                        composingEnd = composing?.end ?: -1,
                    ).forEach(actions.send)
                    field = updated
                },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium.copy(
                    color = MaterialTheme.colorScheme.onSurface,
                ),
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { sendEnter() }),
                modifier = Modifier.weight(1f),
                decorationBox = { innerTextField ->
                    Surface(
                        shape = MaterialTheme.shapes.small,
                        color = MaterialTheme.colorScheme.surfaceVariant,
                        contentColor = MaterialTheme.colorScheme.onSurface,
                    ) {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 12.dp, vertical = 14.dp),
                            contentAlignment = Alignment.CenterStart,
                        ) {
                            if (field.text.isEmpty()) {
                                Text(
                                    text = stringResource(R.string.touchpad_text_placeholder),
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    // 横屏时面板只有 300dp 宽，不限行数的话这句提示会折成
                                    // 两行、把输入框顶高，回车键跟着被拉长
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                            innerTextField()
                        }
                    }
                },
            )

            KeyCapGridRow(
                items = listOf(
                    KeyCapSpec(label = stringResource(R.string.key_enter), onClick = { sendEnter() })
                ),
                modifier = Modifier.width(84.dp),
            )
        }

        KeyCapGridRow(
            items = listOf(
                KeyCapSpec(stringResource(R.string.keyboard_clear)) { clear() },
                KeyCapSpec("Tab") { actions.pressKey("tab") },
                KeyCapSpec("Esc") { actions.pressKey("escape") },
                KeyCapSpec(stringResource(R.string.touchpad_backspace)) {
                    actions.pressKey("backspace")
                },
                KeyCapSpec(stringResource(R.string.key_space)) { actions.pressKey("space") },
            )
        )

        Text(
            text = stringResource(R.string.keyboard_hint),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

// ---------------------------------------------------------------- 按键

@Composable
private fun KeysPanel(activeModifiers: Set<String>, actions: PanelActions) {
    val lockedLabel = stringResource(R.string.state_locked)
    val unlockedLabel = stringResource(R.string.state_unlocked)

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        KeyCapSection(title = stringResource(R.string.panel_section_modifiers)) {
            KeyCapGridRow(
                items = listOf(
                    "ctrl" to "Ctrl",
                    "shift" to "Shift",
                    "alt" to "Alt",
                    "win" to "Win",
                ).map { (key, label) ->
                    val on = key in activeModifiers
                    KeyCapSpec(
                        label = label,
                        active = on,
                        stateDescription = if (on) lockedLabel else unlockedLabel,
                        onClick = { actions.toggleModifier(key) },
                    )
                }
            )
        }

        KeyCapSection(title = stringResource(R.string.panel_section_editing)) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                KeyCapGridRow(
                    items = listOf(
                        KeyCapSpec(stringResource(R.string.key_enter)) { actions.pressKey("enter") },
                        KeyCapSpec("Tab") { actions.pressKey("tab") },
                        KeyCapSpec("Esc") { actions.pressKey("escape") },
                    )
                )
                KeyCapGridRow(
                    items = listOf(
                        KeyCapSpec(stringResource(R.string.touchpad_backspace)) {
                            actions.pressKey("backspace")
                        },
                        KeyCapSpec("Del") { actions.pressKey("delete") },
                        KeyCapSpec("Ins") { actions.pressKey("insert") },
                        KeyCapSpec(stringResource(R.string.key_space)) {
                            actions.pressKey("space")
                        },
                    )
                )
            }
        }

        KeyCapSection(title = stringResource(R.string.panel_section_navigation)) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                KeyCapGridRow(
                    items = listOf(
                        KeyCapSpec("Home") { actions.pressKey("home") },
                        KeyCapSpec("End") { actions.pressKey("end") },
                        KeyCapSpec("PgUp") { actions.pressKey("page_up") },
                        KeyCapSpec("PgDn") { actions.pressKey("page_down") },
                    )
                )
                KeyCapGridRow(
                    items = listOf(
                        KeyCapSpec("↑") { actions.pressKey("up") },
                        KeyCapSpec("↓") { actions.pressKey("down") },
                        KeyCapSpec("←") { actions.pressKey("left") },
                        KeyCapSpec("→") { actions.pressKey("right") },
                    )
                )
            }
        }

        KeyCapSection(title = stringResource(R.string.panel_section_system)) {
            KeyCapGridRow(
                items = listOf(
                    KeyCapSpec(stringResource(R.string.key_caps_lock)) {
                        actions.pressKey("caps_lock")
                    },
                    KeyCapSpec(stringResource(R.string.key_print_screen)) {
                        actions.pressKey("print_screen")
                    },
                    KeyCapSpec("Pause") { actions.pressKey("pause") },
                    KeyCapSpec("Num") { actions.pressKey("num_lock") },
                )
            )
        }

        KeyCapSection(title = stringResource(R.string.panel_section_function)) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                (1..12).chunked(6).forEach { row ->
                    KeyCapGridRow(
                        items = row.map { n -> KeyCapSpec("F$n") { actions.pressKey("f$n") } }
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------- 快捷

@Composable
private fun ShortcutsPanel(actions: PanelActions) {
    // 键名只用于构造协议消息，与显示文案完全解耦：
    // 上一版用 label.lowercase() 反查协议键，文案一被翻译就会往服务端发非法键名。
    val combos = listOf(
        listOf("ctrl", "c") to stringResource(R.string.shortcut_copy),
        listOf("ctrl", "v") to stringResource(R.string.shortcut_paste),
        listOf("ctrl", "x") to stringResource(R.string.shortcut_cut),
        listOf("ctrl", "z") to stringResource(R.string.shortcut_undo),
        listOf("ctrl", "y") to stringResource(R.string.shortcut_redo),
        listOf("ctrl", "a") to stringResource(R.string.shortcut_select_all),
        listOf("ctrl", "s") to stringResource(R.string.shortcut_save),
        listOf("ctrl", "f") to stringResource(R.string.shortcut_find),
        listOf("alt", "tab") to stringResource(R.string.shortcut_switch_window),
        listOf("alt", "f4") to stringResource(R.string.shortcut_close_window),
        listOf("win", "d") to stringResource(R.string.shortcut_show_desktop),
        listOf("ctrl", "shift", "escape") to stringResource(R.string.shortcut_task_manager),
    )

    KeyCapSection(title = stringResource(R.string.panel_section_combos)) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            combos.chunked(3).forEach { row ->
                KeyCapGridRow(
                    items = row.map { (keys, label) ->
                        KeyCapSpec(label) { actions.sendCombo(keys) }
                    }
                )
            }
        }
    }
}

// ---------------------------------------------------------------- 消息构造

/**
 * 敲一个键。
 *
 * 锁定的修饰键**不参与**这个过程 —— 它们在用户点按修饰键的那一刻就已经在电脑上
 * 按下，并一直保持到用户再点一次或会话结束。这正是「锁定」的字面含义，也是
 * Ctrl+点击、Ctrl+拖动这类「按住修饰键再操作别的东西」成立的前提。
 *
 * 上一版每次敲键都给它包一层 down/up。单个组合键看起来能work，但敲完第一个键后
 * 电脑上的 Ctrl 其实已经抬起、界面却还亮着。抓包实测：
 *
 *     ctrl down                          ← 锁定 Ctrl
 *     ctrl down / tab press / ctrl up    ← 敲 Tab
 *     mouse_click left click             ← 此刻电脑上 Ctrl 已抬起，Ctrl+点击 静默失效
 *
 * 所以这里只发按键本身。
 */
fun keyPress(key: String): List<OmniPadMessage> = listOf(Keyboard(key, "press"))

/** 显式组合键，例如 `ctrl + shift + escape`。 */
fun comboSequence(keys: List<String>): List<OmniPadMessage> {
    if (keys.isEmpty()) return emptyList()
    val mods = keys.dropLast(1)
    return buildList {
        mods.forEach { add(Keyboard(it, "down")) }
        add(Keyboard(keys.last(), "press"))
        mods.reversed().forEach { add(Keyboard(it, "up")) }
    }
}

/** 释放所有锁定的修饰键，避免转屏或断开时在电脑上留下一个按住的 Ctrl。 */
fun releaseModifiers(activeModifiers: Set<String>): List<OmniPadMessage> =
    MODIFIER_ORDER.filter { it in activeModifiers }.reversed().map { Keyboard(it, "up") }
