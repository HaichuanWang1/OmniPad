package com.omnipad.client.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * 一个按键。
 *
 * 取代原先「`FilledIconButton` 里塞一个 `Text`」的写法：那个 API 是为图标设计的，
 * 撑出来的触控目标、内边距和无障碍语义都不对。这里用 [Surface] 的可点击重载，
 * 语义是按钮、自带水波纹，并且高度恒定为 [height]（默认 48dp，满足最小触控目标）。
 *
 * [active] 用于表达「按下并保持」的状态（鼠标键按住、修饰键锁定），
 * 视觉上切换成主色，同时通过 [stateDescription] 让读屏用户也能感知。
 */
@Composable
fun KeyCap(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
    active: Boolean = false,
    enabled: Boolean = true,
    height: Dp = 48.dp,
    stateDescription: String? = null,
) {
    val colors = keyCapColors(active)
    Surface(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier
            .heightIn(min = height)
            .then(
                if (active && stateDescription != null) {
                    Modifier.semantics { this.stateDescription = stateDescription }
                } else {
                    Modifier
                }
            ),
        shape = MaterialTheme.shapes.small,
        color = colors.container,
        contentColor = colors.content,
    ) {
        Box(
            modifier = Modifier.padding(horizontal = 4.dp),
            contentAlignment = Alignment.Center,
        ) {
            if (icon != null) {
                Icon(
                    imageVector = icon,
                    // 图标本身不提供语义，语义由外层 Surface 的按钮角色 + 这里的
                    // contentDescription 承担，避免读屏重复朗读
                    contentDescription = label,
                    modifier = Modifier.size(20.dp),
                )
            } else {
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelLarge,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    textAlign = TextAlign.Center,
                )
            }
        }
    }
}

/** 按键的配色对。抽出来是为了让 [KeyCap] 和面板里的其它同类控件保持一致。 */
data class KeyCapColors(
    val container: androidx.compose.ui.graphics.Color,
    val content: androidx.compose.ui.graphics.Color,
)

@Composable
fun keyCapColors(active: Boolean): KeyCapColors = if (active) {
    KeyCapColors(
        container = MaterialTheme.colorScheme.primary,
        content = MaterialTheme.colorScheme.onPrimary,
    )
} else {
    KeyCapColors(
        // 用 surfaceVariant 而不是 surface + 描边：M3 里 surfaceVariant 就是
        // 「比表面略高一层」的容器色，两种主题下都成立。原先写死的
        // primaryContainer.copy(alpha = 0.4f) 是半透明叠色，浅色主题下必然错。
        container = MaterialTheme.colorScheme.surfaceVariant,
        content = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/**
 * 等宽排布的一行按键。
 *
 * 每个按键平分宽度。原先的 `ButtonGroup` 用「个数 <= 3 就画成圆形」来决定形状，
 * 形状跟着数量走而不是跟着含义走，2 个键和 3 个键看起来像两种控件。
 */
@Composable
fun KeyCapRow(
    modifier: Modifier = Modifier,
    spacing: Dp = 8.dp,
    content: @Composable RowScope.() -> Unit,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(spacing),
        verticalAlignment = Alignment.CenterVertically,
        content = content,
    )
}

/**
 * 控制面板里的一组按键。
 *
 * [title] 是可选的组标题 —— 面板里同时有修饰键、编辑键、方向键、F 键，
 * 没有标题就是一整片无从下手的按钮墙（这正是原来那 6 排按钮的问题）。
 */
@Composable
fun KeyCapSection(
    title: String? = null,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Column(modifier = modifier.fillMaxWidth()) {
        if (title != null) {
            Text(
                text = title,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(6.dp))
        }
        content()
    }
}

/** 面板里的一行按键，按 [items] 自动平分宽度。 */
@Composable
fun KeyCapGridRow(
    items: List<KeyCapSpec>,
    modifier: Modifier = Modifier,
    spacing: Dp = 8.dp,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(spacing),
    ) {
        items.forEach { spec ->
            KeyCap(
                label = spec.label,
                onClick = spec.onClick,
                modifier = Modifier.weight(spec.weight),
                icon = spec.icon,
                active = spec.active,
                height = spec.height,
                stateDescription = spec.stateDescription,
            )
        }
    }
}

/**
 * 按键描述。
 *
 * [label] 只用于显示，协议键由 [onClick] 闭包捕获 —— 两者解耦。原先修饰键和方向键用
 * `label.lowercase()` 反查协议键，文案一旦被翻译就会往服务端发非法键名。
 *
 * [onClick] 放在最后，这样调用点可以写成 `KeyCapSpec("Tab") { pressKey("tab") }`。
 */
data class KeyCapSpec(
    val label: String,
    val weight: Float = 1f,
    val icon: ImageVector? = null,
    val active: Boolean = false,
    val height: Dp = 48.dp,
    val stateDescription: String? = null,
    val onClick: () -> Unit,
)

/** 两个按键之间固定宽度的间隙，用于 [KeyCapRow] 之外的场合。 */
@Composable
fun KeyCapSpacer(width: Dp = 8.dp) {
    Spacer(Modifier.width(width))
}
