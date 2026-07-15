package com.omnipad.client.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val DarkColorScheme = darkColorScheme(
    primary = TechBlue,
    onPrimary = OnTechBlue,
    primaryContainer = TechBlueContainer,
    onPrimaryContainer = OnTechBlueContainer,
    secondary = SlateBlue,
    onSecondary = OnSlateBlue,
    secondaryContainer = SlateBlueContainer,
    onSecondaryContainer = OnSlateBlueContainer,
    tertiary = CyanAccent,
    onTertiary = OnCyanAccent,
    tertiaryContainer = CyanAccentContainer,
    onTertiaryContainer = OnCyanAccentContainer,
    error = RedError,
    onError = OnRedError,
    errorContainer = RedErrorContainer,
    onErrorContainer = OnRedErrorContainer,
    background = DarkBackground,
    onBackground = OnDarkBackground,
    surface = DarkSurface,
    onSurface = OnDarkSurface,
    surfaceVariant = DarkSurfaceVariant,
    onSurfaceVariant = OnDarkSurfaceVariant,
    outline = GrayOutline,
    outlineVariant = GrayOutlineVariant,
    inverseSurface = InverseSurface,
    inverseOnSurface = InverseOnSurface,
    inversePrimary = TechBlueDim,
    scrim = Scrim,
)

private val LightColorScheme = lightColorScheme(
    primary = TechBlueDim,
    onPrimary = OnTechBlue,
    primaryContainer = OnTechBlueContainer,
    onPrimaryContainer = TechBlueContainer,
    secondary = SlateBlueDim,
    onSecondary = OnSlateBlue,
    secondaryContainer = OnSlateBlueContainer,
    onSecondaryContainer = SlateBlueContainer,
    tertiary = CyanAccentDim,
    onTertiary = OnCyanAccent,
    tertiaryContainer = OnCyanAccentContainer,
    onTertiaryContainer = CyanAccentContainer,
    error = RedErrorDim,
    onError = OnRedError,
    errorContainer = OnRedErrorContainer,
    onErrorContainer = RedErrorContainer,
    background = LightBackground,
    onBackground = OnLightBackground,
    surface = LightSurface,
    onSurface = OnLightSurface,
    surfaceVariant = LightSurfaceVariant,
    onSurfaceVariant = OnLightSurfaceVariant,
    outline = LightOutline,
    outlineVariant = LightOutlineVariant,
    inverseSurface = LightInverseSurface,
    inverseOnSurface = LightInverseOnSurface,
    inversePrimary = TechBlue,
    scrim = Scrim,
)

@Composable
fun OmniPadTheme(
    darkTheme: Boolean = true,
    content: @Composable () -> Unit,
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme

    MaterialTheme(
        colorScheme = colorScheme,
        typography = OmniPadTypography,
        shapes = OmniPadShapes,
        content = content,
    )
}
