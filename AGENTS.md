# OmniPad 全局规则

## 项目结构

```
OmniPad/
├── AGENTS.md               # 全局规则（本文件）
├── README.md               # 使用说明
├── CHANGELOG.md            # 版本变更记录
├── VERSION                 # 版本号唯一来源（Gradle 与打包脚本都读它）
├── .editorconfig           # 字符集与缩进约定
├── fix.md                  # 待办修复清单
├── docs/                   # 共享协议文档（唯一接口标准）
│   ├── protocol.md         # 手机 ↔ 电脑的通信协议
│   ├── schema.json         # 协议消息的 JSON Schema
│   ├── qr-payload.md       # 连接二维码的载荷格式（扫码配对，非 TCP）
│   └── server-cli.md       # 服务端命令行、状态文件与控制通道（本机接口，非协议）
├── scripts/
│   ├── package.ps1         # 打包发布产物到 dist/
│   ├── make_icon.py        # 生成托盘/exe 图标（纯标准库）
│   └── make_version_info.py # 生成 exe 的版本资源
├── server/                 # Python 电脑端（TCP 服务端）
│   ├── server.py           # 唯一入口：GUI / 无头 / --status / --stop
│   ├── server_ui.py        # Tkinter 控制面板
│   ├── tray.py             # 系统托盘图标（纯 ctypes）
│   ├── state.py            # 连接状态机与运行状态快照（状态的唯一数据源）
│   ├── runtime.py          # 数据目录 / 状态文件 / 单实例 / 日志 / 端口占用
│   ├── control.py          # 本机控制通道（--status / --stop 靠它）
│   ├── handlers.py         # 协议处理器（两个入口共用，唯一一份）
│   ├── pairing.py          # 配对令牌的生成与持久化
│   ├── qr.py               # 连接二维码：载荷构造/解析 + 纯标准库 QR 编码器
│   ├── protocol.py         # 消息分派与发送
│   ├── tcp_server.py       # 多线程 TCP 服务器
│   ├── input_controller.py # Windows SendInput 注入
│   ├── assets/omnipad.ico  # 图标（由 make_icon.py 生成，不要手工编辑）
│   ├── test_client.py      # 手工联调脚本
│   └── test_*.py           # 单元测试与端到端测试
├── client/                 # Kotlin 手机端（TCP 客户端）
│   └── app/src/
│       ├── main/java/com/omnipad/client/
│       │   ├── MainActivity.kt   # 只负责主题、系统栏与内容装配
│       │   ├── MainViewModel.kt  # 全部界面状态（跨旋转存活）
│       │   ├── data/             # SettingsStore（持久化设置）
│       │   ├── network/          # 协议、连接层、参数校验、历史记录、二维码载荷
│       │   └── ui/               # 主题 / 组件 / 页面 / 实时键盘 / 扫码 / 工具
│       └── test/                 # JVM 单元测试（协议、连接层、校验、键盘差分、二维码）
└── dist/                   # 发布产物（不入库，由 GitHub Release 分发）
```

## 开发顺序铁律

必须严格遵守以下阶段顺序，禁止跳阶段开发：

### Phase 1 — 协议设计
在 `docs/` 下完成 `protocol.md` 和 `schema.json`。所有双端开发均以 docs 中的协议为准。

### Phase 2 — 双端开发
开发 `server/` 与 `client/`。

### Phase 3 — 双端联调
手机连接电脑，进行真实局域网联调。

### Phase 4 — 修复与维护（当前）
版本号见仓库根目录 `VERSION`（唯一来源）。双端均已实现并完成联调，协议已稳定。
当前工作以修复缺陷和重构为主，待办清单见 `fix.md`。

## 美术约束

- 客户端 UI 必须使用 **Material 3** 设计体系
- **禁止硬编码颜色值**（颜色必须引用主题色 Token）
- 布局必须适配不同屏幕尺寸

## 接口约束

- 双端通信接口 **仅以 `docs/` 下的文档为准**
- Server 与 Client 不得各自另立接口标准
- 所有协议变更必须先更新 `docs/`，再修改代码

## 发布规范

- 发布产物一律放在 `dist/`，**不入库**（由 GitHub Release 分发）
- Release 资产命名固定为：
  - 客户端 `OmniPad-v<版本>.apk`
  - 服务端 `omnipad-server-v<版本>.zip`
- 不要直接上传 Gradle 原始输出名（如 `app-release.apk`）
- **必须用 `scripts/package.ps1` 产出资产**，不要手工拷贝文件再改名：
  历史上手工打包把 `test_client.py` 和 0 字节的 `__init__.py` 混进了每个发布包，
  beta1.6 还上架了 Gradle 原始输出名
- 版本号只在仓库根目录 `VERSION` 里改一处，Gradle 与打包脚本都会跟着走
- 服务端发布包内容由 `scripts/package.ps1` 的白名单决定；新增运行时模块
  必须同步加进该白名单，否则脚本会拒绝打包
- 服务端包默认包含两个 exe，**不要**为了「快一点」在发布时用 `-SkipExe`：
  - `OmniPad-Server.exe` 图形子系统，普通用户双击用的就是它
  - `OmniPad-Server-CLI.exe` 控制台子系统，`--status` / `--stop` 等命令行专用
  - 为什么是两个：Windows 的子系统标志二选一，图形子系统不会被
    cmd / PowerShell 等待（管道拿不到输出），控制台子系统会弹黑框
- 打包必须保持**可复现**：`SOURCE_DATE_EPOCH` 与 `PYTHONHASHSEED` 都要固定。
  后者不固定时 PyInstaller 归档里的模块顺序会变，两次构建差出一千多字节。
  CI 里有「连打两次比对 SHA256」的守卫
- 提交信息使用约定式提交：`fix(server):` / `fix(client):` / `docs:` / `chore:`

## 服务端状态约束

服务端的运行状态**只能有一个数据源**：`server/state.py` 的 `ServerState`。
状态文件、图形界面表格、`--status` 输出都从它取数。

- 新增「谁连着 / 连得怎么样」的信息时，加到 `ClientRecord` 与 `state.py` 的
  状态流转里，不要在各个界面里各算一份 —— 那正是改造前「界面说在线、
  实际连握手都没过」的成因
- 新增断开原因时，`tcp_server.REASON_*` 与 `state.DISCONNECT_REASON_TEXT`
  必须同时加（有测试盯着）
- 不要在界面线程之外碰 Tk 对象：工作线程只往队列里塞东西，由主线程消费

## 其他约束

你可以自主的选择是否push哦！
要求dsh工作时，使用goal和task来管理任务
这个项目以前做的很乱，可以适量的重构，大改
