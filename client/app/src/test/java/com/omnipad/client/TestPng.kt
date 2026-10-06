package com.omnipad.client

import java.io.ByteArrayOutputStream
import java.util.zip.Inflater

/**
 * 测试用的极简 PNG 解码器。
 *
 * 为什么不用 `javax.imageio`：Android 单元测试编译时以 `android.jar` 为引导类路径，
 * JDK 的 `java.desktop` 根本不在上面，`ImageIO` 编译不过。而 `java.util.zip.Inflater`
 * 是 Android 也有的，于是这里自己解。
 *
 * 只支持**我们自己那个编码器**产出的形态：8 位真彩色、每行 filter 一律 0
 * （`server/qr.py` 的 `png_bytes` 就是这么写的）。它不是一个通用 PNG 解码器，
 * 也不需要是 —— 它要证明的是「服务端产出的那些字节，客户端能读回来」。
 */
object TestPng {

    class GrayImage(val gray: ByteArray, val width: Int, val height: Int)

    fun decode(bytes: ByteArray): GrayImage {
        require(bytes.size > 8 && bytes[0] == 0x89.toByte() && bytes[1] == 'P'.code.toByte()) {
            "不是 PNG：签名不对"
        }

        var offset = 8
        var width = 0
        var height = 0
        var colorType = -1
        val idat = ByteArrayOutputStream()

        while (offset + 8 <= bytes.size) {
            val length = readInt(bytes, offset)
            val type = String(bytes, offset + 4, 4, Charsets.US_ASCII)
            val dataStart = offset + 8
            when (type) {
                "IHDR" -> {
                    width = readInt(bytes, dataStart)
                    height = readInt(bytes, dataStart + 4)
                    colorType = bytes[dataStart + 9].toInt()
                }

                "IDAT" -> idat.write(bytes, dataStart, length)
                "IEND" -> break
            }
            offset = dataStart + length + 4
        }

        require(colorType == 2) { "只支持 8 位真彩色 PNG，实际 colorType=$colorType" }
        require(width > 0 && height > 0) { "PNG 尺寸不合法：${width}x$height" }

        val rowBytes = width * 3 + 1
        val raw = ByteArray(rowBytes * height)
        val inflater = Inflater()
        try {
            inflater.setInput(idat.toByteArray())
            var written = 0
            while (written < raw.size && !inflater.finished()) {
                val count = inflater.inflate(raw, written, raw.size - written)
                if (count == 0 && (inflater.needsInput() || inflater.needsDictionary())) break
                written += count
            }
            require(written == raw.size) { "PNG 数据不完整：$written/${raw.size}" }
        } finally {
            inflater.end()
        }

        val gray = ByteArray(width * height)
        for (y in 0 until height) {
            val rowStart = y * rowBytes
            require(raw[rowStart].toInt() == 0) { "第 $y 行的 filter 不是 0，本解码器不支持" }
            for (x in 0 until width) {
                gray[y * width + x] = raw[rowStart + 1 + x * 3]
            }
        }
        return GrayImage(gray, width, height)
    }

    /** 读 `src/test/resources` 下的文件。 */
    fun readResource(name: String): ByteArray {
        val stream = TestPng::class.java.getResourceAsStream("/$name")
            ?: error("测试资源缺失：client/app/src/test/resources/$name")
        return stream.use { it.readBytes() }
    }

    private fun readInt(bytes: ByteArray, offset: Int): Int =
        ((bytes[offset].toInt() and 0xFF) shl 24) or
            ((bytes[offset + 1].toInt() and 0xFF) shl 16) or
            ((bytes[offset + 2].toInt() and 0xFF) shl 8) or
            (bytes[offset + 3].toInt() and 0xFF)
}
