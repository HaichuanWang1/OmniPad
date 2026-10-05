"""协议消息处理器 —— Server 两个入口共用的唯一一份实现。

历史上 `server.py`（无头）与 `server_ui.py`（GUI）各自写了一份完整 handler，
很快就漂移了：`ACTION_FAILED` 只在无头模式上报，GUI 模式下鼠标点击失败静默
吞掉。更麻烦的是两边都在导入时往全局 `HANDLER_REGISTRY` 注册，同时导入会
互相覆盖。

现在处理器只此一份，两个入口 `import handlers` 即可完成注册。
"""
import logging

import pairing
from input_controller import move_mouse, click_mouse, scroll, send_text, press_key
from protocol import handler, send_json, send_error

logger = logging.getLogger("OmniPad")

PROTOCOL_VERSION = "1.0"

MOUSE_BUTTONS = ("left", "right", "middle")
MOUSE_ACTIONS = ("down", "up", "click")
KEY_ACTIONS = ("down", "up", "press")

# 期望的配对令牌。None 表示不校验（仅供测试）；正常启动时一定会被设置。
_expected_token: str | None = None


def set_pairing_token(token) -> None:
    """设置期望的配对令牌。传入 None 或空串表示关闭校验。"""
    global _expected_token
    _expected_token = pairing.normalize(token) or None


def get_pairing_token() -> str | None:
    return _expected_token


def _fail(conn, code, message):
    """回报错误并保持连接（返回 True 让调用方直接 return）。"""
    send_error(conn, code, message)
    return True


@handler("handshake")
def on_handshake(conn, msg):
    version = msg.get("version", "")
    if version != PROTOCOL_VERSION:
        send_error(
            conn, "VERSION_MISMATCH",
            f"expected {PROTOCOL_VERSION} got {version}",
        )
        return False  # 请求断开，由 tcp_server 关闭连接

    if _expected_token is not None:
        provided = pairing.normalize(msg.get("token", ""))
        if provided != _expected_token:
            logger.warning("handshake rejected: invalid pairing token")
            send_error(conn, "AUTH_FAILED", "invalid pairing token")
            return False

    send_json(conn, {"type": "handshake_ack", "version": PROTOCOL_VERSION})
    logger.info(f"handshake OK, version={version}")
    return True


@handler("heartbeat")
def on_heartbeat(conn, msg):
    send_json(conn, {"type": "heartbeat_ack"})
    return True


@handler("mouse_move")
def on_mouse_move(conn, msg):
    move_mouse(msg.get("dx", 0), msg.get("dy", 0))
    return True


@handler("mouse_click")
def on_mouse_click(conn, msg):
    button = msg.get("button")
    action = msg.get("action")
    if button not in MOUSE_BUTTONS or action not in MOUSE_ACTIONS:
        return _fail(conn, "INVALID_PARAMS", "invalid button or action")

    if action == "click":
        ok = click_mouse(button, "down") and click_mouse(button, "up")
    else:
        ok = click_mouse(button, action)

    if not ok:
        return _fail(conn, "ACTION_FAILED", f"mouse {button} {action} failed")
    return True


@handler("scroll")
def on_scroll(conn, msg):
    scroll(msg.get("delta", 0) * 120)
    return True


@handler("text_input")
def on_text_input(conn, msg):
    text = msg.get("text", "")
    if not text:
        return _fail(conn, "INVALID_PARAMS", "text is empty")
    # 刻意不记录 text 内容：那等于把用户的键盘输入写进日志。
    if not send_text(text):
        return _fail(conn, "ACTION_FAILED", "text input failed")
    return True


@handler("keyboard")
def on_keyboard(conn, msg):
    key = msg.get("key", "")
    action = msg.get("action")
    if not key or action not in KEY_ACTIONS:
        return _fail(conn, "INVALID_PARAMS", "invalid key or action")
    if not press_key(key, action):
        return _fail(conn, "INVALID_PARAMS", f"unknown key: {key}")
    return True
