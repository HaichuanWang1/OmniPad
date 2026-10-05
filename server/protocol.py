import json

HANDLER_REGISTRY = {}

# 每种消息允许出现的字段，与 docs/schema.json 的 additionalProperties: false 一一对应。
#
# 为什么写死在代码里而不是运行时读 schema.json：发布包里不含 docs/，而且服务端
# 刻意只用标准库（见 requirements.txt）。两边的一致性由
# test_handlers.SchemaConformanceTest 逐项比对，漂移会被测试抓住。
ALLOWED_FIELDS = {
    "handshake": frozenset({"type", "version", "token"}),
    "handshake_ack": frozenset({"type", "version"}),
    "mouse_move": frozenset({"type", "dx", "dy"}),
    "mouse_click": frozenset({"type", "button", "action"}),
    "scroll": frozenset({"type", "delta"}),
    "text_input": frozenset({"type", "text"}),
    "keyboard": frozenset({"type", "key", "action"}),
    "heartbeat": frozenset({"type"}),
    "heartbeat_ack": frozenset({"type"}),
    "error": frozenset({"type", "code", "message"}),
}


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
    # json.loads 对 [1,2] / 42 / "hi" 都返回合法结果，但它们不是对象。
    # 不挡住的话下面 msg.get 会抛 AttributeError —— 它不被 InvalidParams 捕获，
    # 会一路冒到 tcp_server 的兜底 except，表现为「客户端发个畸形包，连接直接断」，
    # 而不是回一个 INVALID_PARAMS。
    if not isinstance(msg, dict):
        send_error(
            conn, "INVALID_PARAMS",
            f"message must be a JSON object, got {type(msg).__name__}",
        )
        return True

    msg_type = msg.get("type")
    if msg_type not in HANDLER_REGISTRY:
        send_error(conn, "UNKNOWN_TYPE", f"unknown message type: {msg_type}")
        return True

    handler_fn = HANDLER_REGISTRY[msg_type]
    try:
        # additionalProperties: false。多出来的字段一律拒绝，否则字段名写错
        # （dx 写成 dX）会被静默忽略、退回默认值，客户端以为生效了却毫无反应。
        allowed = ALLOWED_FIELDS.get(msg_type)
        if allowed is not None:
            extra = set(msg) - allowed
            if extra:
                raise InvalidParams(
                    f"unexpected field(s) for {msg_type}: {', '.join(sorted(extra))}"
                )
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
