package com.omnipad.client.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import com.omnipad.client.R
import com.omnipad.client.network.ConnectionNotice
import com.omnipad.client.network.QrError

/**
 * 一条给用户看的连接问题说明。
 *
 * [hint] 存在的意义：只说「配对失败」用户并不知道下一步该做什么。原先这些信息
 * 只以一个 3 秒就消失的 Toast 出现，等用户低头看手机时已经没了，界面上只剩一句
 * 通用的「请检查地址、端口和配对令牌」—— 分不清到底是令牌错了还是电脑没开机。
 */
data class NoticeText(val title: String, val hint: String)

/** 完整说明，用于连接页常驻的错误卡片。 */
@Composable
fun noticeText(notice: ConnectionNotice): NoticeText = when (notice) {
    ConnectionNotice.AuthFailed -> NoticeText(
        stringResource(R.string.failure_auth_failed),
        stringResource(R.string.failure_auth_failed_hint),
    )

    ConnectionNotice.VersionMismatch -> NoticeText(
        stringResource(R.string.failure_version_mismatch),
        stringResource(R.string.failure_version_mismatch_hint),
    )

    ConnectionNotice.HandshakeFailed -> NoticeText(
        stringResource(R.string.failure_handshake_failed),
        stringResource(R.string.failure_handshake_failed_hint),
    )

    ConnectionNotice.ServerNoResponse -> NoticeText(
        stringResource(R.string.failure_server_no_response),
        stringResource(R.string.failure_server_no_response_hint),
    )

    is ConnectionNotice.ConnectFailed -> NoticeText(
        stringResource(R.string.failure_connect_failed),
        // 底层异常信息（如 Connection refused）对排查有用，放在提示行里而不是当标题
        notice.detail?.let { "$it · ${stringResource(R.string.failure_connect_failed_hint)}" }
            ?: stringResource(R.string.failure_connect_failed_hint),
    )

    ConnectionNotice.HeartbeatTimeout -> NoticeText(
        stringResource(R.string.failure_heartbeat_timeout),
        stringResource(R.string.failure_heartbeat_timeout_hint),
    )

    is ConnectionNotice.ServerError -> NoticeText(
        stringResource(R.string.failure_server_error),
        notice.message,
    )
}

/** 单行文案，用于 Snackbar 这种空间有限的地方。 */
@Composable
fun noticeShortText(notice: ConnectionNotice): String = when (notice) {
    is ConnectionNotice.ServerError -> stringResource(R.string.error_server, notice.message)
    ConnectionNotice.AuthFailed -> stringResource(R.string.error_auth_failed)
    ConnectionNotice.VersionMismatch -> stringResource(R.string.error_version_mismatch)
    ConnectionNotice.HandshakeFailed -> stringResource(R.string.error_handshake_failed)
    ConnectionNotice.ServerNoResponse -> stringResource(R.string.error_server_no_response)
    is ConnectionNotice.ConnectFailed -> notice.detail?.let {
        stringResource(R.string.error_connect_failed_detail, it)
    } ?: stringResource(R.string.error_connect_failed)

    ConnectionNotice.HeartbeatTimeout -> stringResource(R.string.error_heartbeat_timeout)
}

/**
 * 二维码解析失败 → 给用户看的两行话。
 *
 * 分类到这里才变成文字：网络层只产出 [QrError]，文案与语言都在这一层决定。
 * 「扫到别人的二维码」和「App 太旧」要说完全不同的话，笼统一句「二维码无效」
 * 等于什么都没说。
 */
@Composable
fun qrErrorText(error: QrError): NoticeText = when (error) {
    QrError.NotOmniPadLink -> NoticeText(
        stringResource(R.string.qr_error_not_omnipad),
        stringResource(R.string.qr_error_not_omnipad_hint),
    )

    is QrError.MissingField -> NoticeText(
        stringResource(R.string.qr_error_missing_field),
        stringResource(R.string.qr_error_missing_field_hint, error.field),
    )

    is QrError.DuplicateField -> NoticeText(
        stringResource(R.string.qr_error_duplicate_field, error.field),
        stringResource(R.string.qr_error_duplicate_field_hint),
    )

    QrError.BadPort -> NoticeText(
        stringResource(R.string.qr_error_bad_port),
        stringResource(R.string.qr_error_bad_port_hint),
    )

    is QrError.BadHost -> NoticeText(
        stringResource(R.string.qr_error_bad_host),
        stringResource(R.string.qr_error_bad_host_hint),
    )

    QrError.BadToken -> NoticeText(
        stringResource(R.string.qr_error_bad_token),
        stringResource(R.string.qr_error_bad_token_hint),
    )

    is QrError.VersionMismatch -> NoticeText(
        stringResource(
            R.string.qr_error_version_mismatch,
            error.found,
            error.expected,
        ),
        stringResource(R.string.qr_error_version_mismatch_hint),
    )

    QrError.TooLong -> NoticeText(
        stringResource(R.string.qr_error_too_long),
        stringResource(R.string.qr_error_too_long_hint),
    )
}
