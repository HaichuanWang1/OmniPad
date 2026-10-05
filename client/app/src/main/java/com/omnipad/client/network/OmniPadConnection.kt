package com.omnipad.client.network

import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException

enum class ConnectionState {
    DISCONNECTED, CONNECTING, CONNECTED, FAILED
}

class OmniPadConnection(private val scope: CoroutineScope) {

    companion object {
        /** 心跳发送间隔，与 docs/protocol.md 一致。 */
        private const val HEARTBEAT_INTERVAL_MS = 5000L

        /** 连续丢失多少次心跳后判定连接已断（5s × 3 = 15s，与文档一致）。 */
        private const val MAX_MISSED_HEARTBEATS = 3
    }

    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    private val _lastError = MutableStateFlow<ConnectionNotice?>(null)

    /** 最近一次需要提示用户的连接事件；UI 展示后应调用 [clearLastError]。 */
    val lastError: StateFlow<ConnectionNotice?> = _lastError.asStateFlow()

    /**
     * 是否在连续丢失心跳后自动断开，由 UI 同步用户开关。
     * 关闭时只持续发心跳、不主动断开。
     */
    var autoDisconnect: Boolean = true

    private var socket: Socket? = null
    private var writer: OutputStreamWriter? = null
    private var reader: BufferedReader? = null
    private var heartbeatJob: Job? = null
    private var readerJob: Job? = null
    private var writerJob: Job? = null
    private var missedHeartbeats = 0

    /**
     * 发送队列。所有出站消息都经此进入唯一的写协程，保证到达顺序与调用顺序一致 ——
     * 组合键（ctrl down → c press → ctrl up）和鼠标移动序列都依赖严格顺序。
     */
    private var outgoing: Channel<OmniPadMessage>? = null

    private var onMessage: ((OmniPadMessage) -> Unit)? = null

    fun setOnMessageListener(listener: (OmniPadMessage) -> Unit) {
        onMessage = listener
    }

    fun clearLastError() {
        _lastError.value = null
    }

    fun connect(
        host: String,
        port: Int,
        token: String,
        onConnected: () -> Unit = {},
        onFailed: (ConnectionNotice) -> Unit = {},
    ) {
        if (_connectionState.value != ConnectionState.DISCONNECTED && _connectionState.value != ConnectionState.FAILED) return

        _connectionState.value = ConnectionState.CONNECTING

        scope.launch(Dispatchers.IO) {
            try {
                val sock = Socket()
                sock.tcpNoDelay = true
                sock.connect(InetSocketAddress(host, port), 5000)
                sock.soTimeout = 30000
                socket = sock
                val out = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
                writer = out
                reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))

                // 握手必须在写协程启动前同步发出，保证它是这条连接上的第一条消息。
                out.write(Handshake(token = token).toJson() + "\n")
                out.flush()

                val response = reader?.readLine()
                if (response != null) {
                    val msg = parseMessage(response)
                    if (msg is HandshakeAck) {
                        _connectionState.value = ConnectionState.CONNECTED
                        startWriter()
                        withContext(Dispatchers.Main) { onConnected() }
                        startHeartbeat()
                        startReader()
                    } else {
                        // 服务端拒绝时会先回一条 error 再断开
                        val err = msg as? Error
                        val notice = if (err == null) {
                            ConnectionNotice.HandshakeFailed
                        } else when (err.code) {
                            "AUTH_FAILED" -> ConnectionNotice.AuthFailed
                            "VERSION_MISMATCH" -> ConnectionNotice.VersionMismatch
                            else -> ConnectionNotice.ServerError(err.message)
                        }
                        disconnect()
                        _connectionState.value = ConnectionState.FAILED
                        _lastError.value = notice
                        withContext(Dispatchers.Main) { onFailed(notice) }
                    }
                } else {
                    disconnect()
                    _connectionState.value = ConnectionState.FAILED
                    _lastError.value = ConnectionNotice.ServerNoResponse
                    withContext(Dispatchers.Main) {
                        onFailed(ConnectionNotice.ServerNoResponse)
                    }
                }
            } catch (e: Exception) {
                disconnect()
                _connectionState.value = ConnectionState.FAILED
                val notice = ConnectionNotice.ConnectFailed(e.message)
                _lastError.value = notice
                withContext(Dispatchers.Main) { onFailed(notice) }
            }
        }
    }

    fun disconnect() {
        outgoing?.close()
        outgoing = null
        missedHeartbeats = 0
        heartbeatJob?.cancel()
        readerJob?.cancel()
        writerJob?.cancel()
        heartbeatJob = null
        readerJob = null
        writerJob = null
        try {
            writer?.close()
            reader?.close()
            socket?.close()
        } catch (_: Exception) {}
        writer = null
        reader = null
        socket = null
        _connectionState.value = ConnectionState.DISCONNECTED
    }

    /**
     * 入队一条消息后立即返回。实际写出由 [startWriter] 启动的唯一写协程串行完成，
     * 因此不会再出现多条消息并发写同一个 OutputStreamWriter 而交错损坏的情况。
     *
     * 队列无上限：鼠标拖动约 60fps，且按键组合不允许丢，故不设丢弃策略。
     * 未连接时静默丢弃（与服务端断开后的既有行为一致）。
     */
    fun sendMessage(msg: OmniPadMessage) {
        outgoing?.trySend(msg)
    }

    /** 启动唯一写协程：串行消费发送队列，保证消息按入队顺序到达服务端。 */
    private fun startWriter() {
        val channel = Channel<OmniPadMessage>(Channel.UNLIMITED)
        outgoing = channel
        writerJob = scope.launch(Dispatchers.IO) {
            try {
                for (msg in channel) {
                    writer?.write(msg.toJson() + "\n")
                    writer?.flush()
                }
            } catch (_: Exception) {
                disconnect()
            }
        }
    }

    /**
     * 心跳发送与超时判定都在连接层，UI 不再自己数心跳。
     *
     * 每轮先记一次「未确认」，再发心跳并等待一个间隔；收到 heartbeat_ack 时计数
     * 清零。因此计数超过 [MAX_MISSED_HEARTBEATS] 恰好意味着连续 15 秒没有收到
     * 任何确认，与 docs/protocol.md 的规定一致。
     */
    private fun startHeartbeat() {
        missedHeartbeats = 0
        heartbeatJob = scope.launch(Dispatchers.IO) {
            while (isActive && _connectionState.value == ConnectionState.CONNECTED) {
                missedHeartbeats++
                if (missedHeartbeats > MAX_MISSED_HEARTBEATS) {
                    if (autoDisconnect) {
                        _lastError.value = ConnectionNotice.HeartbeatTimeout
                        disconnect()
                        break
                    }
                    missedHeartbeats = 0   // 用户关闭了自动断开，继续尝试
                }
                sendMessage(Heartbeat)
                delay(HEARTBEAT_INTERVAL_MS)
            }
        }
    }

    private fun startReader() {
        readerJob = scope.launch(Dispatchers.IO) {
            try {
                while (isActive && _connectionState.value == ConnectionState.CONNECTED) {
                    val line = reader?.readLine() ?: break
                    if (line.isNotEmpty()) {
                        val msg = parseMessage(line)
                        if (msg != null) {
                            // 心跳确认在连接层内部消化，UI 无需关心
                            if (msg is HeartbeatAck) missedHeartbeats = 0
                            withContext(Dispatchers.Main) {
                                onMessage?.invoke(msg)
                            }
                        }
                    }
                }
            } catch (_: SocketTimeoutException) {
            } catch (_: Exception) {
            } finally {
                if (_connectionState.value == ConnectionState.CONNECTED) {
                    disconnect()
                }
            }
        }
    }
}
