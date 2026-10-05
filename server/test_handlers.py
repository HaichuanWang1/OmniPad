"""handshake 配对令牌校验与 pairing 模块的单元测试。

运行：
    cd server && python test_handlers.py
"""
import json
import os
import re
import socket
import tempfile
import threading
import time
import unittest

import handlers
import pairing
import protocol
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
            {"type": "handshake", "version": handlers.PROTOCOL_VERSION, "token": self.TOKEN}
        )
        self.assertTrue(keep)
        self.assertEqual(resp, {"type": "handshake_ack", "version": handlers.PROTOCOL_VERSION})

    def test_token_is_case_insensitive(self):
        keep, resp = self._handshake(
            {"type": "handshake", "version": handlers.PROTOCOL_VERSION, "token": self.TOKEN.lower()}
        )
        self.assertTrue(keep)
        self.assertEqual(resp["type"], "handshake_ack")

    def test_token_is_trimmed(self):
        keep, _ = self._handshake(
            {"type": "handshake", "version": handlers.PROTOCOL_VERSION, "token": f"  {self.TOKEN}  "}
        )
        self.assertTrue(keep)

    def test_wrong_token_rejected(self):
        keep, resp = self._handshake(
            {"type": "handshake", "version": handlers.PROTOCOL_VERSION, "token": "WRONG123"}
        )
        self.assertFalse(keep, "错误令牌必须要求断开")
        self.assertEqual(resp["type"], "error")
        self.assertEqual(resp["code"], "AUTH_FAILED")

    def test_missing_token_rejected(self):
        keep, resp = self._handshake({"type": "handshake", "version": handlers.PROTOCOL_VERSION})
        self.assertFalse(keep)
        self.assertEqual(resp["code"], "AUTH_FAILED")

    def test_empty_token_rejected(self):
        keep, resp = self._handshake(
            {"type": "handshake", "version": handlers.PROTOCOL_VERSION, "token": ""}
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
        keep, resp = self._handshake({"type": "handshake", "version": handlers.PROTOCOL_VERSION})
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
                    {"type": "handshake", "version": handlers.PROTOCOL_VERSION, "token": self.TOKEN}
                ).encode("utf-8") + b"\n"
            )
            self.assertEqual(
                self._read_line(sock),
                {"type": "handshake_ack", "version": handlers.PROTOCOL_VERSION},
            )

    def test_wrong_token_gets_auth_failed_then_disconnect(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(
                json.dumps(
                    {"type": "handshake", "version": handlers.PROTOCOL_VERSION, "token": "WRONG123"}
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


class MessageValidationTest(unittest.TestCase):
    """字段校验：非法类型应回 INVALID_PARAMS，而不是让 ctypes 抛异常。

    注意：本组把注入函数全部换成空实现 —— 否则测试会真的移动你的鼠标、
    敲你的键盘。
    """

    def setUp(self):
        self.conn = FakeConn()
        self.calls = []
        self._orig_token = handlers.get_pairing_token()
        handlers.set_pairing_token(None)   # 本组只关心字段校验

        self._orig_io = (
            handlers.move_mouse, handlers.click_mouse, handlers.scroll,
            handlers.send_text, handlers.press_key,
        )
        handlers.move_mouse = lambda dx, dy: self.calls.append(("move", dx, dy))
        handlers.click_mouse = lambda button, action: (
            self.calls.append(("click", button, action)) or True
        )
        handlers.scroll = lambda delta: self.calls.append(("scroll", delta))
        handlers.send_text = lambda text: self.calls.append(("text", text)) or True
        handlers.press_key = lambda key, action: (
            self.calls.append(("key", key, action)) or True
        )

    def tearDown(self):
        (handlers.move_mouse, handlers.click_mouse, handlers.scroll,
         handlers.send_text, handlers.press_key) = self._orig_io
        handlers.set_pairing_token(self._orig_token)

    def _dispatch(self, msg):
        protocol.handle_message(self.conn, msg)
        return self.conn.sent[-1] if self.conn.sent else None

    def test_unknown_type(self):
        self.assertEqual(self._dispatch({"type": "nope"})["code"], "UNKNOWN_TYPE")

    def test_mouse_move_accepts_integers(self):
        self.assertIsNone(
            self._dispatch({"type": "mouse_move", "dx": 10, "dy": -5})
        )
        self.assertEqual(self.calls, [("move", 10, -5)])

    def test_mouse_move_rejects_string(self):
        resp = self._dispatch({"type": "mouse_move", "dx": "10", "dy": 0})
        self.assertEqual(resp["code"], "INVALID_PARAMS")
        self.assertEqual(self.calls, [], "非法消息不应触发注入")

    def test_mouse_move_rejects_bool(self):
        """True 是 int 的子类，但当成位移显然是客户端出错。"""
        resp = self._dispatch({"type": "mouse_move", "dx": True, "dy": 0})
        self.assertEqual(resp["code"], "INVALID_PARAMS")
        self.assertEqual(self.calls, [])

    def test_scroll_rejects_string(self):
        resp = self._dispatch({"type": "scroll", "delta": "3"})
        self.assertEqual(resp["code"], "INVALID_PARAMS")
        self.assertEqual(self.calls, [])

    def test_scroll_converts_to_wheel_units(self):
        self._dispatch({"type": "scroll", "delta": 3})
        self.assertEqual(self.calls, [("scroll", 360)])

    def test_text_input_rejects_non_string(self):
        resp = self._dispatch({"type": "text_input", "text": 123})
        self.assertEqual(resp["code"], "INVALID_PARAMS")

    def test_text_input_rejects_empty(self):
        resp = self._dispatch({"type": "text_input", "text": ""})
        self.assertEqual(resp["code"], "INVALID_PARAMS")

    def test_keyboard_rejects_non_string_key(self):
        resp = self._dispatch({"type": "keyboard", "key": 5, "action": "press"})
        self.assertEqual(resp["code"], "INVALID_PARAMS")

    def test_keyboard_rejects_unknown_key(self):
        handlers.press_key = lambda key, action: False
        resp = self._dispatch({"type": "keyboard", "key": "nope", "action": "press"})
        self.assertEqual(resp["code"], "INVALID_PARAMS")

    def test_mouse_click_rejects_unknown_button(self):
        resp = self._dispatch(
            {"type": "mouse_click", "button": "side", "action": "click"}
        )
        self.assertEqual(resp["code"], "INVALID_PARAMS")
        self.assertEqual(self.calls, [])


class AdditionalPropertiesTest(unittest.TestCase):
    """docs/schema.json 声明了 additionalProperties: false，这里确认服务端真的强制。

    没有强制时，字段名打错会被静默忽略 —— 客户端把 dx 写成 dX，服务端取不到
    dx 就用默认值 0，于是「拖动没反应」，而日志里一点异常都没有。
    """

    def setUp(self):
        self.conn = FakeConn()
        self._orig_token = handlers.get_pairing_token()
        handlers.set_pairing_token(None)

        self._orig_io = (
            handlers.move_mouse, handlers.click_mouse, handlers.scroll,
            handlers.send_text, handlers.press_key,
        )
        handlers.move_mouse = lambda dx, dy: True
        handlers.click_mouse = lambda button, action: True
        handlers.scroll = lambda delta: True
        handlers.send_text = lambda text: True
        handlers.press_key = lambda key, action: True

    def tearDown(self):
        (handlers.move_mouse, handlers.click_mouse, handlers.scroll,
         handlers.send_text, handlers.press_key) = self._orig_io
        handlers.set_pairing_token(self._orig_token)

    def _dispatch(self, msg):
        protocol.handle_message(self.conn, msg)
        return self.conn.sent[-1] if self.conn.sent else None

    def test_unexpected_field_rejected(self):
        resp = self._dispatch({"type": "mouse_move", "dx": 1, "dy": 2, "dz": 3})
        self.assertEqual(resp["code"], "INVALID_PARAMS")
        self.assertIn("dz", resp["message"])

    def test_misspelled_field_is_not_silently_ignored(self):
        resp = self._dispatch({"type": "mouse_move", "dX": 10, "dy": 0})
        self.assertEqual(resp["code"], "INVALID_PARAMS")

    def test_heartbeat_rejects_extra_field(self):
        resp = self._dispatch({"type": "heartbeat", "seq": 1})
        self.assertEqual(resp["code"], "INVALID_PARAMS")

    def test_valid_message_still_accepted(self):
        self.assertIsNone(self._dispatch({"type": "mouse_move", "dx": 1, "dy": 2}))

    def test_non_object_json_is_rejected(self):
        # 客户端发 [1,2] / 42 / "hi" 是合法 JSON 但不是对象。旧代码在这里抛
        # AttributeError，它不被 InvalidParams 捕获，会一路冒到 tcp_server 的
        # 兜底 except，把整条连接干掉 —— 而不是回一个 INVALID_PARAMS。
        for payload in ([1, 2], 42, "hello", None, True):
            with self.subTest(payload=payload):
                self.conn.sent.clear()
                resp = self._dispatch(payload)
                self.assertIsNotNone(resp, f"{payload!r} 应当收到错误响应")
                self.assertEqual(resp["code"], "INVALID_PARAMS")


class SchemaConformanceTest(unittest.TestCase):
    """protocol.ALLOWED_FIELDS 必须与 docs/schema.json 逐项一致。

    服务端刻意不引入 JSON Schema 校验器：发布包里不含 docs/，而且服务端只用
    标准库。于是允许字段写死在代码里 —— 那就必须有东西盯着它别漂移，就是这个测试。
    """

    def _schema_objects(self):
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "docs", "schema.json"
        )
        with open(path, encoding="utf-8") as f:
            return json.load(f)["oneOf"]

    def test_allowed_fields_match_schema(self):
        from_schema = {}
        for obj in self._schema_objects():
            self.assertFalse(
                obj.get("additionalProperties", True),
                f"{obj.get('title')} 在 schema 里没有声明 additionalProperties: false",
            )
            type_const = obj["properties"]["type"]["const"]
            from_schema[type_const] = frozenset(obj["properties"])

        self.assertEqual(
            from_schema,
            protocol.ALLOWED_FIELDS,
            "protocol.ALLOWED_FIELDS 与 docs/schema.json 不一致，两边必须同步改",
        )

    def test_every_handler_type_is_covered(self):
        missing = set(protocol.HANDLER_REGISTRY) - set(protocol.ALLOWED_FIELDS)
        self.assertEqual(missing, set(), f"这些消息类型没有登记允许字段: {missing}")


class ProtocolVersionConformanceTest(unittest.TestCase):
    """协议版本号散落在服务端、客户端与文档三处，必须有东西盯着它们别漂移。

    三处各写一遍是没办法的事：跨语言没法共享常量，而且发布包里不含 docs/。
    但版本对不上时客户端只会收到一句 VERSION_MISMATCH，排查成本不低 ——
    不如在测试里直接拦住。1.0 -> 1.1 就是因为令牌变成必填而漏改过一次。
    """

    REPO_ROOT = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )

    def _read(self, *parts):
        with open(os.path.join(self.REPO_ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_protocol_md_title_matches(self):
        text = self._read("docs", "protocol.md")
        m = re.search(r"^#\s*OmniPad TCP 协议文档\s*v(\d+\.\d+)", text, re.MULTILINE)
        self.assertIsNotNone(m, "docs/protocol.md 标题里找不到协议版本号")
        self.assertEqual(m.group(1), handlers.PROTOCOL_VERSION)

    def test_schema_description_matches(self):
        schema = json.loads(self._read("docs", "schema.json"))
        self.assertIn(
            handlers.PROTOCOL_VERSION,
            schema["description"],
            "docs/schema.json 的 description 里没有当前协议版本号",
        )

    def test_client_handshake_default_matches(self):
        text = self._read(
            "client", "app", "src", "main", "java", "com", "omnipad", "client",
            "network", "Protocol.kt",
        )
        m = re.search(r'data class Handshake\(val version: String = "(\d+\.\d+)"', text)
        self.assertIsNotNone(m, "Protocol.kt 里找不到 Handshake 的默认版本号")
        self.assertEqual(
            m.group(1),
            handlers.PROTOCOL_VERSION,
            "客户端握手版本与服务端 PROTOCOL_VERSION 不一致",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
