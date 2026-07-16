# Changelog

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
