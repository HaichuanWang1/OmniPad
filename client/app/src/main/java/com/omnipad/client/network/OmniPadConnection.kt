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
import java.util.concurrent.atomic.AtomicBoolean

enum class ConnectionState {
    DISCONNECTED,

    /** 建立连接中（含握手）。 */
    CONNECTING,

    CONNECTED,

    /** 链路意外断开，正在按退避序列重试。 */
    RECONNECTING,

    /** 终态失败：要么不可重试（令牌错、版本不符），要么退避已用尽。 */
    FAILED,
}

class OmniPadConnection(
    private val scope: CoroutineScope,

    /**
     * 心跳发送间隔，默认与 docs/protocol.md 一致。
     *
     * 提成构造参数只为可测性：按默认值验证「连续丢 3 次心跳后断开」要跑满 15 秒，
     * 单元测试里缩短到毫秒级即可覆盖同一条逻辑。
     */
    private val heartbeatIntervalMs: Long = HEARTBEAT_INTERVAL_MS,

    /**
     * 回调 UI 用的调度器。
     *
     * 注入而不是直接用 Dispatchers.Main：单元测试里可以换成直接执行的调度器，
     * 既不必启动 Looper，也不用依赖 Dispatchers.setMain 这类全局状态。
     */
    private val mainDispatcher: CoroutineDispatcher = Dispatchers.Main,

    /**
     * 意外断开后的重连退避序列（毫秒），依次取用，跑完仍未成功就放弃。
     * 同样是为了让测试用毫秒级序列，不必真等半分钟。
     */
    private val reconnectDelaysMs: List<Long> = DEFAULT_RECONNECT_DELAYS_MS,
) {

    companion object {
        /** 心跳发送间隔，与 docs/protocol.md 一致。 */
        const val HEARTBEAT_INTERVAL_MS = 5000L

        /** 连续丢失多少次心跳后判定连接已断（5s × 3 = 15s，与文档一致）。 */
        const val MAX_MISSED_HEARTBEATS = 3

        /** 退避累计约 30 秒；之后交给用户手动重连，避免无限静默重试。 */
        val DEFAULT_RECONNECT_DELAYS_MS =
            listOf(500L, 1_000L, 2_000L, 4_000L, 8_000L, 15_000L)
    }

    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    private val _lastError = MutableStateFlow<ConnectionNotice?>(null)

    /** 最近一次需要提示用户的连接事件；UI 展示后应调用 [clearLastError]。 */
    val lastError: StateFlow<ConnectionNotice?> = _lastError.asStateFlow()

    private val _reconnectAttempt = MutableStateFlow(0)

    /** 当前是第几次重连尝试（从 1 起）；未在重连时为 0。 */
    val reconnectAttempt: StateFlow<Int> = _reconnectAttempt.asStateFlow()

    private val _latencyMs = MutableStateFlow<Int?>(null)

    /**
     * 最近一次心跳的往返耗时（毫秒），未测到时为 null。
     *
     * 远程控制里「卡不卡」是用户能直接感知的，而链路的退化往往远早于断开 ——
     * 把心跳往返时间显示出来，用户能在操作变迟钝时就知道该靠近路由器了。
     */
    val latencyMs: StateFlow<Int?> = _latencyMs.asStateFlow()

    /**
     * 上一条心跳的发出时刻，用于算往返耗时。
     *
     * 心跳协程写、读协程读，跨线程，所以是 @Volatile。
     */
    @Volatile
    private var heartbeatSentAtNanos = 0L

    /**
     * 是否在连续丢失心跳后自动断开，由 UI 同步用户开关。
     * 关闭时只持续发心跳、不主动断开。
     */
    var autoDisconnect: Boolean = true

    /** 意外断开后是否自动重连。用户主动断开、以及不可重试的失败都不受影响。 */
    var autoReconnect: Boolean = true

    private data class Endpoint(val host: String, val port: Int, val token: String)

    /** 记住连接参数，重连时复用。 */
    private var endpoint: Endpoint? = null
    private var onConnectedCb: (() -> Unit)? = null
    private var onFailedCb: ((ConnectionNotice) -> Unit)? = null

    private var socket: Socket? = null
    private var writer: OutputStreamWriter? = null
    private var reader: BufferedReader? = null
    private var heartbeatJob: Job? = null
    private var readerJob: Job? = null
    private var writerJob: Job? = null
    private var reconnectJob: Job? = null
    private var missedHeartbeats = 0

    /**
     * 用户主动断开时为 true。
     *
     * 用来区分「用户点了断开」和「链路掉了」—— 前者绝不能触发重连，后者才应该。
     * 没有这个标志时，Activity 销毁时的 disconnect 会和重连逻辑打架。
     */
    @Volatile
    private var closingByUser = false

    /**
     * 同一次断开会被两条路径同时发现：心跳超时，以及读协程收到 EOF。
     *
     * [tearDown] 会取消读协程，而读协程的 finally 又会调用 [handleDrop] —— 于是
     * 心跳那条路径刚带着 HeartbeatTimeout 认领完，读协程立刻用 notice=null 又处理
     * 一遍，把真正的原因盖掉、状态错写成 DISCONNECTED。用原子标志保证只有先到的
     * 那次生效。每次发起连接时重置。
     */
    private val dropClaimed = AtomicBoolean(false)

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

    /**
     * 发起连接。重连过程中调用它是允许的 —— 用户想换一台机器时不该被重连挡住。
     */
    fun connect(
        host: String,
        port: Int,
        token: String,
        onConnected: () -> Unit = {},
        onFailed: (ConnectionNotice) -> Unit = {},
    ) {
        if (_connectionState.value == ConnectionState.CONNECTING ||
            _connectionState.value == ConnectionState.CONNECTED
        ) {
            return
        }

        cancelReconnect()
        endpoint = Endpoint(host, port, token)
        onConnectedCb = onConnected
        onFailedCb = onFailed
        closingByUser = false
        openSocket()
    }

    /** 用户主动断开。取消任何待执行的重连，且不会触发新的重连。 */
    fun disconnect() {
        closingByUser = true
        cancelReconnect()
        tearDown()
        _connectionState.value = ConnectionState.DISCONNECTED
    }

    private fun openSocket() {
        val ep = endpoint ?: return
        dropClaimed.set(false)
        _connectionState.value = ConnectionState.CONNECTING

        scope.launch(Dispatchers.IO) {
            try {
                val sock = Socket()
                sock.tcpNoDelay = true
                sock.connect(InetSocketAddress(ep.host, ep.port), 5000)
                sock.soTimeout = 30000
                socket = sock
                val out = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
                writer = out
                reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))

                // 握手必须在写协程启动前同步发出，保证它是这条连接上的第一条消息。
                out.write(Handshake(token = ep.token).toJson() + "\n")
                out.flush()

                val response = reader?.readLine()
                if (parseMessage(response.orEmpty()) is HandshakeAck) {
                    _connectionState.value = ConnectionState.CONNECTED
                    _reconnectAttempt.value = 0
                    startWriter()
                    withContext(mainDispatcher) { onConnectedCb?.invoke() }
                    startHeartbeat()
                    startReader()
                } else {
                    // 是否重试交给 isRetryable 判定：令牌错/版本不符不重试，
                    // 而「连不上」和「对端假死」值得重试。
                    failOrRetry(handshakeRejection(response))
                }
            } catch (e: Exception) {
                // 连不上多半是「电脑端还没启动」，值得重试。
                failOrRetry(ConnectionNotice.ConnectFailed(e.message))
            }
        }
    }

    /** 把握手失败的那一行响应翻译成给用户看的事件。 */
    private fun handshakeRejection(response: String?): ConnectionNotice {
        // 读不到任何内容 = 连上了但对端不说话；读到内容却不是合法消息 = 握手协议不符。
        if (response == null) return ConnectionNotice.ServerNoResponse
        val msg = parseMessage(response)
        return when {
            msg is Error -> when (msg.code) {
                "AUTH_FAILED" -> ConnectionNotice.AuthFailed
                "VERSION_MISMATCH" -> ConnectionNotice.VersionMismatch
                else -> ConnectionNotice.ServerError(msg.message)
            }

            else -> ConnectionNotice.HandshakeFailed
        }
    }

    /**
     * 失败后要么排一次重连，要么落到终态。
     *
     * 认证与版本类错误不重试：它们不是暂时性故障，重试只会让用户对着
     * 「正在重连…」干等半分钟，最后还是同样的错。
     */
    private suspend fun failOrRetry(notice: ConnectionNotice) {
        tearDown()
        _lastError.value = notice

        if (isRetryable(notice) && scheduleReconnect()) return

        _connectionState.value = ConnectionState.FAILED
        _reconnectAttempt.value = 0
        withContext(mainDispatcher) { onFailedCb?.invoke(notice) }
    }

    private fun isRetryable(notice: ConnectionNotice): Boolean = when (notice) {
        ConnectionNotice.AuthFailed -> false
        ConnectionNotice.VersionMismatch -> false
        ConnectionNotice.HandshakeFailed -> false
        is ConnectionNotice.ServerError -> false
        ConnectionNotice.ServerNoResponse -> true
        is ConnectionNotice.ConnectFailed -> true
        ConnectionNotice.HeartbeatTimeout -> true
    }

    /** 排一次重连。返回 false 表示不重连（用户关了开关，或退避已用尽）。 */
    private fun scheduleReconnect(): Boolean {
        if (!autoReconnect || closingByUser) return false
        if (endpoint == null) return false
        if (reconnectJob?.isActive == true) return false

        val attempt = _reconnectAttempt.value
        val delayMs = reconnectDelaysMs.getOrNull(attempt) ?: return false

        _reconnectAttempt.value = attempt + 1
        _connectionState.value = ConnectionState.RECONNECTING
        reconnectJob = scope.launch {
            delay(delayMs)
            reconnectJob = null
            // 再查一次：delay 返回到 openSocket 之间没有挂起点，cancel() 可能
            // 来不及生效，只靠 cancelReconnect 不足以保证用户断开后不再连。
            if (closingByUser || !autoReconnect) return@launch
            openSocket()
        }
        return true
    }

    private fun cancelReconnect() {
        reconnectJob?.cancel()
        reconnectJob = null
        _reconnectAttempt.value = 0
    }

    /**
     * 链路意外断开（对端关闭、心跳超时、写失败）。
     *
     * 与 [disconnect] 的区别：这个会按需触发重连，且只在仍处于 CONNECTED 时生效，
     * 避免与握手阶段的失败路径重复处理同一次断开。
     */
    private fun handleDrop(notice: ConnectionNotice?) {
        if (closingByUser) return
        if (_connectionState.value != ConnectionState.CONNECTED) return
        if (!dropClaimed.compareAndSet(false, true)) return

        tearDown()
        notice?.let { _lastError.value = it }

        if (!scheduleReconnect()) {
            // 对端干净地关掉连接且没给原因时，退回未连接比报错更贴切。
            _connectionState.value =
                if (notice != null) ConnectionState.FAILED else ConnectionState.DISCONNECTED
        }
    }

    /** 只拆除链路资源，不动状态、不碰重连。 */
    private fun tearDown() {
        outgoing?.close()
        outgoing = null
        missedHeartbeats = 0
        // 链路已拆，上一次的往返耗时不再代表当前状况，留着会显示成假的好延迟
        heartbeatSentAtNanos = 0L
        _latencyMs.value = null
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
        } catch (_: Exception) {
        }
        writer = null
        reader = null
        socket = null
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
                handleDrop(null)
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
                        handleDrop(ConnectionNotice.HeartbeatTimeout)
                        break
                    }
                    missedHeartbeats = 0   // 用户关闭了自动断开，继续尝试
                }
                sendMessage(Heartbeat)
                heartbeatSentAtNanos = System.nanoTime()
                delay(heartbeatIntervalMs)
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
                            if (msg is HeartbeatAck) {
                                missedHeartbeats = 0
                                val sentAt = heartbeatSentAtNanos
                                if (sentAt != 0L) {
                                    _latencyMs.value =
                                        ((System.nanoTime() - sentAt) / 1_000_000L).toInt()
                                }
                            }
                            withContext(mainDispatcher) {
                                onMessage?.invoke(msg)
                            }
                        }
                    }
                }
            } catch (_: SocketTimeoutException) {
            } catch (_: Exception) {
            } finally {
                // 对端断开走这里；用户主动断开时 handleDrop 会直接返回。
                handleDrop(null)
            }
        }
    }
}
