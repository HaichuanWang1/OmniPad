HANDLER_REGISTRY = {}

def handler(msg_type):
    def decorator(fn):
        HANDLER_REGISTRY[msg_type] = fn
        return fn
    return decorator

def handle_message(conn, msg):
    msg_type = msg.get("type")
    if msg_type not in HANDLER_REGISTRY:
        send_error(conn, "UNKNOWN_TYPE", f"unknown message type: {msg_type}")
        return False

    handler_fn = HANDLER_REGISTRY[msg_type]
    return handler_fn(conn, msg)

def send_json(conn, data):
    try:
        line = (__import__("json").dumps(data) + "\n").encode("utf-8")
        conn.sendall(line)
        return True
    except Exception:
        return False

def send_error(conn, code, message):
    return send_json(conn, {"type": "error", "code": code, "message": message})
