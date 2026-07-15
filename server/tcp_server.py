import json
import socket
import threading
import logging

from protocol import handle_message, send_json, send_error

logger = logging.getLogger("OmniPad")

class TcpServer:
    def __init__(self, host="0.0.0.0", port=5800):
        self.host = host
        self.port = port
        self.server = None
        self.running = False

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
                t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                t.start()
            except OSError:
                break

    def stop(self):
        self.running = False
        if self.server:
            self.server.close()
            logger.info("server stopped")

    def _handle_client(self, conn, addr):
        conn.settimeout(30)
        buffer = ""
        try:
            while self.running:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8")
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
        except socket.timeout:
            logger.warning(f"client {addr} timed out")
        except ConnectionResetError:
            pass
        except Exception as e:
            logger.error(f"client {addr} error: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
            logger.info(f"client {addr} disconnected")
