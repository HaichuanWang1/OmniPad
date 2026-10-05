"""handshake 配对令牌校验与 pairing 模块的单元测试。

运行：
    cd server && python test_handlers.py
"""
import json
import os
import socket
import tempfile
import threading
import time
import unittest

import handlers
import pairing
import tcp_server


class FakeConn:
    """记录 send_json 写出的内容，不碰真实 socket。"""

    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(json.loads(data.decode("utf-8")))

    def close(self):
        pass


class HandshakeTokenTest(unittest.TestCase):
    TOKEN = "K7M2P9QR"

    def setUp(self):
        self.conn = FakeConn()
        self._orig = handlers.get_pairing_token()
        handlers.set_pairing_token(self.TOKEN)

    def tearDown(self):
        handlers.set_pairing_token(self._orig)

    def _handshake(self, msg):
        keep = handlers.on_handshake(self.conn, msg)
        return keep, (self.conn.sent[-1] if self.conn.sent else None)

    def test_correct_token_accepted(self):
        keep, resp = self._handshake(
            {"type": "handshake", "version": "1.0", "token": self.TOKEN}
        )
        self.assertTrue(keep)
        self.assertEqual(resp, {"type": "handshake_ack", "version": "1.0"})

    def test_token_is_case_insensitive(self):
        keep, resp = self._handshake(
            {"type": "handshake", "version": "1.0", "token": self.TOKEN.lower()}
        )
        self.assertTrue(keep)
        self.assertEqual(resp["type"], "handshake_ack")

    def test_token_is_trimmed(self):
        keep, _ = self._handshake(
            {"type": "handshake", "version": "1.0", "token": f"  {self.TOKEN}  "}
        )
        self.assertTrue(keep)

    def test_wrong_token_rejected(self):
        keep, resp = self._handshake(
            {"type": "handshake", "version": "1.0", "token": "WRONG123"}
        )
        self.assertFalse(keep, "错误令牌必须要求断开")
        self.assertEqual(resp["type"], "error")
        self.assertEqual(resp["code"], "AUTH_FAILED")

    def test_missing_token_rejected(self):
        keep, resp = self._handshake({"type": "handshake", "version": "1.0"})
        self.assertFalse(keep)
        self.assertEqual(resp["code"], "AUTH_FAILED")

    def test_empty_token_rejected(self):
        keep, resp = self._handshake(
            {"type": "handshake", "version": "1.0", "token": ""}
        )
        self.assertFalse(keep)
        self.assertEqual(resp["code"], "AUTH_FAILED")

    def test_version_checked_before_token(self):
        """版本错误应报 VERSION_MISMATCH，而不是先报 AUTH_FAILED。"""
        keep, resp = self._handshake(
            {"type": "handshake", "version": "9.9", "token": self.TOKEN}
        )
        self.assertFalse(keep)
        self.assertEqual(resp["code"], "VERSION_MISMATCH")

    def test_disabled_token_skips_check(self):
        """set_pairing_token(None) 关闭校验，仅用于测试环境。"""
        handlers.set_pairing_token(None)
        keep, resp = self._handshake({"type": "handshake", "version": "1.0"})
        self.assertTrue(keep)
        self.assertEqual(resp["type"], "handshake_ack")


class PairingTest(unittest.TestCase):
    def test_generated_token_shape(self):
        for _ in range(50):
            token = pairing.generate_token()
            self.assertEqual(len(token), pairing.TOKEN_LENGTH)
            self.assertTrue(
                set(token) <= set(pairing.ALPHABET),
                f"令牌含字母表外字符: {token}",
            )

    def test_alphabet_excludes_ambiguous_characters(self):
        for ch in "IO01":
            self.assertNotIn(ch, pairing.ALPHABET)

    def test_normalize(self):
        self.assertEqual(pairing.normalize("  ab12cd  "), "AB12CD")
        self.assertEqual(pairing.normalize(None), "")
        self.assertEqual(pairing.normalize(""), "")

    def test_load_or_create_is_stable_across_calls(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pairing_token.txt")
            first = pairing.load_or_create_token(path)
            second = pairing.load_or_create_token(path)
            self.assertEqual(first, second)
            self.assertTrue(os.path.exists(path))

    def test_existing_token_is_reused(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pairing_token.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("  fixed123  \n")
            self.assertEqual(pairing.load_or_create_token(path), "FIXED123")


class PairingIntegrationTest(unittest.TestCase):
    """走真实 TcpServer + 真实 handlers，确认令牌校验真的端到端生效。

    上面的 HandshakeTokenTest 直接调用 on_handshake，绕过了 tcp_server 的
    分帧与连接关闭逻辑；这里补上那一层。
    """

    TOKEN = "K7M2P9QR"

    def setUp(self):
        self._orig = handlers.get_pairing_token()
        handlers.set_pairing_token(self.TOKEN)

        self.server = tcp_server.TcpServer(host="127.0.0.1", port=0, idle_timeout=5)
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
        handlers.set_pairing_token(self._orig)

    @staticmethod
    def _read_line(sock):
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if not buf.strip():
            return None
        return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))

    def test_correct_token_gets_ack(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(
                json.dumps(
                    {"type": "handshake", "version": "1.0", "token": self.TOKEN}
                ).encode("utf-8") + b"\n"
            )
            self.assertEqual(
                self._read_line(sock),
                {"type": "handshake_ack", "version": "1.0"},
            )

    def test_wrong_token_gets_auth_failed_then_disconnect(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(
                json.dumps(
                    {"type": "handshake", "version": "1.0", "token": "WRONG123"}
                ).encode("utf-8") + b"\n"
            )
            resp = self._read_line(sock)
            self.assertEqual(resp["type"], "error")
            self.assertEqual(resp["code"], "AUTH_FAILED")

            # 服务端必须真正关闭连接，而不是继续 recv 阻塞
            sock.settimeout(3)
            try:
                self.assertEqual(sock.recv(4096), b"")
            except socket.timeout:
                self.fail("AUTH_FAILED 后服务端未关闭连接")


if __name__ == "__main__":
    unittest.main(verbosity=2)
