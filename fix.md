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
- [x] `scripts/package.ps1` 可选用 PyInstaller 生成 exe
      （默认关闭：体积大、未签名会触发 SmartScreen，且多数用户已有 Python）
- [x] **已发 exe**（第四轮第 58 条）：改为默认构建，两个 exe
      （图形子系统 + 控制台子系统），并接受 SmartScreen 警告 ——
      让普通用户先装 Python 再敲命令行，是把门槛放在了最前面

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

## 第三轮：客户端 UI/UX 全面改造

**触发**：用户要求「全面改进客户端的 ui 观感和交互逻辑」。
**约束**：不动协议 —— 全部改进只用现有 10 种消息类型，服务端一行未改。

审阅客户端全部 11 个 Kotlin 文件（1478 行）后，按「会真的坏掉」优先排序。

### 🔴 33. 转屏必然掉线

**位置**：`MainActivity.kt:54`（`private val connection = OmniPadConnection(lifecycleScope)`）、`:132`（`onDestroy` 里 `disconnect()`）

`connection` 是 Activity 字段，manifest 没配 `configChanges`，所以每次转屏都重建
Activity → `onDestroy` → 断开。而且 `disconnect()` 会置 `closingByUser = true`，
**自动重连也救不回来**。

- [x] 状态上提到 `MainViewModel`（新增），连接对象随 ViewModel 存活，旋转不再重建
- [x] `MainActivity` 从 135 行瘦到 55 行，只负责主题、系统栏与内容装配
- [x] 顺带修掉监听器泄漏：原监听器注册在 `LaunchedEffect(Unit)` 里但从不注销，
      lambda 持有 Activity；现在捕获的是 ViewModel，且生命周期与之一致

### 🔴 34. 网络抖一下就把用户踢回连接页

**位置**：`MainActivity.kt:102`（`if (state == CONNECTED) 触控板 else 连接页`）

链路一断就进 `RECONNECTING`，界面立刻切回连接页 —— 触控板消失、手感中断。

- [x] 引入会话状态 `inSession`：进入过触控板后，`RECONNECTING` 期间**留在原页**，
      上方只多一条提示条（含「返回连接页」按钮）
- [x] 真机实测：杀掉服务端后触控板仍在原位，提示条显示「连接中断，正在重连（第 4 次）」，
      服务端恢复后自动重连成功

### 🔴 35. 慢速滚动完全没反应

**位置**：`TouchpadScreen.kt:448`（原 `((scrollLastY - avgY) / 3f).toInt()`）

每个事件独立取整，小数余量被丢掉。慢慢滑两指时每次增量都 < 3px，`toInt()` 恒为 0 ——
**输出一直是 0，界面毫无反应**。

- [x] 改为浮点累加器，余量带到下一次：`acc += delta; val n = acc.toInt(); acc -= n`

### 🔴 36. 慢速移动指针同样丢精度

**位置**：`TouchpadScreen.kt:434`（原 `dragAccumX.addAndGet(delta.x.toInt())`）

与第 35 条同源。想精确定位时指针纹丝不动。

- [x] 同样改为浮点累加

### 🔴 37. 拖动的最后一段位移被丢掉

**位置**：`TouchpadSurface.kt` 拖动循环（原 `if (change == null || !change.pressed) break`）

抬手事件里也带着从最后一个 MOVE 到抬手位置之间的位移，原来的写法直接 `break` 丢掉。
**抓包实测：140px 的滑动只发出 128px 的位移**，表现为指针总停在手指停顿位置前面一点，
精细拖动（拖窗口边缘、选文字）会持续偏短。

- [x] 先算完 delta 再判断是否抬起。复测：31 条 `mouse_move` 累加恰好 **140px**

### 🔴 38. 锁定的修饰键与电脑实际状态不一致（Ctrl+点击失效）

**位置**：`ControlPanel.kt` 原 `modifierSequence`

原来每次敲键都给它包一层 `down`/`up`。单个组合键看起来能work，但敲完第一个键后
电脑上的 Ctrl 其实已经抬起、界面却还亮着。抓包实测：

```
ctrl down                          ← 锁定 Ctrl
ctrl down / tab press / ctrl up    ← 敲 Tab
mouse_click left click             ← 此刻电脑上 Ctrl 已抬起 → Ctrl+点击 静默失效
```

- [x] 锁定的修饰键在点按时按下、解锁时释放，**敲键不再夹带 down/up**
- [x] 复测序列变为：`ctrl down` → `tab press` → `mouse_click left click` → `ctrl up`，
      全程电脑上的 Ctrl 保持按下

### 🔴 39. 鼠标键切换时会残留按住状态

**位置**：`TouchpadScreen.kt` 原 `toggleMouseButton`

`heldMouseButton` 是单值，从「按住左键」直接切到「右键」时只发 `right down`，
**左键在 Windows 侧永远处于按下**，直到用户手动再点一次。

- [x] 切换时先补发上一个键的 `up`。抓包确认：`left up` 与 `right down` 成对出现

### 🟠 40. 触控板填充色与页面背景完全相同

深色 `#111318`、浅色 `#FDFBFF` —— `surface` 与 `background` 在本主题里是同一个值，
直接铺 `surface` 会让触控板融进页面、只剩一圈描边。

- [x] 改用 `Surface(tonalElevation = 4.dp)`。这是 M3 里「比背景高一层」的标准做法
      （会按高度叠一层极淡的 surfaceTint），两种主题下都得到清晰但不喧闹的层次
- [x] 真机实测：浅色下触控板 `#E5ECF8` vs 背景 `#FDFBFF`，层次可辨
- [x] 控制面板同样加 `tonalElevation = 2.dp`（横屏时它是独立一栏，与背景同色会显得漂浮）

### 🟠 41. 连接页内容顶对齐，下方空一大片

`verticalScroll` 会把 Column 高度撑成视口高度，默认 `Top` 排列导致内容全挤在顶部。
原版是居中的，重写时丢了。

- [x] 加 `verticalArrangement = Arrangement.Center`（内容超过一屏时自然退化成可滚动）

### 🟠 42. `fillMaxWidth().widthIn(max = 520.dp)` 上限完全无效

`fillMaxWidth` 先把 `minWidth` 顶到父容器宽度，之后 `widthIn` 的 `maxWidth` 与这个
`minWidth` 冲突，`Constraints.constrain` 取 min 的结果仍是父容器宽度。竖屏 360dp 本来
就窄于 520dp 所以一直没暴露；**横屏一测，表单直接拉满 800dp**。

- [x] 调整顺序为 `widthIn(max).fillMaxWidth()`。复测：横屏下表单 472dp 宽、居中

### 🟠 43. `enableEdgeToEdge()` 的刘海设置不生效，横屏留一条黑边

真机（OPPO ColorOS / Android 11）实测：横屏时窗口被刘海裁掉 56px，
`dumpsys window` 里 `mFrame=[56,0][1600,720]`、`mAttrs` 中**没有** `layoutInDisplayCutoutMode`。

根因：`enableEdgeToEdge()` 内部是**就地修改** `window.attributes` 返回的对象、
不经过 `setAttributes`，部分 OEM 上不生效。

- [x] 在 `MainActivity` 用 `window.attributes = window.attributes.apply { ... }`
      显式走一遍 setter。复测：`mAttrs` 出现 `layoutInDisplayCutoutMode=shortEdges`，
      `mFrame=[0,0][1600,720]`，黑边变成页面背景色
- 内容仍靠 `WindowInsets.safeDrawing` 避开刘海，所以刘海区被背景填满而非留黑

### 🟠 44. `windowBackground` 不生效，启动会闪错色

**位置**：`res/values/themes.xml`

实测（模拟器与真机均复现）：把 `windowBackground` 改成品红/绿做对照实验，屏幕底色
**仍是纯黑**；`aapt2 dump resources` 确认资源已正确打进 APK。同时 `windowBackground`
只跟随**系统**深色模式，而用户可以在设置里手动切主题 —— 系统浅色 + App 深色时
窗口背景会从底下透出来。

- [x] 背景改由 Compose 画（`OmniPadApp` 根 `Box` 上 `.background(colorScheme.background)`），
      保证底色永远跟当前生效的主题一致
- [x] `themes.xml` / `values-night/themes.xml` 仍按系统深色模式提供启动窗口底色
      （冷启动首帧用），并补上 `values-night` 一份（原来只有浅色一份，
      而 App 当时恒定深色，等于每次启动都闪白屏）

### 🟡 45. 其余交互改进（无对应缺陷，属主动优化）

- **实时键盘**：由「输入框 + 发送按钮」改为边打边发。新增纯逻辑类 `TextInputTracker`
  做差分：排除输入法合成区间（拼音还在候选框时绝不发出去），算出公共前缀后
  退格 + 补发。支持退格、选中替换、光标中间插入、emoji（按 UTF-16 码元计数，
  与 Windows 编辑框的退格单位一致）
- **双指轻点 = 右键**：触控板的通用约定，比长按更快
- **触觉反馈**：原先全项目零触觉。远程控制时屏幕本身不动，震动是唯一的本地确认通道。
  用 `View.performHapticFeedback` 而非 Compose 的 `HapticFeedbackType` ——
  后者映射的 `TextHandleMove` 是 API 27 常量，minSdk 26 会踩空
- **触摸点涟漪**：拖动时在触点画一圈淡主色，状态只在绘制阶段读取，不触发重组
- **去掉 16ms 轮询循环**：原来用 `while(true) { delay(16) }` 定期转发累加值，
  空闲时也在跑，还给每次拖动加最多 16ms 延迟。指针事件本身按帧到达，无需二次节流
- **链路健康指示**：顶栏显示「已连接 · N ms」（心跳往返耗时）。链路退化远早于断开，
  用户能在操作变迟钝时就察觉。实测真机 USB 转发下 2ms、模拟器 6-7ms
- **保持屏幕常亮**：用手机当键盘打字时，屏幕不该因为没碰手机而熄灭
- **断开确认对话框**：断开是 `closingByUser`，自动重连不会兜底，误触代价高
- **无障碍**：触控板加 `contentDescription` 与 `onClick`/`onLongClick` 语义动作
  （原先零 `semantics`，TalkBack 用户完全无法操作）；按键的锁定状态用
  `stateDescription` 播报
- **协议键与显示文案解耦**：原修饰键/方向键/功能键用 `label.lowercase()` 反查协议键，
  文案一被翻译就会往服务端发非法键名。同一个文件里鼠标键早已专门修过这个坑，
  但另外三处没改 —— 现已全部改为闭包捕获
- **键盘弹起后够不到连接按钮**：原布局不滚动也不处理 IME 内边距
- **连接参数校验**：新增纯函数 `EndpointValidator`（22 个用例）。原实现地址留空会直接
  拿去连接、把 Java 异常原文显示给用户；端口留空静默变成 5800；把
  `192.168.1.5:5800` 整段粘进地址框会当成主机名
- **失败原因常驻显示**：原来只有 3.5 秒的 Toast，消失后界面只剩一句通用提示，
  分不清是令牌错了还是电脑没开机。现在每种失败都有「结论 + 下一步该做什么」

### 🟡 46. 设置项无处可放

原来只有一个「自动断开」开关，而且做成了顶栏里一个**没有任何文字标签**的裸 `Switch`，
用户不可能知道那是什么。

- [x] 新增 `SettingsStore`（8 项，SharedPreferences 持久化）与设置面板
- [x] 主题三选（跟随系统 / 浅色 / 深色）+ 跟随壁纸取色（API 31+，低版本显示为禁用并说明原因）
- [x] 指针速度 / 滚动速度（离散档位，0.5×–3×）
- [x] 触觉反馈 / 保持常亮 / 断线自动断开 / 断线自动重连
- [x] 真机实测：设备为深色模式下手动切浅色，**状态栏图标同步翻转为深色**
      （`WindowInsetsControllerCompat` 跟随实际生效的主题，而不是系统主题）

### 🟡 47. 本轮无法自动化验证的项

- [ ] **双指手势（双指轻点=右键、双指滚动）**：`adb shell input` 不支持多点触控；
      尝试用 `sendevent` 直接写 `/dev/input/event1` 合成 protocol-B 事件，
      被 SELinux 拒绝（`shell` 虽在 `input` 组，但策略不允许写输入设备），
      `adb root` 在正式版固件上亦不可用。**需人工验证**
- [x] ~~R8 运行时验证~~ —— 本轮已补：release 包装到真机跑通完整流程
      （连接 → 握手 → 触控板 → 图标渲染），冷启动 1290ms，
      对比 debug 包冷启动头几帧 3300ms/帧

### 验证记录（第三轮）

| 项 | 结果 |
|---|---|
| 客户端单元测试 | **79 个全过**（新增 43：`EndpointValidator` 22 + `TextInputTracker` 21） |
| 协议级抓包验证 | 轻点→左键、长按→右键、拖动→精确 140px、鼠标键切换补 `up`、修饰键保持、快捷组合、逐字 `text_input`、退格 —— 全部符合预期 |
| 自动重连 | 杀掉服务端 → 提示条出现 → 重启服务端 → 自动重连成功 |
| 握手 | 真机 ↔ 真服务端 `handshake OK, version=1.1` |
| 构建 | debug + release(R8) + `lintVitalRelease` 全通过 |
| release 产物 | 1.22 MB（R8 前 5.06 MB），签名有效，入口 Activity 未混淆，8 个关键字符串资源存活 |
| 真机渲染 | 深色/浅色、竖屏/横屏、三个标签页、设置面板、重连提示条 —— 均已截图核对 |

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

- 第 30 条：配对令牌明文传输。纯 TCP 无 TLS，局域网内可嗅探；Tailscale 内因
  WireGuard 加密而安全。需要时再上 TLS
- 第 31 条：为 beta1.4 补 tag（可选，该版本从未发布）
- 第 47 条：双指手势需人工验证（`adb` 无法驱动多点触控，`sendevent` 被 SELinux 拦住）

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

---

## 第四轮：服务端状态可观测 + 打包 exe

**触发**：用户要求「不要让它状态未知，最好打包成 exe」。
**约束**：不动协议 —— `docs/protocol.md`、`schema.json` 与客户端一行未改。

导火索是一次真实的误判：我告诉用户「当前无客户端连接」，而实际上有个 12:17 起的
无头服务端还挂在 5800 上，手机正连着它。没有任何地方能查到这件事。

### 🔴 48. 状态只活在窗口里，进程一退就查不到

**位置**：`server.py` 全部、`server_ui.py:104`

无头模式的状态只有 stdout，GUI 的状态只有窗口。想回答「刚才在跑吗、谁连着、
为什么断了」，只能靠 `netstat` 找端口占用者、靠进程列表猜。

- [x] 新增 `server/state.py`：`ServerState` + `ClientRecord`，是运行状态的**唯一数据源**。
      状态文件、GUI 表格、`--status` 输出都从它取数
- [x] 新增 `server/runtime.py`：数据目录、状态文件原子写、单实例、日志轮转、
      端口占用查询、本机地址枚举
- [x] 新增 `server/control.py`：只绑回环的控制通道，让状态可以被「问」而不是靠猜
- [x] 新增 `docs/server-cli.md` 记录这套本机接口

### 🔴 49. 界面上的「在线」是假的

**位置**：`server_ui.py:85-93`（原 `_handle_client`）

`ClientInfo.status` 在**握手之前**就被置成 `connected`。没通过令牌校验的连接
在界面上和正常连接一模一样 —— 用户看到「在线」，实际根本用不了。

- [x] 状态流转改为 `connecting → online | rejected`，断开后 `offline`
- [x] `protocol.Connection` + `emit` 事件钩子（对既有调用方零侵入：
      没有 `emit` 的假对象会被安静跳过，`test_handlers.py` 一个字没改）
- [x] 握手结果上报 `handshake_ok` / `handshake_rejected`（带错误码）
- [x] 断开原因细分：`client_closed` / `connection_reset` / `idle_timeout` /
      `rejected` / `server_stopped` / `error`
- [x] 真机验证：表格同时显示「在线」与「已拒绝（配对令牌错误）」两行

### 🔴 50. 令牌会在每次启动时重新生成（打包成 exe 后）

**位置**：`pairing.py:16-18`（原 `DEFAULT_TOKEN_FILE`）

令牌文件按 `__file__` 定位。onefile exe 的 `__file__` 指向启动时解包、
退出即删的临时目录 —— 照旧写在那里，每次启动都会换一个令牌，用户每次都要
重新配对。这是打包 exe 的**硬阻塞**。

- [x] 数据目录改为 `--data-dir` > `OMNIPAD_DATA_DIR` > `%APPDATA%\OmniPad`（exe）
      / `server/`（源码运行）
- [x] 令牌迁移：exe 首次启动时若数据目录没有令牌、而同目录有，则直接沿用

### 🔴 51. `--port 0` 的端口回填竞态

**位置**：`server.py` 的 `ServerSession.start()`（原写法）

原来等 `self.tcp.server is not None`，而那个字段在 `bind()` **之前**就被赋值了。
窗口极小但真实存在：`getsockname()` 返回 `('0.0.0.0', 0)`，于是 `0` 被写进
状态文件，而且再也不会重读。集成测试第一次跑就中了 3/23。

- [x] `TcpServer.ready` 事件在 `bind + listen` 之后才置位；失败时也置位，
      让等待方去读 `start_error` 而不是傻等超时
- [x] 回归用例连跑 8 次连续启动

### 🔴 52. 进程存活检测不能用 `os.kill(pid, 0)`

Windows 上 `os.kill` 没有信号语义，它会直接 `TerminateProcess` ——
拿它做存活检测等于「查询状态顺便把服务端杀了」。

- [x] 改用 `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject(handle, 0)`；
      打不开时区分 `ERROR_ACCESS_DENIED`（存在但受保护）与真的不存在
- [x] 回归用例：检测一个真实存活的子进程之后，确认它还活着

### 🟠 53. 第二个实例只留一行日志

**位置**：`server.py:44-46`、`server_ui.py:305-310`

第二个实例启动失败时只在日志里写 `failed to start server`，GUI 上表现为
「启动按钮弹回来」，用户完全不知道端口被谁占了。

- [x] 命名互斥体单实例（进程无论怎么退出都由内核释放，不留需要人工清理的锁文件），
      锁名按数据目录区分 —— 测试用临时目录、用户用真实目录，互不干扰
- [x] 第二个实例明确报出「已在运行（PID x，端口 y）」并给出 `--stop` 提示，
      退出码 2；GUI 模式下弹窗问「停止它并接管 / 退出」
- [x] 启动前检查端口占用并**指出占用者的 PID**

### 🟠 54. Tkinter 线程安全

**位置**：`server_ui.py:288-289`（原 `on_change`）

客户端线程直接调 `self.root.after(0, ...)`。Tkinter 不是线程安全的。

- [x] 连接事件与托盘回调只往队列里塞东西，全部界面更新在主线程的轮询里消费

### 🟠 55. 日志面板只增不减

**位置**：`server_ui.py:375-386`（原 `_append_log`）

Tk 的 Text 控件不会自己丢旧行，跑一整天就是几十兆内存。

- [x] `LogBuffer` 环形缓冲，保留最近 2000 行
- [x] 日志同时落盘 `logs\server.log`（1 MB × 3 轮转）

### 🟠 56. 状态栏被日志区挤成一条缝

Tk 的 packer 按**打包顺序**分配空间，日志区 `expand=True` 又排在状态栏前面，
状态栏只剩下几个像素、文字全被裁掉，界面上看不出有这一栏。

- [x] 状态栏先打包；live GUI 测试钉住这一条

### 🟠 57. 客户端表格是一块白板

Windows 原生 ttk 主题会**无视** Treeview 的背景色配置，深色界面里就是一块刺眼的白板。

- [x] 切到 `clam` 主题（同时让 `ttk.Scrollbar` 也认配色）

### 🟡 58. 打包成 exe（第 25 条的正解）

- [x] `scripts/package.ps1` 默认构建两个 exe：
      `OmniPad-Server.exe`（图形子系统，双击即用）与
      `OmniPad-Server-CLI.exe`（控制台子系统，命令行专用）
- [x] 为什么不合成一个：Windows 的子系统标志二选一。图形子系统的程序，
      PowerShell / cmd 不等待它结束，`--status --json | ConvertFrom-Json`
      拿到的是空。试过「控制台子系统 + 按 `GetConsoleProcessList` 判断要不要
      隐藏黑框」，本机实测不可靠（资源管理器双击给的是 2 而不是 1）；
      靠父进程名判断又会被 PyInstaller onefile 的自我重启挡住
- [x] 图标由 `scripts/make_icon.py` 生成（纯标准库 PNG→ICO，4 倍超采样，
      按 alpha 预乘避免边缘渗出黑边）
- [x] exe 版本资源由 `scripts/make_version_info.py` 从 `VERSION` 生成
- [x] **打包可复现**：固定 `SOURCE_DATE_EPOCH` 与 `PYTHONHASHSEED`。
      后者是实测发现的 —— 不固定时 PyInstaller 归档里的模块顺序会变，
      两次构建差出 1512 字节
- [x] CI 装 PyInstaller，校验两个 exe 都在包里，并连打两次比对 SHA256

### 🔴 60. 英文 Windows 上打印中文直接崩（CI 抓到的）

**位置**：`scripts/make_version_info.py`、`scripts/make_icon.py`、`server.py` 的 `_configure_stdio`

CI 的 runner 是英文 Windows，stdout 默认编码是 **cp1252**。打印一句中文就抛
`UnicodeEncodeError` —— 「打包脚本冒烟测试」整步变红，而报错信息本身完全看不出
跟中文有关：

```
File "scripts/make_version_info.py", line 78, in main
  print(f"已写入版本资源 {output}（{version}）")
UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-6
```

同一个坑 `server.py` 里也有一半：原来的 `_configure_stdio` **只处理了「非 tty」**，
也就是说英文 Windows 的终端里跑 `--status` 一样会崩。

- [x] 两个构建脚本固定 UTF-8 + `errors="replace"`
- [x] `server.py` 改为「tty 保留系统代码页但允许降级，管道一律 UTF-8」
- [x] 新增 5 个用例（`test_integration.BuildScriptTest`），用
      `PYTHONIOENCODING=cp1252` 精确复现那个环境 ——
      这个坑靠推理发现不了，只能靠复现
- [x] 顺带钉住 `server/assets/omnipad.ico` 与生成脚本一致（图标是生成物）
- [x] 重新上传发布资产（已发布的 exe 带着这个 bug）

### 🟡 59. 本轮无法自动化验证的项

- [ ] **托盘菜单点击**：`Shell_NotifyIcon` 的创建/删除、结构体尺寸、图标文件
      格式都有测试，但「右键弹出的菜单项真的能点」需要人工确认
- [ ] Windows 11 会把新托盘图标收进「隐藏的图标」里（系统行为，非缺陷）

### 验证记录（第四轮）

| 项 | 结果 |
|---|---|
| 服务端测试 | **263 个全过**（第四轮新增 206：`state` 34 + `runtime` 44 + `control` 25 + `tray` 23 + `integration` 33 + 扩充 47） |
| CI | 新增 PyInstaller 打包步骤、发布包内容校验与「连打两次比对 SHA256」的可复现守卫；`PYTHONIOENCODING=cp1252` 的用例复现了英文 Windows 环境 |
| 端到端 | 真进程 + 真 CLI + 真 socket：状态文件出现、客户端显示 `online`、错误令牌显示 `rejected(AUTH_FAILED)`、`--stop` 优雅退出 |
| exe | 两个 exe 均实测可用；`--version` / `--status`（退出码 3）/ `--stop`（退出码 0）/ `--headless` 全部正确 |
| 可复现 | 连续两次打包 SHA256 完全一致 |
| 时延 | exe 从进程启动到状态文件可用 0.33 s；单次 `--status` 1.02 s（onefile 解包占大头） |
| GUI | 截图核对：在线/已拒绝两行状态、状态栏、深色滚动条、日志配色、托盘图标在通知区域显示正常 |
| 协议 | `docs/` 与客户端**零改动** |

---

## 第五轮：扫码配对 + 1.0.0（issue #1）

**触发**：issue #1「新功能追加，准备正式版，扫清所有不规整的部分」。
**约束**：TCP 协议不动（仍是 v1.1）—— 二维码是一条旁路（屏幕 → 摄像头），
载荷格式单独写进 `docs/qr-payload.md`，按 AGENTS 铁律先文档后代码。

手动配对要在手机上敲地址、端口、8 位令牌。令牌的字母表刻意剔除了易混淆字符，
代价是「看起来像乱码」，手输八位出错率高，错一位得到的还是 `AUTH_FAILED` ——
用户以为自己填对了。

### 🔴 61. 服务端不会画二维码

- [x] `server/qr.py`：**自研的纯标准库 QR 编码器**（字节模式、版本 1–40、
      四档纠错、GF(256) 上的 Reed-Solomon、八种掩码按罚分择优、纯标准库 PNG 输出）。
      为什么不引 `qrcode` / `segno`：发布包里不含 `docs/`，服务端也刻意只用标准库
- [x] 图形界面：白底卡片 + 地址下拉（装了 Tailscale 就有多个地址，
      **选哪个就把哪个写进二维码**）+ 复制链接 / 保存图片 / 放大
- [x] 无头模式：把二维码画在终端里（半块字符，宽高比正好），另存 `pairing_qr.png`
- [x] 托盘菜单加「复制扫码链接」

### 🔴 62. 编码器写错了没人知道（本轮最大的风险）

二维码不像协议字段，错了不会报错 —— 它只会**扫不出来**。所以这里的正确性
不靠「我读懂了规范」，靠三条互相独立的验证：

- [x] 服务端 `test_qr.py` 断言入库的 `pairing-qr-v1.png` 与当前编码器逐字节一致
      （编码器一改就红，逼你重新生成并复验）
- [x] 客户端 `PairingQrTest` 用 **ZXing** 把那张 PNG 解码回来。ZXing 是纯 Java，
      能在普通 JVM 单测里跑 —— 这也是选它而不是 ML Kit 的原因之一
- [x] 开发期扫过 **版本 1–39 × 四档纠错 × 中英文 / emoji / 1200 字节** 的 56 个样本，
      全部解回原文（`sweep.py` 那套办法）
- [x] 过程中真的抓到一个：定位图形画在定时图形**之前**，定时图形把定位图形的边缘
      啃掉，56 个样本**全解不出来**。顺序反过来就好了

### 🟠 63. 相机帧是横躺的

`ImageAnalysis` 给的是**传感器方向**的缓冲区，竖屏手机拿到的通常是横躺 90° 的一帧，
而 ZXing 的定位图形识别不吃旋转 —— 不摆正的话「明明对着二维码却扫不出来」。

- [x] `QrLuminance.rotate`：四个方向 + 行尾填充（`rowStride` 常大于图像宽度）
- [x] `QrLuminanceTest` 9 个用例：小数组逐像素对账，再拿真实 fixture 四个方向各转
      一次交给 ZXing，全部要解回同一个载荷

### 🟠 64. 扫码必须是半屏面板，不是独立页面

- [x] `ScanSheet`：`ModalBottomSheet`（默认半屏，可上拖），里面是 CameraX 预览 +
      取景框四角 + 就地显示解析失败原因
- [x] 连接页「连接」按钮下方新增「扫码连接」入口；没有摄像头的设备给出手输提示
- [x] **扫到之后走「填进输入框 → 调用同一个 submit()」**，而不是直接连接：
      「等同于手动输入」这句话由代码结构保证，而不是靠两处逻辑碰巧一致
- [x] 相机权限：打开面板即申请（用户点「扫码连接」的意图已经很明确），
      拒绝后给出「去系统设置开」或「手动填」两条路

### 🟠 65. 二维码顺手解决了「App 太旧」的误报

旧版 App 连新版服务端时，过去会先过版本检查、再倒在令牌校验上，用户看到
`AUTH_FAILED` 以为令牌错了，真实原因是 App 太旧（第 32 条遗留）。

- [x] 载荷里带协议版本，扫码那一刻就能报「二维码来自 v1.0 的服务端，本机是 v1.1」
- [x] 客户端解析失败有 8 种分类（不是我们的码 / 缺字段 / 重复字段 / 端口 / 地址 /
      令牌 / 版本 / 超长），每种给出不同的「下一步该做什么」

### 🟡 66. 令牌进状态文件

- [x] `qr_payload` 是快照里**唯一**带明文令牌的字段：这个字段的用途就是
      「把地址、端口、令牌整条交给手机」，打码等于把功能去掉。暴露面没有变大 ——
      它和同目录下的 `pairing_token.txt` 是同一份秘密
- [x] **但 `--status` 的人读输出不打印它**：那份输出的既定用途是「贴进 issue
      或聊天窗口问人」（`mask_token` 的注释里写明了）。要完整载荷走 `--status --json`
      或状态文件。状态文件 schema 1 → 2

### 验证记录（第五轮）

| 项 | 结果 |
|---|---|
| 服务端测试 | **325 个全过**（第五轮新增 62：`qr` 46 + `server_ui` 9 + `integration` 5 + `state` 2） |
| 客户端测试 | **111 个全过**（新增 32：`PairingQr` 23 + `QrLuminance` 9） |
| 二维码编码器 | 56 个样本（版本 1–39 × L/M/Q/H × 中英文/emoji/1200B）全部被 ZXing 解回原文 |
| 端到端 | 起真进程：`qr_payload` 解析回来 → 用载荷里的地址/端口/令牌**真的握手成功** |
| release 构建 | `assembleRelease` 通过（R8 + lintVital），APK 1.22 MB → **2.02 MB** |
| 协议 | `docs/protocol.md`、`schema.json` 与协议代码**零改动**（仍是 v1.1） |

仍未完成（本轮未动）：

- 第 30 条：配对令牌明文传输（无 TLS）。二维码把这条又强调了一遍 ——
  二维码里就是明文令牌，所以界面上写了「请不要截图外发」
- 第 31 条：为 beta1.4 补 tag（可选，该版本从未发布）
- 第 47 条：双指手势需人工验证（`adb` 无法驱动多点触控）
- 新增待人工验证：真机上扫码的实际识别率与取景框大小是否合适；
  `ModalBottomSheet` 的半屏高度在不同屏幕上是否需要调整
