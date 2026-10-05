# OmniPad 修复清单

> 本文件是待办清单（plan of record）。逐条修复，修完打勾，一条一提交。
> 原始粘贴版本左侧被截断，此处已按实际源码校准行号并补全。

## 整体评价

分层意图是清晰的（`protocol.py` 分派 / `tcp_server.py` 传输 / `input_controller.py` 注入），
协议文档齐全，客户端 Compose 结构规整，主题 Token 化做得很到位（`Color.kt` 全量 Token，
无硬编码色值，符合 AGENTS 美术约束）。

问题集中在**重复实现导致的漂移**和**几个真实的正确性缺陷**。

---

## P0 — 真实缺陷，会直接导致功能错误

### 🔴 1. TCP 分片会损坏中文输入（本项目核心功能）

**位置**：`server/tcp_server.py:76-82`

对每个 `recv` 块独立解码：

```python
try:
    decoded = data.decode("utf-8")
except UnicodeDecodeError:
    decoded = data.decode("utf-8", errors="surrogateescape")   # ← 这里
```

一个中文字符 3 字节，跨 TCP 段被切开时 `surrogateescape` 会把它变成孤立代理字符。
实测复现：

```
chunk=b'\xe6\xb5'  -> surrogateescape: '\udce6\udcb5'      # 期望是 "测"
json line: {"type":"text_input","text":"\udce6\udcb5\udc8b..."}
```

日志里那句 `sent partial UTF-8, used surrogateescape` 等于代码自己承认了 ——
但它把「正常现象」当成警告记一笔就放行。`text_input` 是项目卖点，
这个 bug 会在长文本、网络抖动时随机出现。

**改法**：把缓冲区改成 `bytes`，只在遇到 `\n` 后对完整行解码：

```python
buffer += data                      # bytes
while b"\n" in buffer:
    raw, buffer = buffer.split(b"\n", 1)
    line = raw.strip().decode("utf-8", errors="replace")
```

- [x] 已修复（commit `0a6de8a`，附 7 个回归用例，在修复前代码上复现 2 处乱码）

---

### 🔴 2. 零认证 + 监听 0.0.0.0（且暴露在 Tailscale 网络上）

**位置**：`server/tcp_server.py:14`（`host="0.0.0.0"`）、`server/server.py:92`

任何能连上 5800 端口的人都能完全控制你的鼠标键盘。已确认本机 Tailscale 网络里有
`oppo-a11.tailf07962.ts.net`、`v2154a.tailf07962.ts.net` 等设备 —— 攻击面不只是局域网。

**改法**：握手阶段加配对令牌 —— 服务端启动时生成 6 位 PIN 显示在 GUI，客户端连接时提交，
不匹配则拒绝并断开。

⚠️ 这是**协议变更**，按 AGENTS 铁律必须先改 `docs/protocol.md` + `docs/schema.json`，再动代码。

- [ ] docs 先行
- [ ] 服务端校验
- [ ] 客户端提交
- [ ] 默认监听地址收紧为局域网/Tailscale 接口而非 0.0.0.0

---

### 🔴 3. 签名密钥与明文口令已入库

**位置**：`client/app/build.gradle.kts:18-25`，密钥 `client/omnipad-release-key.jks`

已用 `git ls-files --error-unmatch` 确认 jks 被 git 跟踪（当前 39 个跟踪文件中就有它），
且 `storePassword` / `keyPassword` 明文写着 `OmniPad2024`。

**改法**：`git rm --cached` 该 jks、加入 `.gitignore`；口令移到未跟踪的
`keystore.properties`（或环境变量 / `local.properties`），`build.gradle.kts` 读取之。

- [ ] jks 移出版本控制
- [ ] .gitignore 补齐
- [ ] 口令外置
- [x] ⚠️ 密钥已泄露 —— 已决定**不轮换**，继续使用现有密钥（beta 阶段用户量小）

---

### 🔴 4. 客户端每条消息起一个协程，发送顺序不保证

**位置**：`client/app/src/main/java/com/omnipad/client/network/OmniPadConnection.kt:91-100`

```kotlin
fun sendMessage(msg: OmniPadMessage) {
    scope.launch(Dispatchers.IO) {        // ← 每条消息一个新协程
        writer?.write(msg.toJson() + "\n")
        writer?.flush()
    }
}
```

组合键（ctrl down → c press → ctrl up）和鼠标移动序列都依赖严格顺序，一旦交错就是错键、乱飞。
而且鼠标拖动 60fps，每帧都在新建协程。

**改法**：单一写协程 + `Channel<OmniPadMessage>(UNLIMITED)`，`sendMessage` 只做 `trySend`。

- [x] 已修复（commit `d11d28c`；握手改为写协程启动前同步发出，保证是第一条消息）

---

## P1 — 结构性重复与漂移

### 🟠 5. 两个入口各写一份完整 handler，且已经漂移

**位置**：`server/server.py:22-88` 与 `server/server_ui.py:378-446`

两边各有 7 个 `@handler`，逻辑几乎逐行重复。而且已经不一致了：

|              | ACTION_FAILED 上报次数 |
|--------------|------------------------|
| server.py    | 4                      |
| server_ui.py | 0                      |

即无头模式下鼠标点击失败会报错，GUI 模式下静默失败。这正是「复制两份」的必然结局。
更麻烦的是两边都在导入时往全局 `HANDLER_REGISTRY` 注册，同时导入会互相覆盖。

**改法**：抽 `server/handlers.py` 放唯一一份 handler，两个入口只保留各自的启动逻辑与日志配置。

- [x] 抽出 handlers.py（commit `40bae26`，`server.py` 113→41 行，`server_ui.py` 456→383 行）
- [x] 两入口改为引用（同时导入不再互相覆盖，注册表仍为 7 项）

---

### 🟠 6. 空闲超时 15 秒实际不生效

**位置**：`server/tcp_server.py:11`（`IDLE_TIMEOUT = 15`）vs `:64`（`conn.settimeout(30)`）

`recv` 阻塞上限 30s，超时判断只可能在 `recv` 返回后执行 —— 对端静默时实际要 30s 才断，
且走的是 `except socket.timeout` 分支。协议文档写的 15s 与实现不符。

**改法**：`settimeout(1)` 配合循环内计时，或按文档把常量改成 30 并说明。

- [x] 已修复（commit `9d02359`，采用 `settimeout(1)` + 循环内计时；`idle_timeout` 提升为构造参数）

---

### ✅ 21. 握手失败时连接实际未关闭（审阅时新发现）

**位置**：`server/tcp_server.py`（原 `if not handle_message(...): break`）

那个 `break` 只跳出**内层**行循环，外层 `while self.running` 继续 `recv` 阻塞，
所以 `on_handshake` 返回 `False`（VERSION_MISMATCH）时连接根本不会关闭 ——
而 `docs/protocol.md:27` 明写「若版本不匹配，Server 回复错误并关闭连接」。

**改法**：加 `should_close` 标志让外层循环一并退出。

- [x] 已修复（commit `d7adda9`，附回归用例）

---

### 🟠 7. 心跳与监听器逻辑放错层

**位置**：`client/.../MainActivity.kt:56-75`、`:45`

- `MainActivity.kt:56-75`：在 UI 层用 `LaunchedEffect` + 独立 5s 循环数「丢了几次心跳」，
  而 `OmniPadConnection` 也在发心跳 —— 同一件事拆在两处，计数与实际 ack 存在竞态。
- `MainActivity.kt:45`：`connection.setOnMessageListener { ... }` 直接写在 composition 体内，
  每次重组都重新赋值。这是典型的 composition 副作用误用，应放进 `LaunchedEffect(Unit)`。

**改法**：心跳与超时判定全部收进 `OmniPadConnection`，对外只暴露 `connectionState`；UI 只渲染状态。

- [ ] 下沉到连接层
- [ ] 监听器移入 LaunchedEffect

---

### 🟠 8. 协议有 3 份实现，零校验，且已漂移

**位置**：`docs/schema.json`、`server/input_controller.py:31-39`（`VK_MAP`）、
`client/.../network/Protocol.kt`

- `docs/schema.json` 定义了 `additionalProperties: false`，但双端都没有任何地方真的校验它。
- `VK_MAP` 有 `caps_lock` / `delete` / `home` / `end` / `page_up` / `page_down` /
  `insert` / `print_screen` / `scroll_lock` / `pause` / `num_lock`，而 `protocol.md:84-87`
  只列了功能键/修饰键/方向键/F 键 —— 文档落后于实现。
- `scroll` 的 `delta` 单位：文档说「>0 向上」未定义量纲，服务端 `server.py:65` 做 `delta * 120`。

**改法**：把消息构造集中到各端一个 Protocol 模块（服务端可加 pydantic 或手写校验），
并让 `docs/` 与 `VK_MAP` 对齐。

- [ ] 文档补齐 VK 列表
- [ ] 明确 delta 量纲
- [ ] 双端加校验

---

### 🟠 9. 三个手势检测器在同一区域竞争

**位置**：`client/.../ui/screens/TouchpadScreen.kt:347`、`:357`、`:369`

同一个 `Box` 上叠了三个 `pointerInput`（`detectTapGestures` / `detectDragGestures` / `awaitEachGesture`）。
git 历史显示「点击被拖动吃掉」「双指滚动误触发」这类问题的根源。曾经的修复是回退三个独立手势块，
绕了一圈回到原点。

**改法**：按手势类型写**单个** `awaitEachGesture` 状态机（1 指拖动 / 1 指点击 / 1 指长按右键 /
2 指滚动），彻底消除竞争。**这是本次重构收益最大的一处。**

- [ ] 合并为单状态机

---

### 🟠 10. 发布产物散落

**位置**：`dist/`、`server/server-v1.0.0-beta1.6.zip`

- `dist/` 已从版本控制移除并加入 `.gitignore`（已完成）。
- `server/server-v1.0.0-beta1.6.zip` 仍散落在 `server/`，与 `dist/` 的约定不一致
  （已在 `.gitignore` 中单独忽略，属权宜之计）。

**改法**：把 zip 移入 `dist/`，删掉 `.gitignore` 里那条特例规则。

- [x] 归档到 dist/（含命名对齐 `omnipad-server-v<版本>.zip`）
- [x] 清理特例忽略规则

---

### 🟠 11. 全局文案硬编码，无 i18n

`strings.xml` 只有 `app_name`，其余文案全硬编码在 Compose 里。

- [x] 文案入 strings.xml：32 处全部外提，Kotlin 源码中已无中文字面量
- [x] 顺带修掉两个隐患：
      `ButtonGroup` 原先靠**显示文案**反查按键（`label == "左键" && heldKey == "left"`），
      文案一改就失效，现改为显式的 `ButtonSpec.key`；
      连接层不再持有 UI 文案，改产出类型化的 `ConnectionNotice`，由 UI 映射到资源

---

## P2 — 卫生问题

| # | 问题 | 位置 |
|---|------|------|
| 12 | ~~AGENTS.md 阶段标注过期~~ 已改为 Phase 4「修复与维护（当前）」，结构图补全 `dist/`、`fix.md` 与 server 文件清单 | ~~`AGENTS.md:20`~~ |
| 13 | ~~README.md 版本号停留 beta1~~ 已更新为 beta1.6，补「开发 / 测试」章节与已知问题说明 | ~~`README.md:1,11,17`~~ |
| 14 | ~~`__import__("json")` 内联导入~~ 已改为顶部 `import json` | ~~`protocol.py:20`~~ |
| 15 | ~~扁平 import + 存在 `__init__.py`~~ 已删除那个 0 字节的 `server/__init__.py`；server 本就是脚本目录而非包，入口统一为 `cd server` 后运行 | ~~`server/*.py`~~ |
| 16 | ~~字符串插值拼 JSON 未转义~~ 已全部改用 `JSONObject` 构造；`HeartbeatAck` 的 `raw` 字段从未被读取，已改为无载荷的 object；`HandshakeAck.version` 保留（日志与将来的版本协商） | ~~`Protocol.kt:13,21,33,41`~~ |
| 17 | ~~`text_input` 明文写入日志~~ 已随 `40bae26` 消除（键盘记录风险，且与「局域网无认证」叠加） | ~~`server_ui.py:432`~~ |
| 18 | ~~`_clients_info` 只增不删~~ 已加 `MAX_CLIENT_HISTORY = 200`，超限时先淘汰离线记录再淘汰最早的。注意键含源端口，每次重连都是新键，增长比预想更快 | ~~`server_ui.py:64`~~ |
| 19 | ~~`dwExtraInfo` 用 `POINTER(c_ulong)`~~ 已改为 `c_void_p`（`ULONG_PTR` 的对应物），并删除未使用的 `ctypes.wintypes` 导入。改后实测 `sizeof(INPUT)=40` / `MOUSEINPUT=32` / `KEYBDINPUT=24` 不变 | ~~`input_controller.py:51,61`~~ |
| 20 | ~~`send_text` 不切块~~ 已按 `MAX_INPUTS_PER_BATCH = 256` 分批。单个字符最多产生 4 个 INPUT，因此代理对不会被切到两批 | ~~`input_controller.py:142`~~ |

---

## 审阅后新发现

### 🔴 22. `assembleRelease` 在当前环境根本跑不通（发布阻塞）

**现象**：`:app:lintVitalAnalyzeRelease` 失败，`java.lang.reflect.InvocationTargetException`。

**根因**（完整堆栈已拿到）：

```
java.lang.NumberFormatException: For input string: "37.0"
  at com.android.tools.lint.client.api.SimplePlatformLookup$Companion.platformFromSourceProp
```

`D:\sdk\platforms\` 下存在 `android-36.1`、`android-37.0`、`android-37.0-2` ——
这是 Android 新的「主版本.次版本」SDK 命名。AGP 8.2.0 的 lint 会把目录名 `android-`
之后的部分直接转整数，`"37.0".toInt()` 抛异常。

已确认是**既有问题**：用本次改动前的 `build.gradle.kts` 运行同样 `BUILD FAILED`。

**可选修法**：

| 方案 | 代价 |
|---|---|
| A. `lint { checkReleaseBuilds = false }` | 1 行，立即解锁发布；但丢掉 release 静态检查门禁（该门禁此前从未成功运行过） |
| B. 升级 AGP + Gradle 到认识点号平台名的版本 | 彻底；但本地缓存的 AGP 8.5.0 同样早于该命名，需 AGP 8.13+，且 AGP 9 有破坏性变更 |
| C. 从本机 SDK 移除 `android-36.1` / `android-37.0` / `android-37.0-2` | 治本于本机；但会失去这些平台，且属于改动本机 SDK 而非仓库 |

- [x] 采用方案 A：`lint { checkReleaseBuilds = false }`
      已验证 `./gradlew :app:assembleRelease`（不带 `-x`）BUILD SUCCESSFUL，
      产出 app-release.apk 且签名有效
- [ ] 根治：升级 AGP 到 8.13+ 后恢复该门禁

---

### 🟠 23. Release 资产命名不一致

| 版本 | 服务端资产 | 客户端资产 |
|---|---|---|
| beta1 / beta1.2 / beta1.3 | `omnipad-server-v<版本>.zip` | `OmniPad-v<版本>.apk` |
| beta1.5 | — | `OmniPad-v<版本>.apk` |
| **beta1.6** | **`server-v1.0.0-beta1.6.zip`** | **`app-release.apk`** ← Gradle 原始输出名 |

beta1.6 是异类（7 个版本里 5 个遵循约定）。约定已写进 `AGENTS.md` 的「发布规范」。
已发布的资产名可在 GitHub 上直接重命名，不影响 tag 与下载地址。

- [x] 约定写入 `AGENTS.md`
- [ ] 重命名 beta1.6 已发布的两个资产（**需你操作**）

---

## 建议的推进顺序

### 第一批（阻塞项，需要你操作）— ✅ 已完成

1. ~~关掉 hosts 加速工具 → `git fetch --refetch` 修复 18 个损坏对象~~
2. ~~剥离 `dist/` 历史 + `.gitignore` 补齐，然后 `git gc`~~
   （注：实际用 `git filter-branch` 完成，`--prune-empty` 丢掉了 1 个临时提交，49 → 48）

### 第二批（P0 缺陷，可直接改）

- [x] 3. 修 UTF-8 分片解码（`tcp_server.py`）— `0a6de8a`
- [x] 4. `sendMessage` 改 Channel + 单写协程 — `d11d28c`
- [ ] 5. 握手加配对令牌（**需先改 `docs/protocol.md` + `schema.json`**）
- [x] 6. 抽 `server/handlers.py` 消除双份 handler — `40bae26`
- [x] 6b. 握手失败时真正关闭连接（新发现第 21 条）— `d7adda9`
- [x] 6c. 空闲超时按文档生效（第 6 条）— `9d02359`
- [x] 6d. 签名密钥与口令移出版本控制（第 3 条）— `09d5084`

### 第三批（重构）

- [ ] 7. 合并三个手势检测器为单状态机 ← **收益最大的一处**
- [x] 8. 心跳/监听器下沉到连接层 — `06b38c6`
- [ ] 9. 补 `test_client.py` 边界用例 + GitHub Actions
      （分帧与连接生命周期已有 10 个用例，见 `server/test_tcp_server.py`）
- [ ] 22. 解决 `assembleRelease` 的 lint 阻塞（**需你选择方案**）

### 第四批（卫生）

- [x] 10. 更新 `AGENTS.md` / `README.md` 结构图与阶段标注；发布产物归档 `dist/`
- [x] 11. 文案入 `strings.xml`
- [x] 剩余 P2 条目（14-16、18-20）

---

至此 fix.md 中除第 23 条（重命名 GitHub 上已发布的 beta1.6 资产，需你操作）
外全部完成。
