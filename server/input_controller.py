import ctypes
import ctypes.wintypes
import time

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800

KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001

# 虚拟键码在增强键盘上需要 KEYEVENTF_EXTENDEDKEY 标志才能在目标应用中正确识别
EXTENDED_VK_SET = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # PageUp/Down, Home, End, 方向键
    0x2C, 0x2D, 0x2E,  # Print Screen, Insert, Delete
    0x5B, 0x5C, 0x5D,  # Left/Right Win, Application
    0x90, 0x91,         # Num Lock, Scroll Lock
}

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

VK_MAP = {
    "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08,
    "space": 0x20, "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "win": 0x5B,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "caps_lock": 0x14, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "page_down": 0x22, "insert": 0x2D,
    "print_screen": 0x2C, "scroll_lock": 0x91, "pause": 0x13,
    "num_lock": 0x90,
}

for i in range(1, 25):
    VK_MAP[f"f{i}"] = 0x6F + i

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION),
    ]

def _send_input(inputs):
    inp_array = (INPUT * len(inputs))(*inputs)
    result = ctypes.windll.user32.SendInput(
        ctypes.c_uint(len(inputs)),
        inp_array,
        ctypes.sizeof(INPUT),
    )
    return result == len(inputs)

def move_mouse(dx, dy):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.dx = dx
    inp.union.mi.dy = dy
    inp.union.mi.dwFlags = MOUSEEVENTF_MOVE
    inp.union.mi.time = 0
    _send_input([inp])

def click_mouse(button, action):
    flag_map = {
        ("left", "down"): MOUSEEVENTF_LEFTDOWN,
        ("left", "up"): MOUSEEVENTF_LEFTUP,
        ("right", "down"): MOUSEEVENTF_RIGHTDOWN,
        ("right", "up"): MOUSEEVENTF_RIGHTUP,
        ("middle", "down"): MOUSEEVENTF_MIDDLEDOWN,
        ("middle", "up"): MOUSEEVENTF_MIDDLEUP,
    }
    flags = flag_map.get((button, action))
    if flags is None:
        return False
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.dwFlags = flags
    inp.union.mi.time = 0
    return _send_input([inp])

def scroll(delta):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.mouseData = delta
    inp.union.mi.dwFlags = MOUSEEVENTF_WHEEL
    inp.union.mi.time = 0
    _send_input([inp])

def _unicode_input_pair(code):
    ki_down = KEYBDINPUT()
    ki_down.wVk = 0
    ki_down.wScan = code
    ki_down.dwFlags = KEYEVENTF_UNICODE
    ki_down.time = 0

    ki_up = KEYBDINPUT()
    ki_up.wVk = 0
    ki_up.wScan = code
    ki_up.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    ki_up.time = 0

    inp_down = INPUT()
    inp_down.type = INPUT_KEYBOARD
    inp_down.union.ki = ki_down

    inp_up = INPUT()
    inp_up.type = INPUT_KEYBOARD
    inp_up.union.ki = ki_up

    return [inp_down, inp_up]

def send_text(text):
    inputs = []
    for ch in text:
        code = ord(ch)
        if code < 0x10000:
            inputs.extend(_unicode_input_pair(code))
        else:
            code -= 0x10000
            high = 0xD800 + (code >> 10)
            low = 0xDC00 + (code & 0x3FF)
            inputs.extend(_unicode_input_pair(high))
            inputs.extend(_unicode_input_pair(low))

    if inputs:
        return _send_input(inputs)
    return True

def press_key(key_name, action):
    vk = VK_MAP.get(key_name.lower())
    if vk is None:
        if len(key_name) == 1:
            vk = ord(key_name.upper())
        else:
            return False

    is_down = action in ("down", "press")
    is_up = action in ("up", "press")

    is_extended = vk in EXTENDED_VK_SET
    extended_flag = KEYEVENTF_EXTENDEDKEY if is_extended else 0

    inputs = []
    if is_down:
        ki = KEYBDINPUT()
        ki.wVk = vk
        ki.wScan = 0
        ki.dwFlags = KEYEVENTF_KEYDOWN | extended_flag
        ki.time = 0
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki = ki
        inputs.append(inp)

    if is_up:
        ki = KEYBDINPUT()
        ki.wVk = vk
        ki.wScan = 0
        ki.dwFlags = KEYEVENTF_KEYUP | extended_flag
        ki.time = 0
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki = ki
        inputs.append(inp)

    if inputs:
        return _send_input(inputs)
    return True
