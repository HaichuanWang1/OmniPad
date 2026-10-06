package com.omnipad.client.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.omnipad.client.R
import com.omnipad.client.network.PairingQr
import com.omnipad.client.network.QrError
import com.omnipad.client.network.QrPairing
import com.omnipad.client.network.QrScan
import com.omnipad.client.ui.components.NoticeCard
import com.omnipad.client.ui.qrErrorText
import com.omnipad.client.ui.scan.QrAnalyzer
import com.omnipad.client.ui.util.rememberHaptics
import kotlinx.coroutines.launch
import java.util.concurrent.Executors

/** 取景框的目标高度。实际取值还要看屏幕有多高，见 [previewHeight]。 */
private val PREVIEW_HEIGHT = 260.dp

/**
 * 取景框的实际高度。
 *
 * 面板是**半屏**弹出的，所以内容不能比半屏还高 —— 小屏手机上（640dp 上下）
 * 260dp 的预览会把标题和关闭按钮顶出可视区，用户得先把面板拖上去才看得全。
 * 按屏高的 30% 收一收，两种屏上都正好落在半屏以内。
 */
@Composable
private fun previewHeight(): Dp {
    val screenHeight = LocalConfiguration.current.screenHeightDp.dp
    return minOf(PREVIEW_HEIGHT, screenHeight * 0.3f)
}

/**
 * 扫码面板。
 *
 * **不是独立页面**，而是从底部弹出的半屏面板：扫码是连接页上的一个动作，
 * 不是一段新旅程。做成整页会让用户在「扫码页」和「连接页」之间来回切，
 * 而这两者本来就在讲同一件事 —— 填地址、端口、令牌。
 *
 * 解析失败也在这里就地显示，而不是关掉面板再弹一个 Toast：用户手里还举着手机，
 * 错误提示必须和他正在看的东西在同一个视野里。版本不匹配的提示尤其重要 ——
 * 过去这种情况会一路走到握手才失败，用户看到的是 `AUTH_FAILED`，以为令牌填错了。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScanSheet(
    expectedVersion: String,
    hapticsEnabled: Boolean,
    onScanned: (QrPairing) -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = false)
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val mainHandler = remember { Handler(Looper.getMainLooper()) }
    val haptics = rememberHaptics(hapticsEnabled)

    var error by remember { mutableStateOf<QrError?>(null) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    var handled by remember { mutableStateOf(false) }

    val hasCamera = remember {
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)
    }
    var granted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED
        )
    }
    // 是否已经问过一次权限。用它而不是「用户点了拒绝」：被永久拒绝时系统会直接
    // 返回 false，连对话框都不弹，只有这个标志能让我们换成「去系统设置里开」的说明。
    var asked by remember { mutableStateOf(false) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { ok ->
        granted = ok
        asked = true
    }

    // 打开面板就问权限：用户点「扫码连接」的意图已经很明确了，
    // 再让他多点一次「允许」按钮纯属多余。
    LaunchedEffect(hasCamera) {
        if (hasCamera && !granted) {
            asked = true
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    fun close() {
        scope.launch { sheetState.hide() }.invokeOnCompletion { onDismiss() }
    }

    /** 从相机线程回来的一帧文本。 */
    fun accept(text: String) {
        if (handled) return
        when (val scan = PairingQr.parse(text, expectedVersion)) {
            is QrScan.Ok -> {
                handled = true
                haptics.longPress()
                onScanned(scan.pairing)
                close()
            }

            is QrScan.Invalid -> {
                // 同一张读不出来的码每帧都会送过来，内容没变就别反复刷新提示
                if (error != scan.error) error = scan.error
            }
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .windowInsetsPadding(WindowInsets.navigationBars)
                .padding(bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = stringResource(R.string.scan_title),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
            )

            when {
                !hasCamera -> ScanNotice(
                    title = stringResource(R.string.scan_no_camera),
                    hint = null,
                )

                !granted -> {
                    ScanNotice(
                        title = stringResource(R.string.scan_permission_title),
                        hint = stringResource(
                            if (asked) R.string.scan_permission_denied
                            else R.string.scan_permission_message
                        ),
                    )
                    Button(
                        onClick = {
                            permissionLauncher.launch(Manifest.permission.CAMERA)
                        },
                        modifier = Modifier.fillMaxWidth().height(48.dp),
                        shape = MaterialTheme.shapes.medium,
                    ) {
                        Icon(Icons.Default.CameraAlt, contentDescription = null)
                        Spacer(Modifier.size(8.dp))
                        Text(stringResource(R.string.scan_permission_grant))
                    }
                }

                cameraError != null -> ScanNotice(
                    title = stringResource(R.string.scan_camera_failed, cameraError.orEmpty()),
                    hint = null,
                )

                else -> {
                    val height = previewHeight()
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(height)
                            .clip(RoundedCornerShape(16.dp))
                            .background(MaterialTheme.colorScheme.surfaceVariant),
                    ) {
                        CameraPreview(
                            onText = { text -> mainHandler.post { accept(text) } },
                            onError = { message -> mainHandler.post { cameraError = message } },
                            modifier = Modifier.fillMaxWidth().height(height),
                        )
                        // 取景框：四个角上的直角标记比整块半透明遮罩更不挡视线
                        ViewfinderOverlay(height)
                    }
                    Text(
                        text = stringResource(R.string.scan_hint),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.align(Alignment.CenterHorizontally),
                    )
                }
            }

            error?.let { NoticeCard(qrErrorText(it)) }

            TextButton(onClick = onDismiss, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.scan_close))
            }
        }
    }
}

@Composable
private fun CameraPreview(
    onText: (String) -> Unit,
    onError: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val executor = remember { Executors.newSingleThreadExecutor() }
    val previewView = remember {
        PreviewView(context).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
    }
    // 分析器只建一次：每帧新建一个 MultiFormatReader 会把解码时间拉高一个量级。
    // 回调用 rememberUpdatedState 取最新那份 —— 否则它会一直抓着第一次组合时
    // 那个 lambda，里面的 onScanned 也就是旧的。
    val currentOnText by rememberUpdatedState(onText)
    val analyzer = remember { QrAnalyzer { text -> currentOnText(text) } }

    DisposableEffect(lifecycleOwner, previewView) {
        val providerFuture = ProcessCameraProvider.getInstance(context)
        providerFuture.addListener(
            {
                try {
                    val provider = providerFuture.get()
                    val preview = Preview.Builder().build().also {
                        it.setSurfaceProvider(previewView.surfaceProvider)
                    }
                    val analysis = ImageAnalysis.Builder()
                        // 解码跟不上就丢帧，绝不排队 —— 排队的结果是画面越来越延迟
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                        .also { it.setAnalyzer(executor, analyzer) }

                    provider.unbindAll()
                    provider.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        preview,
                        analysis,
                    )
                } catch (error: Exception) {
                    onError(error.message ?: error.javaClass.simpleName)
                }
            },
            ContextCompat.getMainExecutor(context),
        )

        onDispose {
            runCatching { providerFuture.get().unbindAll() }
            executor.shutdown()
        }
    }

    AndroidView(factory = { previewView }, modifier = modifier)
}

/** 取景框四角。用主题色而不是写死的白色，深浅两套主题下都能看清。 */
@Composable
private fun ViewfinderOverlay(height: Dp) {
    val color = MaterialTheme.colorScheme.primary
    Box(modifier = Modifier.fillMaxWidth().height(height)) {
        listOf(
            Alignment.TopStart,
            Alignment.TopEnd,
            Alignment.BottomStart,
            Alignment.BottomEnd,
        ).forEach { corner ->
            Box(modifier = Modifier.align(corner).padding(18.dp)) {
                // 两条边都对齐到同一个角，拼出来的才是那个角上的直角
                Box(
                    modifier = Modifier
                        .align(corner)
                        .size(width = 26.dp, height = 3.dp)
                        .background(color)
                )
                Box(
                    modifier = Modifier
                        .align(corner)
                        .size(width = 3.dp, height = 26.dp)
                        .background(color)
                )
            }
        }
    }
}

@Composable
private fun ScanNotice(title: String, hint: String?) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.surfaceVariant,
        contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(text = title, style = MaterialTheme.typography.titleSmall)
            if (hint != null) {
                Spacer(Modifier.height(4.dp))
                Text(text = hint, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
