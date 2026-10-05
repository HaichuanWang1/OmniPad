# OmniPad v1.0.0-beta1.6

在局域网下使用手机作为电脑的触控板/键盘。

---

## 快速使用

### 电脑端（Windows）

1. 从 [Release](https://github.com/HaichuanWang1/OmniPad/releases) 下载
   `omnipad-server-v1.0.0-beta1.6.zip` 并解压
2. 需要 **Python 3.10 或更高版本**（[下载](https://www.python.org/downloads/)），
   在解压出的目录里执行：
   ```
   python server_ui.py
   ```
3. 记下窗口顶部显示的 **IP 地址**（如 `192.168.x.x`）和 **配对令牌**（8 位，如 `GBGUAWW9`）

> 服务端只依赖 Python 标准库，不需要 `pip install`。
> 发布包目前不含 `server_ui.exe`；如需单文件可执行程序，见下方「打包发布」。

### 手机端（Android）

1. 从 Release 下载 `OmniPad-v1.0.0-beta1.6.apk` 并安装
2. 打开 App，填入电脑上显示的 IP 地址
3. 填入电脑上显示的配对令牌
4. 点击「连接」

> 配对令牌在电脑端首次启动时随机生成，保存在 `server/pairing_token.txt`。
> 删除该文件即可重新生成（手机端需重新配对）。

### 使用

| 操作 | 效果 |
|---|---|
| 触控板区域拖动 | 鼠标跟随移动 |
| 点击触控板 | 鼠标左键单击 |
| 长按触控板 | 鼠标右键单击 |
| 双指滑动 | 鼠标滚轮 |
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
| 双指滑动 | 鼠标滚轮 |
| 文字输入框 | Unicode 文字注入（支持中文） |
| 功能键按钮 | Enter / Tab / Esc / Backspace / 方向键 / Ctrl+Shift+Alt |

## 项目结构

```
OmniPad/
├── VERSION                  # 版本号唯一来源（Gradle 与打包脚本都读它）
├── docs/                    # 协议文档（唯一接口标准）
│   ├── protocol.md
│   └── schema.json
├── scripts/
│   └── package.ps1          # 打包发布产物到 dist/
├── server/                  # Python 服务端
│   ├── server.py            # 无头模式入口
│   ├── server_ui.py         # Tkinter GUI 控制面板
│   ├── handlers.py          # 协议处理器（两个入口共用，唯一一份）
│   ├── pairing.py           # 配对令牌的生成与持久化
│   ├── protocol.py          # 消息分派框架
│   ├── tcp_server.py        # 多线程 TCP 服务器
│   ├── input_controller.py  # Windows SendInput 注入
│   ├── test_client.py       # 本地手工联调脚本
│   ├── test_handlers.py     # 握手、配对令牌、字段校验测试
│   └── test_tcp_server.py   # 分帧与连接生命周期测试
└── client/                  # Android 客户端
    └── app/src/
        ├── main/java/com/omnipad/client/
        │   ├── ui/theme/    # Material 3 主题（科技蓝深色风）
        │   ├── ui/screens/  # ConnectScreen · TouchpadScreen
        │   ├── network/     # Protocol.kt · OmniPadConnection.kt
        │   └── MainActivity.kt
        └── test/            # JVM 单元测试
```

## 协议

详见 [docs/protocol.md](docs/protocol.md)

### 消息类型

```json
{"type":"handshake","version":"1.0","token":"GBGUAWW9"}
{"type":"mouse_move","dx":100,"dy":50}
{"type":"mouse_click","button":"left","action":"click"}
{"type":"scroll","delta":-3}
{"type":"text_input","text":"你好，世界！"}
{"type":"keyboard","key":"enter","action":"press"}
{"type":"heartbeat"}
```

### 握手流程

```
Client → Server:  {"type":"handshake","version":"1.0","token":"GBGUAWW9"}
Server → Client:  {"type":"handshake_ack","version":"1.0"}
```

版本不匹配返回 `VERSION_MISMATCH`，令牌错误返回 `AUTH_FAILED`，两者都会断开连接。

## 开发

### 运行服务端

```bash
cd server
python server_ui.py        # GUI 模式
python server.py           # 无头模式
```

### 运行测试

```bash
cd server
python test_tcp_server.py  # 分帧与连接生命周期
python test_handlers.py    # 握手、配对令牌、字段校验
```

两个测试文件都只依赖标准库。

### 构建客户端

```bash
cd client
./gradlew assembleRelease
```

Release 签名需要 `client/keystore.properties`（不入库），
字段见 `client/keystore.properties.example`。缺少该文件时产出未签名包。

### 客户端单元测试

```bash
cd client
./gradlew :app:testDebugUnitTest
```

### 打包发布

```bash
pwsh scripts/package.ps1              # 服务端 zip + 客户端 apk
pwsh scripts/package.ps1 -Target server
pwsh scripts/package.ps1 -Target client
pwsh scripts/package.ps1 -BuildExe    # 额外用 PyInstaller 生成 server_ui.exe
```

产物按发布规范命名后写入 `dist/`（不入库），脚本会打印 SHA256 供发布说明使用。
zip 的条目时间戳固定，因此同样的源码每次产出**完全相同的字节**，可以靠重新
构建来核对已发布的包。

> ⚠️ 已知问题：当前 AGP 8.2.0 的 lint 无法解析 SDK 中形如 `android-37.0`
> 的平台目录名，会导致 `assembleRelease` 在 `lintVitalAnalyzeRelease` 失败。
> 该门禁已在 `client/app/build.gradle.kts` 中关闭，根治办法见 [fix.md](fix.md)。

## 性能优化

- 客户端拖动节流 16ms（~60fps）
- TCP_NODELAY 禁用 Nagle 算法，降低小包延迟
- 服务端 ctypes 直接注入，无额外进程开销
- 出站消息经单一写协程串行发送，保证组合键与移动序列的顺序

## 许可证

[Apache 2.0](LICENSE)
