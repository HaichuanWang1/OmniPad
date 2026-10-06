package com.omnipad.client.network

import com.google.zxing.BinaryBitmap
import com.google.zxing.MultiFormatReader
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.omnipad.client.TestPng
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 二维码载荷的解析测试。
 *
 * 契约在 `docs/qr-payload.md`，服务端实现在 `server/qr.py`，服务端那份的用例在
 * `server/test_qr.py`。**这里的重点不是重复那些用例**，而是两件只有客户端能做的事：
 *
 * 1. 解析出来的东西必须与手输走同一套校验（`EndpointValidator`），
 *    否则「扫码等同于手动输入」只是句口号；
 * 2. 服务端那个自研的纯 Python 二维码编码器到底能不能被**真实解码器**读出来 ——
 *    那就是 `pairing-qr-v1.png`，由 `server/test_qr.py --write-fixture` 生成。
 *    编码器写错一个模块，这条用例就会红。
 */
class PairingQrTest {

    private val version = PROTOCOL_VERSION

    private fun parse(text: String) = PairingQr.parse(text, version)

    private fun ok(text: String): QrPairing {
        val result = parse(text)
        assertTrue("期望解析成功，实际是 $result", result is QrScan.Ok)
        return (result as QrScan.Ok).pairing
    }

    private fun error(text: String): QrError {
        val result = parse(text)
        assertTrue("期望解析失败，实际是 $result", result is QrScan.Invalid)
        return (result as QrScan.Invalid).error
    }

    // ---- 正常路径 ----

    @Test
    fun `parses the canonical payload`() {
        val pairing = ok(
            "omnipad://pair?v=$version&host=192.168.1.5&port=5800" +
                "&token=K7M2P9QR&name=DESKTOP-ABC"
        )
        assertEquals("192.168.1.5", pairing.host)
        assertEquals(5800, pairing.port)
        assertEquals("K7M2P9QR", pairing.token)
        assertEquals("DESKTOP-ABC", pairing.name)
    }

    @Test
    fun `name is optional`() {
        assertNull(ok("omnipad://pair?v=$version&host=10.0.0.1&port=5800&token=A1B2C3D4").name)
    }

    @Test
    fun `token is normalized to upper case`() {
        assertEquals(
            "ABCD2345",
            ok("omnipad://pair?v=$version&host=a&port=1&token=abcd2345").token,
        )
    }

    @Test
    fun `scheme and action are case insensitive`() {
        assertEquals("a", ok("OMNIPAD://PAIR?v=$version&host=a&port=1&token=B").host)
    }

    @Test
    fun `space in the name is percent encoded`() {
        assertEquals(
            "HAICHUAN PC",
            ok("omnipad://pair?v=$version&host=a&port=1&token=B&name=HAICHUAN%20PC").name,
        )
    }

    @Test
    fun `non ascii name is decoded from utf8`() {
        assertEquals(
            "我的电脑",
            ok("omnipad://pair?v=$version&host=a&port=1&token=B" +
                "&name=%E6%88%91%E7%9A%84%E7%94%B5%E8%84%91").name,
        )
    }

    @Test
    fun `plus is not a space`() {
        // 本项目刻意偏离 x-www-form-urlencoded：一旦还原，令牌里合法的 '+' 会被改写
        assertEquals("A+B", ok("omnipad://pair?v=$version&host=a&port=1&token=A%2BB").token)
    }

    @Test
    fun `equals inside a value survives`() {
        assertEquals("A=B", ok("omnipad://pair?v=$version&host=a&port=1&token=A=B").token)
    }

    @Test
    fun `unknown fields are ignored`() {
        // 新服务端 + 旧 App 仍要能用
        assertEquals(
            "a",
            ok("omnipad://pair?v=$version&host=a&port=1&token=B&future=1&alt=c").host,
        )
    }

    @Test
    fun `fragment is ignored`() {
        assertEquals("B", ok("omnipad://pair?v=$version&host=a&port=1&token=B#x=1").token)
    }

    @Test
    fun `host that embeds a port is split like manual input`() {
        // 服务端不会这么发，但用户可能手抄/伪造；规则与手输保持一致
        val pairing = ok("omnipad://pair?v=$version&host=10.0.0.9%3A5900&port=5800&token=B")
        assertEquals("10.0.0.9", pairing.host)
        assertEquals(5900, pairing.port)
    }

    // ---- 失败路径 ----

    @Test
    fun `not an omnipad link`() {
        assertEquals(QrError.NotOmniPadLink, error("https://example.com/x"))
        assertEquals(QrError.NotOmniPadLink, error("just some text"))
        assertEquals(QrError.NotOmniPadLink, error("omnipad://connect?v=$version"))
        assertEquals(QrError.NotOmniPadLink, error(""))
    }

    @Test
    fun `missing fields are named`() {
        val full = mapOf("v" to version, "host" to "a", "port" to "1", "token" to "B")
        for (field in full.keys) {
            val query = full.filterKeys { it != field }
                .entries.joinToString("&") { "${it.key}=${it.value}" }
            assertEquals(
                "缺 $field 时应报 MissingField",
                QrError.MissingField(field),
                error("omnipad://pair?$query"),
            )
        }
    }

    @Test
    fun `empty value counts as missing`() {
        assertEquals(
            QrError.MissingField("host"),
            error("omnipad://pair?v=$version&host=&port=1&token=B"),
        )
    }

    @Test
    fun `duplicate field is rejected`() {
        assertEquals(
            QrError.DuplicateField("token"),
            error("omnipad://pair?v=$version&host=a&port=1&token=B&token=C"),
        )
    }

    @Test
    fun `version mismatch reports both versions`() {
        assertEquals(
            QrError.VersionMismatch("1.0", version),
            error("omnipad://pair?v=1.0&host=a&port=1&token=B"),
        )
    }

    @Test
    fun `bad port`() {
        assertEquals(QrError.BadPort, error("omnipad://pair?v=$version&host=a&port=abc&token=B"))
        assertEquals(QrError.BadPort, error("omnipad://pair?v=$version&host=a&port=0&token=B"))
        assertEquals(
            QrError.BadPort,
            error("omnipad://pair?v=$version&host=a&port=65536&token=B"),
        )
    }

    @Test
    fun `bad host`() {
        // 与手输同一套规则：带协议的、带空格的、带路径的都拒
        assertTrue(error("omnipad://pair?v=$version&host=a%2Fb&port=1&token=B") is QrError.BadHost)
        assertTrue(error("omnipad://pair?v=$version&host=a%20b&port=1&token=B") is QrError.BadHost)
    }

    @Test
    fun `bad token`() {
        assertEquals(
            QrError.BadToken,
            error("omnipad://pair?v=$version&host=a&port=1&token=%20"),
        )
    }

    @Test
    fun `overlong content is rejected`() {
        assertEquals(QrError.TooLong, error("omnipad://pair?x=" + "A".repeat(3000)))
    }

    @Test
    fun `broken percent escape is not an omnipad link`() {
        assertEquals(
            QrError.NotOmniPadLink,
            error("omnipad://pair?v=$version&host=a&port=1&token=%ZZ"),
        )
        assertEquals(
            QrError.NotOmniPadLink,
            error("omnipad://pair?v=$version&host=a&port=1&token=%A"),
        )
    }

    // ---- 与服务端编码器的交叉验证 ----

    @Test
    fun `decodes the fixture produced by the python encoder`() {
        // 期望值必须与 server/test_qr.py 里的 FIXTURE_* 常量一致
        val pairing = ok(decodeFixture())

        assertEquals("192.168.1.5", pairing.host)
        assertEquals(5800, pairing.port)
        assertEquals("K7M2P9QR", pairing.token)
        assertEquals("DESKTOP-ABC", pairing.name)
    }

    @Test
    fun `fixture carries the current protocol version`() {
        // 协议版本一变，fixture 就该重新生成（server/test_qr.py --write-fixture），
        // 这条用例会先红，提醒你两端的版本号已经对不上了
        assertTrue(decodeFixture().contains("v=$version"))
    }

    /** 用 ZXing 解码服务端生成的 fixture —— 这就是「自研编码器能不能扫」的答案。 */
    private fun decodeFixture(): String {
        val image = TestPng.decode(TestPng.readResource("pairing-qr-v1.png"))
        val pixels = IntArray(image.width * image.height) { index ->
            val value = image.gray[index].toInt() and 0xFF
            (0xFF shl 24) or (value shl 16) or (value shl 8) or value
        }
        val bitmap = BinaryBitmap(
            HybridBinarizer(RGBLuminanceSource(image.width, image.height, pixels))
        )
        return MultiFormatReader().decode(bitmap).text
    }
}
