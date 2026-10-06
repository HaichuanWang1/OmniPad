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

import base64
import ctypes
import logging
import os
import queue
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import qr
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

# 二维码这一块**刻意不跟随深色主题**：扫描器要的是深色码点 + 浅色底，
# 深底浅码的码很多摄像头认不出来。所以它是深色界面里唯一一块白底。
QR_LIGHT = "#FFFFFF"
QR_DARK = "#000000"
QR_BORDER = 4

# 卡片里的二维码目标边长，以及「放大」窗口里的边长（像素）
QR_CARD_PX = 156
QR_ZOOM_PX = 480

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
TRAY_COPY_QR = 5

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


def qr_scale(module_count, target_px, border=QR_BORDER) -> int:
    """模块数 → 放大倍数，让成品尽量接近 target_px，且至少 1 倍。

    放大倍数必须是整数：非整数倍会让模块宽度不一致，二维码看起来像糊了 ——
    有些扫描器对那种格子很敏感。
    """
    total = module_count + border * 2
    if total <= 0:
        return 1
    return max(1, round(target_px / total))


def qr_image_png(code, target_px) -> bytes:
    return qr.png_bytes(
        code.modules,
        scale=qr_scale(code.size, target_px),
        border=QR_BORDER,
        dark=qr_hex(QR_DARK),
        light=qr_hex(QR_LIGHT),
    )


def qr_hex(color) -> tuple:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


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
        self._poll_id = None
        self._tick_id = None
        # 已经画出来的那份载荷。二维码重画要重新生成 PNG，没必要每秒来一次。
        self._qr_rendered = None
        self._qr_photo = None
        self._qr_ticks = 0

        self.root = tk.Tk()
        self.root.title("OmniPad 服务端")
        self.root.geometry("820x780")
        self.root.minsize(600, 560)
        self.root.configure(bg=BG)

        self._build_ui()
        self._apply_styles()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- 构建 ----

    def _build_ui(self):
        self._build_header()
        self._build_qr()
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

    def _build_qr(self):
        """二维码区块。

        为什么值得占掉一块地方：手动配对要在手机上敲地址、端口、8 位令牌，令牌
        刻意剔除了易混淆字符，看起来就像乱码，错一位得到的还是 `AUTH_FAILED` ——
        用户以为自己填对了。二维码把这三样一次带过去，顺带带上协议版本，旧版
        App 在扫码那一刻就能说「请更新」，而不是倒在令牌校验上。
        """
        container = tk.Frame(self.root, bg=BG)
        container.pack(fill=tk.X, padx=18, pady=(12, 0))

        # 白底卡片：扫描器要的是深色码点 + 浅色底
        card = tk.Frame(container, bg=QR_LIGHT, padx=8, pady=8)
        card.pack(side=tk.LEFT)
        self.qr_label = tk.Label(card, bg=QR_LIGHT, bd=0, highlightthickness=0)
        self.qr_label.pack()

        right = tk.Frame(container, bg=BG, padx=14)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(right, text="手机扫码连接", font=("Segoe UI", 11, "bold"),
                 fg=TEXT, bg=BG, anchor="w").pack(anchor="w")

        self.qr_target_label = tk.Label(right, font=("Consolas", 11, "bold"),
                                        fg=PRIMARY, bg=BG, anchor="w")
        self.qr_target_label.pack(anchor="w", pady=(4, 0))

        self.qr_token_label = tk.Label(right, font=("Consolas", 10),
                                       fg=YELLOW, bg=BG, anchor="w")
        self.qr_token_label.pack(anchor="w", pady=(2, 0))

        self.qr_hint_label = tk.Label(
            right,
            text="二维码里带着地址与令牌，等同于手动输入。它是一份秘密，请不要截图外发。",
            font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG, anchor="w",
            wraplength=400, justify="left",
        )
        self.qr_hint_label.pack(anchor="w", pady=(6, 0))

        row = tk.Frame(right, bg=BG)
        row.pack(anchor="w", pady=(8, 0))

        tk.Label(row, text="地址", font=("Segoe UI", 9), fg=TEXT_DIM,
                 bg=BG).pack(side=tk.LEFT, padx=(0, 6))
        self.qr_host_box = ttk.Combobox(row, state="readonly", width=20,
                                        font=("Consolas", 9), style="Dark.TCombobox")
        self.qr_host_box.pack(side=tk.LEFT)
        self.qr_host_box.bind("<<ComboboxSelected>>", self._on_qr_host_selected)

        for text, command in (
            ("复制链接", self._copy_qr),
            ("保存图片", self._save_qr),
            ("放大", self._zoom_qr),
        ):
            tk.Button(row, text=text, font=("Segoe UI", 9), bg=SURFACE_VARIANT,
                      fg=TEXT, relief=tk.FLAT, padx=10, pady=2, cursor="hand2",
                      activebackground=SURFACE, activeforeground=TEXT,
                      command=command).pack(side=tk.LEFT, padx=(6, 0))

        tk.Frame(self.root, height=1, bg=SURFACE_VARIANT).pack(fill=tk.X, pady=(12, 0))

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
        tree_body = tk.Frame(container, bg=BG)
        tree_body.pack(fill=tk.X, pady=(4, 0))

        self.client_tree = ttk.Treeview(tree_body, columns=columns, show="headings",
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
        self.client_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 记录上限是 200 条，表格只显示 5 行 —— 没有滚动条的话用户根本不知道
        # 下面还有东西（鼠标滚轮能滚，但没有任何提示）。
        tree_scroll = ttk.Scrollbar(tree_body, orient=tk.VERTICAL,
                                    command=self.client_tree.yview,
                                    style="Dark.Vertical.TScrollbar")
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.client_tree.config(yscrollcommand=tree_scroll.set)

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

        # 经典 tk.Scrollbar 在 Windows 上会无视配色，深色界面里是一条刺眼的浅灰。
        # ttk + clam 主题才认这些设置。
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL,
                                  command=self.log_text.yview,
                                  style="Dark.Vertical.TScrollbar")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

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

        style.configure("Dark.Vertical.TScrollbar", background=SURFACE_VARIANT,
                        troughcolor=BG, bordercolor=BG, arrowcolor=TEXT_DIM,
                        darkcolor=SURFACE_VARIANT, lightcolor=SURFACE_VARIANT,
                        relief=tk.FLAT)
        style.map("Dark.Vertical.TScrollbar",
                  background=[("active", PRIMARY), ("pressed", PRIMARY)])

        style.configure("Dark.TCombobox", fieldbackground=SURFACE,
                        background=SURFACE_VARIANT, foreground=TEXT,
                        arrowcolor=TEXT_DIM, bordercolor=BG,
                        lightcolor=SURFACE_VARIANT, darkcolor=SURFACE_VARIANT,
                        selectbackground=SURFACE_VARIANT, selectforeground=TEXT)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", SURFACE)],
                  foreground=[("readonly", TEXT)],
                  background=[("active", SURFACE_VARIANT)])
        # 下拉列表其实是个经典 tk.Listbox，不受 ttk 样式管，只能走 option 数据库
        self.root.option_add("*TCombobox*Listbox.background", SURFACE)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", PRIMARY)
        self.root.option_add("*TCombobox*Listbox.selectForeground", BG)

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
        self._refresh_qr()
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
                (TRAY_COPY_QR, "复制扫码链接"),
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
        if self._closing:
            return
        self._poll_id = None
        self._drain_events()
        self._drain_logs()
        if not self._closing:
            self._poll_id = self.root.after(POLL_INTERVAL_MS, self._poll)

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
            self._refresh_qr()

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
        elif command == TRAY_COPY_QR:
            self._copy_qr()
        elif command == TRAY_OPEN_DATA:
            self._open_data_dir()
        elif command == TRAY_STOP:
            self._shutdown()

    # ---- 定时刷新 ----

    def _tick(self):
        if self._closing:
            return
        self._tick_id = None
        payload = self.session.state.snapshot()
        self.uptime_label.config(
            text=f"运行时间 {runtime.format_uptime(self.session.state.uptime_seconds())}"
        )
        self.status_label.config(text=session_summary(payload))
        if self.tray is not None and self.tray_available:
            self.tray.update_tooltip(tray_tooltip(payload))
        self._refresh_clients()
        # 地址列表要跟着网卡变化走（插拔网线、Tailscale 上下线），但枚举本机地址
        # 要碰 socket，没必要每秒来一次 —— 5 秒一次足够跟上。
        self._qr_ticks += 1
        if self._qr_ticks >= 5:
            self._qr_ticks = 0
            self._refresh_qr()
        self._tick_id = self.root.after(TICK_INTERVAL_MS, self._tick)

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

    # ---- 二维码 ----

    def _refresh_qr(self):
        """把当前载荷画出来。载荷没变就什么都不做 —— 重画一次要重新生成 PNG。"""
        hosts = self.session.qr_hosts()
        if list(self.qr_host_box["values"]) != hosts:
            self.qr_host_box["values"] = hosts
        selected = self.session.selected_qr_host()
        if self.qr_host_box.get() != selected:
            self.qr_host_box.set(selected)

        payload = self.session.state.qr_payload
        if not payload:
            self.qr_target_label.config(
                text="等待服务启动" if not self.session.state.running else "没有可用的本机地址"
            )
            self.qr_token_label.config(text="")
            self._set_qr_image(None)
            self._qr_rendered = None
            return
        if payload == self._qr_rendered:
            return

        code = self.session.qr_code()
        self._set_qr_image(qr_image_png(code, QR_CARD_PX) if code else None)
        self._qr_rendered = payload
        self.qr_target_label.config(text=f"{selected}:{self.session.state.bound_port}")
        self.qr_token_label.config(text=f"配对令牌 {self.session.token}")

    def _set_qr_image(self, png):
        if not png:
            self.qr_label.config(image="")
            self._qr_photo = None
            return
        # PhotoImage 必须留一个强引用：Tk 那边只持弱引用，被回收掉就是一块空白
        self._qr_photo = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
        self.qr_label.config(image=self._qr_photo)

    def _on_qr_host_selected(self, _event=None):
        self.session.set_qr_host(self.qr_host_box.get())
        self._qr_rendered = None
        self._refresh_qr()

    def _copy_qr(self):
        payload = self.session.state.qr_payload
        if not payload:
            self.qr_hint_label.config(text="现在没有可用的本机地址，无法生成二维码")
            return
        self._to_clipboard(payload)
        self.qr_hint_label.config(text="扫码链接已复制（含配对令牌，请勿外发）")

    def _save_qr(self):
        code = self.session.qr_code()
        if code is None:
            messagebox.showwarning("OmniPad 服务端", "现在没有可用的本机地址，无法生成二维码")
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="保存二维码", defaultextension=".png",
            initialfile="omnipad-qr.png", filetypes=[("PNG 图片", "*.png")],
        )
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(qr_image_png(code, QR_ZOOM_PX))
        except OSError as error:
            messagebox.showerror("OmniPad 服务端", f"保存失败：{error}")
            return
        self.qr_hint_label.config(text=f"已保存到 {path}")

    def _zoom_qr(self):
        """放大到另一个窗口 —— 手机离得远时扫卡片里那张小图很吃力。"""
        code = self.session.qr_code()
        if code is None:
            messagebox.showwarning("OmniPad 服务端", "现在没有可用的本机地址，无法生成二维码")
            return

        window = tk.Toplevel(self.root)
        window.title("扫码连接")
        window.configure(bg=BG)
        window.transient(self.root)

        card = tk.Frame(window, bg=QR_LIGHT, padx=10, pady=10)
        card.pack(padx=18, pady=(18, 8))
        photo = tk.PhotoImage(
            data=base64.b64encode(qr_image_png(code, QR_ZOOM_PX)).decode("ascii")
        )
        tk.Label(card, image=photo, bg=QR_LIGHT, bd=0,
                 highlightthickness=0).pack()
        # 挂在窗口上而不是局部变量：函数一返回局部引用就没了，Tk 那边只剩空白
        window.qr_photo = photo

        tk.Label(window, text=self.session.state.qr_payload, font=("Consolas", 8),
                 fg=TEXT_DIM, bg=BG, wraplength=QR_ZOOM_PX + 40,
                 justify="left").pack(padx=18)
        tk.Button(window, text="关闭", font=("Segoe UI", 9), bg=SURFACE_VARIANT, fg=TEXT,
                  relief=tk.FLAT, padx=18, pady=4, cursor="hand2",
                  command=window.destroy).pack(pady=14)
        window.bind("<Escape>", lambda _e: window.destroy())

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
        # 令牌变了，二维码里的令牌也得跟着变 —— 否则用户扫到的还是旧令牌
        self.session.refresh_qr_payload()
        self._qr_rendered = None
        self._refresh_header()
        self._refresh_qr()
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
        qr_payload = payload.get("qr_payload")
        lines += [
            "",
            f"二维码载荷：{qr_payload or '（不可用）'}",
            f"  二维码里的地址：{self.session.selected_qr_host()}:{port}",
            f"  二维码图片：{runtime.qr_file_path(data_dir)}"
            f"（{'存在' if os.path.exists(runtime.qr_file_path(data_dir)) else '未生成'}，"
            "无头模式启动时会写一份）",
        ]
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

        # 先撤掉定时回调。destroy() 之后 Tcl 解释器还在，排队的 after 回调照样会
        # 触发，然后去碰已经销毁的控件 —— 那会甩出一堆 TclError。
        for attribute in ("_poll_id", "_tick_id"):
            after_id = getattr(self, attribute, None)
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, attribute, None)

        logger = logging.getLogger("OmniPad")
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            logger.exception("关闭托盘失败")

        try:
            self.session.stop()
        except Exception:
            logger.exception("停止服务端失败")
        finally:
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
