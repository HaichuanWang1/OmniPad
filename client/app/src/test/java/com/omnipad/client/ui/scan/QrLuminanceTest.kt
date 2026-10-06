package com.omnipad.client.ui.scan

import com.google.zxing.BinaryBitmap
import com.google.zxing.MultiFormatReader
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.omnipad.client.TestPng
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 相机帧摆正的两组测试。
 *
 * 第一组是纯数学：四个方向的旋转结果必须逐像素对得上，行尾填充必须被跳过。
 * 第二组是端到端的：拿服务端生成的那张真实二维码当「相机拍到的一帧」，
 * 四个方向各转一次，交给 ZXing 去认 —— 手机横竖屏拿到的帧方向不同，
 * 少转一个方向就会出现「明明对着二维码却扫不出来」。
 */
class QrLuminanceTest {

    // ---- 纯数学 ----

    /** 2x3 的帧，值就是它的编号，方便一眼看出转完是什么样。 */
    private val frame = byteArrayOf(1, 2, 3, 4, 5, 6)

    private fun rotate(degrees: Int, rowStride: Int = 2, data: ByteArray = frame) =
        QrLuminance.rotate(data, rowStride, 2, 3, degrees)

    @Test
    fun `zero degrees keeps the frame as is`() {
        val result = rotate(0)!!
        assertEquals(2, result.width)
        assertEquals(3, result.height)
        assertArrayEquals(frame, result.data)
    }

    @Test
    fun `ninety degrees rotates clockwise`() {
        val result = rotate(90)!!
        assertEquals(3, result.width)
        assertEquals(2, result.height)
        // [[1,2],[3,4],[5,6]] 顺时针 90° -> [[5,3,1],[6,4,2]]
        assertArrayEquals(byteArrayOf(5, 3, 1, 6, 4, 2), result.data)
    }

    @Test
    fun `one hundred eighty degrees flips both axes`() {
        val result = rotate(180)!!
        assertArrayEquals(byteArrayOf(6, 5, 4, 3, 2, 1), result.data)
    }

    @Test
    fun `two hundred seventy degrees rotates counter clockwise`() {
        val result = rotate(270)!!
        assertEquals(3, result.width)
        assertEquals(2, result.height)
        assertArrayEquals(byteArrayOf(2, 4, 6, 1, 3, 5), result.data)
    }

    @Test
    fun `negative degrees are normalized`() {
        assertArrayEquals(rotate(270)!!.data, rotate(-90)!!.data)
        assertArrayEquals(rotate(0)!!.data, rotate(360)!!.data)
    }

    @Test
    fun `row padding is skipped`() {
        // 每行末尾有两个填充字节，值故意是 9 —— 读进来就说明行距没用对
        val padded = byteArrayOf(1, 2, 9, 9, 3, 4, 9, 9, 5, 6, 9, 9)
        assertArrayEquals(frame, rotate(0, rowStride = 4, data = padded)!!.data)
        assertArrayEquals(
            byteArrayOf(5, 3, 1, 6, 4, 2),
            rotate(90, rowStride = 4, data = padded)!!.data,
        )
    }

    @Test
    fun `implausible parameters are refused instead of crashing`() {
        assertNull(QrLuminance.rotate(frame, 2, 0, 3, 90))
        assertNull(QrLuminance.rotate(frame, 2, 2, 0, 90))
        assertNull(QrLuminance.rotate(frame, 1, 2, 3, 90))       // rowStride < width
        assertNull(QrLuminance.rotate(byteArrayOf(1, 2, 3), 2, 2, 3, 90))   // 数据不够长
    }

    // ---- 真实二维码 ----

    private fun decode(data: ByteArray, width: Int, height: Int): String? = try {
        val source = PlanarYUVLuminanceSource(data, width, height, 0, 0, width, height, false)
        MultiFormatReader().decode(BinaryBitmap(HybridBinarizer(source))).text
    } catch (_: Exception) {
        null
    }

    /** 把 fixture 当成一帧「相机数据」：可选行尾填充，再按 [degrees] 摆正。 */
    private fun decodeFixture(degrees: Int, padding: Int): String? {
        val image = TestPng.decode(TestPng.readResource("pairing-qr-v1.png"))
        val rowStride = image.width + padding

        val raw = ByteArray(rowStride * image.height)
        for (y in 0 until image.height) {
            for (x in 0 until image.width) {
                raw[y * rowStride + x] = image.gray[y * image.width + x]
            }
        }

        val rotated = QrLuminance.rotate(
            raw, rowStride, image.width, image.height, degrees,
        ) ?: return null
        return decode(rotated.data, rotated.width, rotated.height)
    }

    @Test
    fun `a real qr survives every camera orientation`() {
        val expected = "omnipad://pair?"
        for (degrees in listOf(0, 90, 180, 270)) {
            val text = decodeFixture(degrees, padding = 0)
            assertNotNull("旋转 $degrees° 之后解不出来", text)
            assertTrue("旋转 $degrees° 之后解出来的是 $text", text!!.startsWith(expected))
        }
    }

    @Test
    fun `row padding does not break decoding`() {
        // 相机缓冲区每行末尾常有对齐填充，rowStride 会大于图像宽度
        val text = decodeFixture(0, padding = 13)
        assertNotNull(text)
        assertTrue(text!!.contains("token=K7M2P9QR"))
    }
}
