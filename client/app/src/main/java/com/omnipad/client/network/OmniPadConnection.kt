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

    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    private var socket: Socket? = null
    private var writer: OutputStreamWriter? = null
    private var reader: BufferedReader? = null
    private var heartbeatJob: Job? = null
    private var readerJob: Job? = null
    private var writerJob: Job? = null

    /**
     * 发送队列。所有出站消息都经此进入唯一的写协程，保证到达顺序与调用顺序一致 ——
     * 组合键（ctrl down → c press → ctrl up）和鼠标移动序列都依赖严格顺序。
     */
    private var outgoing: Channel<OmniPadMessage>? = null

    private var onMessage: ((OmniPadMessage) -> Unit)? = null

    fun setOnMessageListener(listener: (OmniPadMessage) -> Unit) {
        onMessage = listener
    }

    fun connect(host: String, port: Int, onConnected: () -> Unit = {}, onFailed: (String) -> Unit = {}) {
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
                out.write(Handshake().toJson() + "\n")
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
                        disconnect()
                        _connectionState.value = ConnectionState.FAILED
                        withContext(Dispatchers.Main) { onFailed("握手失败") }
                    }
                } else {
                    disconnect()
                    _connectionState.value = ConnectionState.FAILED
                    withContext(Dispatchers.Main) { onFailed("服务器无响应") }
                }
            } catch (e: Exception) {
                disconnect()
                _connectionState.value = ConnectionState.FAILED
                withContext(Dispatchers.Main) { onFailed(e.message ?: "连接失败") }
            }
        }
    }

    fun disconnect() {
        outgoing?.close()
        outgoing = null
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

    private fun startHeartbeat() {
        heartbeatJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                delay(5000)
                if (_connectionState.value == ConnectionState.CONNECTED) {
                    sendMessage(Heartbeat)
                }
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
