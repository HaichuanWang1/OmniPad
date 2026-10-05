package com.omnipad.client.ui.util

import android.view.HapticFeedbackConstants
import android.view.View
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalView

/**
 * 触觉反馈。
 *
 * 用 `View.performHapticFeedback` 而不是 Compose 的 `HapticFeedbackType`：后者在当前
 * Compose 版本里可用类型很少，而且 `TextHandleMove` 映射到的系统常量是 API 27 才有的，
 * 本项目 minSdk 26 会在旧机上踩空。直接指定系统常量更可控。
 *
 * 系统层面关掉触觉（设置里的「触摸时震动」）时 `performHapticFeedback` 会返回 false，
 * 无需自己判断 —— 用户偏好自动生效。
 */
class Haptics internal constructor(
    private val view: View,
    private val enabled: Boolean,
) {
    /** 轻点：修饰键、功能键这类高频、低风险的确认。 */
    fun tick() {
        if (enabled) view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
    }

    /** 虚拟按键：鼠标左右键点击。 */
    fun click() {
        if (enabled) view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
    }

    /** 长按：右键菜单触发、进入拖动锁定这类需要明确感知的节点。 */
    fun longPress() {
        if (enabled) view.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
    }
}

/**
 * @param enabled 用户在设置里的总开关。关闭时不产生任何震动，
 *   但调用点不需要写 if —— 保持调用侧逻辑线性。
 */
@Composable
fun rememberHaptics(enabled: Boolean): Haptics {
    val view = LocalView.current
    return remember(view, enabled) { Haptics(view, enabled) }
}
