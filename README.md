# OmniPad

在局域网下使用手机作为电脑的触控板/键盘。

当前版本见 [VERSION](VERSION)，发布产物见
[Releases](https://github.com/HaichuanWang1/OmniPad/releases)。

---

## 快速使用

### 电脑端（Windows）

1. 从 [Releases](https://github.com/HaichuanWang1/OmniPad/releases) 下载最新的
   `omnipad-server-v*.zip` 并解压
2. 需要 **Python 3.10 或更高版本**（[下载](https://www.python.org/downloads/)），
   在解压出的目录里执行：
   ```
   python server_ui.py
   ```
3. 记下窗口顶部显示的 **IP 地址**（如 `192.168.x.x`）和 **配对令牌**（8 位，如 `GBGUAWW9`）

> 服务端只依赖 Python 标准库，不需要 `pip install`。
> 发布包目前不含 `server_ui.exe`；如需单文件可执行程序，见下方「打包发布」。

### 手机端（Android）

1. 从 Releases 下载对应版本的 `OmniPad-v*.apk` 并安装
2. 打开 App，填入电脑上显示的 IP 地址
3. 填入电脑上显示的配对令牌
4. 点击「连接」

> 配对令牌在电脑端首次启动时随机生成，保存在 `server/pairing_token.txt`。
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
│   ├── test_server_ui.py    # 客户端历史淘汰等纯逻辑测试
│   └── test_tcp_server.py   # 分帧与连接生命周期测试
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

详见 [docs/protocol.md](docs/protocol.md)

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
python server_ui.py        # GUI 模式
python server.py           # 无头模式
```

### 运行测试

```bash
cd server
python test_tcp_server.py  # 分帧与连接生命周期（10 个用例）
python test_handlers.py    # 握手、配对令牌、字段校验（36 个用例）
python test_server_ui.py   # 客户端历史淘汰等纯逻辑（11 个用例）
```

三个测试文件都只依赖标准库。

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

构建工具链：Gradle 8.13 · AGP 8.13.2 · Kotlin 1.9.21 · JDK 17。

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
