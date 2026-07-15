package com.omnipad.client.network

import kotlinx.coroutines.*
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

    private var onMessage: ((OmniPadMessage) -> Unit)? = null

    fun setOnMessageListener(listener: (OmniPadMessage) -> Unit) {
        onMessage = listener
    }

    fun connect(host: String, port: Int, onConnected: () -> Unit = {}, onFailed: (String) -> Unit = {}) {
        if (_connectionState.value != ConnectionState.DISCONNECTED) return

        _connectionState.value = ConnectionState.CONNECTING

        scope.launch(Dispatchers.IO) {
            try {
                val sock = Socket()
                sock.connect(InetSocketAddress(host, port), 5000)
                sock.soTimeout = 30000
                socket = sock
                writer = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
                reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))

                sendMessage(Handshake())
                val response = reader?.readLine()
                if (response != null) {
                    val msg = parseMessage(response)
                    if (msg is HandshakeAck) {
                        _connectionState.value = ConnectionState.CONNECTED
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
        heartbeatJob?.cancel()
        readerJob?.cancel()
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

    fun sendMessage(msg: OmniPadMessage) {
        scope.launch(Dispatchers.IO) {
            try {
                writer?.write(msg.toJson() + "\n")
                writer?.flush()
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
