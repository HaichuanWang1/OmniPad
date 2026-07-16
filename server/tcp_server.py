import json
import socket
import threading
import time
import logging

from protocol import handle_message, send_error

logger = logging.getLogger("OmniPad")

IDLE_TIMEOUT = 15

class TcpServer:
    def __init__(self, host="0.0.0.0", port=5800):
        self.host = host
        self.port = port
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
        conn.settimeout(30)
        buffer = ""
        last_message = time.time()
        try:
            while self.running:
                try:
                    data = conn.recv(4096)
                except OSError:
                    break
                if not data:
                    break

                try:
                    decoded = data.decode("utf-8")
                except UnicodeDecodeError:
                    decoded = data.decode("utf-8", errors="surrogateescape")
                    logger.warning(f"client {addr} sent partial UTF-8, used surrogateescape")

                buffer += decoded

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        send_error(conn, "INVALID_PARAMS", "invalid JSON")
                        continue
                    if not handle_message(conn, msg):
                        break
                    last_message = time.time()

                if time.time() - last_message > IDLE_TIMEOUT:
                    logger.warning(f"client {addr} idle timeout ({IDLE_TIMEOUT}s)")
                    break
        except socket.timeout:
            logger.warning(f"client {addr} timed out")
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
