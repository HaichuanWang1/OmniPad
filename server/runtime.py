"""运行时支撑：数据目录、进程存活、单实例、原子写、日志、端口占用。

这些是「状态不再未知」的地基 —— 改造前服务端的状态只活在某个终端窗口里，
进程一退就再也查不到；而且令牌文件是按 `__file__` 定位的，一旦打包成 onefile
exe，`__file__` 指向启动时解包、退出即删的临时目录，**每次启动都会重新生成
令牌**，用户每次都要重新配对。数据目录解决的就是这两件事。

跨平台：Windows 专用路径只在实际调用时进入（CI 与本地测试都能在非 Windows 上
导入本模块），`os.name` 分支之外没有平台假设。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import tempfile
from datetime import datetime

APP_NAME = "OmniPad"
APP_DIR_NAME = "OmniPad"

ENV_DATA_DIR = "OMNIPAD_DATA_DIR"

TOKEN_FILENAME = "pairing_token.txt"
STATUS_FILENAME = "server_status.json"
LOG_DIRNAME = "logs"
LOG_FILENAME = "server.log"
LOCK_FILENAME = ".omnipad-server.lock"

DEFAULT_LOCK_NAME = "OmniPad-Server"
DEFAULT_LOG_MAX_BYTES = 1_000_000
DEFAULT_LOG_BACKUPS = 3

# netstat 超时。它偶尔会因为 DNS 反查卡住，所以必须有上限。
NETSTAT_TIMEOUT = 5.0


# --------------------------------------------------------------------------
# 数据目录
# --------------------------------------------------------------------------

def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> str:
    """exe 所在目录（冻结时）或本文件所在目录（源码运行时）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resolve_data_dir(explicit=None, frozen=None, env=None, appdata=None) -> str:
    """数据目录的解析顺序。

    1. 命令行 `--data-dir`
    2. 环境变量 `OMNIPAD_DATA_DIR`
    3. 打包运行时：`%APPDATA%\\OmniPad`（exe 可能被放在只读目录，不能往自己旁边写）
    4. 源码运行时：`server/` —— 与改造前一致，老用户不会因为升级而换目录

    每个输入都可注入，便于测试；不传时读真实环境。
    """
    if explicit:
        return os.path.abspath(explicit)

    env = os.environ.get(ENV_DATA_DIR) if env is None else env
    if env:
        return os.path.abspath(env)

    frozen = is_frozen() if frozen is None else frozen
    if frozen:
        root = appdata if appdata is not None else os.environ.get("APPDATA")
        if not root:
            root = os.path.join(os.path.expanduser("~"), ".config")
        return os.path.join(os.path.abspath(root), APP_DIR_NAME)

    return os.path.dirname(os.path.abspath(__file__))


def ensure_dir(path) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def token_file_path(data_dir) -> str:
    return os.path.join(data_dir, TOKEN_FILENAME)


def status_file_path(data_dir) -> str:
    return os.path.join(data_dir, STATUS_FILENAME)


def log_file_path(data_dir) -> str:
    return os.path.join(data_dir, LOG_DIRNAME, LOG_FILENAME)


def adopt_token(data_dir, candidates):
    """把候选位置里已有的令牌搬进数据目录。

    场景：老用户一直用 `python server_ui.py`（令牌在 `server/pairing_token.txt`），
    现在换成 exe（令牌在 `%APPDATA%\\OmniPad`）。不搬的话他会莫名其妙被要求重新
    配对，而且旧令牌文件还留在原地让人以为仍然生效。

    返回被采纳的令牌，没有可采纳的返回 None。
    """
    target = token_file_path(data_dir)
    if os.path.exists(target):
        return None

    for candidate in candidates:
        if not candidate:
            continue
        if os.path.abspath(candidate) == os.path.abspath(target):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                token = f.read().strip().upper()
        except OSError:
            continue
        if not token:
            continue
        ensure_dir(data_dir)
        with open(target, "w", encoding="utf-8") as f:
            f.write(token + "\n")
        return token
    return None


# --------------------------------------------------------------------------
# 进程存活
# --------------------------------------------------------------------------

def process_alive(pid) -> bool:
    """判断进程是否还活着。

    Windows 上**不能**用 `os.kill(pid, 0)` —— 那个 API 在 Windows 上没有信号
    语义，会直接 TerminateProcess，等于「查询状态顺便把服务端杀了」。
    这里用 OpenProcess + WaitForSingleObject。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True          # 存在但不属于我
        except OSError:
            return False
        return True

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        # 打不开不代表不存在：受保护进程会返回 ERROR_ACCESS_DENIED
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


# --------------------------------------------------------------------------
# 单实例
# --------------------------------------------------------------------------

def instance_lock_name(data_dir=None) -> str:
    """每个数据目录一把锁。

    用全局名字的话，测试（临时数据目录）与用户真实运行的实例会互相挡住 ——
    而它们本来就该互不相干。同一个数据目录仍然只允许一个实例。
    """
    path = os.path.normcase(os.path.abspath(data_dir or resolve_data_dir()))
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
    return f"{DEFAULT_LOCK_NAME}-{digest}"


class InstanceLock:
    """同一个数据目录只允许一个服务端实例。

    没有它的时候，第二个实例只会在日志里留一行 `failed to start server`，
    GUI 上表现为「启动按钮弹回来」，用户完全不知道端口被谁占了。

    Windows 用命名互斥体 —— 进程无论怎么退出（包括崩溃、被 taskkill）都由内核
    自动释放，不会留下需要人工清理的锁文件。其它平台退回文件锁。
    """

    def __init__(self, name=DEFAULT_LOCK_NAME, path=None):
        self.name = name
        self.path = path
        self._handle = None
        self._file = None

    def acquire(self) -> bool:
        if os.name == "nt":
            return self._acquire_windows()
        return self._acquire_posix()

    def release(self):
        if self._handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._file is not None:
            try:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None

    # ---- 平台实现 ----

    def _acquire_windows(self) -> bool:
        ERROR_ALREADY_EXISTS = 183
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def _acquire_posix(self) -> bool:
        import fcntl
        if not self.path:
            raise ValueError("非 Windows 平台上必须给 InstanceLock 一个锁文件路径")
        ensure_dir(os.path.dirname(os.path.abspath(self.path)))
        self._file = open(self.path, "w")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close()
            self._file = None
            return False
        return True

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"已有实例在运行（锁 {self.name}）")
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# --------------------------------------------------------------------------
# 状态文件
# --------------------------------------------------------------------------

def write_json_atomic(path, payload):
    """先写临时文件再 `os.replace`，读取方永远看不到写了一半的 JSON。

    状态文件是「另一个进程随时来读」的，撕裂的 JSON 会让 `--status` 报一个
    让人以为服务端坏了的解析错误。
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    ensure_dir(directory)
    fd, tmp = tempfile.mkstemp(prefix=".status-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_status(data_dir):
    payload = read_json(status_file_path(data_dir))
    return payload if isinstance(payload, dict) else None


def status_is_live(payload) -> bool:
    """状态文件指向的进程还在不在。文件可能指向一个早已退出的 PID。"""
    if not isinstance(payload, dict):
        return False
    return process_alive(payload.get("pid"))


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------

def setup_logging(log_file=None, console=True, level=logging.DEBUG, logger_name="OmniPad"):
    """配置根日志。

    窗口模式的 exe 被双击启动时 `sys.stderr` 是 None，此时再加一个
    StreamHandler 只会让每条日志都在 handleError 里被静默吞掉 —— 所以显式跳过。
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )

    if log_file:
        ensure_dir(os.path.dirname(os.path.abspath(log_file)))
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=DEFAULT_LOG_MAX_BYTES,
            backupCount=DEFAULT_LOG_BACKUPS,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if console and sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


# --------------------------------------------------------------------------
# 端口占用 / 本机地址
# --------------------------------------------------------------------------

def split_host_port(text):
    """`'127.0.0.1:5800'` / `'[::]:5800'` → `('127.0.0.1', 5800)`。

    解析失败时端口返回 None —— 调用方必须能区分「端口是 0」和「这行没解析出来」。
    """
    text = (text or "").strip()
    if not text:
        return "", None
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port_text = rest.lstrip(":")
    else:
        host, sep, port_text = text.rpartition(":")
        if not sep:
            # 根本没有冒号：整段都是主机名，端口未知
            return text, None
    try:
        return host, int(port_text)
    except ValueError:
        return host, None


def parse_netstat_rows(text):
    """把 `netstat -ano -p TCP` 的输出解析成 `[(local, remote, state, pid)]`。

    只认 TCP 行。表头是本地化的，但数据行的列序固定，所以跳过表头靠的是
    「第一列是 TCP」而不是靠行号。
    """
    rows = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or not parts[0].upper().startswith("TCP"):
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        rows.append((parts[1], parts[2], parts[3].upper(), pid))
    return rows


def listening_pid(rows, port):
    """找出正在 LISTEN 指定端口的进程。

    刻意不匹配 state 字符串：那是 netstat 里唯一可能被本地化的列。改为匹配
    「远端是通配地址且远端端口为 0」—— 只有监听套接字长这样。
    """
    fallback = None
    for local, remote, _state, pid in rows:
        _, local_port = split_host_port(local)
        if local_port != port:
            continue
        _, remote_port = split_host_port(remote)
        if remote_port == 0:
            return pid
        if fallback is None:
            fallback = pid
    return fallback


def netstat_rows(timeout=NETSTAT_TIMEOUT):
    """跑一次 `netstat -ano -p TCP`。失败返回空列表（调用方不该因此崩掉）。"""
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
            creationflags=0x08000000 if os.name == "nt" else 0,   # CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_netstat_rows(completed.stdout)


def port_owner(port, rows=None):
    """占用指定端口的 PID，没人占用返回 None。"""
    rows = netstat_rows() if rows is None else rows
    return listening_pid(rows, int(port))


def local_ipv4_addresses():
    """本机所有可用的 IPv4 地址（不含回环与链路本地）。

    改造前只显示一个地址：用 UDP connect 8.8.8.8 拿到的那个。装了 Tailscale
    的用户看到的是局域网地址，不知道该往手机里填哪个 —— 两个都得给出来。
    """
    found = []

    def add(ip):
        if not ip:
            return
        if ip.startswith("127.") or ip.startswith("169.254."):
            return
        if ip not in found:
            found.append(ip)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        add(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    return found


def format_uptime(seconds) -> str:
    total = int(max(0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()
