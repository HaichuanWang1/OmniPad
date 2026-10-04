# OmniPad 全局规则

## 项目结构

```
OmniPad/
├── AGENTS.md               # 全局规则（本文件）
├── README.md               # 使用说明
├── CHANGELOG.md            # 版本变更记录
├── fix.md                  # 待办修复清单
├── docs/                   # 共享协议文档（唯一接口标准）
│   ├── protocol.md
│   └── schema.json
├── server/                 # Python 电脑端（TCP 服务端）
│   ├── server.py           # 无头模式入口
│   ├── server_ui.py        # Tkinter GUI 入口
│   ├── handlers.py         # 协议处理器（两个入口共用，唯一一份）
│   ├── protocol.py         # 消息分派与发送
│   ├── tcp_server.py       # 多线程 TCP 服务器
│   ├── input_controller.py # Windows SendInput 注入
│   ├── test_client.py      # 手工联调脚本
│   ├── test_tcp_server.py  # 自动化回归测试（仅依赖标准库）
│   └── requirements.txt
├── client/                 # Kotlin 手机端（TCP 客户端）
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
版本已到 v1.0.0-beta1.6，双端均已实现并完成联调，协议也已稳定。
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
- 提交信息使用约定式提交：`fix(server):` / `fix(client):` / `docs:` / `chore:`

## 其他约束

你可以自主的选择是否push哦！
要求dsh工作时，使用goal和task来管理任务
这个项目以前做的很乱，可以适量的重构，大改
