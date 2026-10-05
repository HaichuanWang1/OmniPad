r"""tcp_server 回归测试：分帧、连接生命周期。

覆盖两类曾经出过问题的地方：

1. **分帧**：`_handle_client` 曾对每个 recv 块独立调用 `data.decode("utf-8")`，
   失败时回退 `errors="surrogateescape"`。一个中文字符占 3 字节，跨 TCP 段被
   切开时会被拆成孤立代理字符（`"测"` -> `'\udce6\udcb5\udc8b'`），导致长文本
   或网络抖动时中文输入随机损坏。

2. **连接生命周期**：handler 返回 False 时只跳出内层行循环，连接不会关闭；
   以及空闲超时被 `settimeout(30)` 架空，与 docs/protocol.md 规定的 15s 不符。

运行：
    cd server && python test_tcp_server.py
"""
import json
import socket
import threading
import time
import unittest

import tcp_server


class _ServerTestCase(unittest.TestCase):
    """起一个真实 TCP 服务端，但把消息处理替换成可观测的假实现。"""

    IDLE_TIMEOUT = 15

    def setUp(self):
        self.received = []
        self.errors = []
        self.events = []
        self.reject = False

        self._orig_handle = tcp_server.handle_message
        self._orig_error = tcp_server.send_error
        tcp_server.handle_message = self._fake_handle
        tcp_server.send_error = self._fake_error

        self.server = tcp_server.TcpServer(
            host="127.0.0.1", port=0, idle_timeout=self.IDLE_TIMEOUT,
            on_event=self._on_event,
        )
        threading.Thread(target=self.server.start, daemon=True).start()

        deadline = time.time() + 5
        self.port = None
        while time.time() < deadline and self.port is None:
            sock = self.server.server
            if sock is not None:
                try:
                    self.port = sock.getsockname()[1]
                except OSError:
                    pass
            if self.port is None:
                time.sleep(0.01)
        self.assertIsNotNone(self.port, "服务端未能在 5 秒内开始监听")

    def tearDown(self):
        self.server.stop()
        tcp_server.handle_message = self._orig_handle
        tcp_server.send_error = self._orig_error

    def _fake_handle(self, conn, msg):
        self.received.append(msg)
        return not self.reject

    def _fake_error(self, conn, code, message):
        self.errors.append((code, message))
        return True

    def _on_event(self, kind, **fields):
        self.events.append((kind, fields))

    def _events_of(self, kind):
        return [fields for name, fields in self.events if name == kind]

    def _send(self, payload, chunk_size):
        """把 payload 按 chunk_size 字节切片后逐片发送。"""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            for i in range(0, len(payload), chunk_size):
                sock.sendall(payload[i:i + chunk_size])
                time.sleep(0.001)
            self._wait_for(lambda: bool(self.received or self.errors))

    @staticmethod
    def _wait_for(predicate, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False


class TcpFramingTest(_ServerTestCase):
    def test_multibyte_split_one_byte_at_a_time(self):
        """最坏情况：中文消息被逐字节发送，每个字符都跨片。"""
        text = "测试中文输入"
        payload = json.dumps(
            {"type": "text_input", "text": text}, ensure_ascii=False
        ).encode("utf-8") + b"\n"

        self._send(payload, chunk_size=1)

        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0]["text"], text)

    def test_multibyte_split_at_two_byte_boundary(self):
        """按 2 字节切片 —— 正好会把 3 字节的中文切开。"""
        text = "滚动测试滚轮"
        payload = json.dumps(
            {"type": "text_input", "text": text}, ensure_ascii=False
        ).encode("utf-8") + b"\n"

        self._send(payload, chunk_size=2)

        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0]["text"], text)

    def test_multiple_messages_in_one_chunk(self):
        """一次 recv 里含多条消息，必须全部拆出且保持顺序。"""
        lines = [
            {"type": "mouse_move", "dx": 1, "dy": 2},
            {"type": "mouse_move", "dx": 3, "dy": 4},
            {"type": "text_input", "text": "你好"},
        ]
        payload = b"".join(
            json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in lines
        )

        self._send(payload, chunk_size=len(payload))

        self.assertEqual(self.errors, [])
        self.assertEqual(self.received, lines)

    def test_message_split_across_chunks_keeps_order(self):
        """跨片的半条消息 + 后续消息，不得错序或丢行。"""
        lines = [
            {"type": "mouse_move", "dx": 10, "dy": 20},
            {"type": "text_input", "text": "跨片测试"},
            {"type": "scroll", "delta": -3},
        ]
        payload = b"".join(
            json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in lines
        )

        self._send(payload, chunk_size=3)

        self.assertEqual(self.errors, [])
        self.assertEqual(self.received, lines)

    def test_invalid_utf8_is_rejected_without_crashing(self):
        """非法字节序列应回报 INVALID_PARAMS 而不是崩溃或静默吞掉。"""
        self._send(b"\xff\xfe\xfa\n", chunk_size=4)

        self.assertEqual(self.received, [])
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.errors[0][0], "INVALID_PARAMS")

    def test_invalid_json_is_rejected(self):
        self._send(b"{not json}\n", chunk_size=10)

        self.assertEqual(self.received, [])
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.errors[0][0], "INVALID_PARAMS")

    def test_blank_lines_are_ignored(self):
        payload = b"\n\n" + json.dumps({"type": "heartbeat"}).encode() + b"\n\n"

        self._send(payload, chunk_size=1)

        self.assertEqual(self.errors, [])
        self.assertEqual(self.received, [{"type": "heartbeat"}])


class ConnectionLifecycleTest(_ServerTestCase):
    # 用 1 秒而非默认 15 秒，让空闲超时用例跑得快；
    # 旧实现固定 settimeout(30)，此用例会在 6 秒内超时失败。
    IDLE_TIMEOUT = 1

    def test_handler_returning_false_closes_connection(self):
        """handler 返回 False（如 VERSION_MISMATCH）必须真正关闭连接。

        docs/protocol.md 规定「若版本不匹配，Server 回复错误并关闭连接」。
        原实现里的 break 只跳出内层行循环，外层 recv 继续阻塞，连接不会关闭。
        """
        self.reject = True
        payload = json.dumps({"type": "handshake", "version": "9.9"}).encode() + b"\n"

        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(payload)
            sock.settimeout(3)
            try:
                data = sock.recv(4096)
            except socket.timeout:
                self.fail("handler 返回 False 后服务端未关闭连接")

        self.assertEqual(data, b"")
        self.assertEqual(len(self.received), 1)

    def test_idle_client_is_disconnected_after_timeout(self):
        """对端静默超过 idle_timeout 后服务端必须主动断开。

        docs/protocol.md 规定 15 秒。原实现是 conn.settimeout(30)，recv 会一直
        阻塞到 30 秒才超时，空闲判定只可能在 recv 返回后执行 —— 实际要等满 30s。
        """
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.settimeout(self.IDLE_TIMEOUT + 5)
            started = time.time()
            try:
                data = sock.recv(4096)
            except socket.timeout:
                self.fail(
                    f"空闲 {self.IDLE_TIMEOUT}s 后服务端仍未断开连接"
                )
            elapsed = time.time() - started

        self.assertEqual(data, b"")
        self.assertGreaterEqual(
            elapsed, self.IDLE_TIMEOUT * 0.8,
            f"连接断开过早（{elapsed:.2f}s），空闲判定可能没生效",
        )

    def test_activity_resets_idle_timer(self):
        """持续有心跳时不得被空闲超时误杀。"""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            deadline = time.time() + self.IDLE_TIMEOUT * 2.5
            while time.time() < deadline:
                sock.sendall(json.dumps({"type": "heartbeat"}).encode() + b"\n")
                time.sleep(self.IDLE_TIMEOUT / 3)
            # 连接仍然活着：服务端没有关闭它，且我们收到了全部心跳
            self.assertGreater(len(self.received), 2)


class ConnectionEventTest(_ServerTestCase):
    """连接事件与断开原因。

    改造前 tcp_server 只记「连接建立了 / 连接没了」，界面只能靠这个猜状态 ——
    猜出来的「在线」是假的（那条连接连握手都没过）。现在每条连接都带原因上报，
    界面与状态文件据此才能说清「为什么断了」。
    """

    IDLE_TIMEOUT = 1

    def test_connected_event_carries_the_peer(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5):
            self.assertTrue(self._wait_for(lambda: bool(self._events_of("connected"))))

        events = self._events_of("connected")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["peer"], "127.0.0.1")
        self.assertEqual(events[0]["addr"], f"127.0.0.1:{events[0]['port']}")

    def test_client_close_reports_client_closed(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5):
            self._wait_for(lambda: bool(self._events_of("connected")))

        self.assertTrue(self._wait_for(lambda: bool(self._events_of("disconnected"))))
        self.assertEqual(
            self._events_of("disconnected")[0]["reason"], tcp_server.REASON_CLIENT_CLOSED
        )

    def test_idle_timeout_reports_its_own_reason(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5):
            self.assertTrue(
                self._wait_for(
                    lambda: bool(self._events_of("disconnected")),
                    timeout=self.IDLE_TIMEOUT + 5,
                )
            )
        self.assertEqual(
            self._events_of("disconnected")[0]["reason"], tcp_server.REASON_IDLE_TIMEOUT
        )

    def test_rejected_handshake_reports_rejected(self):
        self.reject = True
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b'{"type":"handshake"}\n')
            self.assertTrue(self._wait_for(lambda: bool(self._events_of("disconnected"))))

        self.assertEqual(
            self._events_of("disconnected")[0]["reason"], tcp_server.REASON_REJECTED
        )

    def test_server_stop_reports_server_stopped(self):
        """stop() 会 shutdown 掉所有连接，recv 抛的是 OSError —— 那不是「连接出错」。"""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5):
            self._wait_for(lambda: bool(self._events_of("connected")))
            self.server.stop()
            self.assertTrue(self._wait_for(lambda: bool(self._events_of("disconnected"))))

        self.assertEqual(
            self._events_of("disconnected")[0]["reason"],
            tcp_server.REASON_SERVER_STOPPED,
        )

    def test_events_are_attributed_to_the_right_connection(self):
        first = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        second = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.assertTrue(
            self._wait_for(lambda: len(self._events_of("connected")) == 2)
        )
        first.close()

        self.assertTrue(self._wait_for(lambda: bool(self._events_of("disconnected"))))
        closed = self._events_of("disconnected")[0]["addr"]
        peers = {e["addr"] for e in self._events_of("connected")}
        self.assertIn(closed, peers)
        second.close()

    def test_a_failing_event_handler_does_not_break_the_connection(self):
        """界面画不出来不该让服务端崩掉，更不该把连接弄断。"""
        def boom(kind, **fields):
            raise RuntimeError("ui exploded")

        server = tcp_server.TcpServer(host="127.0.0.1", port=0, on_event=boom)
        threading.Thread(target=server.start, daemon=True).start()
        port = _wait_for_port(server)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.sendall(json.dumps({"type": "heartbeat"}).encode() + b"\n")
                self.assertTrue(
                    self._wait_for(lambda: len(self.received) > 0),
                    "事件处理器抛异常后连接仍应正常处理消息",
                )
        finally:
            server.stop()


def _wait_for_port(server, timeout=5.0):
    """等到服务端真的 bind 上。`server.server` 一被赋值就非 None，但那时还没 bind。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        sock = server.server
        if sock is not None:
            try:
                port = sock.getsockname()[1]
            except OSError:
                port = 0
            if port:
                return port
        time.sleep(0.01)
    raise AssertionError("服务端未能在 5 秒内开始监听")


class BoundPortTest(unittest.TestCase):
    def test_bound_port_is_the_real_port_for_port_zero(self):
        server = tcp_server.TcpServer(host="127.0.0.1", port=0)
        threading.Thread(target=server.start, daemon=True).start()
        try:
            port = _wait_for_port(server)
            self.assertEqual(server.bound_port, port)
            self.assertNotEqual(server.bound_port, server.port)
        finally:
            server.stop()

    def test_bound_port_before_start_falls_back(self):
        self.assertEqual(
            tcp_server.TcpServer(host="127.0.0.1", port=5800).bound_port, 5800
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
