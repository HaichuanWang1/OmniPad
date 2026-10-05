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

- [x] docs 先行（`protocol.md` 新增「配对令牌」小节与 `AUTH_FAILED`；`schema.json` 的 handshake 增加必填 `token`）
- [x] 服务端校验（`server/pairing.py` + `handlers.on_handshake`）
- [x] 客户端提交（`Handshake.token`，并随历史记录记住）
- [ ] 默认监听地址仍为 `0.0.0.0` —— 有了令牌校验后不再是缺口，且收紧会影响
      Tailscale 访问，**有意保留**

---

### 🔴 3. 签名密钥与明文口令已入库

**位置**：`client/app/build.gradle.kts:18-25`，密钥 `client/omnipad-release-key.jks`

已用 `git ls-files --error-unmatch` 确认 jks 被 git 跟踪（当前 39 个跟踪文件中就有它），
且 `storePassword` / `keyPassword` 明文写着 `OmniPad2024`。

**改法**：`git rm --cached` 该 jks、加入 `.gitignore`；口令移到未跟踪的
`keystore.properties`（或环境变量 / `local.properties`），`build.gradle.kts` 读取之。

- [x] jks 移出版本控制（`git rm --cached`，磁盘文件保留）
- [x] .gitignore 补齐（`*.jks` / `*.keystore` / `keystore.properties`）
- [x] 口令外置（改读 `client/keystore.properties`，附 `.example`）
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

- [x] 下沉到连接层
- [x] 监听器移入 LaunchedEffect

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

- [x] 文档补齐 VK 列表（改为表格，与 `VK_MAP` 逐项对齐）
- [x] 明确 delta 量纲（说明是「格数」，服务端乘 `WHEEL_DELTA` = 120）
- [x] 服务端加字段校验：`protocol.InvalidParams` + `handlers._int_field` / `_text_field`，
      非法类型回 `INVALID_PARAMS` 而不是让 ctypes 抛异常把连接搞断；
      客户端用 `JSONObject` 构造消息，本身就是类型安全的
- [x] `additionalProperties: false` 已强制：`protocol.ALLOWED_FIELDS` + `handle_message`
      逐条拒绝多余字段。**没有**引入 JSON Schema 校验器 —— 发布包里不含 `docs/`，
      且服务端刻意只用标准库；改为手写字段表，再由 `SchemaConformanceTest`
      与 `schema.json` 逐项比对，漂移会被测试拦住

---

### 🟠 9. 三个手势检测器在同一区域竞争

**位置**：`client/.../ui/screens/TouchpadScreen.kt:347`、`:357`、`:369`

同一个 `Box` 上叠了三个 `pointerInput`（`detectTapGestures` / `detectDragGestures` / `awaitEachGesture`）。
git 历史显示「点击被拖动吃掉」「双指滚动误触发」这类问题的根源。曾经的修复是回退三个独立手势块，
绕了一圈回到原点。

**改法**：按手势类型写**单个** `awaitEachGesture` 状态机（1 指拖动 / 1 指点击 / 1 指长按右键 /
2 指滚动），彻底消除竞争。**这是本次重构收益最大的一处。**

- [x] 合并为单状态机（`awaitEachGesture` + 判定/执行两阶段，阈值取自 `viewConfiguration`）

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
- [x] 根治：升级到 **AGP 8.13.2 + Gradle 8.13**（Kotlin 1.9.21 保持不变即可），
      删掉 `lint { checkReleaseBuilds = false }`，release lint 门禁恢复默认开启，
      `assembleRelease` 不再需要任何 `-x` 参数

> 升级过程中踩到一个环境坑：旧版 Gradle 8.5 的 daemon 会一直占着上一次构建产出的
> `classes.dex`，导致新版本的 `mergeDexRelease` 删不掉目录而失败（报错是
> 「Unable to delete directory ... being used by another process」）。
> `gradlew --stop` 只停当前版本的 daemon，停不掉旧版本那个，需要手动结束进程。

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
- [x] 打包脚本 `scripts/package.ps1` 按约定命名产出，从源头杜绝再犯（见第 24 条）
- [x] 重命名 beta1.6 已发布的两个资产 —— 已用 `gh api` 完成，现全部 6 个 Release
      均符合规范：`OmniPad-v1.0.0-beta1.6.apk` / `omnipad-server-v1.0.0-beta1.6.zip`

---

## 第二轮审阅新发现

### 🔴 24. 发布流程没有任何脚本，全靠手工拷贝（第 23 条的根因）

解包 4 个已发布的服务端 zip，实际条目：

| 文件 | beta1 | beta1.2 | beta1.3 | beta1.6 |
|---|---|---|---|---|
| `test_client.py`（开发脚本） | 混入 | 混入 | 混入 | 混入 |
| `__init__.py`（0 字节） | 混入 | 混入 | 混入 | 混入 |
| `handlers.py` | — | — | — | — |
| `requirements.txt` | — | — | — | 有 |

**每个**发布包都混进了开发脚本和那个 0 字节文件。没有脚本就没有一致性约束，
第 23 条的命名漂移是必然结果而不是偶然。

- [x] `scripts/package.ps1`：包内容由白名单决定，未归类的 `.py` 会让打包**失败退出**
      （已验证：塞一个未归类文件进去，退出码 1 且不产出任何 zip）
- [x] `VERSION` 成为版本号唯一来源，Gradle 与脚本共用
      （已验证：把 VERSION 改成 `1.0.0-beta1.7`，manifest 变为 `versionCode=7` / `versionName=1.0.0-beta1.7`）
- [x] 服务端包内附 `使用说明.txt`（启动方式、Python 要求、令牌位置）

---

### 🔴 25. README 让用户双击一个从来不存在的 `server_ui.exe`

原文「双击运行 `server_ui.exe`（或 `python server_ui.py`）」，
但 4 个已发布 zip 里的 `.exe` 数量都是 **0**。照 README 走的用户必然扑空。

- [x] README 改为「需要 Python 3.10+，在解压目录执行 `python server_ui.py`」
- [x] `scripts/package.ps1 -BuildExe` 可选用 PyInstaller 生成 exe
      （默认关闭：体积大、未签名会触发 SmartScreen，且多数用户已有 Python）
- [ ] 若确实要发 exe，需把 PyInstaller 纳入发布流程并接受 SmartScreen 警告

---

### 🔴 26. README 从未提到配对令牌 —— 照做根本连不上

握手现在强制要求 `token`，客户端也有令牌输入框，但 README 的「快速使用」
一个字都没提。这是第 2 条改动引入的文档漂移。

- [x] 补上「记下配对令牌」「填入配对令牌」两步，并说明令牌文件位置与重置方式
- [x] 协议示例与握手流程图同步补上 `token` 字段

---

### 🟠 27. CHANGELOG 落后 3 个版本

停在 `v1.0.0-beta1.3`，而版本已到 beta1.6；中间三个版本与本次全部改动都没记录。

- [x] 补齐 beta1.4 / beta1.5 / beta1.6，并新增 v1.0.0-beta1.7 段收录本次改动
- [x] README 标题与下载说明不再钉死版本号（此前已漂移两次：beta1 → beta1.6），
      改为指向 `VERSION` 与 Releases

---

### 🟠 28. 客户端零测试

`client/app/src` 下只有 `main`，没有任何测试目录。协议编解码与连接层全靠人工推理 ——
而这两处恰好是本轮改动最多的地方。

- [x] 引入 JUnit + `org.json`，新增 27 个用例（`ProtocolTest` 14 + `OmniPadConnectionTest` 13）
- [x] 连接层测试对着**真实本地 TCP 假服务端**跑，覆盖握手顺序、错误码映射、
      出站顺序、心跳超时、重连
- [x] 为可测性把心跳间隔与主线程调度器提成构造参数（默认值不变，生产调用点仅 1 处）
- [x] 变异测试确认用例有效：把心跳清零、出站顺序、JSON 转义三处分别改坏，
      对应用例如期变红

---

### 🟠 29. 无自动重连

客户端源码里 `reconnect` / `retry` / `重连` 零命中。网络抖动后必须手动点连接。

- [x] 连接层加入自动重连：退避序列 `500ms → 1s → 2s → 4s → 8s → 15s`（累计约 30 秒），
      用尽后落 `FAILED` 交给用户手动重连
- [x] 新增 `RECONNECTING` 状态与 `reconnectAttempt`，界面显示「正在重连（第 N 次）」
- [x] 区分「用户主动断开」与「链路掉了」：`closingByUser` + 取消待执行的重连任务，
      用户点断开或 Activity 销毁都不会触发重连
- [x] **认证失败与版本不匹配不重试** —— 那不是暂时性故障，重试只会让用户
      对着「正在重连…」干等半分钟，最后还是同样的错
- [x] 修掉一个竞态：`tearDown` 取消读协程时，读协程的 finally 会再次调用断开处理，
      把心跳超时这条真正的原因用 `null` 盖掉。改用原子标志让先到的那次独占
- [x] 8 个新用例覆盖重连，并用变异测试确认有效（去掉原子标志、让认证失败可重试、
      无视开关、退避不耗尽，四处改坏均如期变红）

---

### 🟠 30. 配对令牌明文传输

纯 TCP，无 TLS。局域网内可嗅探到令牌；Tailscale 内因 WireGuard 加密而安全。

- [ ] 待办：视需求上 TLS，或明确「请只在 Tailscale / 可信局域网内使用」

---

### 🟡 31. 历史发布遗漏

- beta1.5 的 Release **只上传了 APK，漏了服务端 zip** —— 该版本没有可下载的服务端包
- beta1.4 打了版本提交（`2a34a0c`）和 APK，但**从未发布 Release，也没有 tag**
- 本地 `dist/omnipad-server-v1.0.0-beta1.1.zip` 是 0 字节且 `Central Directory corrupt`；
  GitHub 上的同名资产是好的（9288 字节），故仅为本地残渣，非坏发布

- [x] 已在 CHANGELOG 中标注
- [ ] 可选：为 beta1.4 补打 tag（提交 `2a34a0c` 仍在历史中）

---

### 🟡 32. 其余工程卫生

- **协议版本号没有跟着破坏性变更递增**：配对令牌让 `token` 变成必填，
  但 `PROTOCOL_VERSION` 仍是 `"1.0"`。后果是 beta1.6 的旧客户端会**先通过版本检查、
  再倒在令牌校验上**，用户看到 `AUTH_FAILED`（以为令牌填错了），而真正的原因是 App 太旧。
- **无 `.editorconfig`**：行尾已由 `.gitattributes` 管住，但缩进与字符集仍靠自觉
  （Python 4 空格 / Kotlin 4 空格 / YAML 2 空格）。
- **未开启代码压缩与混淆**：`isMinifyEnabled = false`，也没有 `proguard-rules.pro`，
  APK 约 5 MB。Compose 对 R8 的 keep 规则有要求，开启前必须实测，不宜盲改。
- **`server_ui.py` 没有自动化测试**：它是主要入口（383 行），但测试只覆盖了
  `handlers.py` 与 `tcp_server.py`。Tkinter 整体测试成本高，可先把其中的纯逻辑
  （客户端历史裁剪、地址格式化）抽成独立函数再单测。

- [x] 协议版本递增到 **1.1**，并加上一致性守卫：版本号在服务端常量、客户端
      `Handshake` 默认值、文档标题与 schema description 共四处出现，跨语言没法共享
      常量，改为由 `ProtocolVersionConformanceTest` 自动比对（已验证：故意只改文档
      不改客户端时，该测试如期报 `'1.0' != '1.1'`）
- [x] 明确「不做版本协商」并写明理由：双端始终一起发布，没有第三方客户端；
      协商会引入真实协议面，收益只多版本共存时才体现
- [x] 补 `.editorconfig`
- [x] release 开启 R8：APK **5.06 MB → 1.10 MB**（缩减 78%）。客户端零反射
      （`Class.forName` / `getDeclaredMethod` / `::class.java` 全无命中），故无需
      keep 规则。已验证：`minifyReleaseWithR8` 通过（R8 会因缺失类而构建失败）、
      签名有效、入口 Activity 未被混淆、`mapping.txt` 保留行号、关键字符串资源
      未被资源收缩误删
- [x] 补 `server_ui.py` 纯逻辑测试（11 个用例，含淘汰循环的终止路径）
- [x] 第 32 条已全部完成

---

## 建议的推进顺序

### 第一批（阻塞项，需要你操作）— ✅ 已完成

1. ~~关掉 hosts 加速工具 → `git fetch --refetch` 修复 18 个损坏对象~~
2. ~~剥离 `dist/` 历史 + `.gitignore` 补齐，然后 `git gc`~~
   （注：实际用 `git filter-branch` 完成，`--prune-empty` 丢掉了 1 个临时提交，49 → 48）

### 第二批（P0 缺陷，可直接改）

- [x] 3. 修 UTF-8 分片解码（`tcp_server.py`）— `0a6de8a`
- [x] 4. `sendMessage` 改 Channel + 单写协程 — `d11d28c`
- [x] 5. 握手加配对令牌（**需先改 `docs/protocol.md` + `schema.json`**）— `d44fb1f`
- [x] 6. 抽 `server/handlers.py` 消除双份 handler — `40bae26`
- [x] 6b. 握手失败时真正关闭连接（新发现第 21 条）— `d7adda9`
- [x] 6c. 空闲超时按文档生效（第 6 条）— `9d02359`
- [x] 6d. 签名密钥与口令移出版本控制（第 3 条）— `09d5084`

### 第三批（重构）

- [x] 7. 合并三个手势检测器为单状态机 ← **收益最大的一处** — `3eb9758`
- [x] 8. 心跳/监听器下沉到连接层 — `06b38c6`
- [x] 9. 补边界用例 + GitHub Actions
      （`server/test_tcp_server.py` 10 个 + `server/test_handlers.py` 26 个，共 36 个用例；
      `.github/workflows/ci.yml` 跑服务端测试与客户端 debug/release 双 variant 编译）
- [x] 22. 解决 `assembleRelease` 的 lint 阻塞（采用方案 A）— `88f5d22`

### 第四批（卫生）

- [x] 10. 更新 `AGENTS.md` / `README.md` 结构图与阶段标注；发布产物归档 `dist/`
- [x] 11. 文案入 `strings.xml`
- [x] 剩余 P2 条目（14-16、18-20）

---

仍未完成：

- 第 25 条：若要发 `server_ui.exe`，需把 PyInstaller 纳入发布流程。
  当前选择不发：exe 体积大、未签名会触发 SmartScreen，且服务端只用标准库
- 第 30 条：配对令牌明文传输。纯 TCP 无 TLS，局域网内可嗅探；Tailscale 内因
  WireGuard 加密而安全。需要时再上 TLS
- 第 31 条：为 beta1.4 补 tag（可选，该版本从未发布）

已全部完成（本轮）：

- 第 8 条：服务端强制 `additionalProperties`，并由 `SchemaConformanceTest`
  与 `docs/schema.json` 逐项对齐
- 第 22 条根治：升级到 Gradle 8.13 + AGP 8.13.2，release lint 门禁恢复开启，
  `lintVitalAnalyzeRelease` 已实际跑通
- 第 23 条：重命名 beta1.6 的两个线上资产 —— 用 `gh api` 完成，6 个 Release 现已全部合规
- 第 29 条：客户端断线自动重连（含状态机与 8 个用例）
- 第 32 条：协议版本递增到 1.1 + 一致性守卫、`.editorconfig`、R8 压缩
  （APK 5.06 MB → 1.10 MB）、`server_ui.py` 测试
- 版本号递增到 `v1.0.0-beta1.7`：main 与 beta1.6 协议不兼容，继续沿用 beta1.6
  会让打包脚本产出与线上同名却不兼容的资产
