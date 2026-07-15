# OmniPad 全局规则

## 项目结构

```
OmniPad/
├── AGENTS.md              # 全局规则（本文件）
├── docs/                  # 共享协议文档（唯一接口标准）
│   ├── protocol.md
│   └── schema.json
├── server/                # Python 电脑端（TCP 服务端）
│   └── requirements.txt
└── client/                # Kotlin 手机端（TCP 客户端）
```

## 开发顺序铁律

必须严格遵守以下阶段顺序，禁止跳阶段开发：

### Phase 1 — 协议设计（当前）
在 `docs/` 下完成 `protocol.md` 和 `schema.json`。所有双端开发均以 docs 中的协议为准。

### Phase 2 — Server 优先
开发 `server/`。必须先实现 TCP 接收和 Windows API 注入。**在 Server 端能通过本地测试脚本成功控制鼠标/键盘前，禁止编写 Client 端任何业务代码。**

### Phase 3 — Client 跟进
开发 `client/`。基于已验证的 Server 接口，实现 Android UI 和网络层。

### Phase 4 — 双端联调
手机连接电脑，进行真实局域网联调。

## 美术约束

- 客户端 UI 必须使用 **Material 3** 设计体系
- **禁止硬编码颜色值**（颜色必须引用主题色 Token）
- 布局必须适配不同屏幕尺寸

## 接口约束

- 双端通信接口 **仅以 `docs/` 下的文档为准**
- Server 与 Client 不得各自另立接口标准
- 所有协议变更必须先更新 `docs/`，再修改代码
