package com.omnipad.client.ui

import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.omnipad.client.MainViewModel
import com.omnipad.client.network.ConnectionNotice
import com.omnipad.client.ui.screens.ConnectScreen
import com.omnipad.client.ui.screens.SettingsSheet
import com.omnipad.client.ui.screens.TouchpadScreen

/**
 * 顶层界面装配。
 *
 * 只做三件事：把 ViewModel 的状态分发给两个页面、承载 Snackbar、管理设置面板的显隐。
 * 业务状态一概不在这里保存 —— 上一版把这些全放在 `MainActivity` 的字段里，
 * 直接导致转屏掉线。
 */
@Composable
fun OmniPadApp(viewModel: MainViewModel, modifier: Modifier = Modifier) {
    val connectionState by viewModel.connectionState.collectAsState()
    val inSession by viewModel.inSession.collectAsState()
    val reconnectAttempt by viewModel.reconnectAttempt.collectAsState()
    val latencyMs by viewModel.latencyMs.collectAsState()
    val failure by viewModel.failure.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val recentHosts by viewModel.recentHosts.collectAsState()
    val lastEndpoint by viewModel.lastEndpoint.collectAsState()

    var showSettings by remember { mutableStateOf(false) }
    val snackbarHostState = remember { SnackbarHostState() }

    // 文案必须在组合里解析（stringResource 是 @Composable），所以先落到状态上，
    // 再交给 effect 去弹 —— effect 内部不能调用 @Composable。
    var pendingNotice by remember { mutableStateOf<ConnectionNotice?>(null) }
    LaunchedEffect(viewModel) {
        viewModel.snackbars.collect { pendingNotice = it }
    }
    val snackbarText = pendingNotice?.let { noticeShortText(it) }
    LaunchedEffect(snackbarText) {
        if (snackbarText != null) {
            snackbarHostState.showSnackbar(snackbarText)
            pendingNotice = null
        }
    }

    // 背景画在这里，而不是依赖 windowBackground。
    //
    // windowBackground 只跟随**系统**的深色模式，而用户可以在设置里手动把主题切成
    // 深色 —— 系统浅色 + App 深色时，窗口背景会从底下透出来。Compose 自己画才能
    // 保证底色永远和当前生效的主题一致。
    //
    // （另外实测发现：无头模拟器上 windowBackground 根本没有被绘制，页面底色是纯黑。
    //   不管是模拟器怪癖还是真机行为，自己画都不吃亏。）
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
    ) {
        Crossfade(
            targetState = inSession,
            animationSpec = tween(durationMillis = 220),
            label = "session",
        ) { session ->
            if (session) {
                TouchpadScreen(
                    connectionState = connectionState,
                    reconnectAttempt = reconnectAttempt,
                    latencyMs = latencyMs,
                    settings = settings,
                    onSendMessage = { viewModel.sendMessage(it) },
                    onDisconnect = { viewModel.disconnect() },
                    onOpenSettings = { showSettings = true },
                    modifier = Modifier.fillMaxSize(),
                )
            } else {
                ConnectScreen(
                    connectionState = connectionState,
                    reconnectAttempt = reconnectAttempt,
                    failure = failure,
                    recentHosts = recentHosts,
                    initialEndpoint = lastEndpoint,
                    hapticsEnabled = settings.hapticsEnabled,
                    onConnect = { host, port, token ->
                        viewModel.connect(host, port, token)
                    },
                    onCancelReconnect = { viewModel.disconnect() },
                    onDeleteHost = { host, port -> viewModel.deleteHost(host, port) },
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }

        SnackbarHost(
            hostState = snackbarHostState,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .windowInsetsPadding(
                    WindowInsets.safeDrawing.only(WindowInsetsSides.Bottom)
                ),
        )
    }

    if (showSettings) {
        SettingsSheet(
            settings = settings,
            onUpdate = { transform -> viewModel.updateSettings(transform) },
            onDismiss = { showSettings = false },
        )
    }
}
