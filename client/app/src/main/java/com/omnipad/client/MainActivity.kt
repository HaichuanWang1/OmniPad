package com.omnipad.client

import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.omnipad.client.ui.OmniPadApp
import com.omnipad.client.ui.theme.OmniPadTheme
import com.omnipad.client.ui.theme.isDarkTheme

/**
 * 唯一的 Activity。
 *
 * 只负责主题、系统栏与内容装配。所有业务状态都在 [MainViewModel] 里，
 * 因此转屏不再重建连接（上一版把连接对象作为 Activity 字段持有，
 * `onDestroy` 里断开，转一下手机就掉线）。
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        applyDisplayCutoutMode()

        setContent {
            val viewModel: MainViewModel = viewModel()
            val settings by viewModel.settings.collectAsState()
            val dark = isDarkTheme(settings.themeMode)

            // 系统栏图标的明暗必须跟着**实际生效**的主题走：用户手动选了浅色
            // 但状态栏还是白色图标的话，状态栏会直接看不见。
            val view = LocalView.current
            SideEffect {
                WindowCompat.getInsetsController(window, view).apply {
                    isAppearanceLightStatusBars = !dark
                    isAppearanceLightNavigationBars = !dark
                }
            }

            OmniPadTheme(
                themeMode = settings.themeMode,
                dynamicColor = settings.dynamicColor,
            ) {
                OmniPadApp(viewModel)
            }
        }
    }

    /**
     * 允许内容延伸到刘海区域。
     *
     * `enableEdgeToEdge()` 内部也会设这个值，但它是**就地修改** `window.attributes`
     * 返回的对象、不经过 `setAttributes`。在部分 OEM 系统（实测 OPPO ColorOS，
     * Android 11）上这个改动不会生效：横屏时窗口被刘海裁掉 56px，
     * `dumpsys window` 里 `mFrame=[56,0][1600,720]`，那条区域由系统填成黑色。
     *
     * 这里用 `window.attributes = ...` 显式走一遍 setter，触发一次真正的
     * `setAttributes`。内容本身仍然靠 `WindowInsets.safeDrawing` 避开刘海，
     * 这样刘海区域会被页面背景填满，而不是留一条黑边。
     */
    private fun applyDisplayCutoutMode() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return
        window.attributes = window.attributes.apply {
            layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
    }
}
