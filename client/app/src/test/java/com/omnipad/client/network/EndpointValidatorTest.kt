package com.omnipad.client.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 连接参数校验。
 *
 * 这些规则原先散在 `ConnectScreen` 里，表现为「端口留空静默变成 5800」和
 * 「地址留空直接把 Java 异常原文糊到界面上」。抽成纯函数后可以逐条钉住。
 */
class EndpointValidatorTest {

    private fun ok(host: String, port: String, token: String): ValidatedEndpoint {
        val result = EndpointValidator.validate(host, port, token)
        assertTrue("期望校验通过，实际 $result", result is EndpointCheck.Ok)
        return (result as EndpointCheck.Ok).endpoint
    }

    private fun error(host: String, port: String, token: String): EndpointError {
        val result = EndpointValidator.validate(host, port, token)
        assertTrue("期望校验失败，实际 $result", result is EndpointCheck.Invalid)
        return (result as EndpointCheck.Invalid).error
    }

    @Test
    fun `完整合法的参数通过校验`() {
        val ep = ok("192.168.1.5", "5800", "K7M2P9QR")
        assertEquals("192.168.1.5", ep.host)
        assertEquals(5800, ep.port)
        assertEquals("K7M2P9QR", ep.token)
    }

    @Test
    fun `主机名带点和连字符也合法`() {
        assertEquals("my-pc.local", ok("my-pc.local", "5800", "ABCD2345").host)
    }

    @Test
    fun `两端空白会被裁掉`() {
        val ep = ok("  192.168.1.5  ", " 5800 ", " k7m2p9qr ")
        assertEquals("192.168.1.5", ep.host)
        assertEquals("K7M2P9QR", ep.token)
    }

    @Test
    fun `令牌统一转成大写`() {
        assertEquals("K7M2P9QR", ok("10.0.0.2", "5800", "k7m2p9qr").token)
    }

    @Test
    fun `端口留空采用协议默认值`() {
        assertEquals(EndpointValidator.DEFAULT_PORT, ok("10.0.0.2", "", "K7M2P9QR").port)
        assertEquals(5800, EndpointValidator.DEFAULT_PORT)
    }

    @Test
    fun `地址留空报地址为空而不是拿去连接`() {
        assertEquals(EndpointError.HostBlank, error("", "5800", "K7M2P9QR"))
        assertEquals(EndpointError.HostBlank, error("   ", "5800", "K7M2P9QR"))
    }

    @Test
    fun `地址里带空格会被指出来`() {
        assertEquals(EndpointError.HostHasWhitespace, error("192.168 1.5", "5800", "K7M2P9QR"))
    }

    @Test
    fun `粘进整段 URL 会被识别`() {
        assertEquals(
            EndpointError.HostLooksLikeUrl,
            error("http://192.168.1.5", "5800", "K7M2P9QR"),
        )
        assertEquals(
            EndpointError.HostLooksLikeUrl,
            error("192.168.1.5/omnipad", "5800", "K7M2P9QR"),
        )
    }

    @Test
    fun `地址里带了端口会被自动拆开`() {
        val ep = ok("192.168.1.5:5900", "", "K7M2P9QR")
        assertEquals("192.168.1.5", ep.host)
        assertEquals(5900, ep.port)
    }

    @Test
    fun `地址里的端口优先于端口框`() {
        val ep = ok("192.168.1.5:5900", "5800", "K7M2P9QR")
        assertEquals(5900, ep.port)
    }

    @Test
    fun `地址里带了越界端口报端口越界而不是当成主机名`() {
        // 这条曾经会漏过去：主机名合法字符集允许冒号，于是 "1.2.3.4:99999"
        // 会被当成一个合法主机名，最后以「连不上」的形式失败。
        assertEquals(EndpointError.PortOutOfRange, error("192.168.1.5:99999", "5800", "K7M2P9QR"))
        assertEquals(EndpointError.PortOutOfRange, error("192.168.1.5:0", "5800", "K7M2P9QR"))
    }

    @Test
    fun `端口越界会被拦住`() {
        assertEquals(EndpointError.PortOutOfRange, error("10.0.0.2", "0", "K7M2P9QR"))
        assertEquals(EndpointError.PortOutOfRange, error("10.0.0.2", "65536", "K7M2P9QR"))
        assertEquals(EndpointError.PortOutOfRange, error("10.0.0.2", "99999", "K7M2P9QR"))
    }

    @Test
    fun `端口边界值可用`() {
        assertEquals(1, ok("10.0.0.2", "1", "K7M2P9QR").port)
        assertEquals(65535, ok("10.0.0.2", "65535", "K7M2P9QR").port)
    }

    @Test
    fun `端口非数字会被拦住`() {
        assertEquals(EndpointError.PortNotANumber, error("10.0.0.2", "58a0", "K7M2P9QR"))
    }

    @Test
    fun `令牌留空会被拦住`() {
        assertEquals(EndpointError.TokenBlank, error("10.0.0.2", "5800", ""))
    }

    @Test
    fun `地址含无法识别的字符会被拦住`() {
        assertEquals(EndpointError.HostMalformed, error("我的电脑", "5800", "K7M2P9QR"))
        assertEquals(EndpointError.HostMalformed, error("host;rm", "5800", "K7M2P9QR"))
        assertEquals(EndpointError.HostMalformed, error("host|pipe", "5800", "K7M2P9QR"))
    }

    @Test
    fun `空格优先按空格报错而不是笼统的非法字符`() {
        // "host;rm -rf" 既含空格又含非法字符，报「有空格」对用户更有指向性
        assertEquals(EndpointError.HostHasWhitespace, error("host;rm -rf", "5800", "K7M2P9QR"))
    }

    @Test
    fun `方括号不成对会被拦住`() {
        assertEquals(EndpointError.HostMalformed, error("[fe80::1", "5800", "K7M2P9QR"))
    }

    @Test
    fun `裸 IPv6 字面量不会被误拆成端口`() {
        val ep = ok("fe80::1", "5800", "K7M2P9QR")
        assertEquals("fe80::1", ep.host)
        assertEquals(5800, ep.port)
        assertEquals("::1", ok("::1", "5800", "K7M2P9QR").host)
    }

    @Test
    fun `方括号 IPv6 带端口能拆开`() {
        val ep = ok("[fe80::1]:5900", "", "K7M2P9QR")
        assertEquals("[fe80::1]", ep.host)
        assertEquals(5900, ep.port)
    }

    @Test
    fun `extractPort 只对合法端口给出结果`() {
        assertEquals(5900, EndpointValidator.extractPort("192.168.1.5:5900"))
        assertEquals(5900, EndpointValidator.extractPort("[fe80::1]:5900"))
        assertNull(EndpointValidator.extractPort("192.168.1.5"))
        assertNull(EndpointValidator.extractPort("fe80::1"))
        assertNull(EndpointValidator.extractPort("192.168.1.5:99999"))
    }

    @Test
    fun `先校验地址再校验端口`() {
        // 两个都错时，先报地址 —— 否则用户改完端口才发现地址也是空的
        assertEquals(EndpointError.HostBlank, error("", "99999", ""))
    }
}
