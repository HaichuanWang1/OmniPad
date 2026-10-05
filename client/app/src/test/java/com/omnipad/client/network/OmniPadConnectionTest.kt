package com.omnipad.client.network

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

private const val TIMEOUT_MS = 5_000L

/**
 * 假服务端：在随机端口上接受一条连接，把收到的每一行交给 [onLine]，
 * 并允许测试随时主动推消息。
 *
 * 用真实 socket 而不是 mock，是为了让握手顺序、分帧、超时这些
 * 「跨线程 + 跨进程边界」的行为真的被跑到。
 */
private class FakeServer(
    private val onLine: (String, FakeServer) -> Unit = { _, _ -> },
) : AutoCloseable {

    private val serverSocket = ServerSocket(0)
    private val executor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "fake-server").apply { isDaemon = true }
    }

    /** 收到的原始行，按到达顺序。 */
    val received = CopyOnWriteArrayList<String>()
    val gotFirstLine = CountDownLatch(1)

    val port: Int get() = serverSocket.localPort

    @Volatile private var client: Socket? = null
    @Volatile private var writer: OutputStreamWriter? = null

    init {
        // 顺序接受多条连接：断线重连的用例需要它，否则第二条连接只会躺在
        // 内核 backlog 里没人读，客户端等到超时。
        executor.submit {
            try {
                while (true) {
                    val sock = serverSocket.accept()
                    client = sock
                    writer = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
                    val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
                    try {
                        while (true) {
                            val line = reader.readLine() ?: break
                            received.add(line)
                            if (received.size == 1) gotFirstLine.countDown()
                            onLine(line, this)
                        }
                    } catch (_: Exception) {
                        // 这条连接结束，回去接受下一条
                    }
                }
            } catch (_: Exception) {
                // serverSocket 被关闭，测试收尾的正常路径
            }
        }
    }

    /** 主动推一行给客户端。调用前应确保握手行已经到达（[gotFirstLine]）。 */
    @Synchronized
    fun push(json: String) {
        writer?.apply {
            write(json + "\n")
            flush()
        }
    }

    /**
     * 半关闭输出流，让客户端读到 EOF。
     *
     * 用 shutdownOutput 而不是直接 close：后者在客户端仍在写时可能触发 RST，
     * 让客户端拿到 SocketException 而不是干净的流结束，测试就不稳定了。
     */
    fun stopResponding() {
        try {
            client?.shutdownOutput()
        } catch (_: Exception) {
        }
    }

    /** 硬掐断当前连接，但服务端继续接受下一条 —— 模拟网络抖动。 */
    fun dropClient() {
        try {
            client?.close()
        } catch (_: Exception) {
        }
    }

    override fun close() {
        try {
            client?.close()
        } catch (_: Exception) {
        }
        try {
            serverSocket.close()
        } catch (_: Exception) {
        }
        executor.shutdownNow()
    }
}

private fun typeOf(line: String): String =
    try {
        JSONObject(line).optString("type")
    } catch (_: Exception) {
        ""
    }

/** 收到握手就回确认；其余一律不理。 */
private fun ackHandshake(line: String, server: FakeServer) {
    if (typeOf(line) == "handshake") {
        server.push("""{"type":"handshake_ack","version":"1.0"}""")
    }
}

class OmniPadConnectionTest {

    private val scopes = mutableListOf<CoroutineScope>()

    @After
    fun tearDown() {
        scopes.forEach { it.cancel() }
        scopes.clear()
    }

    /**
     * 构造一个连接。mainDispatcher 传 Dispatchers.Unconfined：
     * withContext 会就地执行回调，测试无需等待另一个线程。
     */
    private fun newConnection(
        heartbeatIntervalMs: Long = OmniPadConnection.HEARTBEAT_INTERVAL_MS,
        reconnectDelaysMs: List<Long> = listOf(20L, 20L, 20L),
    ): OmniPadConnection {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scopes += scope
        // 默认关掉自动重连：多数用例关心的是「一次连接尝试的结果」，
        // 开着重连会让它们先去等退避。重连相关的用例自己打开。
        return OmniPadConnection(
            scope, heartbeatIntervalMs, Dispatchers.Unconfined, reconnectDelaysMs,
        ).apply { autoReconnect = false }
    }

    private fun awaitState(
        conn: OmniPadConnection,
        expected: ConnectionState,
        timeoutMs: Long = TIMEOUT_MS,
    ) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (conn.connectionState.value == expected) return
            Thread.sleep(5)
        }
        fail("等待连接状态 $expected 超时，实际为 ${conn.connectionState.value}")
    }

    // ---- 握手 ----

    @Test
    fun `连接成功后第一条消息是带令牌的 handshake`() {
        FakeServer(::ackHandshake).use { server ->
            val conn = newConnection()
            conn.connect("127.0.0.1", server.port, "GBGUAWW9")
            awaitState(conn, ConnectionState.CONNECTED)

            assertTrue("服务端没收到任何消息", server.gotFirstLine.await(2, TimeUnit.SECONDS))
            // 握手必须排在心跳和用户消息之前，否则服务端会先看到未认证的消息。
            val hs = JSONObject(server.received[0])
            assertEquals("handshake", hs.getString("type"))
            // 只断言「线上发的就是模型声明的那个版本」，具体数值由服务端
            // ProtocolVersionConformanceTest 盯着，避免这里再写死一份。
            assertEquals(Handshake().version, hs.getString("version"))
            assertEquals("GBGUAWW9", hs.getString("token"))
        }
    }

    @Test
    fun `令牌错误映射为 AuthFailed`() {
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                server.push("""{"type":"error","code":"AUTH_FAILED","message":"配对令牌不正确"}""")
            }
        }.use { server ->
            val conn = newConnection()
            conn.connect("127.0.0.1", server.port, "WRONG123")
            awaitState(conn, ConnectionState.FAILED)
            assertEquals(ConnectionNotice.AuthFailed, conn.lastError.value)
        }
    }

    @Test
    fun `版本不匹配映射为 VersionMismatch`() {
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                server.push("""{"type":"error","code":"VERSION_MISMATCH","message":"expected 1.0"}""")
            }
        }.use { server ->
            val conn = newConnection()
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.FAILED)
            assertEquals(ConnectionNotice.VersionMismatch, conn.lastError.value)
        }
    }

    @Test
    fun `无法识别的错误码回落为 ServerError`() {
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                server.push("""{"type":"error","code":"SOMETHING_ELSE","message":"服务端开小差了"}""")
            }
        }.use { server ->
            val conn = newConnection()
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.FAILED)
            assertEquals(ConnectionNotice.ServerError("服务端开小差了"), conn.lastError.value)
        }
    }

    @Test
    fun `服务端不回握手确认时映射为 ServerNoResponse`() {
        FakeServer { _, server -> server.stopResponding() }.use { server ->
            val conn = newConnection()
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.FAILED)
            assertEquals(ConnectionNotice.ServerNoResponse, conn.lastError.value)
        }
    }

    @Test
    fun `端口无人监听时映射为 ConnectFailed`() {
        // 先占一个端口再释放，确保它确实没人监听。
        val port = ServerSocket(0).use { it.localPort }

        val conn = newConnection()
        conn.connect("127.0.0.1", port, "T")
        awaitState(conn, ConnectionState.FAILED)
        assertTrue(
            "期望 ConnectFailed，实际 ${conn.lastError.value}",
            conn.lastError.value is ConnectionNotice.ConnectFailed,
        )
    }

    // ---- 出站顺序 ----

    @Test
    fun `消息按入队顺序串行到达`() {
        FakeServer(::ackHandshake).use { server ->
            val conn = newConnection()
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)

            // 组合键与移动序列都依赖严格顺序：一旦多条协程并发写同一个
            // OutputStreamWriter，这些行就会交错损坏。
            val expected = listOf(
                Keyboard("ctrl", "down"),
                Keyboard("c", "press"),
                Keyboard("ctrl", "up"),
            ) + (1..100).map { MouseMove(it, -it) }
            val want = expected.map { it.toJson() }

            expected.forEach { conn.sendMessage(it) }

            var got = emptyList<String>()
            val deadline = System.currentTimeMillis() + TIMEOUT_MS
            while (System.currentTimeMillis() < deadline) {
                got = server.received.drop(1).filterNot { typeOf(it) == "heartbeat" }
                if (got.size >= want.size) break
                Thread.sleep(5)
            }
            assertEquals(want, got.take(want.size))
        }
    }

    @Test
    fun `未连接时发送消息不抛异常`() {
        // UI 在断开瞬间仍可能触发一次 sendMessage，不能因此崩溃。
        newConnection().sendMessage(MouseMove(1, 1))
    }

    // ---- 心跳 ----

    @Test
    fun `心跳被确认时保持连接`() {
        FakeServer { line, server ->
            when (typeOf(line)) {
                "handshake" -> server.push("""{"type":"handshake_ack","version":"1.0"}""")
                "heartbeat" -> server.push("""{"type":"heartbeat_ack"}""")
            }
        }.use { server ->
            val interval = 40L
            val conn = newConnection(heartbeatIntervalMs = interval)
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)

            // 跑满 8 个周期。若 ack 没有把计数清零，4 个周期（>3 次未确认）就该断了。
            Thread.sleep(interval * 8)
            assertEquals(ConnectionState.CONNECTED, conn.connectionState.value)
        }
    }

    @Test
    fun `连续丢失心跳后判定断开`() {
        FakeServer(::ackHandshake).use { server ->
            val conn = newConnection(heartbeatIntervalMs = 40)
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)

            // 关掉自动重连时是终态。曾经连上过却以失败收场，故为 FAILED 而非 DISCONNECTED。
            awaitState(conn, ConnectionState.FAILED)
            assertEquals(ConnectionNotice.HeartbeatTimeout, conn.lastError.value)
        }
    }

    @Test
    fun `关闭自动断开后心跳超时不切断连接`() {
        FakeServer(::ackHandshake).use { server ->
            val interval = 40L
            val conn = newConnection(heartbeatIntervalMs = interval)
            conn.autoDisconnect = false
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)

            Thread.sleep(interval * 10)
            assertEquals(ConnectionState.CONNECTED, conn.connectionState.value)
        }
    }

    // ---- 状态机 ----

    @Test
    fun `连接中重复调用 connect 会被忽略`() {
        FakeServer(::ackHandshake).use { server ->
            val conn = newConnection()
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)

            // 已连接时再次 connect 不应建立第二条连接。
            conn.connect("127.0.0.1", server.port, "T")
            Thread.sleep(100)
            assertEquals(ConnectionState.CONNECTED, conn.connectionState.value)
            assertEquals("服务端不该收到第二条连接", 1, server.received.count { typeOf(it) == "handshake" })
        }
    }

    @Test
    fun `断开后可以重新连接`() {
        FakeServer(::ackHandshake).use { server ->
            val conn = newConnection()
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)

            conn.disconnect()
            assertEquals(ConnectionState.DISCONNECTED, conn.connectionState.value)

            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)
        }
    }

    // ---- 自动重连 ----

    @Test
    fun `默认开启自动重连`() {
        // 默认值本身也是行为：生产代码靠它扛住 WiFi 抖动。
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scopes += scope
        assertTrue(OmniPadConnection(scope).autoReconnect)
    }

    @Test
    fun `对端断开后自动重连并恢复`() {
        var handshakes = 0
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                // 第一次假装服务端假死，逼出重连；第二次正常确认。
                if (++handshakes == 1) server.stopResponding()
                else server.push("""{"type":"handshake_ack","version":"1.0"}""")
            }
        }.use { server ->
            val conn = newConnection(reconnectDelaysMs = listOf(30L))
            conn.autoReconnect = true
            conn.connect("127.0.0.1", server.port, "T")

            awaitState(conn, ConnectionState.CONNECTED)
            assertEquals("应当自动重连并再次握手", 2, handshakes)
            assertEquals("连上后重连计数归零", 0, conn.reconnectAttempt.value)
        }
    }

    @Test
    fun `重连期间状态为 RECONNECTING 且计数递增`() {
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") server.stopResponding()
        }.use { server ->
            // 退避给得长一些，便于稳定观察到 RECONNECTING 这个中间态。
            val conn = newConnection(reconnectDelaysMs = listOf(300L, 300L))
            conn.autoReconnect = true
            conn.connect("127.0.0.1", server.port, "T")

            awaitState(conn, ConnectionState.RECONNECTING)
            assertEquals(1, conn.reconnectAttempt.value)
        }
    }

    @Test
    fun `退避用尽后落到 FAILED 且不再重试`() {
        var handshakes = 0
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                handshakes++
                server.stopResponding()
            }
        }.use { server ->
            val conn = newConnection(reconnectDelaysMs = listOf(10L, 10L, 10L))
            conn.autoReconnect = true
            conn.connect("127.0.0.1", server.port, "T")

            awaitState(conn, ConnectionState.FAILED)
            assertEquals("初次 + 3 次重连", 4, handshakes)

            Thread.sleep(150)
            assertEquals("退避用尽后不该继续重试", 4, handshakes)
            assertEquals(0, conn.reconnectAttempt.value)
        }
    }

    @Test
    fun `认证失败不触发重连`() {
        var handshakes = 0
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                handshakes++
                server.push("""{"type":"error","code":"AUTH_FAILED","message":"令牌不正确"}""")
            }
        }.use { server ->
            val conn = newConnection(reconnectDelaysMs = listOf(20L, 20L, 20L))
            conn.autoReconnect = true
            conn.connect("127.0.0.1", server.port, "WRONG")

            awaitState(conn, ConnectionState.FAILED)
            Thread.sleep(200)
            assertEquals("认证失败重试多少次都是同样的错，不该重连", 1, handshakes)
        }
    }

    @Test
    fun `版本不匹配不触发重连`() {
        var handshakes = 0
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                handshakes++
                server.push(
                    """{"type":"error","code":"VERSION_MISMATCH","message":"expected 1.0"}"""
                )
            }
        }.use { server ->
            val conn = newConnection(reconnectDelaysMs = listOf(20L, 20L, 20L))
            conn.autoReconnect = true
            conn.connect("127.0.0.1", server.port, "T")

            awaitState(conn, ConnectionState.FAILED)
            Thread.sleep(200)
            assertEquals(1, handshakes)
        }
    }

    @Test
    fun `关闭自动重连时掉线即为终态`() {
        var handshakes = 0
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                handshakes++
                if (handshakes == 1) server.push("""{"type":"handshake_ack","version":"1.0"}""")
            }
        }.use { server ->
            val conn = newConnection(reconnectDelaysMs = listOf(20L))
            conn.autoReconnect = false
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)

            server.dropClient()
            awaitState(conn, ConnectionState.DISCONNECTED)

            Thread.sleep(150)
            assertEquals("关掉重连后不该再发起连接", 1, handshakes)
        }
    }

    @Test
    fun `重连等待期间用户断开则停止重连`() {
        var handshakes = 0
        FakeServer { line, server ->
            if (typeOf(line) == "handshake") {
                handshakes++
                if (handshakes == 1) server.stopResponding()
                else server.push("""{"type":"handshake_ack","version":"1.0"}""")
            }
        }.use { server ->
            // 退避比观察窗口长，好在重连真正发起之前按下断开。
            val conn = newConnection(reconnectDelaysMs = listOf(400L))
            conn.autoReconnect = true
            conn.connect("127.0.0.1", server.port, "T")

            awaitState(conn, ConnectionState.RECONNECTING)
            conn.disconnect()
            assertEquals(ConnectionState.DISCONNECTED, conn.connectionState.value)

            Thread.sleep(700)   // 超过退避时间
            assertEquals("用户主动断开后不该再发起连接", 1, handshakes)
            assertEquals(ConnectionState.DISCONNECTED, conn.connectionState.value)
        }
    }

    @Test
    fun `心跳超时在开启重连时转为重连而非终态`() {
        FakeServer(::ackHandshake).use { server ->
            // ackHandshake 只确认握手、不确认心跳，所以心跳必然超时。
            val conn = newConnection(heartbeatIntervalMs = 40, reconnectDelaysMs = listOf(500L))
            conn.autoReconnect = true
            conn.connect("127.0.0.1", server.port, "T")
            awaitState(conn, ConnectionState.CONNECTED)

            // 关键：应当去重连，而不是停在 FAILED 让用户手动重连。
            awaitState(conn, ConnectionState.RECONNECTING)
        }
    }
}
