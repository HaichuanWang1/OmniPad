# OmniPad v1.0.0-beta1

在局域网下使用手机作为电脑的触控板/键盘。

---

## 快速使用

### 电脑端（Windows）

1. 从 Release 下载 `omnipad-server-v1.0.0-beta1.zip` 并解压
2. 双击运行 `server_ui.exe`（或 `python server_ui.py`）
3. 记下窗口顶部显示的 IP 地址（如 `192.168.x.x`）

### 手机端（Android）

1. 从 Release 下载 `OmniPad-v1.0.0-beta1.apk` 并安装
2. 打开 App，输入电脑上显示的 IP 地址
3. 点击"连接"

### 使用

| 操作 | 效果 |
|---|---|
| 触控板区域拖动 | 鼠标跟随移动 |
| 点击触控板 | 鼠标左键单击 |
| 长按触控板 | 鼠标右键单击 |
| 文字输入框输入 + 发送 | 电脑端实时输入文字（支持中文） |
| 点击功能键 | Enter / Tab / Esc 等 |

---

## 架构

```
手机 (Kotlin/Compose)  ──TCP JSON Lines──>  Python 服务端  ──SendInput──>  Windows
```

## 技术栈

| 端 | 技术 |
|---|---|
| Android 客户端 | Kotlin · Jetpack Compose · Material 3 · Coroutines |
| Python 服务端 | 原生库（ctypes · socket · threading） |
| 通信协议 | TCP + JSON Lines，端口 5800 |

## 功能

| 手机操作 | 电脑效果 |
|---|---|
| 触控板拖动 | 鼠标相对移动 |
| 单击 | 左键点击 |
| 长按 | 右键点击 |
| 滚动按钮 | 鼠标滚轮 |
| 文字输入框 | Unicode 文字注入（支持中文） |
| 功能键按钮 | Enter / Tab / Esc / Backspace / 方向键 / Ctrl+Shift+Alt |

## 项目结构

```
OmniPad/
├── docs/                    # 协议文档（唯一接口标准）
│   ├── protocol.md
│   └── schema.json
├── server/                  # Python 服务端
│   ├── server.py            # 无头模式入口
│   ├── server_ui.py         # Tkinter GUI 控制面板
│   ├── tcp_server.py        # 多线程 TCP 服务器
│   ├── protocol.py          # 消息分派框架
│   ├── input_controller.py  # Windows SendInput 注入
│   └── test_client.py       # 本地测试脚本
└── client/                  # Android 客户端
    └── app/src/main/java/com/omnipad/client/
        ├── ui/theme/        # Material 3 主题（科技蓝深色风）
        ├── ui/screens/      # ConnectScreen · TouchpadScreen
        ├── network/         # Protocol.kt · OmniPadConnection.kt
        └── MainActivity.kt
```

## 协议

详见 [docs/protocol.md](docs/protocol.md)

### 消息类型

```json
{"type":"handshake","version":"1.0"}
{"type":"mouse_move","dx":100,"dy":50}
{"type":"mouse_click","button":"left","action":"click"}
{"type":"scroll","delta":-3}
{"type":"text_input","text":"你好，世界！"}
{"type":"keyboard","key":"enter","action":"press"}
{"type":"heartbeat"}
```

### 握手流程

```
Client → Server:  {"type":"handshake","version":"1.0"}
Server → Client:  {"type":"handshake_ack","version":"1.0"}
```

## 性能优化

- 客户端拖动节流 16ms（~60fps）
- TCP_NODELAY 禁用 Nagle 算法，降低小包延迟
- 服务端 ctypes 直接注入，无额外进程开销

## 许可证

[Apache 2.0](LICENSE)
