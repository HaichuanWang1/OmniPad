"""control.py 的单元测试：本机控制通道。

状态文件是一张可能过期的快照，控制通道是活着的进程本人给出的回答。
这里盯住三件事：能问出实时状态、`stop` 一定先回执再停、只绑回环。

运行：
    cd server && python test_control.py
"""
import json
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import control


def _send_raw(host, port, payload, timeout=3.0):
    """发一段原始字节，返回服务端回的内容（连接被直接关掉时是 b""）。"""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        buffer = b""
        while b"\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
        return buffer


class ControlServerTest(unittest.TestCase):
    def setUp(self):
        self.status_calls = []
        self.stop_called = threading.Event()
        self.server = control.ControlServer(
            status_provider=self._provider,
            on_stop=self.stop_called.set,
        )
        self.port = self.server.start()

    def tearDown(self):
        self.server.stop()

    def _provider(self):
        self.status_calls.append(time.time())
        return {"pid": os.getpid(), "running": True, "clients": []}

    def test_binds_loopback_only(self):
        """控制通道绝不能被局域网访问到 —— 它能停掉服务端。"""
        self.assertEqual(self.port, self.server.port)
        self.assertEqual(self.server._sock.getsockname()[0], "127.0.0.1")
        self.assertNotEqual(self.port, 0)

    def test_ping(self):
        response = control.request(control.CMD_PING, "127.0.0.1", self.port)
        self.assertTrue(response["ok"])
        self.assertEqual(response["cmd"], "ping")
        self.assertEqual(response["pid"], os.getpid())

    def test_status_returns_the_live_snapshot(self):
        response = control.request(control.CMD_STATUS, "127.0.0.1", self.port)
        self.assertTrue(response["ok"])
        self.assertEqual(response["status"]["pid"], os.getpid())
        self.assertEqual(len(self.status_calls), 1, "状态必须现取，不能缓存")

    def test_status_is_fresh_on_every_call(self):
        control.request(control.CMD_STATUS, "127.0.0.1", self.port)
        control.request(control.CMD_STATUS, "127.0.0.1", self.port)
        self.assertEqual(len(self.status_calls), 2)

    def test_stop_replies_before_stopping(self):
        """先回执再停。反过来的话调用方永远等不到那句「好」。"""
        response = control.request(control.CMD_STOP, "127.0.0.1", self.port)
        self.assertTrue(response["ok"])
        self.assertTrue(
            self.stop_called.wait(timeout=3),
            "stop 命令必须触发 on_stop 回调",
        )

    def test_unknown_command(self):
        response = control.request("explode", "127.0.0.1", self.port)
        self.assertFalse(response["ok"])
        self.assertIn("unknown command", response["error"])
        self.assertEqual(response["commands"], list(control.COMMANDS))

    def test_malformed_json_gets_no_response_but_does_not_crash(self):
        raw = _send_raw("127.0.0.1", self.port, b"not json\n")
        self.assertEqual(raw, b"", "畸形请求应被静默丢弃，而不是回一个半截响应")

        # 服务端必须还活着
        self.assertTrue(control.request(control.CMD_PING, "127.0.0.1", self.port)["ok"])

    def test_non_object_json_is_rejected(self):
        raw = _send_raw("127.0.0.1", self.port, b"[1,2,3]\n")
        self.assertEqual(raw, b"")

    def test_oversized_request_is_rejected(self):
        original = control.MAX_LINE_BYTES
        control.MAX_LINE_BYTES = 64
        try:
            raw = _send_raw("127.0.0.1", self.port, b"x" * 500)
            self.assertEqual(raw, b"")
        finally:
            control.MAX_LINE_BYTES = original

    def test_empty_connection_is_ignored(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as sock:
            sock.close()
        self.assertTrue(control.request(control.CMD_PING, "127.0.0.1", self.port)["ok"])

    def test_two_commands_can_run_concurrently(self):
        """控制通道的读写必须并发 —— 串行处理时一个卡住的客户端会拖死所有查询。"""
        results = []

        def run():
            results.append(control.request(control.CMD_STATUS, "127.0.0.1", self.port))

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(results), 8)
        self.assertTrue(all(r["ok"] for r in results))

    def test_stop_closes_the_listener(self):
        self.server.stop()
        with self.assertRaises(control.ControlError):
            control.request(control.CMD_PING, "127.0.0.1", self.port)


class RequestFailureTest(unittest.TestCase):
    def test_connection_refused_raises_control_error(self):
        # 先占一个端口再立刻释放，拿到一个几乎不可能有人在监听的端口号
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        with self.assertRaises(control.ControlError):
            control.request(control.CMD_PING, "127.0.0.1", port, timeout=1.0)

    def test_empty_response_raises(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def accept_and_close():
            conn, _ = listener.accept()
            conn.close()
            listener.close()

        threading.Thread(target=accept_and_close, daemon=True).start()
        with self.assertRaises(control.ControlError):
            control.request(control.CMD_PING, "127.0.0.1", port, timeout=2.0)


class FetchStatusTest(unittest.TestCase):
    def setUp(self):
        self.server = control.ControlServer(
            status_provider=lambda: {"pid": os.getpid(), "running": True}
        )
        self.port = self.server.start()

    def tearDown(self):
        self.server.stop()

    def test_success(self):
        status, error = control.fetch_status(
            {"control": {"host": "127.0.0.1", "port": self.port}}
        )
        self.assertIsNone(error)
        self.assertEqual(status["pid"], os.getpid())

    def test_missing_control_block(self):
        status, error = control.fetch_status({"pid": 1})
        self.assertIsNone(status)
        self.assertIn("控制通道", error)

        status, error = control.fetch_status(None)
        self.assertIsNone(status)
        self.assertIsNotNone(error)

    def test_unreachable_control_port(self):
        status, error = control.fetch_status(
            {"control": {"host": "127.0.0.1", "port": 1}}, timeout=0.5
        )
        self.assertIsNone(status)
        self.assertIsNotNone(error)


class ReadLineTest(unittest.TestCase):
    """`_read_line` 直接吃 socket，用一对真实 socket 喂它。"""

    def test_reads_one_line_and_ignores_the_rest(self):
        left, right = socket.socketpair()
        try:
            right.sendall(b'{"cmd":"ping"}\n{"cmd":"stop"}\n')
            payload = control.ControlServer._read_line(left)
            self.assertEqual(payload, {"cmd": "ping"})
        finally:
            left.close()
            right.close()

    def test_returns_none_on_empty_input(self):
        left, right = socket.socketpair()
        try:
            right.close()
            self.assertIsNone(control.ControlServer._read_line(left))
        finally:
            left.close()

    def test_rejects_non_object(self):
        left, right = socket.socketpair()
        try:
            right.sendall(b"42\n")
            with self.assertRaises(ValueError):
                control.ControlServer._read_line(left)
        finally:
            left.close()
            right.close()

    def test_rejects_invalid_utf8(self):
        left, right = socket.socketpair()
        try:
            right.sendall(b"\xff\xfe\n")
            with self.assertRaises(ValueError):
                control.ControlServer._read_line(left)
        finally:
            left.close()
            right.close()


class DispatchTest(unittest.TestCase):
    def _server(self):
        return control.ControlServer(status_provider=lambda: {"ok": 1})

    def test_dispatch_marks_stop(self):
        server = self._server()
        response, stop = server._dispatch({"cmd": control.CMD_STOP})
        self.assertTrue(response["ok"])
        self.assertTrue(stop)

    def test_dispatch_does_not_stop_for_other_commands(self):
        server = self._server()
        for cmd in (control.CMD_PING, control.CMD_STATUS):
            with self.subTest(cmd=cmd):
                response, stop = server._dispatch({"cmd": cmd})
                self.assertTrue(response["ok"])
                self.assertFalse(stop)

    def test_missing_cmd(self):
        response, stop = self._server()._dispatch({})
        self.assertFalse(response["ok"])
        self.assertFalse(stop)

    def test_responses_are_json_serializable(self):
        server = self._server()
        for cmd in control.COMMANDS:
            with self.subTest(cmd=cmd):
                response, _ = server._dispatch({"cmd": cmd})
                json.dumps(response)


if __name__ == "__main__":
    unittest.main(verbosity=2)
