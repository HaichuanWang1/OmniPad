# OmniPad TCP 协议文档 v1.1

## 概述

OmniPad 使用 **TCP + JSON Lines** 协议进行通信。每条消息为单行 UTF-8 JSON，以 `\n`（0x0A）分隔。

- 默认端口：**5800**
- 编码：UTF-8
- 分隔符：`\n`（LF）

---

## 消息格式约束

每条消息必须是 JSON **对象**（不是数组、数字或字符串），且只允许出现该消息类型
定义的字段。多出的字段一律返回 `INVALID_PARAMS`，不会被静默忽略。

这条约束的意义在于：字段名拼错（如把 `dx` 写成 `dX`）时会明确报错，而不是
「取不到就用默认值 0」，让客户端表现成「拖动没反应、日志却毫无异常」这种难查的问题。

完整字段清单见 [schema.json](schema.json)，每个类型的 `additionalProperties` 均为 `false`。

---

## 协议版本与兼容性

当前版本 **1.1**。双方必须使用**完全一致**的版本；Server 在握手时先校验版本、再校验令牌。

版本号在仓库里出现三处，改动时必须同步：

| 位置 | 形态 |
|---|---|
| `server/handlers.py` | `PROTOCOL_VERSION` |
| `client/app/src/main/java/com/omnipad/client/network/Protocol.kt` | `Handshake.version` 的默认值 |
| 本文档标题与 `docs/schema.json` 的 `description` | 文字 |

三处的一致性由 `server/test_handlers.py` 的 `ProtocolVersionConformanceTest` 自动校验 ——
跨语言没法共享常量，但漂移可以在测试里拦住。

递增规则：**只要线上格式有变就递增**（新增必填字段、改字段语义、改取值）。
由于双方要求版本完全一致，主次版本目前不做区分。1.0 → 1.1 就是因为 `token` 变成必填。

版本不匹配时 Server 回 `VERSION_MISMATCH` 并断开，客户端应提示用户更新 App。

> 为什么不做版本协商：双端始终一起发布，没有第三方客户端。协商（交换各自支持的
> 版本列表再取交集）会引入真实的协议面，而收益只多版本共存时才体现。真需要再加不迟。

---

## 握手流程

1. Client 连接 Server 后，立即发送握手请求，携带配对令牌。
2. Server 先校验版本号，再校验令牌；全部通过才回复握手确认。
3. 握手成功后，双方进入命令/响应循环。

```
Client → Server:
{"type":"handshake","version":"1.1","token":"K7M2P9QR"}

Server → Client:
{"type":"handshake_ack","version":"1.1"}
```

### 配对令牌

Server 首次启动时随机生成一个 **8 位令牌**，持久化到 `server/pairing_token.txt`
（不入库），并在 GUI 顶部与无头模式日志中显示。Client 必须提交相同令牌。

- 令牌字母表为 `A-Z` 与 `2-9`，已剔除易混淆的 `I` `O` `0` `1`
- 比较时大小写不敏感，两侧都会 trim 后转大写
- 无头模式可用 `--token` 覆盖，便于脚本化联调

若版本不匹配，Server 回复错误并关闭连接：

```
Server → Client:
{"type":"error","code":"VERSION_MISMATCH","message":"expected 1.1 got x.y"}
```

若令牌不正确，Server 同样回复错误并关闭连接：

```
Server → Client:
{"type":"error","code":"AUTH_FAILED","message":"invalid pairing token"}
```

### 扫码配对（旁路，不属于本协议）

地址、端口、令牌可以打包成一张二维码，由服务端显示、客户端扫。
这条通路**不经过 TCP**，因此载荷格式单独定义在 [qr-payload.md](qr-payload.md)，
本文档不重复描述。要点只有一条：二维码里带着协议版本，客户端在扫码阶段就能
发现版本不匹配，不必等到握手才拿到 `VERSION_MISMATCH`。

---

## 消息类型

### 鼠标控制

#### 鼠标相对移动
```
{"type":"mouse_move","dx":<int>,"dy":<int>}
```
`dx` / `dy`：相对位移的像素值（可为负）。

#### 鼠标点击
```
{"type":"mouse_click","button":"<button>","action":"<action>"}
```
- `button`：`"left"` | `"right"` | `"middle"`
- `action`：`"down"` | `"up"` | `"click"`（按下后立即释放）

#### 鼠标滚轮
```
{"type":"scroll","delta":<int>}
```
`delta`：滚轮**格数**，> 0 向上滚动，< 0 向下滚动。
Server 收到后乘以 `WHEEL_DELTA`（120）换算成 Windows 的滚轮单位，
所以这里传的是「格」而不是原始增量。

---

### 文字输入（核心功能）

```
{"type":"text_input","text":"<string>"}
```
`text`：要输入的字符串，支持任意 Unicode 字符（中文、英文、标点等）。
Server 端使用 `SendInput` + `KEYEVENTF_UNICODE` 逐个字符注入。

示例：
```
{"type":"text_input","text":"你好，世界！"}
{"type":"text_input","text":"Hello, OmniPad!"}
```

---

### 键盘控制（特殊按键）

```
{"type":"keyboard","key":"<key_name>","action":"<action>"}
```
- `key`：使用 Windows 虚拟键码（VK）的字符串名称。仅用于特殊按键，**常规文字输入请使用 `text_input`**。
  Server 端认可以下名称（与 `server/input_controller.py` 的 `VK_MAP` 保持一致）：

  | 分类 | 取值 |
  |------|------|
  | 功能键 | `enter` `tab` `escape` `backspace` `space` |
  | 修饰键 | `shift` `ctrl` `alt` `win` |
  | 方向键 | `up` `down` `left` `right` |
  | 编辑键 | `insert` `delete` `home` `end` `page_up` `page_down` |
  | 锁定/系统键 | `caps_lock` `num_lock` `scroll_lock` `pause` `print_screen` |
  | F 键 | `f1` ~ `f24` |
  | 单字符 | 任意单个字符，如 `"a"` `"1"`（按大写形式的码位取 VK，大小写不敏感） |

  名称大小写不敏感。无法识别的名称会返回 `INVALID_PARAMS`。
- `action`：`"down"` | `"up"` | `"press"`（按下后立即释放）

组合键示例（由 Client 拆分为多条消息发送）：
```
{"type":"keyboard","key":"ctrl","action":"down"}
{"type":"keyboard","key":"c","action":"press"}
{"type":"keyboard","key":"ctrl","action":"up"}
```

---

### 心跳

Client 每 **5 秒** 发送一次心跳。若 Server 连续 15 秒未收到心跳，可认为连接已断开。

```
Client → Server:
{"type":"heartbeat"}

Server → Client:
{"type":"heartbeat_ack"}
```

---

### 错误响应

```
{"type":"error","code":"<code>","message":"<message>"}
```

| code | 说明 |
|------|------|
| `VERSION_MISMATCH` | 协议版本不匹配 |
| `AUTH_FAILED` | 配对令牌不正确 |
| `UNKNOWN_TYPE` | 未知消息类型 |
| `INVALID_PARAMS` | 参数无效 |
| `ACTION_FAILED` | 执行操作失败 |

---

## 完整消息示例

```
{"type":"handshake","version":"1.1","token":"K7M2P9QR"}
{"type":"handshake_ack","version":"1.1"}
{"type":"mouse_move","dx":100,"dy":50}
{"type":"mouse_click","button":"left","action":"click"}
{"type":"scroll","delta":-3}
{"type":"text_input","text":"你好，世界！"}
{"type":"keyboard","key":"enter","action":"press"}
{"type":"heartbeat"}
{"type":"heartbeat_ack"}
```
