"""OmniPad 服务端唯一入口。

    OmniPad-Server.exe                  图形界面（默认，双击即用）
    OmniPad-Server.exe --headless       无头模式
    OmniPad-Server.exe --status         打印运行状态
    OmniPad-Server.exe --status --json  机器可读的状态
    OmniPad-Server.exe --stop           优雅停止正在运行的实例

改造前这里是两个入口各写一份启动逻辑，而且**状态只活在终端或窗口里** ——
进程一退就再也查不到「刚才是不是在跑、谁连着」。现在无论哪种模式，启动后都会：

  * 在数据目录写 `server_status.json`（原子写，每 5 秒刷新）
  * 开一个只绑回环的控制通道，让状态可以被「问」而不是靠猜
  * 用命名互斥体保证只有一个实例，第二个实例会明确告诉你它被谁挡住了

退出码：0 成功 / 1 失败 / 2 已在运行 / 3 未在运行
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

import control
import handlers
import pairing
import runtime
import state as state_module
from tcp_server import TcpServer

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ALREADY_RUNNING = 2
EXIT_NOT_RUNNING = 3

# 状态变化后立刻落盘；消息事件不落盘 —— 鼠标拖动每秒几十条，写盘会把磁盘打满。
# 「最后活动时间」靠每 5 秒的心跳刷新就够了。
PERSIST_ON_EVENTS = frozenset(
    {"connected", "disconnected", "handshake_ok", "handshake_rejected"}
)

STATUS_HEARTBEAT_SECONDS = 5.0

CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------
# 版本
# --------------------------------------------------------------------------

def app_version() -> str:
    """版本号唯一来源是仓库根目录的 VERSION。

    打包成 exe 后 `../VERSION` 不存在，所以 PyInstaller 会把它一并塞进包里
    （`--add-data`），运行时从 `sys._MEIPASS` 读。
    """
    candidates = []
    if runtime.is_frozen():
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidates.append(os.path.join(bundle, "VERSION"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "VERSION"))
    candidates.append(os.path.join(runtime.executable_dir(), "VERSION"))

    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                version = f.read().strip()
        except OSError:
            continue
        if version:
            return version
    return "unknown"


# --------------------------------------------------------------------------
# 会话：状态机 + TCP + 控制通道 + 状态文件心跳
# --------------------------------------------------------------------------

class ServerSession:
    """一次服务端运行的全部部件。

    无头模式与 GUI 模式共用它 —— 两个入口各写一份启动逻辑正是改造前的病根之一。
    """

    def __init__(self, data_dir, host, port, token, mode, on_event=None,
                 protocol_version=None):
        self.data_dir = data_dir
        self.host = host
        self.port = port
        self.token = token
        self.mode = mode
        self.on_event = on_event
        self.logger = None

        self.state = state_module.ServerState(
            pid=os.getpid(),
            mode=mode,
            host=host,
            port=port,
            protocol_version=protocol_version or handlers.PROTOCOL_VERSION,
            data_dir=data_dir,
            log_file=runtime.log_file_path(data_dir),
            token=token,
        )
        self.tcp = TcpServer(
            host=host, port=port, on_event=self.handle_event
        )
        self.control = None
        self._tcp_thread = None
        self._heartbeat_thread = None
        self._heartbeat_stop = threading.Event()
        self._persist_lock = threading.Lock()
        self._stop_hooks = []

    # ---- 生命周期 ----

    def add_stop_hook(self, hook):
        """注册停止回调（无头模式用来解除主循环的等待，GUI 用来关窗口）。"""
        self._stop_hooks.append(hook)

    def start(self):
        """起 TCP、控制通道与心跳。TCP 在后台线程跑，调用方保留主线程。"""
        self.control = control.ControlServer(
            status_provider=self.state.snapshot,
            on_stop=self.request_stop,
        )
        try:
            control_port = self.control.start()
        except OSError as e:
            raise RuntimeError(f"控制通道无法监听回环端口：{e}") from e
        self.state.set_control_endpoint(control.CONTROL_HOST, control_port)

        self._tcp_thread = threading.Thread(
            target=self._run_tcp, name="omnipad-tcp", daemon=True
        )
        self._tcp_thread.start()

        # 等 bind + listen 真的完成再回填端口。`--port 0` 时真实端口由内核分配，
        # 早读一步就会把 0 写进状态文件 —— 而 0 是连不上的，读的人只会以为
        # 服务端「在跑但没监听」。
        if not self.tcp.ready.wait(timeout=5):
            raise RuntimeError("TCP 服务端未能在 5 秒内开始监听")
        if self.tcp.start_error is not None:
            raise RuntimeError(f"TCP 服务端启动失败：{self.tcp.start_error}")
        if not self.tcp.bound_port:
            raise RuntimeError("TCP 服务端没有拿到有效端口")

        self.state.set_bound_port(self.tcp.bound_port)
        self.state.set_running(True)
        self.persist()

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat, name="omnipad-status", daemon=True
        )
        self._heartbeat_thread.start()
        return self

    def _run_tcp(self):
        try:
            self.tcp.start()
        except OSError as e:
            self._log().error(f"TCP 服务端启动失败：{e}")
            self.tcp.start_error = e
            self.tcp.ready.set()
            self.request_stop()

    def request_stop(self):
        """请求停止。可以从任意线程调用（控制通道就是这么用的）。"""
        for hook in list(self._stop_hooks):
            try:
                hook()
            except Exception:
                self._log().exception("stop hook failed")

    def stop(self):
        self._heartbeat_stop.set()
        # 必须等心跳线程真的退出再返回：它可能正卡在 persist() 里写状态文件，
        # 而调用方（尤其是测试与卸载流程）会紧接着删掉数据目录。
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5)
        self.state.set_running(False)
        if self.control is not None:
            self.control.stop()
            self.control = None
        self.tcp.stop()
        if self._tcp_thread is not None:
            self._tcp_thread.join(timeout=5)
        self.persist()

    # ---- 事件 ----

    def handle_event(self, kind, **fields):
        """tcp_server / protocol 上报的连接事件，转成状态机流转。"""
        addr = fields.get("addr")
        try:
            if kind == "connected":
                self.state.client_connected(fields["peer"], fields["port"])
            elif kind == "message":
                self.state.client_message(addr)
            elif kind == "handshake_ok":
                self.state.client_authenticated(addr)
            elif kind == "handshake_rejected":
                self.state.client_rejected(addr, fields.get("code"))
            elif kind == "disconnected":
                self.state.client_disconnected(addr, fields.get("reason"))
        except Exception:
            self._log().exception(f"failed to apply event {kind}")

        if kind in PERSIST_ON_EVENTS:
            self.persist()
        if self.on_event is not None:
            try:
                self.on_event(kind, **fields)
            except Exception:
                self._log().exception("event listener failed")

    # ---- 状态文件 ----

    def persist(self):
        """把当前状态写进状态文件。写失败不能让服务端倒下。"""
        try:
            with self._persist_lock:
                runtime.write_json_atomic(
                    runtime.status_file_path(self.data_dir), self.state.snapshot()
                )
        except OSError as e:
            self._log().warning(f"写入状态文件失败：{e}")

    def _heartbeat(self):
        while not self._heartbeat_stop.wait(STATUS_HEARTBEAT_SECONDS):
            self.persist()

    def _log(self):
        import logging
        return self.logger or logging.getLogger("OmniPad")

    # ---- 展示 ----

    def connect_addresses(self):
        return runtime.local_ipv4_addresses()

    def banner_lines(self):
        addresses = self.connect_addresses() or ["127.0.0.1"]
        lines = [
            f"OmniPad 服务端 v{app_version()}（{'无头' if self.mode == 'headless' else '图形界面'}）",
            f"监听：{self.host}:{self.state.bound_port}",
        ]
        for ip in addresses:
            lines.append(f"手机连接：{ip}:{self.state.bound_port}")
        lines += [
            f"配对令牌：{self.token}",
            f"数据目录：{self.data_dir}",
            f"日志文件：{runtime.log_file_path(self.data_dir)}",
            f"状态文件：{runtime.status_file_path(self.data_dir)}",
        ]
        return lines


# --------------------------------------------------------------------------
# 令牌 / 单实例
# --------------------------------------------------------------------------

def prepare_token(args, data_dir):
    """决定这次运行用哪个令牌。

    优先级：`--token` > 数据目录里已有的 > 从旧位置搬过来的 > 新生成。
    搬运这一步是给「一直用 `python server_ui.py`，现在换 exe」的用户准备的，
    否则他会莫名其妙被要求重新配对。
    """
    if args.token:
        return pairing.normalize(args.token)

    runtime.adopt_token(data_dir, [
        os.path.join(runtime.executable_dir(), runtime.TOKEN_FILENAME),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), runtime.TOKEN_FILENAME),
    ])
    return pairing.load_or_create_token(runtime.token_file_path(data_dir))


def describe_existing(data_dir):
    """已经有一个实例在跑吗？返回 `(payload, live)`。"""
    payload = runtime.load_status(data_dir)
    return payload, runtime.status_is_live(payload)


def stop_instance(data_dir, log=print):
    """优雅停止正在运行的实例。返回退出码。"""
    payload = runtime.load_status(data_dir)
    if payload is None:
        log(f"没有找到状态文件（{runtime.status_file_path(data_dir)}），服务端似乎没在运行。")
        return EXIT_NOT_RUNNING

    pid = payload.get("pid")
    if not runtime.status_is_live(payload):
        log(f"状态文件指向 PID {pid}，但该进程已不存在 —— 服务端没在运行（文件是过期的）。")
        return EXIT_NOT_RUNNING

    stopped = _request_graceful_stop(payload, log)
    if not stopped:
        _taskkill(pid, log)

    deadline = time.time() + 10
    while time.time() < deadline and runtime.process_alive(pid):
        time.sleep(0.1)

    if runtime.process_alive(pid):
        log(f"PID {pid} 在 10 秒内没有退出，请手动结束它。")
        return EXIT_FAILED

    log(f"已停止（PID {pid}）。")
    return EXIT_OK


def _request_graceful_stop(payload, log):
    control_info = payload.get("control") or {}
    host, port = control_info.get("host"), control_info.get("port")
    if not host or not port:
        return False
    try:
        response = control.request(control.CMD_STOP, host, port)
    except control.ControlError as e:
        log(f"控制通道不可用（{e}），改用 taskkill。")
        return False
    return bool(response.get("ok"))


def _taskkill(pid, log):
    if os.name != "nt":
        return
    log(f"强制结束 PID {pid} ...")
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"taskkill 失败：{e}")


# --------------------------------------------------------------------------
# --status
# --------------------------------------------------------------------------

def collect_status(data_dir):
    """汇总状态。返回 `(payload, live, source, error)`。

    source: "control" 表示活进程亲口回答，可信；"file" 表示只能读快照。
    """
    payload = runtime.load_status(data_dir)
    if payload is None:
        return None, False, None, "没有找到状态文件"

    if not runtime.status_is_live(payload):
        return payload, False, "file", None

    snapshot, error = control.fetch_status(payload)
    if snapshot is not None:
        return snapshot, True, "control", None
    return payload, True, "file", error


def format_status(payload, live, source, error=None):
    if payload is None:
        return [
            "OmniPad 服务端：未运行",
            f"没有找到状态文件（{runtime.status_file_path(runtime.resolve_data_dir())}）",
        ]

    pid = payload.get("pid")
    port = payload.get("port")
    lines = [f"OmniPad 服务端 v{app_version()}"]

    if live:
        mode = "图形界面" if payload.get("mode") == "gui" else "无头"
        lines.append(f"状态：运行中（PID {pid}，{mode}模式）")
        if source == "file" and error:
            lines.append(f"  ⚠ 控制通道没有响应（{error}），以下是状态文件里的快照")
    else:
        lines.append(f"状态：未运行（状态文件指向 PID {pid}，该进程已不存在）")

    lines.append(f"监听：{payload.get('host')}:{port}")
    lines.append(f"协议：{payload.get('protocol_version')}"
                 f"　　配对令牌：{payload.get('token_masked')}")

    started = payload.get("started_at")
    if started and live:
        lines.append(f"启动于：{started}")
    lines.append(f"数据目录：{payload.get('data_dir')}")
    lines.append(f"日志文件：{payload.get('log_file')}")

    control_info = payload.get("control")
    if control_info:
        lines.append(f"控制通道：{control_info.get('host')}:{control_info.get('port')}")

    clients = payload.get("clients") or []
    online = [c for c in clients if c.get("state") == state_module.STATE_ONLINE]
    lines.append(f"客户端：{len(online)} 个在线 / 共 {len(clients)} 条记录")
    for client in clients:
        mark = "●" if client.get("state") == state_module.STATE_ONLINE else "○"
        last = client.get("last_message_at") or "-"
        lines.append(
            f"  {mark} {client.get('addr'):<22} {client.get('state_text', ''):<20}"
            f" 最后活动 {last}  消息 {client.get('messages', 0)}"
        )
    return lines


def cmd_status(data_dir, as_json=False):
    payload, live, source, error = collect_status(data_dir)

    if as_json:
        print(json.dumps({
            "ok": live,
            "live": live,
            "source": source,
            "error": error,
            "status_file": runtime.status_file_path(data_dir),
            "status": payload,
        }, ensure_ascii=False, indent=2))
    else:
        for line in format_status(payload, live, source, error):
            print(line)

    return EXIT_OK if live else EXIT_NOT_RUNNING


# --------------------------------------------------------------------------
# 运行
# --------------------------------------------------------------------------

def run_headless(session, logger):
    stopping = threading.Event()
    session.add_stop_hook(stopping.set)

    def shutdown(sig, frame):
        logger.info("收到停止信号，正在退出 ...")
        stopping.set()

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (AttributeError, ValueError):
        pass

    session.start()
    for line in session.banner_lines():
        logger.info(line)
    logger.info("按 Ctrl+C 停止。")

    try:
        while not stopping.is_set():
            stopping.wait(0.5)
    finally:
        session.stop()
    return EXIT_OK


def gui_available() -> bool:
    """这个可执行文件里有没有图形界面。

    CLI 那个 exe 排除了 tkinter（省约 3 MB），所以它开不了窗口。提前问一句，
    总比让用户先看到「端口被占用」再看到 ImportError 强。
    """
    import importlib.util
    return importlib.util.find_spec("tkinter") is not None


def run_server(args, data_dir):
    if not args.headless and not gui_available():
        print(
            "这个可执行文件只包含命令行模式，无法打开图形界面。\n"
            "请运行同目录下的 OmniPad-Server.exe，或加上 --headless 走无头模式。"
        )
        return EXIT_FAILED

    runtime.ensure_dir(data_dir)
    logger = runtime.setup_logging(runtime.log_file_path(data_dir))

    lock = runtime.InstanceLock(name=runtime.instance_lock_name(data_dir))
    if not lock.acquire():
        payload, live = describe_existing(data_dir)
        pid = payload.get("pid") if payload else None
        port = payload.get("port") if payload else None
        detail = f"PID {pid}" if pid else "另一个进程"
        if port:
            detail += f"，端口 {port}"
        message = (
            f"OmniPad 服务端已经在运行（{detail}）。\n"
            f"要接管的话先执行：{_prog()} --stop"
        )
        if not args.headless and live:
            if _prompt_takeover(pid, port):
                stop_instance(data_dir)
                if lock.acquire():
                    return _launch(args, data_dir, logger, lock)
        print(message)
        return EXIT_ALREADY_RUNNING

    try:
        problem = check_port(args.host, args.port)
        if problem:
            _report(problem, args.headless)
            return EXIT_FAILED
        try:
            return _launch(args, data_dir, logger, lock)
        except RuntimeError as e:
            _report(str(e), args.headless)
            return EXIT_FAILED
    finally:
        lock.release()


def check_port(host, port):
    """启动前的端口占用检查。

    只在真的占用了时返回一句人话（含占用者的 PID）—— 「启动失败」这四个字
    对用户毫无价值，他需要知道的是「谁占着」。端口 0 交给内核，不检查。
    """
    if not port:
        return None
    owner = runtime.port_owner(port)
    if owner is None or owner == os.getpid():
        return None
    return (
        f"端口 {port} 已被 PID {owner} 占用，无法启动。\n"
        f"如果是上一个 OmniPad 服务端残留，执行 {_prog()} --stop 即可；\n"
        f"否则请换一个端口：--port 5801"
    )


def _report(message, headless):
    print(message)
    if headless:
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        return
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("OmniPad 服务端", message)
    finally:
        root.destroy()


def _launch(args, data_dir, logger, lock):
    token = prepare_token(args, data_dir)
    handlers.set_pairing_token(token)
    logger.info(f"pairing token: {token}")

    mode = "headless" if args.headless else "gui"
    session = ServerSession(
        data_dir=data_dir, host=args.host, port=args.port,
        token=token, mode=mode,
    )
    session.logger = logger

    if args.headless:
        return run_headless(session, logger)

    try:
        import server_ui
    except ImportError:
        print(
            "这个可执行文件只包含命令行模式，无法打开图形界面。\n"
            "请运行同目录下的 OmniPad-Server.exe，或加上 --headless 走无头模式。"
        )
        return EXIT_FAILED
    return server_ui.run_app(session)


def _prompt_takeover(pid, port):
    """GUI 模式下问一句要不要接管。无头模式不会走到这里。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        return False
    root = tk.Tk()
    root.withdraw()
    try:
        return bool(messagebox.askyesno(
            "OmniPad 服务端",
            f"已经有一个服务端在运行（PID {pid}，端口 {port}）。\n\n"
            f"要停止它并接管吗？",
        ))
    finally:
        root.destroy()


def _prog():
    return os.path.basename(sys.executable) if runtime.is_frozen() else "python server.py"


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="OmniPad-Server",
        description="OmniPad 服务端：把手机变成电脑的触控板与键盘。",
    )
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址（默认 0.0.0.0，即所有网卡）")
    parser.add_argument("--port", type=int, default=5800,
                        help="监听端口（默认 5800；填 0 表示随机端口）")
    parser.add_argument("--token", default=None,
                        help="配对令牌；默认读取或生成数据目录下的 pairing_token.txt")
    parser.add_argument("--data-dir", default=None,
                        help="数据目录（令牌、状态文件、日志）")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式，不打开窗口")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="打印运行状态后退出")
    mode.add_argument("--stop", action="store_true", help="停止正在运行的实例后退出")

    parser.add_argument("--json", action="store_true", help="配合 --status 输出 JSON")
    parser.add_argument("--version", action="store_true", help="打印版本后退出")
    return parser


def _configure_stdio():
    """让标准输出在任何 Windows 代码页下都不会因为中文而崩。

    - 输出被重定向到管道/文件时一律 UTF-8：那种场景下代码页编码就是个惊喜，
      脚本拿到的会是 cp936 或 cp1252 的字节。
    - 直接显示在终端时保留系统代码页（简中是 cp936，中文正常显示），
      但把无法编码的字符降级 —— 否则在**英文** Windows 的终端里打印一句中文
      就会抛 UnicodeEncodeError，而报错信息本身完全看不出跟中文有关。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    _configure_stdio()
    args = build_parser().parse_args(argv)

    if args.version:
        print(app_version())
        return EXIT_OK

    data_dir = runtime.resolve_data_dir(args.data_dir)

    if args.status:
        return cmd_status(data_dir, as_json=args.json)
    if args.stop:
        return stop_instance(data_dir)

    return run_server(args, data_dir)


if __name__ == "__main__":
    sys.exit(main())
