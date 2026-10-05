package com.omnipad.client.network

/**
 * 需要提示用户的连接事件。
 *
 * 连接层不持有任何界面文案，只产出这里的事件；具体显示什么文字由 UI 层
 * 映射到字符串资源。这样文案可以集中管理，也能被翻译。
 */
sealed interface ConnectionNotice {

    /** 服务端主动回报的错误，message 来自服务端。 */
    data class ServerError(val message: String) : ConnectionNotice

    /** 配对令牌不正确。 */
    data object AuthFailed : ConnectionNotice

    /** 协议版本不匹配。 */
    data object VersionMismatch : ConnectionNotice

    /** 服务端拒绝了握手，但没给出可识别的错误码。 */
    data object HandshakeFailed : ConnectionNotice

    /** 连上了但服务端没有回复握手确认。 */
    data object ServerNoResponse : ConnectionNotice

    /** 建立连接本身失败，detail 为底层异常信息。 */
    data class ConnectFailed(val detail: String?) : ConnectionNotice

    /** 连续丢失心跳，判定连接已断。 */
    data object HeartbeatTimeout : ConnectionNotice
}
