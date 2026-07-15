package com.omnipad.client.network

import org.json.JSONObject

sealed class OmniPadMessage {
    abstract fun toJson(): String
}

data class Handshake(val version: String = "1.0") : OmniPadMessage() {
    override fun toJson() = """{"type":"handshake","version":"$version"}"""
}

data class HandshakeAck(val version: String) : OmniPadMessage() {
    override fun toJson() = """{"type":"handshake_ack","version":"$version"}"""
}

data class MouseMove(val dx: Int, val dy: Int) : OmniPadMessage() {
    override fun toJson() = """{"type":"mouse_move","dx":$dx,"dy":$dy}"""
}

data class MouseClick(val button: String, val action: String) : OmniPadMessage() {
    override fun toJson() = """{"type":"mouse_click","button":"$button","action":"$action"}"""
}

data class Scroll(val delta: Int) : OmniPadMessage() {
    override fun toJson() = """{"type":"scroll","delta":$delta}"""
}

data class TextInput(val text: String) : OmniPadMessage() {
    override fun toJson(): String {
        val escaped = text
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        return """{"type":"text_input","text":"$escaped"}"""
    }
}

data class Keyboard(val key: String, val action: String) : OmniPadMessage() {
    override fun toJson() = """{"type":"keyboard","key":"$key","action":"$action"}"""
}

object Heartbeat : OmniPadMessage() {
    override fun toJson() = """{"type":"heartbeat"}"""
}

data class HeartbeatAck(val raw: String) : OmniPadMessage() {
    override fun toJson() = raw
}

data class Error(val code: String, val message: String) : OmniPadMessage() {
    override fun toJson() = """{"type":"error","code":"$code","message":"$message"}"""
}

fun parseMessage(raw: String): OmniPadMessage? {
    return try {
        val obj = JSONObject(raw)
        when (obj.optString("type")) {
            "handshake_ack" -> HandshakeAck(raw)
            "heartbeat_ack" -> HeartbeatAck(raw)
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
