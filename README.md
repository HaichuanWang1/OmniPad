# OmniPad

在局域网下使用手机作为电脑的触控板/键盘。

当前版本见 [VERSION](VERSION)，发布产物见
[Releases](https://github.com/HaichuanWang1/OmniPad/releases)。

---

## 快速使用

### 电脑端（Windows）

1. 从 [Releases](https://github.com/HaichuanWang1/OmniPad/releases) 下载最新的
   `omnipad-server-v*.zip` 并解压
2. **双击 `OmniPad-Server.exe`** —— 不需要安装 Python，也不需要命令行
3. 记下窗口顶部显示的 **IP 地址**（如 `192.168.x.x`）和 **配对令牌**（8 位，如 `GBGUAWW9`）

> 首次运行 Windows 可能弹出 SmartScreen 提示（本程序没有做代码签名），
> 点「更多信息 → 仍要运行」即可。
>
> Windows 11 默认把新出现的托盘图标收进「隐藏的图标」里，可以拖出来固定。
> 关闭窗口时会问「最小化到托盘继续运行 / 停止并退出」。
>
> 想用命令行的话用同目录下的 `OmniPad-Server-CLI.exe`，详见
> [docs/server-cli.md](docs/server-cli.md)：
>
> ```
> OmniPad-Server-CLI.exe --status        服务端在不在跑、谁连着
> OmniPad-Server-CLI.exe --stop          停掉正在运行的实例
> ```
>
> 发布包里也带了源码：装了 Python 3.10+ 的话，`python server.py` 一样能跑，
> 不需要 `pip install`（服务端只用标准库）。

### 手机端（Android）

1. 从 Releases 下载对应版本的 `OmniPad-v*.apk` 并安装
2. 打开 App，填入电脑上显示的 IP 地址
3. 填入电脑上显示的配对令牌
4. 点击「连接」

> 配对令牌在电脑端首次启动时随机生成，保存在数据目录里
> （exe 是 `%APPDATA%\OmniPad\pairing_token.txt`，源码运行是 `server/pairing_token.txt`）。
> 删除该文件即可重新生成（手机端需重新配对）。

### 使用

| 操作 | 效果 |
|---|---|
| 触控板区域拖动 | 鼠标跟随移动 |
| 轻点触控板 | 鼠标左键单击 |
| 长按触控板 | 鼠标右键单击 |
| **双指轻点** | 鼠标右键单击（触控板通用约定，比长按快） |
| 双指拖动 | 鼠标滚轮 |
| 在输入框里打字 | 电脑端**逐字实时**输入（支持中文，输入法候选未确认时不会发出去） |
| 左键 / 中键 / 右键 | 按下并保持，再点一次松开（用于拖拽选中、拖动窗口） |
| 按键面板 | Enter / Tab / Esc / 方向键 / 编辑键 / F1–F12 |
| 快捷面板 | 复制、粘贴、撤销、全选、切换窗口、显示桌面等一键组合键 |

> 锁定的修饰键（Ctrl / Shift / Alt / Win）会**一直保持按下**，直到你再点一次 ——
> 所以「先锁 Ctrl，再点触控板」就是 Ctrl+点击。断开或转屏时会自动释放，不会残留。

> 连接意外中断时会自动重连（退避重试最多 6 次，累计约 30 秒），顶栏会显示
> 正在第几次尝试，**触控板不会消失**。配对令牌错误或协议版本不匹配不会重试 ——
> 重试也不会变好。

> 顶栏的状态胶囊显示心跳往返耗时（如「已连接 · 12 ms」），链路变慢时能提前察觉。

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
| 触控板拖动 | 鼠标相对移动（浮点累加，慢速微调不丢精度） |
| 轻点 | 左键点击 |
| 长按 / 双指轻点 | 右键点击 |
| 双指拖动 | 鼠标滚轮 |
| 输入框打字 | Unicode 文字注入，逐字实时送达（支持中文） |
| 按键面板 | Enter / Tab / Esc / Backspace / 方向键 / 编辑键 / F 键 / Ctrl·Shift·Alt·Win |
| 快捷面板 | Ctrl+C/V/X/Z/Y/A/S/F、Alt+Tab、Alt+F4、Win+D、Ctrl+Shift+Esc |

### 界面

- **深色 / 浅色主题**：默认跟随系统，也可在设置里固定；Android 12+ 可选跟随壁纸取色
- **横竖屏自适应**：竖屏触控板在上、控制面板在下；横屏左右分栏
- **可调手感**：指针速度与滚动速度各 8 档（0.5×–3×），触觉反馈可关
- **保持屏幕常亮**：用手机当键盘打字时不会自动熄屏
- **历史连接**：显示上次使用时间，轻点直连、长按删除

## 项目结构

```
OmniPad/
├── VERSION                  # 版本号唯一来源（Gradle 与打包脚本都读它）
├── .editorconfig            # 字符集与缩进约定
├── docs/                    # 协议文档（唯一接口标准）
│   ├── protocol.md          # 手机 ↔ 电脑的通信协议
│   ├── schema.json          # 协议消息的 JSON Schema
│   └── server-cli.md        # 服务端命令行、状态文件与控制通道
├── scripts/
│   ├── package.ps1          # 打包发布产物到 dist/
│   ├── make_icon.py         # 生成托盘/exe 图标（纯标准库）
│   └── make_version_info.py # 生成 exe 的版本资源
├── server/                  # Python 服务端
│   ├── server.py            # 唯一入口：GUI / 无头 / --status / --stop
│   ├── server_ui.py         # Tkinter 控制面板
│   ├── tray.py              # 系统托盘图标（纯 ctypes）
│   ├── state.py             # 连接状态机与运行状态快照
│   ├── runtime.py           # 数据目录 / 状态文件 / 单实例 / 日志
│   ├── control.py           # 本机控制通道（--status / --stop 靠它）
│   ├── handlers.py          # 协议处理器（两个入口共用，唯一一份）
│   ├── pairing.py           # 配对令牌的生成与持久化
│   ├── protocol.py          # 消息分派框架
│   ├── tcp_server.py        # 多线程 TCP 服务器
│   ├── input_controller.py  # Windows SendInput 注入
│   ├── assets/omnipad.ico   # 图标（由 make_icon.py 生成）
│   ├── test_client.py       # 本地手工联调脚本
│   └── test_*.py            # 单元测试与端到端测试
└── client/                  # Android 客户端
    └── app/
        ├── proguard-rules.pro   # R8 规则（仅补崩溃堆栈可读性）
        └── src/
            ├── main/java/com/omnipad/client/
            │   ├── MainActivity.kt   # 只负责主题、系统栏与内容装配
            │   ├── MainViewModel.kt  # 全部界面状态（跨旋转存活）
            │   ├── data/             # SettingsStore（持久化设置）
            │   ├── network/          # Protocol · OmniPadConnection
            │   │                     # EndpointValidator（连接参数校验）· RecentHostsStore
            │   └── ui/
            │       ├── OmniPadApp.kt     # 顶层装配与会话状态机
            │       ├── NoticeText.kt     # 连接事件 → 用户可读文案
            │       ├── theme/            # Material 3 主题（品牌蓝，深浅双方案）
            │       ├── components/       # 按键、状态胶囊等复用组件
            │       ├── input/            # TextInputTracker（实时键盘差分）
            │       ├── util/             # 触觉反馈、相对时间
            │       └── screens/          # 连接页 · 触控板 · 控制面板 · 设置
            └── test/            # JVM 单元测试（79 个用例）
```

## 主题

颜色全部走 Material 3 的语义 Token（`ui/theme/Color.kt`），无任何硬编码色值 ——
符合 `AGENTS.md` 的美术约束。表面层次靠 `tonalElevation` 表达，而不是手写半透明叠色，
因此深浅两套方案共用同一份布局代码。

## 协议

详见 [docs/protocol.md](docs/protocol.md)。服务端的命令行、状态文件与控制通道
见 [docs/server-cli.md](docs/server-cli.md)。

### 消息类型

```json
{"type":"handshake","version":"1.1","token":"GBGUAWW9"}
{"type":"mouse_move","dx":100,"dy":50}
{"type":"mouse_click","button":"left","action":"click"}
{"type":"scroll","delta":-3}
{"type":"text_input","text":"你好，世界！"}
{"type":"keyboard","key":"enter","action":"press"}
{"type":"heartbeat"}
```

### 握手流程

```
Client → Server:  {"type":"handshake","version":"1.1","token":"GBGUAWW9"}
Server → Client:  {"type":"handshake_ack","version":"1.1"}
```

版本不匹配返回 `VERSION_MISMATCH`，令牌错误返回 `AUTH_FAILED`，两者都会断开连接。

## 开发

### 运行服务端

```bash
cd server
python server.py           # 图形界面（默认）
python server.py --headless   # 无头模式
python server.py --status     # 看看在不在跑、谁连着
python server.py --stop       # 停掉正在运行的实例
```

图形界面里还能看到：连接状态（在线 / 已连接·未配对 / 已拒绝并给出原因 / 已断开
并给出原因）、端口占用者、本机全部地址、日志文件位置，以及一个「自检」面板。
关窗口时会问「最小化到托盘继续运行 / 停止并退出」。

### 运行测试

```bash
cd server
python test_state.py        # 连接状态机与状态快照（34）
python test_runtime.py      # 数据目录、单实例、原子写、日志（44）
python test_control.py      # 本机控制通道（25）
python test_tcp_server.py   # 分帧、连接生命周期与断开原因（22）
python test_handlers.py     # 握手、配对令牌、字段校验（46）
python test_tray.py         # 托盘图标的 Win32 结构体与图标文件（23）
python test_server_ui.py    # 界面纯逻辑（36）
python test_integration.py  # 端到端：真进程 + 真 CLI + 真 socket + 构建脚本（33）
```

全部只依赖标准库。`test_integration.py` 会真的起 `server.py` 子进程，用真实
命令行与真实客户端去查它 —— 这是「状态可观测」这条需求的最终验收；其中的
`BuildScriptTest` 用 `PYTHONIOENCODING=cp1252` 复现英文 Windows 的环境，
因为「打印中文会崩」这类问题靠推理发现不了。

两个用例组需要真实桌面会话，默认跳过：

```powershell
$env:OMNIPAD_GUI_TEST='1';  python test_server_ui.py   # 真的把窗口搭起来
$env:OMNIPAD_TRAY_TEST='1'; python test_tray.py        # 真的把图标放进通知区域
```

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
pwsh scripts/package.ps1 -Target server -SkipExe   # 跳过 exe，快速迭代用
```

服务端 zip 里有两个 exe：`OmniPad-Server.exe`（图形子系统，双击即用）与
`OmniPad-Server-CLI.exe`（控制台子系统，脚本用）。**默认就会构建它们** ——
exe 才是普通用户实际拿到的东西，把它排除在默认路径之外，等于发布流程里最关键
的一步从来没被验证过。需要 `python -m pip install pyinstaller`。

产物按发布规范命名后写入 `dist/`（不入库），脚本会打印 SHA256 供发布说明使用。
**打包是可复现的**：zip 的条目时间戳固定为 2000-01-01，PyInstaller 也固定了
`SOURCE_DATE_EPOCH` 与 `PYTHONHASHSEED`（后者不固定时，模块在归档里的顺序会变，
两次构建能差出一千多字节）。同样的源码两次打包产出**完全相同的字节**，
可以靠重新构建来核对已发布的包。CI 里有这一步的守卫。

构建工具链：Gradle 8.13 · AGP 8.13.2 · Kotlin 1.9.21 · JDK 17 · PyInstaller 6.x。

## 性能

- 指针事件按帧直接发送，不做二次节流（事件本身就是显示刷新率到达的，
  多加一层 16ms 轮询只会增加延迟）
- 位移用浮点累加器保留小数余量，慢速微调不会因为逐次取整而丢失
- TCP_NODELAY 禁用 Nagle 算法，降低小包延迟
- 服务端 ctypes 直接注入，无额外进程开销
- 出站消息经单一写协程串行发送，保证组合键与移动序列的顺序
- release 开启 R8 压缩与资源收缩，APK 从 5.06 MB 降到 1.22 MB（缩减 76%）；
  `proguard-rules.pro` 只补了崩溃堆栈可读性，未加 keep 规则（客户端无反射查找）
- 真机实测冷启动 1.29 s（Android 11 / OPPO PCHM10）

## 许可证

[Apache 2.0](LICENSE)
