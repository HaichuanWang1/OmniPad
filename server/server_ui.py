import json
import logging
import queue
import socket
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime

from input_controller import move_mouse, click_mouse, scroll, send_text, press_key
from protocol import handler, handle_message, send_json, send_error
from tcp_server import TcpServer as BaseTcpServer

BG = "#0F1117"
SURFACE = "#1A1C23"
SURFACE_VARIANT = "#2A2D36"
PRIMARY = "#4A9EFF"
TEXT = "#E2E2E6"
TEXT_DIM = "#8E9099"
GREEN = "#4ADE80"
RED = "#FF6B6B"
YELLOW = "#FFD93D"

COLORS = {
    "INFO": PRIMARY,
    "WARNING": YELLOW,
    "ERROR": RED,
    "DEBUG": TEXT_DIM,
}

log_queue = queue.Queue()


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class QueueHandler(logging.Handler):
    def emit(self, record):
        log_queue.put(record)


class ClientInfo:
    def __init__(self, addr):
        self.addr = addr
        self.connected_at = datetime.now()
        self.status = "connected"
        self.disconnected_at = None


class TcpServer(BaseTcpServer):
    def __init__(self, host="0.0.0.0", port=5800, on_change=None):
        super().__init__(host, port)
        self.on_change = on_change
        self._clients_info: dict[str, ClientInfo] = {}

    def _handle_client(self, conn, addr):
        addr_str = f"{addr[0]}:{addr[1]}"
        info = ClientInfo(addr)
        self._clients_info[addr_str] = info
        self._notify()
        super()._handle_client(conn, addr)
        info.status = "disconnected"
        info.disconnected_at = datetime.now()
        self._notify()

    def get_clients(self) -> list[ClientInfo]:
        return list(self._clients_info.values())

    def _notify(self):
        if self.on_change:
            self.on_change()


class ServerUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OmniPad Server")
        self.root.geometry("720x540")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(520, 400)

        self.server = None
        self.server_thread = None
        self.running = False

        self._build_ui()
        self._poll_log()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        header = tk.Frame(self.root, bg=BG, padx=20, pady=16)
        header.pack(fill=tk.X)

        title_frame = tk.Frame(header, bg=BG)
        title_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(
            title_frame, text="OmniPad", font=("Segoe UI", 22),
            fg=PRIMARY, bg=BG, anchor="w",
        ).pack(anchor="w")

        self.status_dot = tk.Canvas(title_frame, width=10, height=10, bg=BG, highlightthickness=0)
        self.status_dot.pack(anchor="w", pady=(4, 0))
        self.status_indicator = self.status_dot.create_oval(0, 0, 10, 10, fill=RED, outline="")

        self.status_label = tk.Label(
            title_frame, text="已停止", font=("Segoe UI", 10),
            fg=TEXT_DIM, bg=BG, anchor="w",
        )
        self.status_label.pack(anchor="w")

        lan_ip = get_lan_ip()
        self.connect_info = tk.Label(
            title_frame, text=f"手机连接：{lan_ip}:5800",
            font=("Consolas", 10, "bold"), fg=PRIMARY, bg=BG, anchor="w",
        )
        self.connect_info.pack(anchor="w", pady=(4, 0))

        control_frame = tk.Frame(header, bg=BG)
        control_frame.pack(side=tk.RIGHT)

        cfg_frame = tk.Frame(control_frame, bg=BG)
        cfg_frame.pack(side=tk.TOP, pady=(0, 8))

        tk.Label(cfg_frame, text="地址", font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG).pack(side=tk.LEFT, padx=(0, 4))
        self.host_entry = tk.Entry(
            cfg_frame, width=14, bg=SURFACE_VARIANT, fg=TEXT, relief=tk.FLAT,
            font=("Consolas", 10), insertbackground=TEXT,
        )
        self.host_entry.insert(0, "0.0.0.0")
        self.host_entry.pack(side=tk.LEFT, padx=(0, 8))

        tk.Label(cfg_frame, text="端口", font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG).pack(side=tk.LEFT, padx=(0, 4))
        self.port_entry = tk.Entry(
            cfg_frame, width=6, bg=SURFACE_VARIANT, fg=TEXT, relief=tk.FLAT,
            font=("Consolas", 10), insertbackground=TEXT,
        )
        self.port_entry.insert(0, "5800")
        self.port_entry.pack(side=tk.LEFT)

        self.toggle_btn = tk.Button(
            control_frame, text="启动", font=("Segoe UI", 10, "bold"),
            bg=PRIMARY, fg="#fff", relief=tk.FLAT, padx=24, pady=4,
            cursor="hand2", activebackground="#3B82F6", activeforeground="#fff",
            command=self._toggle_server,
        )
        self.toggle_btn.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))

        sep = tk.Frame(self.root, height=1, bg=SURFACE_VARIANT)
        sep.pack(fill=tk.X)

        client_container = tk.Frame(self.root, bg=BG, padx=16, pady=(8, 0))
        client_container.pack(fill=tk.X)

        tk.Label(
            client_container, text="已连接客户端", font=("Segoe UI", 9, "bold"),
            fg=TEXT, bg=BG, anchor="w",
        ).pack(anchor="w")

        self.client_tree = ttk.Treeview(
            client_container,
            columns=("addr", "connected", "status"),
            show="headings",
            height=4,
            style="Client.Treeview",
        )
        self.client_tree.heading("addr", text="地址")
        self.client_tree.heading("connected", text="连接时间")
        self.client_tree.heading("status", text="状态")
        self.client_tree.column("addr", width=200)
        self.client_tree.column("connected", width=150)
        self.client_tree.column("status", width=80, anchor="center")
        self.client_tree.pack(fill=tk.X, pady=(4, 0))

        sep2 = tk.Frame(self.root, height=1, bg=SURFACE_VARIANT)
        sep2.pack(fill=tk.X, pady=(8, 0))

        log_container = tk.Frame(self.root, bg=BG)
        log_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(8, 4))

        self.log_text = tk.Text(
            log_container, font=("Consolas", 10), bg=SURFACE, fg=TEXT,
            relief=tk.FLAT, padx=12, pady=8, state=tk.DISABLED, wrap=tk.WORD,
            highlightthickness=0, borderwidth=0, insertbackground=TEXT,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(log_container, bg=SURFACE_VARIANT, troughcolor=BG)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_text.yview)

        self._apply_text_tags()
        self._apply_tree_style()

        status_bar = tk.Frame(self.root, bg=SURFACE, padx=16, pady=6)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.uptime_label = tk.Label(
            status_bar, text="运行时间: --", font=("Segoe UI", 9),
            fg=TEXT_DIM, bg=SURFACE, anchor="w",
        )
        self.uptime_label.pack(side=tk.LEFT)

    def _apply_text_tags(self):
        for level, color in COLORS.items():
            self.log_text.tag_config(level.lower(), foreground=color)
        self.log_text.tag_config("bold", font=("Consolas", 10, "bold"))
        self.log_text.tag_config("dim", foreground=TEXT_DIM)
        self.log_text.tag_config("highlight", background="#2A2D36")

    def _apply_tree_style(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Client.Treeview", background=SURFACE, foreground=TEXT, fieldbackground=SURFACE, borderwidth=0)
        style.configure("Client.Treeview.Heading", background=SURFACE_VARIANT, foreground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"))
        style.map("Client.Treeview", background=[("selected", SURFACE_VARIANT)], foreground=[("selected", TEXT)])
        style.layout("Client.Treeview", [("Client.Treeview.treearea", {"sticky": "nswe"})])

    def _toggle_server(self):
        if self.running:
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self):
        host = self.host_entry.get().strip()
        port_str = self.port_entry.get().strip()
        try:
            port = int(port_str)
        except ValueError:
            self._log_now("WARNING", f"端口无效: {port_str}")
            return

        def on_change():
            self.root.after(0, self._update_clients)

        self.server = TcpServer(host=host, port=port, on_change=on_change)
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()

        self.host_entry.config(state=tk.DISABLED)
        self.port_entry.config(state=tk.DISABLED)
        self.toggle_btn.config(text="停止", bg=RED, activebackground="#E55555")
        self.running = True

        self._update_status(GREEN, "运行中")
        self._start_time = datetime.now()
        self._update_uptime()

    def _run_server(self):
        try:
            self.server.start()
        except OSError as e:
            logging.error(f"启动失败: {e}")
            self.root.after(0, self._stop_server)

    def _stop_server(self):
        if self.server:
            self.server.stop()
            self.server = None
            self.server_thread = None

        self.host_entry.config(state=tk.NORMAL)
        self.port_entry.config(state=tk.NORMAL)
        self.toggle_btn.config(text="启动", bg=PRIMARY, activebackground="#3B82F6")
        self.running = False

        self._update_status(RED, "已停止")
        self._update_clients()
        self.uptime_label.config(text="运行时间: --")

    def _update_status(self, color, text):
        self.status_dot.itemconfig(self.status_indicator, fill=color)
        self.status_label.config(text=text)

    def _update_clients(self):
        for row in self.client_tree.get_children():
            self.client_tree.delete(row)
        if not self.server:
            return
        for info in self.server.get_clients():
            if info.status == "connected":
                ts = info.connected_at.strftime("%H:%M:%S")
                tag = "online"
                status_text = "在线"
            else:
                ts = info.disconnected_at.strftime("%H:%M:%S") if info.disconnected_at else "--:--:--"
                tag = "offline"
                status_text = "离线"
            self.client_tree.insert("", tk.END, values=(info.addr, ts, status_text), tags=(tag,))
        self.client_tree.tag_configure("online", foreground=GREEN)
        self.client_tree.tag_configure("offline", foreground=RED)

    def _update_uptime(self):
        if not self.running:
            return
        elapsed = datetime.now() - self._start_time
        total_sec = int(elapsed.total_seconds())
        h, r = divmod(total_sec, 3600)
        m, s = divmod(r, 60)
        self.uptime_label.config(text=f"运行时间: {h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self._update_uptime)

    def _log_now(self, level, msg):
        record = logging.LogRecord("OmniPad", getattr(logging, level), "", 0, msg, None, None)
        log_queue.put(record)

    def _poll_log(self):
        while not log_queue.empty():
            record = log_queue.get_nowait()
            self._append_log(record)
        self.root.after(100, self._poll_log)

    def _append_log(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = record.levelname
        msg = record.getMessage()
        tag = level.lower()

        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f" {timestamp} ", "dim")
        self.log_text.insert(tk.END, f"{level:7}", tag)
        self.log_text.insert(tk.END, f"  {msg}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _on_close(self):
        if self.server:
            self.server.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def configure_logging():
    root_logger = logging.getLogger("OmniPad")
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(QueueHandler())
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root_logger.addHandler(console)


@handler("handshake")
def on_handshake(conn, msg):
    version = msg.get("version", "")
    if version != "1.0":
        send_error(conn, "VERSION_MISMATCH", f"expected 1.0 got {version}")
        return False
    send_json(conn, {"type": "handshake_ack", "version": "1.0"})
    logging.getLogger("OmniPad").info(f"handshake OK, version={version}")
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
    logging.getLogger("OmniPad").info(f"text_input: {text[:40]}{'...' if len(text) > 40 else ''}")
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
    configure_logging()
    app = ServerUI()
    app.run()


if __name__ == "__main__":
    main()
