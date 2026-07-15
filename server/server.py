import argparse
import logging
import signal
import sys

from input_controller import move_mouse, click_mouse, scroll, send_text, press_key
from protocol import handler, send_json, send_error
from tcp_server import TcpServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("OmniPad")

@handler("handshake")
def on_handshake(conn, msg):
    version = msg.get("version", "")
    if version != "1.0":
        send_error(conn, "VERSION_MISMATCH", f"expected 1.0 got {version}")
        return False
    send_json(conn, {"type": "handshake_ack", "version": "1.0"})
    logger.info(f"handshake OK, version={version}")
    return True

@handler("heartbeat")
def on_heartbeat(conn, msg):
    send_json(conn, {"type": "heartbeat_ack"})
    return True

@handler("mouse_move")
def on_mouse_move(conn, msg):
    dx = msg.get("dx", 0)
    dy = msg.get("dy", 0)
    move_mouse(dx, dy)
    return True

@handler("mouse_click")
def on_mouse_click(conn, msg):
    button = msg.get("button")
    action = msg.get("action")
    if button not in ("left", "right", "middle") or action not in ("down", "up", "click"):
        send_error(conn, "INVALID_PARAMS", "invalid button or action")
        return True
    if action == "click":
        click_mouse(button, "down")
        click_mouse(button, "up")
    else:
        click_mouse(button, action)
    return True

@handler("scroll")
def on_scroll(conn, msg):
    delta = msg.get("delta", 0)
    scroll(delta * 120)
    return True

@handler("text_input")
def on_text_input(conn, msg):
    text = msg.get("text", "")
    if not text:
        send_error(conn, "INVALID_PARAMS", "text is empty")
        return True
    send_text(text)
    return True

@handler("keyboard")
def on_keyboard(conn, msg):
    key = msg.get("key", "")
    action = msg.get("action")
    if not key or action not in ("down", "up", "press"):
        send_error(conn, "INVALID_PARAMS", "invalid key or action")
        return True
    ok = press_key(key, action)
    if not ok:
        send_error(conn, "INVALID_PARAMS", f"unknown key: {key}")
    return True

def main():
    parser = argparse.ArgumentParser(description="OmniPad Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5800)
    args = parser.parse_args()

    server = TcpServer(host=args.host, port=args.port)

    def shutdown(sig, frame):
        logger.info("shutting down...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.start()
    except OSError as e:
        logger.error(f"failed to start server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
