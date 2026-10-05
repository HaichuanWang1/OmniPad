package com.omnipad.client.network

import org.json.JSONObject

sealed class OmniPadMessage {
    abstract fun toJson(): String
}

/**
 * 统一用 JSONObject 构造消息。
 *
 * 原先用字符串插值拼 JSON（`"""{"key":"$value"}"""`），一旦字段里出现引号或
 * 反斜杠就会产出非法 JSON。当前取值虽然可控，但没必要留这个坑。
 */
private fun jsonMessage(type: String, vararg fields: Pair<String, Any>): String {
    val obj = JSONObject().put("type", type)
    fields.forEach { (key, value) -> obj.put(key, value) }
    return obj.toString()
}

/**
 * 握手请求。version 必须与服务端 `handlers.PROTOCOL_VERSION` 一致，
 * 以及 docs/protocol.md 的标题 —— 三处漂移会被
 * `server/test_handlers.py` 的 ProtocolVersionConformanceTest 抓住。
 */
data class Handshake(val version: String = "1.1", val token: String = "") : OmniPadMessage() {
    override fun toJson() = jsonMessage(
        "handshake",
        "version" to version,
        "token" to token,
    )
}

/** 服务端握手确认。version 目前只用于日志与排查，版本校验已在请求侧完成。 */
data class HandshakeAck(val version: String) : OmniPadMessage() {
    override fun toJson() = jsonMessage("handshake_ack", "version" to version)
}

data class MouseMove(val dx: Int, val dy: Int) : OmniPadMessage() {
    override fun toJson() = jsonMessage("mouse_move", "dx" to dx, "dy" to dy)
}

data class MouseClick(val button: String, val action: String) : OmniPadMessage() {
    override fun toJson() = jsonMessage(
        "mouse_click",
        "button" to button,
        "action" to action,
    )
}

data class Scroll(val delta: Int) : OmniPadMessage() {
    override fun toJson() = jsonMessage("scroll", "delta" to delta)
}

data class TextInput(val text: String) : OmniPadMessage() {
    override fun toJson() = jsonMessage("text_input", "text" to text)
}

data class Keyboard(val key: String, val action: String) : OmniPadMessage() {
    override fun toJson() = jsonMessage("keyboard", "key" to key, "action" to action)
}

object Heartbeat : OmniPadMessage() {
    override fun toJson() = jsonMessage("heartbeat")
}

/** 心跳确认不含任何载荷，原先那个 raw 字段从未被读取过。 */
object HeartbeatAck : OmniPadMessage() {
    override fun toJson() = jsonMessage("heartbeat_ack")
}

data class Error(val code: String, val message: String) : OmniPadMessage() {
    override fun toJson() = jsonMessage("error", "code" to code, "message" to message)
}

fun parseMessage(raw: String): OmniPadMessage? {
    return try {
        val obj = JSONObject(raw)
        when (obj.optString("type")) {
            "handshake_ack" -> HandshakeAck(obj.optString("version", ""))
            "heartbeat_ack" -> HeartbeatAck
            "error" -> Error(
                obj.optString("code", ""),
                obj.optString("message", "")
            )
            else -> null
        }
    } catch (e: Exception) {
        null
    }
}
