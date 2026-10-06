package com.omnipad.client.network

/**
 * 二维码载荷的解析。
 *
 * 契约在 `docs/qr-payload.md`，服务端的实现在 `server/qr.py`。这里刻意**不做**
 * 自己的字段校验：解析出 `host`/`port`/`token` 之后一律交给 [EndpointValidator]
 * 过一遍 —— 那是手输路径用的同一份规则，「扫码等同于手动输入」这句话才有保证，
 * 而不是两套规则各自漂移。
 *
 * 解析失败必须给出**分类**：扫到别人的二维码、扫到旧版 App 生成的二维码、
 * 扫到一张被压缩糊掉的图，对用户来说该说的话完全不同。
 */
sealed interface QrError {
    /** scheme 或动作不对 —— 这不是 OmniPad 的连接二维码。 */
    data object NotOmniPadLink : QrError

    /** 必填字段缺失或为空。 */
    data class MissingField(val field: String) : QrError

    /** 同一个字段出现两次。歧义不猜，让用户重扫。 */
    data class DuplicateField(val field: String) : QrError

    /** 端口不是数字或越界。 */
    data object BadPort : QrError

    /** 地址不合法（与手输同一套规则，具体原因带在 [error] 里）。 */
    data class BadHost(val error: EndpointError) : QrError

    /** 令牌为空。 */
    data object BadToken : QrError

    /**
     * 协议版本不匹配。
     *
     * 这是二维码顺带解决的一个老问题：旧版 App 连新版服务端时，过去会先过版本检查、
     * 再倒在令牌校验上，用户看到 `AUTH_FAILED` 以为令牌填错了，真实原因是 App 太旧。
     * 扫码阶段就能发现，于是可以直说「请更新 App」。
     */
    data class VersionMismatch(val found: String, val expected: String) : QrError

    /** 内容离谱地长，对面根本不是 OmniPad。 */
    data object TooLong : QrError
}

/** 二维码里带回来的连接参数。`name` 只用于显示，不参与连接。 */
data class QrPairing(
    val host: String,
    val port: Int,
    val token: String,
    val name: String?,
)

sealed interface QrScan {
    data class Ok(val pairing: QrPairing) : QrScan
    data class Invalid(val error: QrError) : QrScan
}

object PairingQr {

    const val SCHEME = "omnipad"
    const val ACTION = "pair"

    /** 纯防御性上限：正常载荷只有几十字节。 */
    const val MAX_SCAN_BYTES = 2048

    private val REQUIRED_FIELDS = listOf("v", "host", "port", "token")

    /**
     * 解析一段二维码文本。
     *
     * [expectedVersion] 传 App 自己的协议版本（`Handshake().version`）。
     */
    fun parse(text: String, expectedVersion: String): QrScan {
        val trimmed = text.trim()
        if (trimmed.toByteArray(Charsets.UTF_8).size > MAX_SCAN_BYTES) {
            return QrScan.Invalid(QrError.TooLong)
        }

        val separator = trimmed.indexOf("://")
        if (separator <= 0) return QrScan.Invalid(QrError.NotOmniPadLink)
        if (!trimmed.substring(0, separator).equals(SCHEME, ignoreCase = true)) {
            return QrScan.Invalid(QrError.NotOmniPadLink)
        }

        val rest = trimmed.substring(separator + 3)
        val actionEnd = rest.indexOf('?')
        val action = if (actionEnd < 0) rest else rest.substring(0, actionEnd)
        if (!action.trim().trim('/').equals(ACTION, ignoreCase = true)) {
            return QrScan.Invalid(QrError.NotOmniPadLink)
        }
        if (actionEnd < 0) return QrScan.Invalid(QrError.MissingField("v"))

        // '#' 之后是片段，按 RFC 3986 由客户端处理，这里直接丢掉
        val query = rest.substring(actionEnd + 1).substringBefore('#')

        val params = mutableMapOf<String, String>()
        for (chunk in query.split('&')) {
            if (chunk.isEmpty()) continue
            val key = chunk.substringBefore('=')
            if (params.containsKey(key)) {
                return QrScan.Invalid(QrError.DuplicateField(key))
            }
            // 值里出现 '=' 是合法的，只按**第一个** '=' 切
            val rawValue = if (chunk.contains('=')) chunk.substringAfter('=') else ""
            params[key] = percentDecode(rawValue)
                ?: return QrScan.Invalid(QrError.NotOmniPadLink)
        }

        for (field in REQUIRED_FIELDS) {
            if (params[field].isNullOrEmpty()) {
                return QrScan.Invalid(QrError.MissingField(field))
            }
        }

        val version = params.getValue("v")
        if (version != expectedVersion) {
            return QrScan.Invalid(QrError.VersionMismatch(version, expectedVersion))
        }

        // 剩下的校验一律复用上手输那一套，两边不可能给出不同的结论
        val portText = params.getValue("port")
        return when (
            val check = EndpointValidator.validate(
                hostInput = params.getValue("host"),
                portInput = portText,
                tokenInput = params.getValue("token"),
            )
        ) {
            is EndpointCheck.Ok -> QrScan.Ok(
                QrPairing(
                    host = check.endpoint.host,
                    port = check.endpoint.port,
                    token = check.endpoint.token,
                    name = params["name"]?.takeIf { it.isNotEmpty() },
                )
            )

            is EndpointCheck.Invalid -> QrScan.Invalid(
                when (check.error) {
                    EndpointError.PortNotANumber,
                    EndpointError.PortOutOfRange,
                    -> QrError.BadPort

                    EndpointError.TokenBlank -> QrError.BadToken

                    // 端口框里本来就有值，所以「地址里带了端口」不会走到 PortNotANumber；
                    // 剩下的主机类错误原样带出去
                    else -> QrError.BadHost(check.error)
                }
            )
        }
    }

    /**
     * 百分号解码。非法转义返回 null。
     *
     * 与 `server/qr.py` 的约定一致：**不把 `+` 当成空格**。这里是本项目唯一一处
     * 刻意偏离 `application/x-www-form-urlencoded` 的地方 —— 一旦还原，令牌里
     * 合法的 `+` 就会被静默改写。
     */
    internal fun percentDecode(text: String): String? {
        val bytes = ArrayList<Byte>(text.length)
        var index = 0
        while (index < text.length) {
            val char = text[index]
            if (char == '%') {
                if (index + 2 >= text.length) return null
                val value = text.substring(index + 1, index + 3).toIntOrNull(16)
                    ?: return null
                bytes.add(value.toByte())
                index += 3
                continue
            }
            // 字面量非 ASCII 字符按 UTF-8 收下：生成方不该这么写，但收下比报错友好
            bytes.addAll(char.toString().toByteArray(Charsets.UTF_8).toList())
            index += 1
        }
        val decoded = String(bytes.toByteArray(), Charsets.UTF_8)
        // 非法 UTF-8 会被替换成 U+FFFD，那说明这段文本不是我们生成的
        return if (decoded.contains('\uFFFD')) null else decoded
    }
}
