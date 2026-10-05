package com.omnipad.client.network

/** 连接参数校验失败的原因。文案由 UI 层映射，这里不带任何界面文字。 */
sealed interface EndpointError {
    data object HostBlank : EndpointError
    data object HostLooksLikeUrl : EndpointError
    data object HostHasWhitespace : EndpointError
    data object HostMalformed : EndpointError
    data object PortNotANumber : EndpointError
    data object PortOutOfRange : EndpointError
    data object TokenBlank : EndpointError
}

/** 校验通过后的规范化连接参数。 */
data class ValidatedEndpoint(
    val host: String,
    val port: Int,
    val token: String,
)

sealed interface EndpointCheck {
    data class Ok(val endpoint: ValidatedEndpoint) : EndpointCheck

    data class Invalid(val error: EndpointError) : EndpointCheck
}

/**
 * 连接参数校验。
 *
 * 抽成纯函数是为了能直接在 JVM 上测 —— 这些规则以前散在界面里，表现为
 * 「端口留空静默变成 5800」「地址留空直接把 Java 异常原文显示给用户」。
 */
object EndpointValidator {

    /** 协议规定的默认端口，见 docs/protocol.md。 */
    const val DEFAULT_PORT = 5800

    /** `192.168.1.5:5800` */
    private val HOST_PORT_PLAIN = Regex("""^([^:]+):(\d+)$""")

    /** `[fe80::1]:5800` */
    private val HOST_PORT_BRACKETED = Regex("""^(\[[^\]]+]):(\d+)$""")

    /** 允许出现在地址里的字符：主机名字符、IPv4 点分、IPv6 冒号与方括号、区域号 %。 */
    private val ALLOWED_HOST = Regex("""^[A-Za-z0-9.\-_:%\[\]]+$""")

    fun validate(hostInput: String, portInput: String, tokenInput: String): EndpointCheck {
        val host = hostInput.trim()
        val portText = portInput.trim()
        val token = tokenInput.trim()

        if (host.isEmpty()) return EndpointCheck.Invalid(EndpointError.HostBlank)

        if (host.any { it.isWhitespace() }) {
            return EndpointCheck.Invalid(EndpointError.HostHasWhitespace)
        }
        // 常见误操作：把浏览器地址栏里的 URL 整段粘进来
        if (host.contains("://") || host.contains('/')) {
            return EndpointCheck.Invalid(EndpointError.HostLooksLikeUrl)
        }

        // 常见误操作：把 "192.168.1.5:5800" 整个填进地址框。
        // 与其报错让用户自己拆，不如把端口拆出来还给他。
        // 注意这里只负责「拆」，端口是否合法仍由下面统一判定 —— 否则
        // "192.168.1.5:99999" 会被当成一个合法主机名，最后以连不上的形式失败。
        val split = splitHostAndPort(host)
        val effectiveHost = split?.first ?: host
        val effectivePortText = split?.second ?: portText

        if (!ALLOWED_HOST.matches(effectiveHost)) {
            return EndpointCheck.Invalid(EndpointError.HostMalformed)
        }
        // 方括号必须成对：单独一个 '[' 是打错了，不是合法地址
        if (effectiveHost.startsWith("[") != effectiveHost.endsWith("]")) {
            return EndpointCheck.Invalid(EndpointError.HostMalformed)
        }

        // 端口留空即采用协议默认值。界面上该框会预填 5800，所以这里不是「静默兜底」，
        // 而是「用户清空 = 用默认」的明确语义。
        val port = if (effectivePortText.isEmpty()) {
            DEFAULT_PORT
        } else {
            effectivePortText.toIntOrNull()
                ?: return EndpointCheck.Invalid(EndpointError.PortNotANumber)
        }
        if (port !in 1..65535) return EndpointCheck.Invalid(EndpointError.PortOutOfRange)

        if (token.isEmpty()) return EndpointCheck.Invalid(EndpointError.TokenBlank)

        return EndpointCheck.Ok(
            ValidatedEndpoint(
                host = effectiveHost,
                port = port,
                // 服务端比较时两侧都会 trim 后转大写（见 docs/protocol.md），
                // 客户端先规范化，让历史记录里的令牌形式统一。
                token = token.uppercase(),
            )
        )
    }

    /**
     * 从「地址里带了端口」的输入中取出端口，供界面实时回填。
     * 输入不含端口、或端口越界时返回 null。
     */
    fun extractPort(hostInput: String): Int? = splitHostAndPort(hostInput.trim())
        ?.second?.toIntOrNull()
        ?.takeIf { it in 1..65535 }

    /**
     * 把 `host:port` 或 `[ipv6]:port` 拆开，端口以字符串返回（合法性另行判定）。
     *
     * 裸 IPv6 字面量（`fe80::1`、`::1`，两个以上冒号）不会命中任何一条规则，
     * 因此不会被误拆 —— 它的端口由端口框单独指定。
     */
    private fun splitHostAndPort(input: String): Pair<String, String>? {
        val match = HOST_PORT_BRACKETED.matchEntire(input)
            ?: HOST_PORT_PLAIN.matchEntire(input)
            ?: return null
        return match.groupValues[1] to match.groupValues[2]
    }
}
