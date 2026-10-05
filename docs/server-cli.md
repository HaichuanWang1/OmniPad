# OmniPad 服务端命令行与运行状态

> 这是**服务端的本机接口**，不是手机 ↔ 电脑的通信协议。后者见 [protocol.md](protocol.md)。
> 两者互不影响：本文件里的东西怎么改都不会动到协议版本。

## 为什么有这个东西

服务端曾经把状态只放在终端输出或 Tk 窗口里。进程一退，就再也回答不了三个问题：

- 它刚才在跑吗？
- 谁连着？
- 为什么断了？

后果不是抽象的：排查时只能靠 `netstat` 找端口占用者、靠 `pidof` 猜进程，然后
发现一个没人记得的后台服务端还挂着，手机正连着它。所以状态必须落盘，而且必须
能被**另一个进程**问出来。

现在服务端无论跑在哪种模式（图形界面 / 无头），都会：

1. 在数据目录写 `server_status.json`（原子写，每 5 秒刷新）
2. 开一个只绑 `127.0.0.1` 的控制通道，让状态可以被「问」而不是靠猜
3. 用命名互斥体保证同一个数据目录只有一个实例

## 命令行

发布包里有**两个**可执行文件：

| 文件 | 子系统 | 用途 |
|---|---|---|
| `OmniPad-Server.exe` | 图形 | 双击即用。**命令行也能用**，但见下方说明 |
| `OmniPad-Server-CLI.exe` | 控制台 | 脚本与命令行专用 |

为什么不合成一个：Windows 的子系统标志二选一，而两种用法对它的要求正好相反。
图形子系统的程序，PowerShell / cmd **不会等待它结束** ——

```powershell
# 这样拿不到任何东西：管道在程序写出输出之前就已经收尾了
.\OmniPad-Server.exe --status --json | ConvertFrom-Json
```

控制台子系统的程序则会弹出一个黑框。试过「控制台子系统 + 启动时按
`GetConsoleProcessList` 判断要不要隐藏黑框」，在本机实测不可靠（资源管理器双击
给出的是 2 而不是 1）。与其赌一个启发式，不如老实地打两个 exe。

`OmniPad-Server-CLI.exe` 排除了 tkinter（省约 3 MB），因此**打不开图形界面**；
无参数运行时会明确告诉你该用哪一个。

### 用法

```
OmniPad-Server-CLI.exe                     图形界面（等同于 GUI exe）
OmniPad-Server-CLI.exe --headless          无头模式，不开窗口
OmniPad-Server-CLI.exe --status            打印运行状态
OmniPad-Server-CLI.exe --status --json     机器可读的状态
OmniPad-Server-CLI.exe --stop              优雅停止正在运行的实例
OmniPad-Server-CLI.exe --version           打印版本号
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `5800` | 监听端口；填 `0` 表示由内核分配随机端口 |
| `--token` | 数据目录里的令牌 | 覆盖配对令牌，不写回文件 |
| `--data-dir` | 见下 | 数据目录 |
| `--json` | 关 | 配合 `--status` 输出 JSON |

### 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 启动或停止失败（端口被占用、控制通道起不来等） |
| `2` | 已在运行（同一数据目录已有实例） |
| `3` | 未在运行（没有状态文件，或状态文件指向的进程已消失） |

`--status` 用 `0` / `3` 表达「在跑 / 没在跑」，可以直接当条件用。

### 示例

```powershell
# 是不是在跑？谁连着？
.\OmniPad-Server-CLI.exe --status

# 脚本里用
$state = .\OmniPad-Server-CLI.exe --status --json | ConvertFrom-Json
if ($state.live) { $state.status.clients | Format-Table addr, state_text, messages }

# 停掉
.\OmniPad-Server-CLI.exe --stop

# 临时换端口跑一个无头实例
.\OmniPad-Server-CLI.exe --headless --port 5801
```

## 数据目录

| 运行方式 | 数据目录 |
|---|---|
| `OmniPad-Server.exe` / `OmniPad-Server-CLI.exe` | `%APPDATA%\OmniPad` |
| `python server.py`（源码运行） | `server/` |

可以用 `--data-dir` 或环境变量 `OMNIPAD_DATA_DIR` 覆盖。

exe 之所以不写在自己旁边：它可能被放在 `C:\Program Files\` 这类只读位置。
从源码运行时沿用脚本目录，是为了让老用户的令牌不因为升级而搬家。

目录内容：

| 文件 | 说明 |
|---|---|
| `pairing_token.txt` | 配对令牌。删除即可重新生成（手机端需要重新配对） |
| `server_status.json` | 运行状态快照 |
| `logs\server.log` | 日志，1 MB × 3 轮转 |

**令牌迁移**：exe 首次启动时，如果数据目录里没有令牌、而 exe 同目录有
`pairing_token.txt`，会直接沿用后者。这样从「`python server_ui.py`」换到 exe 的
用户不会莫名其妙被要求重新配对。

## 状态文件

`server_status.json`，UTF-8，每次都是**先写临时文件再 `os.replace`** ——
读取方永远看不到写了一半的 JSON。

```json
{
  "schema": 1,
  "pid": 6152,
  "mode": "gui",
  "running": true,
  "started_at": "2026-10-05T13:10:52",
  "updated_at": "2026-10-05T13:11:12",
  "host": "0.0.0.0",
  "port": 5801,
  "protocol_version": "1.1",
  "token_masked": "DN2E****",
  "data_dir": "C:\\Users\\me\\AppData\\Roaming\\OmniPad",
  "log_file": "...\\logs\\server.log",
  "online_count": 1,
  "control": { "host": "127.0.0.1", "port": 50834 },
  "clients": [
    {
      "addr": "192.168.1.20:51234",
      "peer": "192.168.1.20",
      "port": 51234,
      "state": "online",
      "state_text": "在线",
      "reason": null,
      "connected_at": "2026-10-05T13:10:58",
      "authenticated_at": "2026-10-05T13:10:58",
      "last_message_at": "2026-10-05T13:11:11",
      "disconnected_at": null,
      "messages": 143
    }
  ]
}
```

### 字段

| 字段 | 说明 |
|---|---|
| `schema` | 状态文件的格式版本。字段增删时递增，读取方据此判断能否解析 |
| `pid` | 服务端进程号。**判断文件是否过期就看它** |
| `mode` | `gui` / `headless` |
| `running` | 是否已开始监听 |
| `port` | **实际**监听的端口。`--port 0` 时这里是被内核分配的真实端口 |
| `token_masked` | 令牌打码，只留前 4 位。状态文件会被贴进 issue 和聊天窗口 |
| `control` | 控制通道地址，见下节 |
| `clients` | 连接记录，最多 200 条，最新的在后 |

### 客户端状态

一次连接的状态流转：

```
connecting ──握手成功──> online ──断开──> offline
     └──────握手失败──> rejected
```

**只有 `online` 代表「这台手机现在真的能控制电脑」。** `connecting` 是 TCP 连上
但还没握手 —— 改造前这两种情况在界面上都显示「在线」，所以没通过令牌校验的连接
看起来和正常连接一模一样。

`reason` 字段：

| state | reason | 含义 |
|---|---|---|
| `rejected` | `AUTH_FAILED` | 配对令牌错误 |
| `rejected` | `VERSION_MISMATCH` | 协议版本不匹配（App 太旧） |
| `offline` | `client_closed` | 客户端主动断开 |
| `offline` | `connection_reset` | 对端异常断开（进程被杀等） |
| `offline` | `idle_timeout` | 空闲超时（15 秒没有消息） |
| `offline` | `server_stopped` | 服务端停止 |
| `offline` | `error` | 连接出错 |

### 写入时机

- 启动、停止
- 客户端连接、握手成功 / 被拒、断开
- 每 5 秒一次心跳（刷新 `updated_at` 与 `last_message_at`）

**收到消息不落盘** —— 鼠标拖动每秒几十条，每次都写盘会把磁盘打满。

### 怎么判断文件是否过期

文件在、进程可能已经没了。**用 `pid` 去查进程是否存活**，不要看 `updated_at`：

```powershell
$s = Get-Content "$env:APPDATA\OmniPad\server_status.json" -Raw | ConvertFrom-Json
if (Get-Process -Id $s.pid -ErrorAction SilentlyContinue) { '在跑' } else { '文件是过期的' }
```

`--status` 做的就是这件事：先查进程存活，活着再去问控制通道要实时状态。

> ⚠️ 在 Windows 上**不要**用 `os.kill(pid, 0)` 做存活检测 —— 它没有信号语义，
> 会直接 `TerminateProcess`，等于「查询状态顺便把服务端杀了」。服务端自己用的是
> `OpenProcess` + `WaitForSingleObject`。

## 控制通道

状态文件是一张可能过期的快照；控制通道是**活着的进程本人**给出的回答。

- 只绑 `127.0.0.1`，端口由内核随机分配后写进状态文件
- 行分隔 JSON，一行一个请求、一行一个响应
- 不额外校验身份：能读到状态文件的本机进程本来就能 `taskkill` 掉服务端

| 命令 | 响应 |
|---|---|
| `{"cmd":"ping"}` | `{"ok":true,"cmd":"ping","pid":<pid>}` |
| `{"cmd":"status"}` | `{"ok":true,"cmd":"status","status":{...}}`（就是上面那份快照，但**现取**） |
| `{"cmd":"stop"}` | 先回执 `{"ok":true,...}`，**然后**才停 |

`stop` 一定先回执再停 —— 反过来的话调用方永远等不到那句「好」。

`--stop` 优先走控制通道；控制通道联系不上时回退到 `taskkill /PID <pid> /T /F`。

## 单实例

同一**数据目录**只允许一个实例，用 Windows 命名互斥体实现（进程无论怎么退出，
包括崩溃和被 `taskkill`，都由内核自动释放，不会留下要人工清理的锁文件）。

第二个实例会明确告诉你它被谁挡住了：

```
OmniPad 服务端已经在运行（PID 6152，端口 5800）。
要接管的话先执行：OmniPad-Server-CLI.exe --stop
```

图形界面模式下会弹窗问「停止它并接管 / 退出」。

不同数据目录各有一把锁 —— 测试用临时目录、用户用真实目录，两者互不干扰。

## 端口占用

启动前会检查端口，并**指出占用者的 PID**：

```
端口 5800 已被 PID 2832 占用，无法启动。
如果是上一个 OmniPad 服务端残留，执行 OmniPad-Server-CLI.exe --stop 即可；
否则请换一个端口：--port 5801
```

「启动失败」这四个字对用户毫无价值，他需要知道的是「谁占着」。
