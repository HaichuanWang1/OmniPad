package com.omnipad.client.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * 品牌色板（Material 3 色调板，种子色 #4A9EFF）。
 *
 * 每个角色都按 M3 的色调规则取值：容器色取 tone 30/90，其上文字取 tone 90/10，
 * 保证任意组合的对比度都过 WCAG AA。
 *
 * 上一版把浅色方案的 `primaryContainer` 填成了亮蓝、`onPrimaryContainer` 填成了
 * 深蓝容器色 —— 角色整体反了，浅色主题下按钮文字几乎看不见。这里按 M3 规则重排，
 * 并用 `ColorContrastTest` 锁住关键组合的对比度。
 */

// ---- 深色方案 ----
val DarkPrimary = Color(0xFFA8C8FF)
val DarkOnPrimary = Color(0xFF00315F)
val DarkPrimaryContainer = Color(0xFF004786)
val DarkOnPrimaryContainer = Color(0xFFD5E3FF)

val DarkSecondary = Color(0xFFBFC6DC)
val DarkOnSecondary = Color(0xFF293041)
val DarkSecondaryContainer = Color(0xFF3F4759)
val DarkOnSecondaryContainer = Color(0xFFDBE2F9)

val DarkTertiary = Color(0xFF7FD3E8)
val DarkOnTertiary = Color(0xFF00363F)
val DarkTertiaryContainer = Color(0xFF004E5A)
val DarkOnTertiaryContainer = Color(0xFFA6EEFF)

val DarkError = Color(0xFFFFB4AB)
val DarkOnError = Color(0xFF690005)
val DarkErrorContainer = Color(0xFF93000A)
val DarkOnErrorContainer = Color(0xFFFFDAD6)

val DarkBackground = Color(0xFF111318)
val DarkOnBackground = Color(0xFFE2E2E9)
val DarkSurface = Color(0xFF111318)
val DarkOnSurface = Color(0xFFE2E2E9)
val DarkSurfaceVariant = Color(0xFF43474E)
val DarkOnSurfaceVariant = Color(0xFFC3C6CF)
val DarkOutline = Color(0xFF8D9199)
val DarkOutlineVariant = Color(0xFF43474E)

val DarkInverseSurface = Color(0xFFE2E2E9)
val DarkInverseOnSurface = Color(0xFF2E3136)
val DarkInversePrimary = Color(0xFF005EB8)

// ---- 浅色方案 ----
val LightPrimary = Color(0xFF005EB8)
val LightOnPrimary = Color(0xFFFFFFFF)
val LightPrimaryContainer = Color(0xFFD5E3FF)
val LightOnPrimaryContainer = Color(0xFF001C3B)

val LightSecondary = Color(0xFF565E71)
val LightOnSecondary = Color(0xFFFFFFFF)
val LightSecondaryContainer = Color(0xFFDAE2F9)
val LightOnSecondaryContainer = Color(0xFF131C2B)

val LightTertiary = Color(0xFF00687A)
val LightOnTertiary = Color(0xFFFFFFFF)
val LightTertiaryContainer = Color(0xFFA6EEFF)
val LightOnTertiaryContainer = Color(0xFF001F26)

val LightError = Color(0xFFBA1A1A)
val LightOnError = Color(0xFFFFFFFF)
val LightErrorContainer = Color(0xFFFFDAD6)
val LightOnErrorContainer = Color(0xFF410002)

val LightBackground = Color(0xFFFDFBFF)
val LightOnBackground = Color(0xFF1A1C1E)
val LightSurface = Color(0xFFFDFBFF)
val LightOnSurface = Color(0xFF1A1C1E)
val LightSurfaceVariant = Color(0xFFE1E2EC)
val LightOnSurfaceVariant = Color(0xFF44474E)
val LightOutline = Color(0xFF74777F)
val LightOutlineVariant = Color(0xFFC4C6D0)

val LightInverseSurface = Color(0xFF2F3033)
val LightInverseOnSurface = Color(0xFFF1F0F4)
val LightInversePrimary = Color(0xFFA8C8FF)

/** 两个方案共用，M3 规定 scrim 恒为纯黑。 */
val Scrim = Color(0xFF000000)

/**
 * 启动窗口背景。
 *
 * `themes.xml` 里 `windowBackground` 用它，让冷启动时系统画的第一帧就和 Compose
 * 的首帧同色。上一版 themes.xml 用的是平台浅色主题，深色模式下会先闪一下白屏。
 */
val LaunchBackgroundDark = DarkBackground
val LaunchBackgroundLight = LightBackground
