import json

HANDLER_REGISTRY = {}


class InvalidParams(ValueError):
    """消息字段不符合 docs/protocol.md 的规定。

    处理器抛出它，由 handle_message 统一转成 INVALID_PARAMS 错误响应，
    这样每个处理器不必各写一遍错误回包。
    """


def handler(msg_type):
    def decorator(fn):
        HANDLER_REGISTRY[msg_type] = fn
        return fn
    return decorator

def handle_message(conn, msg):
    msg_type = msg.get("type")
    if msg_type not in HANDLER_REGISTRY:
        send_error(conn, "UNKNOWN_TYPE", f"unknown message type: {msg_type}")
        return True

    handler_fn = HANDLER_REGISTRY[msg_type]
    try:
        return handler_fn(conn, msg)
    except InvalidParams as e:
        send_error(conn, "INVALID_PARAMS", str(e))
        return True

def send_json(conn, data):
    try:
        line = (json.dumps(data) + "\n").encode("utf-8")
        conn.sendall(line)
        return True
    except Exception:
        return False

def send_error(conn, code, message):
    return send_json(conn, {"type": "error", "code": code, "message": message})
