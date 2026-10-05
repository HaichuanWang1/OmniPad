package com.omnipad.client.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.unit.dp
import com.omnipad.client.R
import com.omnipad.client.network.ConnectionState

/**
 * 连接状态胶囊。
 *
 * 存在的理由：链路会先变慢、再断开，而界面在此之前毫无表示。用户在电脑没反应的
 * 时候无从判断「是电脑卡了」还是「手机已经掉线了」。这里把状态和心跳往返耗时
 * 摆在顶栏，并把整块合并成一个无障碍节点，读屏一次念完。
 */
@Composable
fun ConnectionStatusPill(
    state: ConnectionState,
    reconnectAttempt: Int,
    latencyMs: Int?,
    modifier: Modifier = Modifier,
) {
    val label = when (state) {
        ConnectionState.CONNECTED -> {
            val base = stringResource(R.string.status_connected)
            if (latencyMs != null) {
                "$base · ${stringResource(R.string.status_latency, latencyMs)}"
            } else {
                base
            }
        }

        ConnectionState.CONNECTING -> stringResource(R.string.status_connecting)
        ConnectionState.RECONNECTING ->
            stringResource(R.string.status_reconnecting, reconnectAttempt)

        ConnectionState.FAILED -> stringResource(R.string.status_failed)
        ConnectionState.DISCONNECTED -> stringResource(R.string.status_disconnected)
    }

    val dotColor = when (state) {
        ConnectionState.CONNECTED -> MaterialTheme.colorScheme.tertiary
        ConnectionState.CONNECTING, ConnectionState.RECONNECTING ->
            MaterialTheme.colorScheme.secondary

        else -> MaterialTheme.colorScheme.error
    }

    // 重连中让圆点呼吸：静态圆点会让人以为界面卡住了
    val pulsing = state == ConnectionState.CONNECTING || state == ConnectionState.RECONNECTING
    val transition = rememberInfiniteTransition(label = "status-pulse")
    val dotAlpha by transition.animateFloat(
        initialValue = 1f,
        targetValue = if (pulsing) 0.25f else 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 700),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "status-dot-alpha",
    )

    Surface(
        modifier = modifier.clearAndSetSemantics { contentDescription = label },
        shape = MaterialTheme.shapes.extraLarge,
        color = MaterialTheme.colorScheme.surfaceVariant,
        contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .alpha(dotAlpha)
                    .background(dotColor, CircleShape)
            )
            Text(text = label, style = MaterialTheme.typography.labelMedium)
        }
    }
}
