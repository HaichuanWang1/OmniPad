import json
import socket
import sys
import time

HOST = "127.0.0.1"
PORT = 5800

def send(conn, data):
    line = (json.dumps(data) + "\n").encode("utf-8")
    conn.sendall(line)

def recv(conn):
    buf = b""
    while True:
        ch = conn.recv(1)
        if not ch or ch == b"\n":
            break
        buf += ch
    return json.loads(buf.decode("utf-8")) if buf else None

def test():
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        conn.connect((HOST, PORT))
    except ConnectionRefusedError:
        print("[FAIL] 连接被拒绝 — 请确保 Server 已启动")
        sys.exit(1)

    print("[OK] 已连接到 Server")

    send(conn, {"type": "handshake", "version": "1.0"})
    resp = recv(conn)
    assert resp["type"] == "handshake_ack", f"握手失败: {resp}"
    print("[OK] 握手成功")

    send(conn, {"type": "mouse_move", "dx": 100, "dy": 50})
    time.sleep(0.3)
    print("[OK] 鼠标相对移动 (100, 50)")

    send(conn, {"type": "mouse_click", "button": "left", "action": "click"})
    time.sleep(0.3)
    print("[OK] 鼠标左键单击")

    send(conn, {"type": "scroll", "delta": 3})
    time.sleep(0.3)
    print("[OK] 滚轮向上 3 格")

    send(conn, {"type": "text_input", "text": "测试中文输入"})
    time.sleep(0.5)
    print("[OK] 文字输入: 测试中文输入")

    send(conn, {"type": "keyboard", "key": "enter", "action": "press"})
    time.sleep(0.3)
    print("[OK] 键盘 Enter")

    send(conn, {"type": "keyboard", "key": "ctrl", "action": "down"})
    send(conn, {"type": "keyboard", "key": "a", "action": "press"})
    send(conn, {"type": "keyboard", "key": "ctrl", "action": "up"})
    time.sleep(0.3)
    print("[OK] 键盘 Ctrl+A")

    send(conn, {"type": "heartbeat"})
    resp = recv(conn)
    assert resp["type"] == "heartbeat_ack", f"心跳失败: {resp}"
    print("[OK] 心跳正常")

    print("\n所有测试通过！")

if __name__ == "__main__":
    test()
