package com.omnipad.client.network

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Protocol.kt 的编解码测试。
 *
 * 不触碰 Android 框架，跑在普通 JVM 上（org.json 由 testImplementation 提供）。
 */
class ProtocolTest {

    private fun obj(msg: OmniPadMessage) = JSONObject(msg.toJson())

    // ---- 编码 ----

    @Test
    fun `handshake 带上版本与令牌`() {
        val o = obj(Handshake(version = "1.0", token = "GBGUAWW9"))
        assertEquals("handshake", o.getString("type"))
        assertEquals("1.0", o.getString("version"))
        assertEquals("GBGUAWW9", o.getString("token"))
    }

    @Test
    fun `mouse_move 保留 dx dy 的符号`() {
        val o = obj(MouseMove(dx = -12, dy = 34))
        assertEquals("mouse_move", o.getString("type"))
        assertEquals(-12, o.getInt("dx"))
        assertEquals(34, o.getInt("dy"))
    }

    @Test
    fun `scroll 的 delta 是滚轮格数`() {
        // 服务端会乘 WHEEL_DELTA(120)，这里必须是格数而不是像素。
        val o = obj(Scroll(delta = -3))
        assertEquals("scroll", o.getString("type"))
        assertEquals(-3, o.getInt("delta"))
    }

    @Test
    fun `mouse_click 与 keyboard 携带动作`() {
        val click = obj(MouseClick(button = "left", action = "down"))
        assertEquals("left", click.getString("button"))
        assertEquals("down", click.getString("action"))

        val key = obj(Keyboard(key = "ctrl", action = "up"))
        assertEquals("ctrl", key.getString("key"))
        assertEquals("up", key.getString("action"))
    }

    @Test
    fun `文本里的引号与反斜杠不会破坏 JSON`() {
        // 这正是当初从字符串插值改成 JSONObject 的原因：
        // 旧写法遇到引号会产出非法 JSON，整条消息被服务端丢弃。
        val nasty = "他说\"你好\" \\ 反斜杠 \t 制表"
        val o = obj(TextInput(nasty))
        assertEquals(nasty, o.getString("text"))
    }

    @Test
    fun `中文与 BMP 外字符原样往返`() {
        val text = "你好，世界！🙂 𝕏 𝔘"
        assertEquals(text, obj(TextInput(text)).getString("text"))
    }

    @Test
    fun `heartbeat 与 heartbeat_ack 不带载荷`() {
        val hb = obj(Heartbeat)
        assertEquals("heartbeat", hb.getString("type"))
        assertEquals(1, hb.length())

        val ack = obj(HeartbeatAck)
        assertEquals("heartbeat_ack", ack.getString("type"))
        assertEquals(1, ack.length())
    }

    // ---- 解码 ----

    @Test
    fun `解析 handshake_ack 的 version`() {
        assertEquals(
            HandshakeAck("1.0"),
            parseMessage("""{"type":"handshake_ack","version":"1.0"}"""),
        )
    }

    @Test
    fun `handshake_ack 缺少 version 时回落为空串`() {
        assertEquals(HandshakeAck(""), parseMessage("""{"type":"handshake_ack"}"""))
    }

    @Test
    fun `解析 error 的 code 与 message`() {
        assertEquals(
            Error("AUTH_FAILED", "配对令牌不正确"),
            parseMessage("""{"type":"error","code":"AUTH_FAILED","message":"配对令牌不正确"}"""),
        )
    }

    @Test
    fun `heartbeat_ack 解析为单例`() {
        assertTrue(parseMessage("""{"type":"heartbeat_ack"}""") === HeartbeatAck)
    }

    @Test
    fun `未知类型返回 null`() {
        assertNull(parseMessage("""{"type":"who_knows"}"""))
    }

    @Test
    fun `缺少 type 字段返回 null`() {
        assertNull(parseMessage("""{"version":"1.0"}"""))
    }

    @Test
    fun `非法 JSON 返回 null 而不是抛异常`() {
        // 服务端异常断开时可能送来半行，不能让它把读协程炸掉。
        assertNull(parseMessage(""))
        assertNull(parseMessage("not json at all"))
        assertNull(parseMessage("""{"type":"""))
    }
}
