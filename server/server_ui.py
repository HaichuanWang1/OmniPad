"""Tkinter 控制面板。

改造前这里同时干了三件事：管 TCP 服务器、管客户端记录、画界面。而且客户端记录
是在**握手之前**建的，所以界面上那个「在线」是假的 —— 没通过令牌校验的连接
看起来和正常连接一模一样。

现在它只做一件事：把 `state.ServerState` 里的状态画出来。状态本身由
`server.ServerSession` 维护，与无头模式共用同一份实现。

线程模型：TCP 与托盘都在各自的线程里，它们**只能往队列里塞东西**，
绝不碰 Tk 的任何对象（Tkinter 不是线程安全的）。所有界面更新都发生在
`_poll()` 里，也就是 Tk 主线程。
"""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

import runtime
import state as state_module
import tray

BG = "#0F1117"
SURFACE = "#1A1C23"
SURFACE_VARIANT = "#2A2D36"
PRIMARY = "#4A9EFF"
TEXT = "#E2E2E6"
TEXT_DIM = "#8E9099"
GREEN = "#4ADE80"
RED = "#FF6B6B"
YELLOW = "#FFD93D"

# 状态 → 表格里的颜色。只有 online 是绿的：它代表「这台手机现在真的能控制电脑」。
STATE_COLORS = {
    state_module.STATE_ONLINE: GREEN,
    state_module.STATE_CONNECTING: YELLOW,
    state_module.STATE_REJECTED: RED,
    state_module.STATE_OFFLINE: TEXT_DIM,
}

LOG_COLORS = {
    "INFO": PRIMARY,
    "WARNING": YELLOW,
    "ERROR": RED,
    "DEBUG": TEXT_DIM,
}

# Tk 的 Text 控件不会自己丢旧行，跑一整天就是几十兆内存。
MAX_LOG_LINES = 2000

POLL_INTERVAL_MS = 100
TICK_INTERVAL_MS = 1000

TRAY_SHOW = 1
TRAY_COPY_TOKEN = 2
TRAY_OPEN_DATA = 3
TRAY_STOP = 4

GITHUB_URL = "https://github.com/HaichuanWang1/OmniPad"


# --------------------------------------------------------------------------
# 纯逻辑（可以在没有显示器的情况下单测）
# --------------------------------------------------------------------------

def format_time(moment) -> str:
    return moment.strftime("%H:%M:%S") if isinstance(moment, datetime) else "-"


def client_row_tag(record) -> str:
    return record.state if record.state in STATE_COLORS else state_module.STATE_OFFLINE


def client_rows(records):
    """连接记录 → 表格行。

    在线的排在最前面：这张表的用途是「谁现在能控制我的电脑」，不是流水账。
    """
    ordered = sorted(
        records,
        key=lambda r: (not r.is_online, r.disconnected_at or r.connected_at),
        reverse=False,
    )
    rows = []
    for record in ordered:
        rows.append((
            record.addr,
            state_module.describe(record),
            format_time(record.connected_at),
            format_time(record.last_message_at),
            str(record.messages),
            client_row_tag(record),
        ))
    return rows


def session_summary(payload) -> str:
    """顶栏那一行状态文字。"""
    if not payload:
        return "未运行"
    online = payload.get("online_count", 0)
    total = len(payload.get("clients") or [])
    if online:
        return f"运行中 · {online} 台在线（共 {total} 条记录）"
    if total:
        return f"运行中 · 无设备在线（共 {total} 条记录）"
    return "运行中 · 等待手机连接"


def tray_tooltip(payload) -> str:
    if not payload:
        return "OmniPad 服务端"
    online = payload.get("online_count", 0)
    port = payload.get("port")
    if online:
        return f"OmniPad 服务端 · {online} 台在线 · 端口 {port}"
    return f"OmniPad 服务端 · 等待连接 · 端口 {port}"


class LogBuffer:
    """日志行环形缓冲。

    返回「该从顶部删掉几行」而不是自己保存全部文本：Tk 的 Text 控件才是真正的
    显示载体，再存一份完整副本纯属浪费。
    """

    def __init__(self, max_lines=MAX_LOG_LINES):
        self.max_lines = max_lines
        self._lines = 0

    @property
    def line_count(self) -> int:
        return self._lines

    def add(self) -> int:
        """记一行，返回需要从顶部删掉的行数（0 或 1）。"""
        self._lines += 1
        if self._lines > self.max_lines:
            self._lines -= 1
            return 1
        return 0

    def clear(self):
        self._lines = 0


class QueueHandler(logging.Handler):
    """把日志记录塞进队列，由 Tk 主线程取走显示。"""

    def __init__(self, target: queue.Queue):
        super().__init__()
        self.target = target

    def emit(self, record):
        self.target.put(record)


def enable_dpi_awareness():
    """高分屏下让窗口清晰。

    Tkinter 默认是 DPI 无感知的，Windows 会把整个窗口位图拉伸 —— 字全是糊的。
    """
    if os.name != "nt":
        return False
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # PROCESS_SYSTEM_DPI_AWARE
        return True
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except (AttributeError, OSError):
        return False


def open_path(path):
    """用系统默认程序打开文件或目录。"""
    try:
        if os.name == "nt":
            os.startfile(path)                              # noqa: S606
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        return False


def port_owner_text(port) -> str:
    owner = runtime.port_owner(port) if port else None
    if owner is None:
        return f"{port}（空闲）"
    if owner == os.getpid():
        return f"{port}（本进程 PID {owner}）"
    return f"{port}（被 PID {owner} 占用）"


# --------------------------------------------------------------------------
# 界面
# --------------------------------------------------------------------------

class ServerApp:
    def __init__(self, session):
        self.session = session
        self.events = queue.Queue()
        self.log_queue = queue.Queue()
        self.log_buffer = LogBuffer()
        self.tray = None
        self.tray_available = False
        self._closing = False

        self.root = tk.Tk()
        self.root.title("OmniPad 服务端")
        self.root.geometry("780x680")
        self.root.minsize(560, 460)
        self.root.configure(bg=BG)

        self._build_ui()
        self._apply_styles()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- 构建 ----

    def _build_ui(self):
        self._build_header()
        self._build_clients()
        # 状态栏必须在日志区**之前**打包。Tk 的 packer 按打包顺序分配空间，
        # 排在最后又被 expand=True 的日志区抢光，结果就是状态栏被裁成一条缝。
        self._build_status_bar()
        self._build_log()

    def _build_header(self):
        header = tk.Frame(self.root, bg=BG, padx=18, pady=14)
        header.pack(fill=tk.X)

        left = tk.Frame(header, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(left, text="OmniPad", font=("Segoe UI", 20),
                 fg=PRIMARY, bg=BG, anchor="w").pack(anchor="w")

        status_row = tk.Frame(left, bg=BG)
        status_row.pack(anchor="w", pady=(4, 0))

        self.status_dot = tk.Canvas(status_row, width=10, height=10, bg=BG,
                                    highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self.status_indicator = self.status_dot.create_oval(0, 0, 10, 10,
                                                            fill=RED, outline="")

        self.status_label = tk.Label(status_row, text="未运行", font=("Segoe UI", 10),
                                     fg=TEXT, bg=BG, anchor="w")
        self.status_label.pack(side=tk.LEFT)

        self.address_label = tk.Label(left, font=("Consolas", 10, "bold"),
                                      fg=PRIMARY, bg=BG, anchor="w")
        self.address_label.pack(anchor="w", pady=(6, 0))

        self.token_label = tk.Label(left, font=("Consolas", 10, "bold"),
                                    fg=YELLOW, bg=BG, anchor="w")
        self.token_label.pack(anchor="w", pady=(2, 0))

        self.hint_label = tk.Label(
            left, text="手机端填入上面的地址与令牌", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=BG, anchor="w",
        )
        self.hint_label.pack(anchor="w", pady=(4, 0))

        right = tk.Frame(header, bg=BG)
        right.pack(side=tk.RIGHT)

        for text, command in (
            ("复制地址", self._copy_address),
            ("复制令牌", self._copy_token),
            ("重新生成令牌", self._reset_token),
            ("自检", self._show_self_check),
        ):
            tk.Button(right, text=text, font=("Segoe UI", 9), bg=SURFACE_VARIANT,
                      fg=TEXT, relief=tk.FLAT, padx=10, pady=3, cursor="hand2",
                      activebackground=SURFACE, activeforeground=TEXT,
                      command=command).pack(side=tk.TOP, fill=tk.X, pady=2)

        tk.Frame(self.root, height=1, bg=SURFACE_VARIANT).pack(fill=tk.X)

    def _build_clients(self):
        container = tk.Frame(self.root, bg=BG)
        container.pack(fill=tk.X, padx=16, pady=(10, 0))

        title_row = tk.Frame(container, bg=BG)
        title_row.pack(fill=tk.X)
        tk.Label(title_row, text="客户端连接", font=("Segoe UI", 9, "bold"),
                 fg=TEXT, bg=BG, anchor="w").pack(side=tk.LEFT)
        tk.Button(title_row, text="清空记录", font=("Segoe UI", 8), bg=BG, fg=TEXT_DIM,
                  relief=tk.FLAT, cursor="hand2", activebackground=BG,
                  activeforeground=TEXT, command=self._clear_clients).pack(side=tk.RIGHT)

        columns = ("addr", "status", "connected", "last", "messages")
        self.client_tree = ttk.Treeview(container, columns=columns, show="headings",
                                        height=5, style="Client.Treeview")
        for key, text, width, anchor in (
            ("addr", "地址", 180, "w"),
            ("status", "状态", 190, "w"),
            ("connected", "连接时间", 90, "center"),
            ("last", "最后活动", 90, "center"),
            ("messages", "消息", 70, "center"),
        ):
            self.client_tree.heading(key, text=text)
            self.client_tree.column(key, width=width, anchor=anchor)
        self.client_tree.pack(fill=tk.X, pady=(4, 0))

        for tag, color in STATE_COLORS.items():
            self.client_tree.tag_configure(tag, foreground=color)

        tk.Frame(self.root, height=1, bg=SURFACE_VARIANT).pack(fill=tk.X, pady=(10, 0))

    def _build_log(self):
        container = tk.Frame(self.root, bg=BG)
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(10, 4))

        title_row = tk.Frame(container, bg=BG)
        title_row.pack(fill=tk.X)
        tk.Label(title_row, text="运行日志", font=("Segoe UI", 9, "bold"),
                 fg=TEXT, bg=BG, anchor="w").pack(side=tk.LEFT)
        self.log_hint = tk.Label(title_row, text=f"（只保留最近 {MAX_LOG_LINES} 行）",
                                 font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG)
        self.log_hint.pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(title_row, text="打开日志文件", font=("Segoe UI", 8), bg=BG,
                  fg=TEXT_DIM, relief=tk.FLAT, cursor="hand2", activebackground=BG,
                  activeforeground=TEXT, command=self._open_log).pack(side=tk.RIGHT)

        body = tk.Frame(container, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.log_text = tk.Text(body, font=("Consolas", 9), bg=SURFACE, fg=TEXT,
                                relief=tk.FLAT, padx=10, pady=6, state=tk.DISABLED,
                                wrap=tk.WORD, highlightthickness=0, borderwidth=0,
                                insertbackground=TEXT, height=8)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(body, bg=SURFACE_VARIANT, troughcolor=BG)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_text.yview)

        for level, color in LOG_COLORS.items():
            self.log_text.tag_config(level.lower(), foreground=color)
        self.log_text.tag_config("dim", foreground=TEXT_DIM)

    def _build_status_bar(self):
        bar = tk.Frame(self.root, bg=SURFACE, padx=16, pady=6)
        bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.uptime_label = tk.Label(bar, text="运行时间 --", font=("Segoe UI", 9),
                                     fg=TEXT_DIM, bg=SURFACE, anchor="w")
        self.uptime_label.pack(side=tk.LEFT)

        self.version_label = tk.Label(bar, text="", font=("Segoe UI", 9),
                                      fg=TEXT_DIM, bg=SURFACE)
        self.version_label.pack(side=tk.LEFT, padx=(12, 0))

        tk.Label(bar, text="超重氢", font=("Segoe UI", 9), fg=TEXT_DIM,
                 bg=SURFACE).pack(side=tk.RIGHT, padx=(0, 4))
        github = tk.Label(bar, text="GitHub", font=("Segoe UI", 9, "underline"),
                          fg=PRIMARY, bg=SURFACE, cursor="hand2")
        github.pack(side=tk.RIGHT)
        github.bind("<Button-1>", lambda _e: webbrowser.open(GITHUB_URL))

        tk.Button(bar, text="数据目录", font=("Segoe UI", 9), bg=SURFACE, fg=TEXT_DIM,
                  relief=tk.FLAT, cursor="hand2", activebackground=SURFACE,
                  activeforeground=TEXT,
                  command=self._open_data_dir).pack(side=tk.RIGHT, padx=(0, 8))

    def _apply_styles(self):
        style = ttk.Style()
        # Windows 原生的 vista 主题会**无视** Treeview 的背景色配置，表格永远是
        # 一块白板 —— 在深色界面里非常刺眼。clam 尊重这些设置。
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Client.Treeview", background=BG, foreground=TEXT,
                        fieldbackground=BG, borderwidth=0, rowheight=22)
        style.configure("Client.Treeview.Heading", background=SURFACE, foreground=TEXT,
                        relief=tk.FLAT, font=("Segoe UI", 9, "bold"))
        style.map("Client.Treeview", background=[("selected", SURFACE_VARIANT)],
                  foreground=[("selected", TEXT)])
        style.map("Client.Treeview.Heading",
                  background=[("active", SURFACE_VARIANT)])

    # ---- 生命周期 ----

    def run(self) -> int:
        import server

        self.session.on_event = self._on_worker_event
        self.session.add_stop_hook(lambda: self.events.put(("stop", {})))
        logging.getLogger("OmniPad").addHandler(QueueHandler(self.log_queue))

        try:
            self.session.start()
        except RuntimeError as error:
            messagebox.showerror("OmniPad 服务端", str(error))
            return server.EXIT_FAILED

        self._refresh_header()
        self.version_label.config(text=f"协议 v{self.session.state.protocol_version}")
        self._start_tray()
        self._poll()
        self._tick()
        self.root.mainloop()
        return server.EXIT_OK

    def _start_tray(self):
        self.tray = tray.TrayIcon(
            icon_path=tray.default_icon_path(),
            tooltip=tray_tooltip(self.session.state.snapshot()),
            menu_items=[
                (TRAY_SHOW, "显示窗口"),
                (TRAY_COPY_TOKEN, "复制配对令牌"),
                (0, None),
                (TRAY_OPEN_DATA, "打开数据目录"),
                (0, None),
                (TRAY_STOP, "停止并退出"),
            ],
            on_command=self.events.put,      # 只入队，绝不碰 Tk
        )
        self.tray_available = self.tray.start()

    # ---- 事件（工作线程 → 队列）----

    def _on_worker_event(self, kind, **fields):
        """从 TCP / 控制通道线程被调用。除了入队什么都不做。"""
        self.events.put((kind, fields))

    def _poll(self):
        self._drain_events()
        self._drain_logs()
        if not self._closing:
            self.root.after(POLL_INTERVAL_MS, self._poll)

    def _drain_events(self):
        dirty = False
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                break

            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], dict):
                kind = item[0]
                if kind == "stop":
                    self._shutdown()
                    return
                dirty = True
            else:
                # 托盘菜单命令（就是一个整数 id）
                self._handle_tray_command(item)

        if dirty:
            self._refresh_header()
            self._refresh_clients()

    def _drain_logs(self):
        appended = False
        while True:
            try:
                record = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(record)
            appended = True

        if appended:
            self.log_text.see(tk.END)

    def _append_log(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = record.levelname
        try:
            message = record.getMessage()
        except Exception:
            message = "<格式化失败>"

        self.log_text.config(state=tk.NORMAL)
        drop = self.log_buffer.add()
        if drop:
            self.log_text.delete("1.0", f"{drop + 1}.0")
        self.log_text.insert(tk.END, f" {timestamp} ", "dim")
        self.log_text.insert(tk.END, f"{level:7}", level.lower())
        self.log_text.insert(tk.END, f"  {message}\n", level.lower())
        self.log_text.config(state=tk.DISABLED)

    def _handle_tray_command(self, command):
        if command == TRAY_SHOW:
            self._show_window()
        elif command == TRAY_COPY_TOKEN:
            self._copy_token(silent=True)
        elif command == TRAY_OPEN_DATA:
            self._open_data_dir()
        elif command == TRAY_STOP:
            self._shutdown()

    # ---- 定时刷新 ----

    def _tick(self):
        if self._closing:
            return
        payload = self.session.state.snapshot()
        self.uptime_label.config(
            text=f"运行时间 {runtime.format_uptime(self.session.state.uptime_seconds())}"
        )
        self.status_label.config(text=session_summary(payload))
        if self.tray is not None and self.tray_available:
            self.tray.update_tooltip(tray_tooltip(payload))
        self._refresh_clients()
        self.root.after(TICK_INTERVAL_MS, self._tick)

    def _refresh_header(self):
        addresses = self.session.connect_addresses() or ["127.0.0.1"]
        port = self.session.state.bound_port
        primary = f"{addresses[0]}:{port}"
        extra = "，".join(f"{ip}:{port}" for ip in addresses[1:])
        self.address_label.config(
            text=f"手机连接：{primary}" + (f"（其他网卡：{extra}）" if extra else "")
        )
        self.token_label.config(text=f"配对令牌：{self.session.token}")

    def _refresh_clients(self):
        records = self.session.state.get_clients()
        self.client_tree.delete(*self.client_tree.get_children())
        for row in client_rows(records):
            self.client_tree.insert("", tk.END, values=row[:5], tags=(row[5],))

    # ---- 动作 ----

    def _copy_address(self):
        addresses = self.session.connect_addresses() or ["127.0.0.1"]
        self._to_clipboard(f"{addresses[0]}:{self.session.state.bound_port}")

    def _copy_token(self, silent=False):
        self._to_clipboard(self.session.token)
        if not silent:
            self.hint_label.config(text="配对令牌已复制到剪贴板")

    def _to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def _reset_token(self):
        if not messagebox.askyesno(
            "重新生成配对令牌",
            "生成新令牌后，所有手机都需要重新配对。\n\n确定继续吗？",
        ):
            return

        import pairing
        new_token = pairing.generate_token()
        path = runtime.token_file_path(self.session.data_dir)
        try:
            runtime.ensure_dir(self.session.data_dir)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_token + "\n")
        except OSError as error:
            messagebox.showerror("OmniPad 服务端", f"写入令牌文件失败：{error}")
            return

        import handlers
        handlers.set_pairing_token(new_token)
        self.session.token = new_token
        self.session.state.token = new_token
        self._refresh_header()
        self.hint_label.config(text="已生成新令牌，请在手机上重新配对")
        logging.getLogger("OmniPad").warning("配对令牌已重新生成，手机端需要重新配对")

    def _clear_clients(self):
        self.session.state.clear_clients()
        self.session.persist()
        self._refresh_clients()

    def _open_data_dir(self):
        if not open_path(self.session.data_dir):
            messagebox.showwarning("OmniPad 服务端", "无法打开数据目录")

    def _open_log(self):
        if not open_path(runtime.log_file_path(self.session.data_dir)):
            messagebox.showwarning("OmniPad 服务端", "无法打开日志文件")

    def _show_self_check(self):
        payload = self.session.state.snapshot()
        port = payload.get("port")
        control_info = payload.get("control") or {}
        data_dir = self.session.data_dir
        token_path = runtime.token_file_path(data_dir)
        log_path = runtime.log_file_path(data_dir)

        lines = [
            f"版本：{_app_version()}",
            f"模式：图形界面",
            f"协议版本：{payload.get('protocol_version')}",
            "",
            f"监听地址：{payload.get('host')}:{port}",
            f"端口占用：{port_owner_text(port)}",
            f"控制通道：{control_info.get('host')}:{control_info.get('port')}"
            "（仅本机可访问）",
            "",
            "本机地址（手机填其中一个）：",
        ]
        addresses = self.session.connect_addresses()
        lines += [f"  {ip}:{port}" for ip in addresses] or ["  没有检测到可用的 IPv4 地址"]
        lines += [
            "",
            f"数据目录：{data_dir}",
            f"  可写：{'是' if os.access(data_dir, os.W_OK) else '否'}",
            f"令牌文件：{token_path}",
            f"  存在：{'是' if os.path.exists(token_path) else '否'}"
            f"　可写：{'是' if os.access(token_path, os.W_OK) else '否'}",
            f"日志文件：{log_path}",
            f"  存在：{'是' if os.path.exists(log_path) else '否'}"
            f"　可写：{'是' if os.access(log_path, os.W_OK) else '否'}",
            "",
            f"托盘图标：{'可用' if self.tray_available else '不可用'}"
            "（Windows 11 默认收进「隐藏的图标」里，可以拖出来固定）",
        ]

        _show_text_dialog(self.root, "自检", "\n".join(lines))

    # ---- 关闭 ----

    def _on_close(self):
        if self._closing:
            return

        if not self.tray_available:
            if messagebox.askyesno("OmniPad 服务端", "停止服务端并退出吗？"):
                self._shutdown()
            return

        answer = messagebox.askyesnocancel(
            "OmniPad 服务端",
            "要把服务端留在后台继续运行吗？\n\n"
            "是 —— 最小化到托盘，手机仍可连接\n"
            "否 —— 停止服务端并退出",
        )
        if answer is None:
            return
        if answer:
            self._hide_to_tray()
        else:
            self._shutdown()

    def _hide_to_tray(self):
        self.root.withdraw()
        if self.tray is not None:
            self.tray.update_tooltip(
                tray_tooltip(self.session.state.snapshot()) + "（双击图标显示窗口）"
            )

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _shutdown(self):
        if self._closing:
            return
        self._closing = True
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            logging.getLogger("OmniPad").exception("关闭托盘失败")
        self.session.stop()
        self.root.destroy()


def _app_version():
    import server
    return server.app_version()


def _show_text_dialog(parent, title, text):
    window = tk.Toplevel(parent)
    window.title(title)
    window.configure(bg=BG)
    window.geometry("640x420")
    window.transient(parent)

    body = tk.Frame(window, bg=BG, padx=14, pady=12)
    body.pack(fill=tk.BOTH, expand=True)

    widget = tk.Text(body, font=("Consolas", 9), bg=SURFACE, fg=TEXT, relief=tk.FLAT,
                     padx=10, pady=8, wrap=tk.NONE, highlightthickness=0, borderwidth=0)
    widget.pack(fill=tk.BOTH, expand=True)
    widget.insert("1.0", text)
    widget.config(state=tk.DISABLED)

    tk.Button(window, text="关闭", font=("Segoe UI", 9), bg=SURFACE_VARIANT, fg=TEXT,
              relief=tk.FLAT, padx=18, pady=4, cursor="hand2",
              command=window.destroy).pack(pady=(0, 12))
    window.bind("<Escape>", lambda _e: window.destroy())


def run_app(session) -> int:
    enable_dpi_awareness()
    return ServerApp(session).run()


def main(argv=None):
    """直接 `python server_ui.py` 也能跑起来（等价于 `python server.py`）。"""
    import server
    args = list(argv) if argv is not None else sys.argv[1:]
    return server.main(args)


if __name__ == "__main__":
    import sys
    sys.exit(main())
