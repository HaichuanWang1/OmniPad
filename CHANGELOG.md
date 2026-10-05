# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## v1.0.0-beta1.7 (2026-10-05)

> ⚠️ **与 beta1.6 不兼容。**
> 本版包含握手协议的破坏性变更：`handshake` 新增必填字段 `token`。
> beta1.6 的 APK 连不上本版服务端，反之亦然 —— 升级时必须**同时**替换
> 客户端与服务端。

### 新功能
- 握手增加配对令牌：服务端首次启动生成 8 位令牌，客户端需填入才能连接，
  堵住「任何能连上 5800 端口的人都能控制鼠标键盘」的零认证缺口
- 客户端断线自动重连：按退避序列重试（累计约 30 秒），期间界面显示第几次尝试；
  用户主动断开、以及认证失败与版本不匹配都不会触发重连
- 服务端协议字段校验，非法参数返回 `INVALID_PARAMS` 而不是静默失败
- 服务端强制 `docs/schema.json` 声明的 `additionalProperties`，字段名写错
  （如 `dx` 写成 `dX`）不再被静默忽略
- 客户端 JVM 单元测试（协议编解码与连接层）
- 打包脚本 `scripts/package.ps1`，按发布规范产出 `dist/` 资产
- 新增 CI：服务端测试（windows-latest）+ 客户端编译与单元测试（ubuntu-latest）

### 修复
- 服务端收到非对象 JSON（`[1,2]`、`42`、`"hi"`）时不再因未捕获的
  `AttributeError` 直接断开连接，改为回 `INVALID_PARAMS`
- 服务端 TCP 分片按字节缓冲，修复中文输入损坏
- 服务端扩展键补充 `KEYEVENTF_EXTENDEDKEY` 标志
- 服务端握手失败时真正关闭连接（此前 handler 返回 `False` 被忽略）
- 服务端空闲超时按文档生效，改为每秒唤醒，超时状态改存每个连接本地变量
- 客户端 `handshake_ack` 正确解析 `version` 字段
- 客户端心跳计数器在连接状态变化时重置
- 客户端出站消息改为单一写协程串行发送，保证组合键与移动序列的顺序
- 客户端三个手势检测器合并为单一状态机，消除手势冲突
- 关闭 release lint 门禁，解锁 `assembleRelease`

### 安全
- 签名密钥与口令移出版本控制（`client/keystore.properties` 不入库）

### 重构
- 服务端抽出 `handlers.py`，消除两份重复的处理器
- 客户端心跳与超时判定下沉到连接层
- 客户端文案全部外提到 `strings.xml`

### 文档与工程
- 版本号统一到仓库根目录 `VERSION`，Gradle 与打包脚本共用，消除命名漂移
- 修正 README 中不存在 `server_ui.exe` 的说明，补上配对令牌的使用步骤
- `dist/` 发布产物不再入库，改由 GitHub Release 分发
- 补上缺失的 `gradlew` 与 `.gitattributes`，修复 CI 客户端任务

## v1.0.0-beta1.6 (2026-07-17)

### 新功能
- 安卓端与 Server 端底部添加作者「超重氢」及 GitHub 链接
- Server 端设备列表改为黑底统一风格

## v1.0.0-beta1.5 (2026-07-16)

> 注：本版 Release **只上传了 APK，漏了服务端 zip**，该版本没有可下载的服务端包。

### 新功能
- 键盘修饰键 toggle（Ctrl / Shift / Alt / Win 可组合）
- 鼠标按键支持 down / up 切换，可实现按住拖拽
- 连接丢失时自动断开的开关
- 应用图标：深色背景 + 蓝色触控板轮廓 + 白色光标箭头，带层次与光影

### 修复
- 服务端心跳超时断连
- 服务端新增 `ACTION_FAILED` 错误码
- 服务端支持 BMP 之外的 Unicode 字符注入
- 客户端滚动方向与步长修正
- 服务端空闲超时改用每个连接局部变量，移除全局 `conn_heartbeat`
- 正式 release 签名配置

> 注：本版与 beta1.4 之间曾引入「合并手势处理器」和「手势从第 1 像素开始跟踪」
> 两项改动，随后在 `7f80056` 中回退，故未列入。

## v1.0.0-beta1.4 (2026-07-16)

### 新功能
- 应用图标（深色背景 + 蓝色触控板轮廓 + 白色光标箭头）
- 图标重新设计，增加层次感与光影效果

> 注：本版打了版本提交（`2a34a0c`）并构建了 APK，但**从未发布 GitHub Release，
> 也没有打标签**，随后被 beta1.5 取代。

## v1.0.0-beta1.3 (2026-07-16)

### 修复
- 双指滚动只响应多指手势，单指拖动不再误触发滚动

## v1.0.0-beta1.2 (2026-07-16)

### 新功能
- 触控板双指滚动手势
- 服务端端口输入实时更新连接信息

### 修复
- 历史记录点击无反应（AssistChip 消费事件，改用 Surface + combinedClickable）
- 连接失败后状态卡 FAILED，无法重新连接
- 点击连接时立即保存历史记录，不再依赖异步 onConnected 回调
- 服务端 Windows 上 stop() 先 shutdown 再 close 确保线程退出
- 服务端闪退（Frame padx/pady 参数位置错误、port_entry 初始化时序）
- 'BS' 按钮改为中文 '退格'

## v1.0.0-beta1.1 (2026-07-16)

### 新功能
- 服务端 UI 显示客户端列表（在线/离线），保留断开记录
- 安卓端历史连接设备，点击即可连接

### 修复
- 服务端停止时关闭所有客户端连接

## v1.0.0-beta1 (2026-07-15)

首个测试版发布。

### 功能
- 触控板：拖动移动鼠标、点击/长按左右键
- 文字输入：支持中文 Unicode 注入
- 键盘功能键：Enter/Tab/Esc/方向键等
- TCP_NODELAY 优化，低延迟操控

### 文件说明
- `OmniPad-v1.0.0-beta1.apk` — Android 客户端安装包
- `omnipad-server-v1.0.0-beta1.zip` — Windows 服务端
