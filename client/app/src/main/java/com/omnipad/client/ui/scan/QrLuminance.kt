package com.omnipad.client.ui.scan

/**
 * 一帧摆正后的灰度图（每字节一个像素）。
 *
 * 不用 data class：`ByteArray` 的 `equals` 是引用比较，data class 生成的那个
 * `equals` 会给人一种「内容相等」的错觉。这里就是个简单的载体。
 */
internal class RotatedLuminance(
    val data: ByteArray,
    val width: Int,
    val height: Int,
)

/**
 * 把相机 YUV 帧里的 Y 平面旋转到「摆正」的方向。
 *
 * 为什么必须自己做：CameraX 交给 `ImageAnalysis` 的缓冲区是**传感器方向**的，
 * 竖屏手机拿到的通常是 90° 横躺的一帧（`imageInfo.rotationDegrees` 告诉你差多少）。
 * ZXing 的定位图形识别不吃旋转 —— 横躺 90° 的二维码它一个都找不到。
 *
 * 顺带处理行距：相机缓冲区每行末尾常有对齐填充，`rowStride` 会大于图像宽度，
 * 直接按 width 逐行读会把每一行都读歪。
 */
internal object QrLuminance {

    /**
     * @param data       Y 平面（可能带行尾填充）
     * @param rowStride  每行实际占用的字节数
     * @param width      有效像素宽度
     * @param height     有效像素高度
     * @param degrees    顺时针需要旋转的角度（`ImageInfo.rotationDegrees`）
     * @return 摆正后的灰度图；参数不可信时返回 null（这一帧直接丢掉，下一帧再来）
     */
    fun rotate(
        data: ByteArray,
        rowStride: Int,
        width: Int,
        height: Int,
        degrees: Int,
    ): RotatedLuminance? {
        if (width <= 0 || height <= 0 || rowStride < width) return null
        if (data.size < rowStride * (height - 1) + width) return null

        // 相机只会给 0/90/180/270，四舍五入到最近的 90° 是为了防御意外取值
        val quarter = (((degrees % 360) + 360) % 360 + 45) / 90 % 4

        val outWidth = if (quarter % 2 == 0) width else height
        val outHeight = if (quarter % 2 == 0) height else width
        val out = ByteArray(outWidth * outHeight)

        for (y in 0 until outHeight) {
            val rowStart = y * outWidth
            for (x in 0 until outWidth) {
                val sx: Int
                val sy: Int
                when (quarter) {
                    0 -> {
                        sx = x
                        sy = y
                    }

                    1 -> {
                        sx = y
                        sy = height - 1 - x
                    }

                    2 -> {
                        sx = width - 1 - x
                        sy = height - 1 - y
                    }

                    else -> {
                        sx = width - 1 - y
                        sy = x
                    }
                }
                out[rowStart + x] = data[sy * rowStride + sx]
            }
        }
        return RotatedLuminance(out, outWidth, outHeight)
    }
}
