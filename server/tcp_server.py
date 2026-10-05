import json
import socket
import threading
import time
import logging

from protocol import Connection, handle_message, send_error

logger = logging.getLogger("OmniPad")

IDLE_TIMEOUT = 15

# 连接关闭的原因，与 state.py 的 DISCONNECT_REASON_TEXT 一一对应。
REASON_CLIENT_CLOSED = "client_closed"
REASON_IDLE_TIMEOUT = "idle_timeout"
REASON_REJECTED = "rejected"
REASON_ERROR = "error"
REASON_CONNECTION_RESET = "connection_reset"
REASON_SERVER_STOPPED = "server_stopped"

class TcpServer:
    def __init__(self, host="0.0.0.0", port=5800, idle_timeout=IDLE_TIMEOUT, on_event=None):
        self.host = host
        self.port = port
        self.idle_timeout = idle_timeout
        self.on_event = on_event
        self.server = None
        self.running = False
        # bind + listen 完成的信号。`self.server` 一被赋值就非 None，但那时还没
        # bind —— 等待方据此读端口会读到 0，而且再也不会重读。
        self.ready = threading.Event()
        self.start_error = None
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    @property
    def bound_port(self):
        """实际监听的端口。

        `--port 0` 时端口由内核分配，必须回读 —— 否则状态文件里会写一个 0，
        而调用方拿着 0 是连不上的。
        """
        if self.server is None:
            return self.port
        try:
            return self.server.getsockname()[1]
        except OSError:
            return self.port

    def start(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server.bind((self.host, self.port))
            self.server.listen(5)
        finally:
            # 无论成败都要唤醒等待方 —— 失败时让它去读 start_error，
            # 而不是傻等到超时再报一句「没能在 N 秒内开始监听」。
            self.ready.set()
        self.running = True
        logger.info(f"TCP server listening on {self.host}:{self.bound_port}")

        while self.running:
            try:
                conn, addr = self.server.accept()
                logger.info(f"client connected: {addr}")
                with self._lock:
                    self._clients.append(conn)
                t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                t.start()
            except OSError:
                break

    def stop(self):
        self.running = False
        with self._lock:
            for conn in self._clients:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    conn.close()
                except OSError:
                    pass
            self._clients.clear()
        if self.server:
            try:
                self.server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.server.close()
            logger.info("server stopped")

    def _emit(self, kind, **fields):
        """把连接事件报给上层（状态机 / 界面）。

        上报失败绝不能影响协议处理 —— 一个画不出来的表格不该让服务端崩掉。
        """
        if self.on_event is None:
            return
        try:
            self.on_event(kind, **fields)
        except Exception:
            logger.exception(f"event handler failed for {kind}")

    def _handle_client(self, conn, addr):
        session = Connection(conn, addr, on_event=self._emit)
        self._emit("connected", addr=session.addr, peer=session.peer, port=session.port)

        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # 每 1 秒醒一次，而不是阻塞在对端静默的 recv 上。这样空闲超时才能按
        # idle_timeout（docs/protocol.md 规定 15s）真正生效 —— 原先 settimeout(30)
        # 让对端静默时要等满 30s 才断开，与文档不符。同时 self.running 也能被
        # 及时察觉，stop() 后连接线程可尽快退出。
        conn.settimeout(1)
        buffer = b""
        last_message = time.time()
        should_close = False
        reason = None
        try:
            while self.running:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    data = None          # 本轮无数据，落到下面做空闲判定
                except ConnectionResetError:
                    # 对端异常关闭（进程被杀、socket 带着未读数据被 close）。
                    # 与「客户端主动断开」分开报，否则用户分不清是手机退出了
                    # 还是链路被掐了。
                    reason = REASON_CONNECTION_RESET
                    break
                except OSError:
                    # stop() 会 shutdown 掉所有连接，此时 recv 抛的是 OSError 而不是
                    # 返回空 —— 那不是「连接出错」，是服务端自己关的。
                    reason = REASON_SERVER_STOPPED if not self.running else REASON_ERROR
                    break

                if data is not None:
                    if not data:
                        reason = REASON_CLIENT_CLOSED
                        break

                    # 按字节累积：一个 UTF-8 多字节字符（中文 3 字节）可能被 TCP
                    # 分片切开，必须等完整的 \n 行到齐后再整体解码，否则会被
                    # surrogateescape 拆成孤立代理字符，导致中文输入损坏。
                    buffer += data

                    while b"\n" in buffer:
                        raw_line, buffer = buffer.split(b"\n", 1)
                        if not raw_line.strip():
                            continue
                        try:
                            line = raw_line.decode("utf-8")
                        except UnicodeDecodeError:
                            logger.warning(f"client {addr} sent invalid UTF-8, message dropped")
                            send_error(session, "INVALID_PARAMS", "invalid UTF-8")
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            send_error(session, "INVALID_PARAMS", "invalid JSON")
                            continue
                        if not handle_message(session, msg):
                            # handler 返回 False 表示要求断开（如 VERSION_MISMATCH）。
                            # 只跳出内层行循环是不够的：外层 while 会继续 recv 阻塞，
                            # 连接实际不会关闭，与 docs/protocol.md 的规定不符。
                            should_close = True
                            reason = REASON_REJECTED
                            break
                        last_message = time.time()

                if should_close:
                    break

                if time.time() - last_message > self.idle_timeout:
                    logger.warning(f"client {addr} idle timeout ({self.idle_timeout}s)")
                    reason = REASON_IDLE_TIMEOUT
                    break
        except ConnectionResetError:
            reason = REASON_ERROR
        except Exception as e:
            logger.error(f"client {addr} error: {e}")
            reason = REASON_ERROR
        finally:
            with self._lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
            if reason is None:
                # while 条件不成立而退出，说明是 stop() 把 running 置了 False
                reason = REASON_SERVER_STOPPED if not self.running else REASON_CLIENT_CLOSED
            logger.info(f"client {addr} disconnected ({reason})")
            self._emit("disconnected", addr=session.addr, reason=reason)
