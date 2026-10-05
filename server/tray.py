"""Windows 系统托盘图标（纯 ctypes 调 Shell_NotifyIconW，无第三方依赖）。

为什么要自己搭一套 Win32 消息循环，而不是复用 Tk 的窗口：Tk 的窗口过程是它自己
的，往上面挂子类化（SetWindowLongPtrW + GWLP_WNDPROC）在 Tkinter 里是出了名的脆。
这里另起一个线程 + 一个从不显示的窗口 + 自己的消息泵，与 Tk 完全解耦 ——
唯一的耦合是回调只往队列里塞东西，绝不碰 Tk 的任何对象（Tk 不是线程安全的）。

只依赖标准库。非 Windows 平台上 `TrayIcon.start()` 直接返回 False，
调用方按「没有托盘」继续跑。
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading

logger = logging.getLogger("OmniPad")

IS_WINDOWS = os.name == "nt"

# ---- 消息与常量 ----
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040

MF_STRING = 0x00000000
TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020
TPM_LEFTALIGN = 0x0000

CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001

TOOLTIP_LIMIT = 127
ICON_ID = 1

if IS_WINDOWS:
    from ctypes import wintypes

    # LRESULT / WPARAM / LPARAM 都是指针宽度，32 位下 c_longlong 会写坏栈
    LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 ctypes.c_size_t, ctypes.c_ssize_t)

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        """Vista 之后的完整结构（含 guidItem / hBalloonIcon）。

        cbSize 必须正好等于 sizeof(这个结构)，否则 Shell_NotifyIcon 会失败 ——
        test_tray.py 里钉住了 64 位下的 976 字节。
        """

        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", ctypes.c_size_t),
            ("lParam", ctypes.c_ssize_t),
            ("time", wintypes.DWORD),
            ("pt", POINT),
            ("lPrivate", wintypes.DWORD),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _declare():
        """显式声明原型。

        不声明的话 ctypes 默认把返回值当 C int —— 64 位的 HWND/HMENU/HICON 会被
        截断成垃圾指针，而字符串参数也不会自动按宽字符传。这类错误不会报异常，
        只会表现为「图标不出现」或者更糟的随机崩溃。
        """
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM

        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        user32.DefWindowProcW.restype = LRESULT

        user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]

        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE

        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR,
        ]
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
        ]
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]

        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW),
        ]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    _declare()
else:                                            # pragma: no cover - 仅非 Windows
    user32 = shell32 = kernel32 = None


def loword(value) -> int:
    """WM_COMMAND 的 wParam 低 16 位是菜单项 ID。"""
    return int(value) & 0xFFFF


def truncate_tooltip(text) -> str:
    """托盘提示上限 127 个字符，超了 Shell_NotifyIcon 直接失败。"""
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= TOOLTIP_LIMIT:
        return text
    return text[:TOOLTIP_LIMIT - 1] + "…"


class TrayIcon:
    """托盘图标。

    `on_command(command_id)` 在托盘线程里被调用 —— 实现方必须只做线程安全的事
    （往队列里塞一个 id），绝不能碰 Tk。
    """

    CLASS_NAME = "OmniPadTrayWindow"

    def __init__(self, icon_path, tooltip, menu_items, on_command,
                 on_double_click=None):
        self.icon_path = icon_path
        self.tooltip = truncate_tooltip(tooltip)
        self.menu_items = list(menu_items)          # [(command_id, label), ...]
        self.on_command = on_command
        self.on_double_click = on_double_click

        self._thread = None
        self._ready = threading.Event()
        self._started = False
        self._hwnd = None
        self._hicon = None
        self._menu = None
        self._nid = None
        self._wndproc_ref = None                    # 必须持有，否则回调会被 GC 掉

    # ---- 对外 ----

    def start(self, timeout=5.0) -> bool:
        if not IS_WINDOWS:
            logger.info("非 Windows 平台，跳过托盘图标")
            return False
        if not self.icon_path or not os.path.exists(self.icon_path):
            logger.warning(f"找不到托盘图标文件：{self.icon_path}")
            return False

        self._thread = threading.Thread(
            target=self._run, name="omnipad-tray", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            logger.warning("托盘图标创建超时")
            return False
        return self._started

    def update_tooltip(self, text):
        """更新提示文字。从任意线程调用都可以。"""
        self.tooltip = truncate_tooltip(text)
        if self._hwnd:
            # 交给托盘线程去改，跨线程直接调 Win32 容易和消息泵抢
            user32.PostMessageW(self._hwnd, WM_TRAYICON, 0, 0x7FFF)

    def stop(self, timeout=5.0):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    # ---- 托盘线程 ----

    def _run(self):
        try:
            self._create_window()
            self._add_icon()
            self._started = True
        except Exception:
            logger.exception("托盘图标创建失败")
            self._started = False
            self._ready.set()
            self._cleanup()
            return

        self._ready.set()
        self._pump()

    def _create_window(self):
        hinstance = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._wndproc)

        wc = WNDCLASSW()
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = self.CLASS_NAME

        if not user32.RegisterClassW(ctypes.byref(wc)):
            error = ctypes.get_last_error()
            # 1410 = 类已注册。同一个进程里创建第二个托盘时会遇到，不算错。
            if error != 1410:
                raise ctypes.WinError(error)

        # 一个从不显示的顶层窗口。用 HWND_MESSAGE 消息窗口不行：TrackPopupMenu
        # 要求窗口能成为前台窗口，消息窗口做不到，菜单会一闪就没。
        self._hwnd = user32.CreateWindowExW(
            0, self.CLASS_NAME, "OmniPad", 0, 0, 0, 0, 0, None, None, hinstance, None
        )
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _add_icon(self):
        self._hicon = user32.LoadImageW(
            None, self.icon_path, IMAGE_ICON, 0, 0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        if not self._hicon:
            raise ctypes.WinError(ctypes.get_last_error())

        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = ICON_ID
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAYICON
        data.hIcon = self._hicon
        data.szTip = self.tooltip

        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._nid = data

    def _refresh_tooltip(self):
        data = getattr(self, "_nid", None)
        if data is None:
            return
        data.szTip = self.tooltip
        data.uFlags = NIF_TIP
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _pump(self):
        msg = MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._cleanup()

    def _cleanup(self):
        if getattr(self, "_nid", None) is not None:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                logger.exception("移除托盘图标失败")
            self._nid = None
        if self._menu:
            user32.DestroyMenu(self._menu)
            self._menu = None
        if self._hicon:
            user32.DestroyIcon(self._hicon)
            self._hicon = None
        self._hwnd = None

    # ---- 窗口过程 ----

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                if lparam == 0x7FFF:
                    self._refresh_tooltip()
                elif lparam in (WM_RBUTTONUP, WM_LBUTTONUP):
                    self._show_menu(hwnd)
                elif lparam == WM_LBUTTONDBLCLK and self.on_double_click:
                    self.on_double_click()
                return 0
            if msg == WM_COMMAND:
                command = loword(wparam)
                if self.on_command:
                    self.on_command(command)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._cleanup()
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            # 窗口过程里抛异常会直接终止进程，兜住它
            logger.exception("托盘窗口过程出错")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self, hwnd):
        if not self._menu:
            self._menu = user32.CreatePopupMenu()
            for command_id, label in self.menu_items:
                if label is None:
                    user32.AppendMenuW(self._menu, 0x00000800, 0, None)  # MF_SEPARATOR
                else:
                    user32.AppendMenuW(self._menu, MF_STRING, command_id, label)

        point = POINT()
        user32.GetCursorPos(ctypes.byref(point))

        # 必须先抢前台，否则点别处菜单不会关（Windows 的既有约定）
        user32.SetForegroundWindow(hwnd)
        user32.TrackPopupMenu(
            self._menu, TPM_RIGHTBUTTON | TPM_LEFTALIGN | TPM_BOTTOMALIGN,
            point.x, point.y, 0, hwnd, None,
        )
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)


def build_menu_items(commands):
    """`[(id, label), ...]`，label 为 None 表示分隔线。"""
    return list(commands)


def default_icon_path():
    """图标文件的位置：打包后在 `_MEIPASS/assets`，源码运行时在 `server/assets`。"""
    import sys
    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidate = os.path.join(bundle, "assets", "omnipad.ico")
            if os.path.exists(candidate):
                return candidate
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "assets", "omnipad.ico")
