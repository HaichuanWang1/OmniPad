import json
import logging

logger = logging.getLogger("OmniPad")

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

class Connection:
    """一次客户端连接的上下文。

    处理器只把它当成「能 sendall 的东西」用（`send_json` / `send_error` 就是），
    所以 `test_handlers.py` 里的 FakeConn 一个字都不用改 —— 它们本来就只有
    `sendall`。

    多出来的 `emit` 把连接上发生的事（收到消息、握手成功/被拒）带回服务端，
    状态机与界面据此更新。改造前这些信息在 `tcp_server` 里被丢掉了，于是
    界面只能靠「连接建立了没有」来猜状态，猜出来的「在线」是假的。
    """

    __slots__ = ("sock", "addr", "peer", "port", "_on_event")

    def __init__(self, sock, addr, on_event=None):
        self.sock = sock
        self.peer = str(addr[0])
        self.port = int(addr[1])
        self.addr = f"{self.peer}:{self.port}"
        self._on_event = on_event

    def sendall(self, data):
        return self.sock.sendall(data)

    def close(self):
        return self.sock.close()

    def emit(self, kind, **fields):
        if self._on_event is None:
            return
        try:
            self._on_event(kind, addr=self.addr, **fields)
        except Exception:                      # 上报失败绝不能影响协议处理
            logger.exception(f"event sink failed for {kind}")


def emit(conn, kind, **fields):
    """把连接上的事件报给服务端。

    没有 emit 能力的 conn（测试里的假对象、以及 tcp_server 直接调 send_error
    时的原始 socket）会被安静跳过，因此这个钩子对既有调用方是零侵入的。
    """
    fn = getattr(conn, "emit", None)
    if fn is None:
        return
    try:
        fn(kind, **fields)
    except Exception:
        logger.exception(f"event sink failed for {kind}")


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
        # 字段校验通过即算「收到一条合法消息」，即使处理器随后拒绝它
        # （比如握手令牌错误）—— 用户看到的「最后活动时间」应该包含这一条。
        emit(conn, "message", type=msg_type)
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
    emit(conn, "error", code=code)
    return send_json(conn, {"type": "error", "code": code, "message": message})
