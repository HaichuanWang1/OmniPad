import json
import socket
import threading
import time
import logging

from protocol import handle_message, send_error

logger = logging.getLogger("OmniPad")

IDLE_TIMEOUT = 15

class TcpServer:
    def __init__(self, host="0.0.0.0", port=5800, idle_timeout=IDLE_TIMEOUT):
        self.host = host
        self.port = port
        self.idle_timeout = idle_timeout
        self.server = None
        self.running = False
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    def start(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.host, self.port))
        self.server.listen(5)
        self.running = True
        logger.info(f"TCP server listening on {self.host}:{self.port}")

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

    def _handle_client(self, conn, addr):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # 每 1 秒醒一次，而不是阻塞在对端静默的 recv 上。这样空闲超时才能按
        # idle_timeout（docs/protocol.md 规定 15s）真正生效 —— 原先 settimeout(30)
        # 让对端静默时要等满 30s 才断开，与文档不符。同时 self.running 也能被
        # 及时察觉，stop() 后连接线程可尽快退出。
        conn.settimeout(1)
        buffer = b""
        last_message = time.time()
        should_close = False
        try:
            while self.running:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    data = None          # 本轮无数据，落到下面做空闲判定
                except OSError:
                    break

                if data is not None:
                    if not data:
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
                            send_error(conn, "INVALID_PARAMS", "invalid UTF-8")
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            send_error(conn, "INVALID_PARAMS", "invalid JSON")
                            continue
                        if not handle_message(conn, msg):
                            # handler 返回 False 表示要求断开（如 VERSION_MISMATCH）。
                            # 只跳出内层行循环是不够的：外层 while 会继续 recv 阻塞，
                            # 连接实际不会关闭，与 docs/protocol.md 的规定不符。
                            should_close = True
                            break
                        last_message = time.time()

                if should_close:
                    break

                if time.time() - last_message > self.idle_timeout:
                    logger.warning(f"client {addr} idle timeout ({self.idle_timeout}s)")
                    break
        except ConnectionResetError:
            pass
        except Exception as e:
            logger.error(f"client {addr} error: {e}")
        finally:
            with self._lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
            logger.info(f"client {addr} disconnected")
