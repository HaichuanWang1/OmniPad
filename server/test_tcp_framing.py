r"""TCP 分帧回归测试。

验证 UTF-8 多字节字符（如中文，3 字节）被 TCP 分片切开时不会被损坏。

历史 bug：`tcp_server._handle_client` 曾对每个 recv 块独立调用
`data.decode("utf-8")`，失败时回退 `errors="surrogateescape"`，
把跨片的多字节字符拆成孤立代理字符（`"测"` -> `'\udce6\udcb5\udc8b'`），
导致长文本 / 网络抖动时中文输入随机损坏。

运行：
    cd server && python test_tcp_framing.py
"""
import json
import socket
import threading
import time
import unittest

import tcp_server


class TcpFramingTest(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.errors = []

        self._orig_handle = tcp_server.handle_message
        self._orig_error = tcp_server.send_error
        tcp_server.handle_message = self._fake_handle
        tcp_server.send_error = self._fake_error

        self.server = tcp_server.TcpServer(host="127.0.0.1", port=0)
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
        return True

    def _fake_error(self, conn, code, message):
        self.errors.append((code, message))
        return True

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

    # --- 回归用例 -------------------------------------------------------

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
        """非法字节序列应回报 INVALID_UTF8 而不是崩溃或静默吞掉。"""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
