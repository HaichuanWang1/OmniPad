"""连接状态机与运行状态快照 —— 服务端「状态不再未知」的唯一数据源。

改造前的病根在这里：`ClientInfo.status` 只有 connected / disconnected 两个值，
而且是在**握手之前**就记成 connected 的。于是没通过令牌校验的连接在界面上照样
显示「在线」，窗口说的和实际能用的根本不是一回事。

现在状态、GUI 表格、`--status` 输出三处都从本模块取数，因此三者不可能各说各话。

本模块刻意保持纯逻辑：不碰 ctypes / Tkinter / socket，只读系统时钟，
所以它能被完整单测，也能在 Linux 上跑（CI 的客户端 job 与将来的跨平台测试）。
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime

# 状态文件的 schema 版本。字段增删时递增，读取方据此判断能否解析。
STATUS_SCHEMA = 1

# 客户端记录上限。键里含源端口，每次重连都是新键，不设上限会一直增长。
MAX_CLIENT_HISTORY = 200

# 一次连接的状态流转：
#
#     connecting ──握手成功──> online ──断开──> offline
#          └──────握手失败──> rejected
#
# 只有 online 才代表「这台手机现在真的能控制电脑」。
STATE_CONNECTING = "connecting"
STATE_ONLINE = "online"
STATE_REJECTED = "rejected"
STATE_OFFLINE = "offline"

# 断开原因（机器可读 → 用户可读）。tcp_server 只发机器可读的那个。
DISCONNECT_REASON_TEXT = {
    "client_closed": "客户端断开",
    "connection_reset": "对端异常断开",
    "idle_timeout": "空闲超时",
    "rejected": "未通过握手",
    "error": "连接出错",
    "server_stopped": "服务端停止",
}

# 握手拒绝原因，取值就是协议里的错误码（docs/protocol.md）。
REJECT_REASON_TEXT = {
    "AUTH_FAILED": "配对令牌错误",
    "VERSION_MISMATCH": "协议版本不匹配",
}

STATE_TEXT = {
    STATE_CONNECTING: "已连接·未配对",
    STATE_ONLINE: "在线",
    STATE_REJECTED: "已拒绝",
    STATE_OFFLINE: "已断开",
}


def iso(moment):
    """本地时间 → ISO 8601（秒精度）。None 透传为 None。"""
    if moment is None:
        return None
    return moment.replace(microsecond=0).isoformat()


def mask_token(token):
    """令牌打码，只留前 4 位。

    状态文件会被贴进 issue、日志、聊天窗口 —— 那是唯一能泄露配对令牌的地方。
    留前 4 位是为了让用户能确认「这是我这台机器」，而不是为了安全。
    """
    token = token or ""
    if len(token) <= 4:
        return "*" * len(token)
    return token[:4] + "*" * (len(token) - 4)


def describe(record) -> str:
    """把一条记录渲染成一句人话，GUI 表格与 --status 共用。"""
    base = STATE_TEXT.get(record.state, record.state)
    if record.state == STATE_REJECTED:
        reason = REJECT_REASON_TEXT.get(record.reason, record.reason or "")
        return f"{base}（{reason}）" if reason else base
    if record.state == STATE_OFFLINE and record.reason:
        return f"{base}（{DISCONNECT_REASON_TEXT.get(record.reason, record.reason)}）"
    return base


class ClientRecord:
    """一次客户端连接的完整生命史。

    键是 `addr`（含源端口），所以手机每次重连都是一条新记录 —— 这正是要保留
    历史的原因：用户问的往往是「刚才是不是连上过」。
    """

    __slots__ = (
        "peer", "port", "addr", "state", "connected_at", "authenticated_at",
        "last_message_at", "disconnected_at", "messages", "reason",
    )

    def __init__(self, peer, port, now=None):
        now = now if now is not None else datetime.now()
        self.peer = str(peer)
        self.port = int(port)
        self.addr = f"{self.peer}:{self.port}"
        self.state = STATE_CONNECTING
        self.connected_at = now
        self.authenticated_at = None
        self.last_message_at = None
        self.disconnected_at = None
        self.messages = 0
        self.reason = None

    # ---- 状态流转 ----

    def mark_online(self, now=None):
        self.state = STATE_ONLINE
        self.authenticated_at = now if now is not None else datetime.now()
        self.reason = None

    def mark_rejected(self, code, now=None):
        """握手被拒。连接随即被服务端关闭，所以同时记下断开时间。"""
        now = now if now is not None else datetime.now()
        self.state = STATE_REJECTED
        self.reason = code
        self.disconnected_at = now

    def mark_offline(self, reason, now=None):
        """连接关闭。

        被拒过的连接保持 rejected —— 用户要看到的是「为什么被拒」，而不是
        它后来被关掉了这个次要事实。
        """
        now = now if now is not None else datetime.now()
        if self.state == STATE_REJECTED:
            if self.disconnected_at is None:
                self.disconnected_at = now
            return
        self.state = STATE_OFFLINE
        self.reason = reason
        self.disconnected_at = now

    def note_message(self, now=None):
        self.messages += 1
        self.last_message_at = now if now is not None else datetime.now()

    # ---- 派生属性 ----

    @property
    def is_online(self) -> bool:
        return self.state == STATE_ONLINE

    def to_dict(self):
        return {
            "addr": self.addr,
            "peer": self.peer,
            "port": self.port,
            "state": self.state,
            "state_text": describe(self),
            "reason": self.reason,
            "connected_at": iso(self.connected_at),
            "authenticated_at": iso(self.authenticated_at),
            "last_message_at": iso(self.last_message_at),
            "disconnected_at": iso(self.disconnected_at),
            "messages": self.messages,
        }


class ServerState:
    """线程安全的状态快照。

    写入方是若干客户端线程（tcp_server）与控制通道线程，读取方是 Tk 主线程与
    `--status` 的调用方，所以所有访问都走同一把锁。

    它是「运行状态」的唯一真相：状态文件与控制通道都只是它的两种序列化形式。
    """

    def __init__(self, pid, mode, host, port, protocol_version,
                 data_dir=None, log_file=None, token=None, now=None):
        self._lock = threading.RLock()
        self.pid = int(pid)
        self.mode = mode                      # "gui" | "headless"
        self.host = host
        self.bound_port = int(port)           # 实际监听到的端口（--port 0 时是随机端口）
        self.protocol_version = protocol_version
        self.data_dir = data_dir
        self.log_file = log_file
        self.token = token
        self.running = False
        self.started_at = now if now is not None else datetime.now()
        self.control_host = None
        self.control_port = None
        self.max_history = MAX_CLIENT_HISTORY
        self._clients: "OrderedDict[str, ClientRecord]" = OrderedDict()

    # ---- 服务端自身状态 ----

    def set_running(self, running):
        with self._lock:
            self.running = bool(running)

    def set_bound_port(self, port):
        """--port 0 时真实端口由内核分配，必须回填，否则状态文件会写 0。"""
        with self._lock:
            self.bound_port = int(port)

    def set_control_endpoint(self, host, port):
        with self._lock:
            self.control_host = host
            self.control_port = int(port)

    def uptime_seconds(self, now=None):
        with self._lock:
            now = now if now is not None else datetime.now()
            return max(0.0, (now - self.started_at).total_seconds())

    # ---- 客户端记录 ----

    def client_connected(self, peer, port):
        record = ClientRecord(peer, port)
        with self._lock:
            self._remember(record)
        return record

    def _remember(self, record):
        """记住一条记录，超出上限时先淘汰已结束的，再淘汰最早的。"""
        self._clients[record.addr] = record
        while len(self._clients) > self.max_history:
            oldest = min(
                (k for k in self._clients if k != record.addr),
                key=lambda k: (
                    self._clients[k].is_online,
                    self._clients[k].connected_at,
                ),
                default=None,
            )
            if oldest is None:
                break
            del self._clients[oldest]

    def client_authenticated(self, addr):
        with self._lock:
            record = self._clients.get(addr)
            if record is not None:
                record.mark_online()
            return record

    def client_rejected(self, addr, code):
        with self._lock:
            record = self._clients.get(addr)
            if record is not None:
                record.mark_rejected(code)
            return record

    def client_disconnected(self, addr, reason):
        with self._lock:
            record = self._clients.get(addr)
            if record is not None:
                record.mark_offline(reason)
            return record

    def client_message(self, addr):
        with self._lock:
            record = self._clients.get(addr)
            if record is not None:
                record.note_message()
            return record

    def get_clients(self):
        """返回副本列表 —— 调用方一改就动到内部状态是这类代码的经典坑。"""
        with self._lock:
            return list(self._clients.values())

    def online_count(self):
        with self._lock:
            return sum(1 for c in self._clients.values() if c.is_online)

    def clear_clients(self):
        with self._lock:
            self._clients.clear()

    # ---- 序列化 ----

    def snapshot(self, now=None):
        """当前状态的完整快照，写进状态文件、或经控制通道返回。"""
        with self._lock:
            return {
                "schema": STATUS_SCHEMA,
                "pid": self.pid,
                "mode": self.mode,
                "running": self.running,
                "started_at": iso(self.started_at),
                "updated_at": iso(now if now is not None else datetime.now()),
                "host": self.host,
                "port": self.bound_port,
                "protocol_version": self.protocol_version,
                "token_masked": mask_token(self.token),
                "data_dir": self.data_dir,
                "log_file": self.log_file,
                "online_count": sum(
                    1 for c in self._clients.values() if c.is_online
                ),
                "control": (
                    {"host": self.control_host, "port": self.control_port}
                    if self.control_port else None
                ),
                "clients": [c.to_dict() for c in self._clients.values()],
            }
