package com.omnipad.client.ui.screens

import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.omnipad.client.R
import com.omnipad.client.data.Settings
import com.omnipad.client.ui.components.KeyCap
import com.omnipad.client.ui.theme.ThemeMode
import kotlin.math.roundToInt

/**
 * 设置面板。
 *
 * 上一版只有「自动断开」一个开关，而且它被做成了一个**没有任何文字标签**的
 * 裸 `Switch` 塞在顶栏里 —— 用户不可能知道那是干什么的。这里把所有可调项
 * 集中到一个底部面板，每项都有标题和一句说明。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsSheet(
    settings: Settings,
    onUpdate: ((Settings) -> Settings) -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp)
                // 底部导航栏留白，否则最后一项会被系统手势条压住
                .windowInsetsPadding(WindowInsets.navigationBars)
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                text = stringResource(R.string.settings_title),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(bottom = 8.dp),
            )

            // ---- 主题 ----
            SettingLabel(stringResource(R.string.settings_theme))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(
                    ThemeMode.SYSTEM to stringResource(R.string.settings_theme_system),
                    ThemeMode.LIGHT to stringResource(R.string.settings_theme_light),
                    ThemeMode.DARK to stringResource(R.string.settings_theme_dark),
                ).forEach { (mode, label) ->
                    KeyCap(
                        label = label,
                        onClick = { onUpdate { it.copy(themeMode = mode) } },
                        active = settings.themeMode == mode,
                        modifier = Modifier.weight(1f),
                    )
                }
            }

            Spacer(Modifier.height(12.dp))

            SwitchRow(
                title = stringResource(R.string.settings_dynamic_color),
                description = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    stringResource(R.string.settings_dynamic_color_desc)
                } else {
                    stringResource(R.string.settings_dynamic_color_unavailable)
                },
                checked = settings.dynamicColor,
                enabled = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S,
                onCheckedChange = { value -> onUpdate { it.copy(dynamicColor = value) } },
            )

            // ---- 手感 ----
            Spacer(Modifier.height(8.dp))

            SensitivityRow(
                title = stringResource(R.string.settings_pointer_sensitivity),
                value = settings.pointerSensitivity,
                onChange = { value -> onUpdate { it.copy(pointerSensitivity = value) } },
            )
            SensitivityRow(
                title = stringResource(R.string.settings_scroll_sensitivity),
                value = settings.scrollSensitivity,
                onChange = { value -> onUpdate { it.copy(scrollSensitivity = value) } },
            )

            Spacer(Modifier.height(8.dp))

            SwitchRow(
                title = stringResource(R.string.settings_haptics),
                description = stringResource(R.string.settings_haptics_desc),
                checked = settings.hapticsEnabled,
                onCheckedChange = { value -> onUpdate { it.copy(hapticsEnabled = value) } },
            )
            SwitchRow(
                title = stringResource(R.string.settings_keep_screen_on),
                description = stringResource(R.string.settings_keep_screen_on_desc),
                checked = settings.keepScreenOn,
                onCheckedChange = { value -> onUpdate { it.copy(keepScreenOn = value) } },
            )
            SwitchRow(
                title = stringResource(R.string.settings_auto_disconnect),
                description = stringResource(R.string.settings_auto_disconnect_desc),
                checked = settings.autoDisconnect,
                onCheckedChange = { value -> onUpdate { it.copy(autoDisconnect = value) } },
            )
            SwitchRow(
                title = stringResource(R.string.settings_auto_reconnect),
                description = stringResource(R.string.settings_auto_reconnect_desc),
                checked = settings.autoReconnect,
                onCheckedChange = { value -> onUpdate { it.copy(autoReconnect = value) } },
            )

            Spacer(Modifier.height(12.dp))

            TextButton(onClick = onDismiss, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.settings_close))
            }
        }
    }
}

@Composable
private fun SettingLabel(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(bottom = 6.dp),
    )
}

@Composable
private fun SwitchRow(
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    enabled: Boolean = true,
) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                color = if (enabled) {
                    MaterialTheme.colorScheme.onSurface
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
            )
            Text(
                text = description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange, enabled = enabled)
    }
}

/**
 * 灵敏度档位选择。
 *
 * 用离散档位而不是连续滑块：指针倍率是手感参数，0.87× 和 0.91× 没有可感知的区别，
 * 但会让用户难以回到上次那个「正好」的值。
 */
@Composable
private fun SensitivityRow(
    title: String,
    value: Float,
    onChange: (Float) -> Unit,
) {
    val steps = Settings.SENSITIVITY_STEPS
    // 找不到完全相等的档位时取最接近的，避免因为浮点误差让滑块落在两档之间
    val index = steps.indices.minByOrNull { kotlin.math.abs(steps[it] - value) } ?: 0

    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = stringResource(
                    R.string.settings_sensitivity_value,
                    formatMultiplier(steps[index]),
                ),
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.primary,
            )
        }
        Slider(
            value = index.toFloat(),
            onValueChange = { raw ->
                val target = raw.roundToInt().coerceIn(steps.indices)
                onChange(steps[target])
            },
            valueRange = 0f..(steps.size - 1).toFloat(),
            // steps 参数是「两端之间」的分段数
            steps = (steps.size - 2).coerceAtLeast(0),
        )
    }
}

/** 0.75 显示成 "0.75"，1.0 显示成 "1"。省掉没意义的小数位。 */
internal fun formatMultiplier(value: Float): String =
    if (value == value.toInt().toFloat()) value.toInt().toString() else value.toString()
