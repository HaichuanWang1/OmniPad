"""本机控制通道：让状态可以被「问」出来，而不是靠猜。

状态文件是一张可能过期的快照 —— 文件还在，进程可能已经死了。控制通道是活着的
进程本人给出的回答，两者互补：

    状态文件  → 谁在跑、谁连着（不依赖进程还活着）
    控制通道  → 现在到底怎么样、请优雅地停下来

只绑 `127.0.0.1`，端口由内核随机分配后写进状态文件。不额外存什么秘密：能读到
状态文件的本机进程本来就能 taskkill 掉服务端，加一层令牌只是自欺欺人。

协议是 JSON Lines，一行一个请求、一行一个响应。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading

logger = logging.getLogger("OmniPad")

CONTROL_HOST = "127.0.0.1"
DEFAULT_TIMEOUT = 3.0
MAX_LINE_BYTES = 256 * 1024

CMD_PING = "ping"
CMD_STATUS = "status"
CMD_STOP = "stop"
COMMANDS = (CMD_PING, CMD_STATUS, CMD_STOP)


class ControlError(RuntimeError):
    """控制通道不可用（没在跑、端口不对、响应畸形）。"""


class ControlServer:
    """控制通道服务端。

    `status_provider` 必须线程安全 —— 它会在控制线程里被调用。
    `on_stop` 在响应发出并断开之后才触发，这样调用方一定能先收到回执。
    """

    def __init__(self, status_provider, on_stop=None, host=CONTROL_HOST, port=0):
        self.host = host
        self._requested_port = port
        self._status_provider = status_provider
        self._on_stop = on_stop
        self._sock = None
        self._thread = None
        self._running = False
        self._port = None

    @property
    def port(self):
        return self._port

    def start(self) -> int:
        """开始监听并返回真实端口（port=0 时由内核分配）。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self._requested_port))
            sock.listen(8)
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self._port = sock.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(
            target=self._serve, name="omnipad-control", daemon=True
        )
        self._thread.start()
        logger.info(f"control channel listening on {self.host}:{self._port}")
        return self._port

    def stop(self):
        self._running = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    # ---- 内部 ----

    def _serve(self):
        while self._running and self._sock is not None:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                break
            threading.Thread(
                target=self._handle, args=(conn,), daemon=True
            ).start()

    def _handle(self, conn):
        stop_requested = False
        try:
            conn.settimeout(DEFAULT_TIMEOUT)
            request = self._read_line(conn)
            if request is None:
                return
            response, stop_requested = self._dispatch(request)
            conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        except (OSError, ValueError) as e:
            logger.debug(f"control request failed: {e}")
        finally:
            try:
                conn.close()
            except OSError:
                pass

        # 回执已经发出去、连接也已经关了，这时候停才不会让调用方空等。
        if stop_requested and self._on_stop is not None:
            threading.Thread(target=self._on_stop, daemon=True).start()

    @staticmethod
    def _read_line(conn):
        buffer = b""
        while b"\n" not in buffer:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > MAX_LINE_BYTES:
                raise ValueError("control request too large")
        if not buffer.strip():
            return None
        raw = buffer.split(b"\n", 1)[0]
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"invalid control request: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("control request must be a JSON object")
        return payload

    def _dispatch(self, request):
        """返回 `(响应, 是否请求停止)`。"""
        cmd = request.get("cmd")
        if cmd == CMD_PING:
            return {"ok": True, "cmd": CMD_PING, "pid": os.getpid()}, False
        if cmd == CMD_STATUS:
            return {
                "ok": True,
                "cmd": CMD_STATUS,
                "status": self._status_provider(),
            }, False
        if cmd == CMD_STOP:
            logger.info("stop requested via control channel")
            return {"ok": True, "cmd": CMD_STOP, "pid": os.getpid()}, True
        return {
            "ok": False,
            "error": f"unknown command: {cmd!r}",
            "commands": list(COMMANDS),
        }, False


def request(cmd, host, port, timeout=DEFAULT_TIMEOUT):
    """向控制通道发一条命令，返回响应字典。

    失败一律抛 ControlError —— 调用方需要区分「服务端说不行」和「根本联系不上」。
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((json.dumps({"cmd": cmd}) + "\n").encode("utf-8"))
            buffer = b""
            while b"\n" not in buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_LINE_BYTES:
                    raise ControlError("控制通道响应过大")
    except OSError as e:
        raise ControlError(f"无法连接控制通道 {host}:{port}：{e}") from e

    if not buffer.strip():
        raise ControlError("控制通道没有返回内容")
    try:
        payload = json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ControlError(f"控制通道返回了无法解析的内容：{e}") from e
    if not isinstance(payload, dict):
        raise ControlError("控制通道返回的不是 JSON 对象")
    return payload


def fetch_status(status_payload, timeout=DEFAULT_TIMEOUT):
    """按状态文件里记录的控制端口去问活进程要实时状态。

    成功返回 `(status_dict, None)`，失败返回 `(None, 原因)`。调用方通常会在失败时
    回退到状态文件里的快照，并如实说明这是快照。
    """
    control = (status_payload or {}).get("control") or {}
    host, port = control.get("host"), control.get("port")
    if not host or not port:
        return None, "状态文件里没有控制通道信息"
    try:
        response = request(CMD_STATUS, host, port, timeout=timeout)
    except ControlError as e:
        return None, str(e)
    if not response.get("ok"):
        return None, response.get("error") or "控制通道返回失败"
    status = response.get("status")
    if not isinstance(status, dict):
        return None, "控制通道没有返回状态"
    return status, None
