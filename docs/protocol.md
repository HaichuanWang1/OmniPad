# OmniPad TCP 协议文档 v1.0

## 概述

OmniPad 使用 **TCP + JSON Lines** 协议进行通信。每条消息为单行 UTF-8 JSON，以 `\n`（0x0A）分隔。

- 默认端口：**5800**
- 编码：UTF-8
- 分隔符：`\n`（LF）

---

## 握手流程

1. Client 连接 Server 后，立即发送握手请求。
2. Server 校验版本号，回复握手确认或错误。
3. 握手成功后，双方进入命令/响应循环。

```
Client → Server:
{"type":"handshake","version":"1.0"}

Server → Client:
{"type":"handshake_ack","version":"1.0"}
```

若版本不匹配，Server 回复错误并关闭连接：

```
Server → Client:
{"type":"error","code":"VERSION_MISMATCH","message":"expected 1.0 got x.y"}
```

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
`delta`：> 0 向上滚动，< 0 向下滚动。

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
  常用值：
  - 功能键：`"enter"`, `"tab"`, `"escape"`, `"backspace"`, `"space"`
  - 修饰键：`"shift"`, `"ctrl"`, `"alt"`, `"win"`
  - 方向键：`"up"`, `"down"`, `"left"`, `"right"`
  - F 键：`"f1"` ~ `"f24"`
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
| `UNKNOWN_TYPE` | 未知消息类型 |
| `INVALID_PARAMS` | 参数无效 |
| `ACTION_FAILED` | 执行操作失败 |

---

## 完整消息示例

```
{"type":"handshake","version":"1.0"}
{"type":"handshake_ack","version":"1.0"}
{"type":"mouse_move","dx":100,"dy":50}
{"type":"mouse_click","button":"left","action":"click"}
{"type":"scroll","delta":-3}
{"type":"text_input","text":"你好，世界！"}
{"type":"keyboard","key":"enter","action":"press"}
{"type":"heartbeat"}
{"type":"heartbeat_ack"}
```
