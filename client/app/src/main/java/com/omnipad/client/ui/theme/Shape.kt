package com.omnipad.client.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/**
 * 形状梯度。
 *
 * 比 M3 默认值略大一号：触控板、按键这类高频点击目标用更圆的角更容易被识别成
 * 「可以按的东西」，也让整体观感更接近现代遥控类 App。
 */
val OmniPadShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(20.dp),
    extraLarge = RoundedCornerShape(28.dp),
)
