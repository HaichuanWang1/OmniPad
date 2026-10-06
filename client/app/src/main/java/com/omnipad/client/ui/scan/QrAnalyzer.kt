package com.omnipad.client.ui.scan

import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 从相机帧里读二维码。
 *
 * 只认 QR：`POSSIBLE_FORMATS` 限死之后，ZXing 不会去尝试 Code128、EAN 那些格式，
 * 一帧的解码时间从几十毫秒降到个位数 —— 扫码要的是「举起来就中」。
 *
 * `AtomicBoolean` 是防重入：`ImageAnalysis` 用 KEEP_ONLY_LATEST 背压，理论上
 * 不会堆积，但分析器在别的线程上跑，多一重保险不亏。解不出来是常态（对焦中、
 * 手抖、画面里根本没有码），静默丢掉这一帧继续下一帧。
 */
internal class QrAnalyzer(
    private val onDecoded: (String) -> Unit,
) : ImageAnalysis.Analyzer {

    private val reader = MultiFormatReader().apply {
        setHints(
            mapOf(
                DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),
                // 允许更费力的尝试：二维码在电脑屏幕上有反光、摩尔纹时更稳
                DecodeHintType.TRY_HARDER to true,
            )
        )
    }

    private val busy = AtomicBoolean(false)

    override fun analyze(image: ImageProxy) {
        if (!busy.compareAndSet(false, true)) {
            image.close()
            return
        }
        try {
            decode(image)?.let(onDecoded)
        } catch (_: Throwable) {
            // 单帧失败不是错误，继续下一帧
        } finally {
            busy.set(false)
            image.close()
        }
    }

    private fun decode(image: ImageProxy): String? {
        val plane = image.planes.firstOrNull() ?: return null
        val buffer = plane.buffer
        val bytes = ByteArray(buffer.remaining())
        buffer.get(bytes)

        val luminance = QrLuminance.rotate(
            data = bytes,
            rowStride = plane.rowStride,
            width = image.width,
            height = image.height,
            degrees = image.imageInfo.rotationDegrees,
        ) ?: return null

        val source = PlanarYUVLuminanceSource(
            luminance.data,
            luminance.width,
            luminance.height,
            0,
            0,
            luminance.width,
            luminance.height,
            false,
        )
        return reader.decode(BinaryBitmap(HybridBinarizer(source))).text
    }
}
