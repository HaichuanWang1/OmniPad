package com.omnipad.client.data

import android.content.Context
import android.content.SharedPreferences
import com.omnipad.client.ui.theme.ThemeMode
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 用户设置。
 *
 * 全部字段都有合理默认值，读不到时回落到默认 —— 这样新增设置项不需要写迁移代码，
 * 老版本存下的 JSON 里没有这个键也能正常启动。
 */
data class Settings(
    val themeMode: ThemeMode = ThemeMode.SYSTEM,
    val dynamicColor: Boolean = false,
    /** 指针速度倍率。相对位移模式下，手机 DPI 与电脑分辨率不匹配时靠它补偿。 */
    val pointerSensitivity: Float = 1.0f,
    /** 滚动速度倍率。 */
    val scrollSensitivity: Float = 1.0f,
    /** 触觉反馈。远程控制时屏幕本身不动，震动是唯一的本地确认通道。 */
    val hapticsEnabled: Boolean = true,
    /** 连接期间保持屏幕常亮：用手机当键盘打字时，屏幕不该因为没碰手机而熄灭。 */
    val keepScreenOn: Boolean = true,
    /** 连续丢失心跳后自动断开。 */
    val autoDisconnect: Boolean = true,
    /** 意外断开后自动重连。 */
    val autoReconnect: Boolean = true,
) {
    companion object {
        /** 倍率的可选档位，UI 用它渲染滑块刻度。 */
        val SENSITIVITY_STEPS = listOf(0.5f, 0.75f, 1.0f, 1.25f, 1.5f, 2.0f, 2.5f, 3.0f)
    }
}

/**
 * 设置的持久化存储。
 *
 * 用 SharedPreferences 而不是 DataStore：这里只有 8 个标量，DataStore 会多引入一个
 * 依赖和一套 Flow/协程 API，收益不成比例。写入是同步 apply()，读取在构造时一次性完成，
 * 之后走 StateFlow —— 组合里不会碰到磁盘。
 *
 * 连接层的两个开关（autoDisconnect / autoReconnect）不在这里直连，由 ViewModel 观察
 * 设置变化后同步过去，避免存储层反向依赖网络层。
 */
class SettingsStore(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("omnipad_settings", Context.MODE_PRIVATE)

    private val _settings = MutableStateFlow(load())
    val settings: StateFlow<Settings> = _settings.asStateFlow()

    private fun load(): Settings = Settings(
        themeMode = prefs.getString(KEY_THEME_MODE, null)
            ?.let { name -> ThemeMode.entries.firstOrNull { it.name == name } }
            ?: ThemeMode.SYSTEM,
        dynamicColor = prefs.getBoolean(KEY_DYNAMIC_COLOR, false),
        pointerSensitivity = prefs.getFloat(KEY_POINTER_SENSITIVITY, 1.0f),
        scrollSensitivity = prefs.getFloat(KEY_SCROLL_SENSITIVITY, 1.0f),
        hapticsEnabled = prefs.getBoolean(KEY_HAPTICS, true),
        keepScreenOn = prefs.getBoolean(KEY_KEEP_SCREEN_ON, true),
        autoDisconnect = prefs.getBoolean(KEY_AUTO_DISCONNECT, true),
        autoReconnect = prefs.getBoolean(KEY_AUTO_RECONNECT, true),
    )

    /** 按 [transform] 修改当前设置并落盘，同时把新值推给观察者。 */
    fun update(transform: (Settings) -> Settings) {
        val next = transform(_settings.value)
        if (next == _settings.value) return
        _settings.value = next
        prefs.edit().apply {
            putString(KEY_THEME_MODE, next.themeMode.name)
            putBoolean(KEY_DYNAMIC_COLOR, next.dynamicColor)
            putFloat(KEY_POINTER_SENSITIVITY, next.pointerSensitivity)
            putFloat(KEY_SCROLL_SENSITIVITY, next.scrollSensitivity)
            putBoolean(KEY_HAPTICS, next.hapticsEnabled)
            putBoolean(KEY_KEEP_SCREEN_ON, next.keepScreenOn)
            putBoolean(KEY_AUTO_DISCONNECT, next.autoDisconnect)
            putBoolean(KEY_AUTO_RECONNECT, next.autoReconnect)
        }.apply()
    }

    private companion object {
        const val KEY_THEME_MODE = "theme_mode"
        const val KEY_DYNAMIC_COLOR = "dynamic_color"
        const val KEY_POINTER_SENSITIVITY = "pointer_sensitivity"
        const val KEY_SCROLL_SENSITIVITY = "scroll_sensitivity"
        const val KEY_HAPTICS = "haptics"
        const val KEY_KEEP_SCREEN_ON = "keep_screen_on"
        const val KEY_AUTO_DISCONNECT = "auto_disconnect"
        const val KEY_AUTO_RECONNECT = "auto_reconnect"
    }
}
