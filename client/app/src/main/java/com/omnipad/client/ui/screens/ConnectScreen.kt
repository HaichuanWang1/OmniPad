package com.omnipad.client.ui.screens

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.ContentPaste
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.omnipad.client.EndpointSnapshot
import com.omnipad.client.R
import com.omnipad.client.network.ConnectionNotice
import com.omnipad.client.network.ConnectionState
import com.omnipad.client.network.EndpointCheck
import com.omnipad.client.network.EndpointError
import com.omnipad.client.network.EndpointValidator
import com.omnipad.client.network.PROTOCOL_VERSION
import com.omnipad.client.network.QrPairing
import com.omnipad.client.network.RecentHost
import com.omnipad.client.ui.components.NoticeCard
import com.omnipad.client.ui.noticeText
import com.omnipad.client.ui.util.RelativeBucket
import com.omnipad.client.ui.util.RelativeTimeFormatter

private const val GITHUB_URL = "https://github.com/HaichuanWang1/OmniPad"

/**
 * 连接页。
 *
 * 相对上一版的三处结构性改动：
 * 1. **可滚动 + 处理安全区/输入法内边距**。原来是一个不滚动的居中 `Column`，
 *    软键盘一弹内容被挤出视口，「连接」按钮再也点不到。
 * 2. **失败原因常驻显示**。原来只有一个 3 秒的 Toast，消失后界面上只剩一句通用提示，
 *    用户分不清是令牌错了还是电脑没开机。
 * 3. **提交前校验**。原来地址留空会直接拿去连接，然后把 Java 异常原文显示给用户。
 */
@Composable
fun ConnectScreen(
    connectionState: ConnectionState,
    reconnectAttempt: Int,
    failure: ConnectionNotice?,
    recentHosts: List<RecentHost>,
    initialEndpoint: EndpointSnapshot?,
    hapticsEnabled: Boolean,
    onConnect: (host: String, port: Int, token: String) -> Unit,
    onCancelReconnect: () -> Unit,
    onDeleteHost: (host: String, port: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    // rememberSaveable：转屏后输入内容不再丢失（上一版用 remember，旋转即清空）
    var host by rememberSaveable { mutableStateOf(initialEndpoint?.host.orEmpty()) }
    var port by rememberSaveable {
        mutableStateOf((initialEndpoint?.port ?: EndpointValidator.DEFAULT_PORT).toString())
    }
    var token by rememberSaveable { mutableStateOf(initialEndpoint?.token.orEmpty()) }
    var revealed by rememberSaveable { mutableStateOf(false) }
    var fieldError by remember { mutableStateOf<EndpointError?>(null) }
    var deleteTarget by remember { mutableStateOf<RecentHost?>(null) }
    var showScanner by remember { mutableStateOf(false) }
    var scanNotice by remember { mutableStateOf<QrPairing?>(null) }

    val focusManager = LocalFocusManager.current
    val clipboard = LocalClipboardManager.current
    val context = LocalContext.current

    fun submit() {
        focusManager.clearFocus()
        when (val check = EndpointValidator.validate(host, port, token)) {
            is EndpointCheck.Ok -> {
                fieldError = null
                val ep = check.endpoint
                // 回写规范化后的值，让用户看到实际使用的地址与令牌形式
                host = ep.host
                port = ep.port.toString()
                token = ep.token
                onConnect(ep.host, ep.port, ep.token)
            }

            is EndpointCheck.Invalid -> fieldError = check.error
        }
    }

    /**
     * 扫码结果的处理。
     *
     * 刻意走「填进输入框 -> 调用同一个 [submit]」这条路，而不是直接连接：
     * 需求上扫码**等同于手动输入**，那这条等价关系就该由代码结构保证，
     * 而不是靠两处逻辑碰巧一致。用户也能在框里看见扫到了什么，填错了可以改。
     */
    fun applyScan(pairing: QrPairing) {
        host = pairing.host
        port = pairing.port.toString()
        token = pairing.token
        fieldError = null
        scanNotice = pairing
        showScanner = false
        submit()
    }

    val busy = connectionState == ConnectionState.CONNECTING
    val reconnecting = connectionState == ConnectionState.RECONNECTING

    Column(
        modifier = modifier
            .fillMaxSize()
            // safeDrawing 已经包含了状态栏、导航栏和输入法三种内边距，
            // 再叠一次 imePadding 会把输入法高度算两遍
            .windowInsetsPadding(WindowInsets.safeDrawing)
            .verticalScroll(rememberScrollState()),
        // 内容不足一屏时垂直居中。
        //
        // verticalScroll 会把 Column 的高度撑成视口高度，默认的 Top 排列会让内容全部
        // 挤在顶部、下方留一大片空白（原版是居中的，重写时丢了）。Center 在内容超过
        // 一屏时自然退化成可滚动，两种情形都正确。
        verticalArrangement = Arrangement.Center,
    ) {
        Column(
            modifier = Modifier
                // widthIn 必须在 fillMaxWidth **之前**。
                //
                // fillMaxWidth 会把 minWidth 顶到父容器宽度，之后 widthIn 的 maxWidth
                // 与这个 minWidth 冲突，Constraints.constrain 取 min 的结果仍是父容器宽度 ——
                // 上限完全不生效。竖屏 360dp 本来就窄于 520dp，所以一直没暴露；
                // 横屏一测，表单直接拉满 800dp。
                .widthIn(max = 520.dp)
                .fillMaxWidth()
                .align(Alignment.CenterHorizontally)
                .padding(horizontal = 24.dp, vertical = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = stringResource(R.string.app_name),
                style = MaterialTheme.typography.displaySmall,
                fontWeight = FontWeight.Light,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                text = stringResource(R.string.connect_tagline),
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )

            failure?.let {
                Spacer(Modifier.height(20.dp))
                NoticeCard(noticeText(it))
            }

            Spacer(Modifier.height(26.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.Top,
            ) {
                OutlinedTextField(
                    value = host,
                    onValueChange = { input ->
                        host = input
                        if (fieldError.isHostError()) fieldError = null
                        // 用户常把 "192.168.1.5:5800" 整段粘进来，识别到就把端口填好
                        EndpointValidator.extractPort(input)?.let { port = it.toString() }
                    },
                    label = { Text(stringResource(R.string.connect_host_label)) },
                    singleLine = true,
                    isError = fieldError.isHostError(),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Uri,
                        imeAction = ImeAction.Next,
                    ),
                    trailingIcon = if (host.isNotEmpty()) {
                        {
                            IconButton(onClick = { host = "" }) {
                                Icon(
                                    Icons.Default.Clear,
                                    contentDescription = stringResource(R.string.action_delete),
                                )
                            }
                        }
                    } else {
                        null
                    },
                    shape = MaterialTheme.shapes.medium,
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = port,
                    // 只让数字进来：非法字符在源头就被挡住，校验只需管范围
                    onValueChange = { input ->
                        port = input.filter { it.isDigit() }.take(5)
                        if (fieldError.isPortError()) fieldError = null
                    },
                    label = { Text(stringResource(R.string.connect_port_label)) },
                    singleLine = true,
                    isError = fieldError.isPortError(),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Number,
                        imeAction = ImeAction.Next,
                    ),
                    shape = MaterialTheme.shapes.medium,
                    modifier = Modifier.width(104.dp),
                )
            }

            fieldError.hostOrPortMessage()?.let {
                Spacer(Modifier.height(6.dp))
                FieldErrorText(it)
            }

            Spacer(Modifier.height(14.dp))

            OutlinedTextField(
                value = token,
                onValueChange = { input ->
                    token = input
                    if (fieldError == EndpointError.TokenBlank) fieldError = null
                },
                label = { Text(stringResource(R.string.connect_token_label)) },
                placeholder = { Text(stringResource(R.string.connect_token_placeholder)) },
                singleLine = true,
                isError = fieldError == EndpointError.TokenBlank,
                visualTransformation = if (revealed) {
                    VisualTransformation.None
                } else {
                    PasswordVisualTransformation()
                },
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Ascii,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(onDone = { submit() }),
                // 粘贴放前面、显示/隐藏放后面：令牌是从电脑上复制过来的，
                // 粘贴是主路径，一眼就能看到
                leadingIcon = {
                    IconButton(
                        onClick = {
                            val text = clipboard.getText()?.text
                            if (!text.isNullOrBlank()) {
                                token = text.trim()
                                fieldError = null
                            }
                        }
                    ) {
                        Icon(
                            Icons.Default.ContentPaste,
                            contentDescription = stringResource(R.string.connect_paste),
                        )
                    }
                },
                trailingIcon = {
                    IconButton(onClick = { revealed = !revealed }) {
                        Icon(
                            imageVector = if (revealed) {
                                Icons.Default.VisibilityOff
                            } else {
                                Icons.Default.Visibility
                            },
                            contentDescription = stringResource(
                                if (revealed) R.string.connect_token_hide
                                else R.string.connect_token_reveal
                            ),
                        )
                    }
                },
                shape = MaterialTheme.shapes.medium,
                modifier = Modifier.fillMaxWidth(),
            )

            if (fieldError == EndpointError.TokenBlank) {
                Spacer(Modifier.height(6.dp))
                FieldErrorText(stringResource(R.string.error_token_blank))
            }

            Spacer(Modifier.height(22.dp))

            if (reconnecting) {
                Text(
                    text = stringResource(R.string.session_reconnecting, reconnectAttempt),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(12.dp))
                OutlinedButton(
                    onClick = onCancelReconnect,
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                    shape = MaterialTheme.shapes.medium,
                ) {
                    Text(stringResource(R.string.connect_cancel_reconnect))
                }
            } else {
                Button(
                    onClick = { submit() },
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                    shape = MaterialTheme.shapes.medium,
                ) {
                    if (busy) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(20.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                    } else {
                        Text(
                            text = stringResource(R.string.connect_action),
                            style = MaterialTheme.typography.titleMedium,
                        )
                    }
                }
            }

            // 扫码入口紧跟在「连接」下面：两条路通向同一件事，不该隔得很远
            if (!reconnecting) {
                Spacer(Modifier.height(10.dp))
                OutlinedButton(
                    onClick = {
                        focusManager.clearFocus()
                        showScanner = true
                    },
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                    shape = MaterialTheme.shapes.medium,
                ) {
                    Icon(
                        imageVector = Icons.Default.QrCodeScanner,
                        contentDescription = null,
                        modifier = Modifier.size(20.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = stringResource(R.string.scan_action),
                        style = MaterialTheme.typography.titleMedium,
                    )
                }

                scanNotice?.let { pairing ->
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = pairing.name?.let { name ->
                            stringResource(
                                R.string.scan_filled_named,
                                pairing.host,
                                pairing.port,
                                name,
                            )
                        } ?: stringResource(
                            R.string.scan_filled,
                            pairing.host,
                            pairing.port,
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }

            if (recentHosts.isNotEmpty()) {
                Spacer(Modifier.height(28.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(
                        imageVector = Icons.Default.History,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = stringResource(R.string.connect_recent_hosts),
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.weight(1f))
                    Text(
                        text = stringResource(R.string.connect_recent_hint),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.height(8.dp))
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    recentHosts.forEach { recent ->
                        RecentHostRow(
                            host = recent,
                            enabled = !busy && !reconnecting,
                            onClick = {
                                host = recent.host
                                port = recent.port.toString()
                                token = recent.token
                                fieldError = null
                                focusManager.clearFocus()
                                onConnect(recent.host, recent.port, recent.token)
                            },
                            onLongClick = { deleteTarget = recent },
                            onDelete = { deleteTarget = recent },
                        )
                    }
                }
            }

            Spacer(Modifier.height(28.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = stringResource(R.string.connect_author),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = " · ",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                // 用 Surface 承载点击：原先是一个纯 Text 加 clickable，
                // 触控目标只有十几 dp，远低于 48dp 的最小值
                Surface(
                    onClick = {
                        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(GITHUB_URL)))
                    },
                    shape = MaterialTheme.shapes.small,
                    color = MaterialTheme.colorScheme.surface,
                    contentColor = MaterialTheme.colorScheme.primary,
                ) {
                    Text(
                        text = "GitHub",
                        style = MaterialTheme.typography.bodySmall,
                        textDecoration = TextDecoration.Underline,
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 15.dp),
                    )
                }
            }
        }
    }

    deleteTarget?.let { target ->
        AlertDialog(
            onDismissRequest = { deleteTarget = null },
            title = { Text(stringResource(R.string.connect_delete_title)) },
            text = {
                Text(stringResource(R.string.connect_delete_message, target.host, target.port))
            },
            confirmButton = {
                TextButton(onClick = {
                    onDeleteHost(target.host, target.port)
                    deleteTarget = null
                }) {
                    Text(
                        text = stringResource(R.string.action_delete),
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            },
            dismissButton = {
                TextButton(onClick = { deleteTarget = null }) {
                    Text(stringResource(R.string.action_cancel))
                }
            },
        )
    }

    // 扫码面板是**底部弹出的半屏窗口**，不是独立页面：扫码是连接页上的一个动作，
    // 做成整页会让用户在两个页面之间来回切，而它们讲的是同一件事。
    if (showScanner) {
        ScanSheet(
            expectedVersion = PROTOCOL_VERSION,
            hapticsEnabled = hapticsEnabled,
            onScanned = { applyScan(it) },
            onDismiss = { showScanner = false },
        )
    }
}

/** 字段级校验错误的说明，紧贴在对应输入框下面。 */
@Composable
private fun FieldErrorText(message: String) {
    Text(
        text = message,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.error,
        modifier = Modifier.fillMaxWidth(),
    )
}

/**
 * 一条历史连接。
 *
 * 轻点连接，长按或点右侧图标删除 —— 上一版只有长按能删且没有任何提示，
 * 等于没有入口。
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun RecentHostRow(
    host: RecentHost,
    enabled: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    onDelete: () -> Unit,
) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .combinedClickable(
                enabled = enabled,
                onClick = onClick,
                onLongClick = onLongClick,
            ),
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.surfaceVariant,
        contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
    ) {
        Row(
            modifier = Modifier.padding(start = 14.dp, end = 4.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(vertical = 10.dp)
            ) {
                Text(
                    text = "${host.host}:${host.port}",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text = relativeLabel(host.timestamp),
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            IconButton(onClick = onDelete, enabled = enabled) {
                Icon(
                    imageVector = Icons.Default.Delete,
                    contentDescription = stringResource(R.string.action_delete),
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}

/** 把时间戳渲染成「刚刚 / 12 分钟前 / …」。 */
@Composable
private fun relativeLabel(timestamp: Long): String {
    val relative = RelativeTimeFormatter.format(System.currentTimeMillis(), timestamp)
    return when (relative.bucket) {
        RelativeBucket.JUST_NOW -> stringResource(R.string.connect_recent_just_now)
        RelativeBucket.MINUTES -> stringResource(R.string.connect_recent_minutes, relative.amount)
        RelativeBucket.HOURS -> stringResource(R.string.connect_recent_hours, relative.amount)
        RelativeBucket.DAYS -> stringResource(R.string.connect_recent_days, relative.amount)
    }
}

// ---- 校验错误 -> 字段 的映射 ----

private fun EndpointError?.isHostError(): Boolean = when (this) {
    EndpointError.HostBlank,
    EndpointError.HostLooksLikeUrl,
    EndpointError.HostHasWhitespace,
    EndpointError.HostMalformed,
    -> true

    else -> false
}

private fun EndpointError?.isPortError(): Boolean =
    this == EndpointError.PortNotANumber || this == EndpointError.PortOutOfRange

@Composable
private fun EndpointError?.hostOrPortMessage(): String? = when (this) {
    EndpointError.HostBlank -> stringResource(R.string.error_host_blank)
    EndpointError.HostLooksLikeUrl -> stringResource(R.string.error_host_url)
    EndpointError.HostHasWhitespace -> stringResource(R.string.error_host_whitespace)
    EndpointError.HostMalformed -> stringResource(R.string.error_host_malformed)
    EndpointError.PortNotANumber, EndpointError.PortOutOfRange ->
        stringResource(R.string.error_port_invalid)

    else -> null
}
