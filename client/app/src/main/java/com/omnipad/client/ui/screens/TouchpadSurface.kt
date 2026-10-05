package com.omnipad.client.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.AwaitPointerEventScope
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.onLongClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.omnipad.client.R
import com.omnipad.client.ui.util.Haptics
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.math.abs

/** 一次触摸最终被判定成哪一类手势。 */
private enum class TouchMode { TAP, TWO_FINGER_TAP, LONG_PRESS, DRAG, SCROLL }

/** 轻点之后多久内再按下，算 tap-drag（毫秒）。 */
private const val TAP_DRAG_WINDOW_MS = 400L

/** 轻点后第二次按下的位置容差（touchSlop 的倍数）。 */
private const val TAP_DRAG_SLOP_FACTOR = 3f

/**
 * 双指滚动，直到手指少于两根。
 *
 * **必须是 `AwaitPointerEventScope` 的扩展**：`AwaitPointerEventScope` 是受限挂起
 * 作用域，普通局部 `suspend fun` 不属于它，Kotlin 会直接拒绝编译
 * （"Restricted suspending functions can only invoke member or extension
 * suspending functions on their restricted coroutine scope"）。
 * 而滚动又必须在这里消费事件 —— 不能跳出去用协程，那样会跟手势状态机抢事件。
 */
private suspend fun AwaitPointerEventScope.scrollWithTwoFingers(
    sensitivity: Float,
    onScroll: (Int) -> Unit,
    initialAnchor: Float?,
): Float? {
    var anchor = initialAnchor
    var acc = 0f
    while (true) {
        val event = awaitPointerEvent()
        val pressed = event.changes.filter { it.pressed }
        // 少了一根手指就结束，否则平均值会跳变，产生一次假滚动
        if (pressed.size < 2) break
        val avgY = pressed.map { it.position.y }.average().toFloat()
        val base = anchor ?: avgY      // 第一帧只建立锚点，不产生滚动
        // 手指下滑 = 向下滚动，与 Windows 触控板默认方向一致
        acc += (base - avgY) * sensitivity
        anchor = avgY
        val delta = acc.toInt()
        if (delta != 0) {
            acc -= delta
            onScroll(delta)
        }
        pressed.forEach { it.consume() }
    }
    return anchor
}

/**
 * 触控板。
 *
 * ## 单一手势状态机
 *
 * 所有手势由同一个 `pointerInput` 驱动：先判定这次触摸属于哪一类，再执行对应的动作。
 * 拆成多个 `pointerInput` 会互相抢事件，出现「拖动被点击吃掉」这类问题。
 *
 * ## 相对上一版修正的三件事
 *
 * 1. **浮点累加**。原来把每次事件的位移 `toInt()` 后累加，小数部分每次都被丢掉，
 *    于是「慢慢地精确移动」输出恒为 0，指针纹丝不动。现在保留余量，慢速移动也精确。
 * 2. **不再有 16ms 轮询循环**。原来用 `while(true) { delay(16) }` 定期把累加值转发出去，
 *    空闲时也在跑，还给每次拖动加上最多 16ms 延迟。现在直接在事件里发送 ——
 *    指针事件本身就是按帧到达的，不需要二次节流。
 * 3. **新增双指轻点 = 右键**。触控板的通用约定，比长按更快。
 *
 * ## 轻点后按住 = 按住左键拖动（tap-drag）
 *
 * 触控板的通用约定：轻点一下，第二次按下**不抬手**直接拖，就是按住左键拖动 ——
 * 拖文件、选文字不用再去按面板上的「左键」开关。
 *
 * 这**必须**是按下与抬起两条独立消息，而不是一条 `click`：
 * `click` 在服务端是 down+up 连发，拖到一半左键就被松开了。
 *
 * 第一次轻点**照常立即**发出完整 click —— 为了等第二次触摸而推迟第一次的点击，
 * 单击会有肉眼可见的延迟，不可接受。代价是 tap-tap（第二次没动就抬起）恰好等于
 * 一次双击，而这本来就是想要的行为。
 *
 * 与面板「左键」开关共享同一个状态（[TouchpadScreen] 传入的 press/release 会更新
 * `heldMouseButton`）：手势按住了左键、面板却显示没按的话，用户再点一下面板就会
 * 送出 `left up`，把拖动无声无息地掐断。
 */
@Composable
fun TouchpadSurface(
    pointerSensitivity: Float,
    scrollSensitivity: Float,
    haptics: Haptics,
    onPointerMove: (dx: Int, dy: Int) -> Unit,
    onButtonClick: (button: String) -> Unit,
    onButtonPress: (button: String) -> Unit,
    onButtonRelease: (button: String) -> Unit,
    onScroll: (delta: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    var dragging by remember { mutableStateOf(false) }
    // 只在绘制阶段读取，避免每次移动都触发重组
    val touchIndicator = remember { mutableStateOf<Offset?>(null) }

    // 手势协程的生命周期与 pointerInput(Unit) 绑定，不会因为参数变化重启；
    // 用 rememberUpdatedState 保证它读到的是最新的回调与设置。
    val currentOnMove by rememberUpdatedState(onPointerMove)
    val currentOnClick by rememberUpdatedState(onButtonClick)
    val currentOnPress by rememberUpdatedState(onButtonPress)
    val currentOnRelease by rememberUpdatedState(onButtonRelease)
    val currentOnScroll by rememberUpdatedState(onScroll)
    val currentHaptics by rememberUpdatedState(haptics)
    val currentPointerSensitivity by rememberUpdatedState(pointerSensitivity)
    val currentScrollSensitivity by rememberUpdatedState(scrollSensitivity)

    val description = stringResource(R.string.touchpad_accessibility)
    val leftClickLabel = stringResource(R.string.mouse_left)
    val rightClickLabel = stringResource(R.string.mouse_right)
    val hint = stringResource(R.string.touchpad_hint)

    // 半透明是这里的本意：涟漪要透出底下的触控板表面。
    // 底色本身是主题 Token（surface），所以叠加结果在两种主题下都成立 ——
    // 这和「用半透明色表达控件状态」不是一回事。
    val rippleColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.16f)

    // 用 Surface + tonalElevation 而不是 Modifier.background()：
    // 背景色和 surface 在本主题里是同一个值（深色 #111318 / 浅色 #FDFBFF），
    // 直接铺 surface 会让触控板完全融进页面、只剩一圈描边。
    // tonalElevation 是 M3 里「比背景高一层」的标准做法 —— 它会用 surfaceTint
    // 按高度叠一层极淡的主色，两种主题下都能得到清晰但不喧闹的层次。
    Surface(
        modifier = modifier
            // 相对位移对读屏用户没有意义，但至少要让「点击」和「长按」可用，
            // 否则整个触控板对 TalkBack 用户是一块死区。
            // tap-drag 依赖两次触摸之间的精确时序，读屏路径做不了，也不假装能做。
            .semantics {
                contentDescription = description
                onClick(label = leftClickLabel) {
                    currentOnClick("left")
                    currentHaptics.click()
                    true
                }
                onLongClick(label = rightClickLabel) {
                    currentOnClick("right")
                    currentHaptics.longPress()
                    true
                }
            }
            .pointerInput(Unit) {
                // tap-drag 的待命状态要**跨手势**存活：上一次轻点抬起的位置与时刻。
                // 放在 awaitEachGesture 外面，否则每次手势都会把它清掉。
                var armedPosition: Offset? = null
                var armedAtUptime = 0L

                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false)
                    val longPressTimeout = viewConfiguration.longPressTimeoutMillis
                    val touchSlop = viewConfiguration.touchSlop

                    // ---- tap-drag 待命判定 ----
                    val armed = armedPosition?.let { pos ->
                        down.uptimeMillis - armedAtUptime <= TAP_DRAG_WINDOW_MS &&
                            (down.position - pos).getDistance() <= touchSlop * TAP_DRAG_SLOP_FACTOR
                    } == true
                    // 待命只能被消费一次：无论这次手势最后判成什么，都不带进下一次
                    armedPosition = null

                    var mode: TouchMode? = null
                    var twoFingerSeen = false
                    var scrollAnchorY: Float? = null
                    var lastPos = down.position
                    var accumulated = Offset.Zero
                    var lastUptime = down.uptimeMillis

                    // ---- 判定阶段 ----
                    if (!armed) {
                        withTimeoutOrNull(longPressTimeout) {
                            while (mode == null) {
                                val event = awaitPointerEvent()
                                val pressed = event.changes.filter { it.pressed }
                                lastUptime = event.changes.firstOrNull()?.uptimeMillis ?: lastUptime
                                when {
                                    pressed.isEmpty() ->
                                        mode = if (twoFingerSeen) {
                                            TouchMode.TWO_FINGER_TAP
                                        } else {
                                            TouchMode.TAP
                                        }

                                    pressed.size >= 2 -> {
                                        twoFingerSeen = true
                                        val avgY = pressed.map { it.position.y }.average().toFloat()
                                        val anchor = scrollAnchorY
                                        if (anchor == null) {
                                            scrollAnchorY = avgY
                                        } else if (abs(avgY - anchor) > touchSlop) {
                                            mode = TouchMode.SCROLL
                                        }
                                    }

                                    // 出现过两根手指后又抬起一根：不再回到单指分支，
                                    // 静候全部抬起判成双指轻点
                                    twoFingerSeen -> Unit

                                    else -> {
                                        val change = event.changes
                                            .firstOrNull { it.id == down.id }
                                            ?: pressed.first()
                                        accumulated += change.position - lastPos
                                        lastPos = change.position
                                        if (accumulated.getDistance() > touchSlop) {
                                            mode = TouchMode.DRAG
                                        }
                                    }
                                }
                            }
                        }

                        // 超时 = 手指一直没动也没多按。双指按住时按双指轻点处理，
                        // 单指按住才是长按。
                        mode = mode
                            ?: if (twoFingerSeen) TouchMode.TWO_FINGER_TAP else TouchMode.LONG_PRESS
                    } else {
                        // 待命中的第二次按下直接进入拖动：左键此刻已经按下。
                        // 跳过判定意味着这段时间里不会有「长按 = 右键」——
                        // 用户按住不动就是在等一个拖动起点，不是在要右键菜单。
                        mode = TouchMode.DRAG
                    }

                    // ---- 执行阶段 ----
                    // tapDragHeld：本次触摸已经把左键按下并保持。**所有**出口都必须补 up，
                    // 否则 Windows 侧左键会一直保持按下。
                    var tapDragHeld = false
                    if (armed) {
                        currentOnPress("left")
                        currentHaptics.click()
                        tapDragHeld = true
                    }

                    // 局部非空副本：armed 分支里 mode 一定被赋过值，但编译器推不出来
                    val decided = requireNotNull(mode)

                    when (decided) {
                        TouchMode.TAP -> {
                            currentOnClick("left")
                            currentHaptics.click()
                            // 记下待命：短时间内、在附近再次按下就是 tap-drag
                            armedPosition = down.position
                            armedAtUptime = lastUptime
                        }

                        TouchMode.TWO_FINGER_TAP -> {
                            currentOnClick("right")
                            currentHaptics.longPress()
                        }

                        TouchMode.LONG_PRESS -> {
                            // 判定超时的瞬间就给反馈：手指还按着，震动是唯一的确认信号
                            currentHaptics.longPress()
                            currentOnClick("right")
                            // 吃掉后续事件直到全部抬起，避免抬手时又被判成点击
                            while (true) {
                                if (awaitPointerEvent().changes.none { it.pressed }) break
                            }
                        }

                        TouchMode.DRAG -> {
                            dragging = true
                            touchIndicator.value = down.position
                            var accX = 0f
                            var accY = 0f

                            fun flush(dx: Float, dy: Float) {
                                accX += dx * currentPointerSensitivity
                                accY += dy * currentPointerSensitivity
                                // toInt() 向零截断，余量带着符号留在累加器里
                                val ix = accX.toInt()
                                val iy = accY.toInt()
                                if (ix != 0 || iy != 0) {
                                    accX -= ix
                                    accY -= iy
                                    currentOnMove(ix, iy)
                                }
                            }

                            // 判定阶段已经积累的位移不能丢
                            flush(accumulated.x, accumulated.y)

                            while (true) {
                                val event = awaitPointerEvent()
                                val pressed = event.changes.filter { it.pressed }

                                // tap-drag 途中插入第二根手指：退出拖动、转成双指滚动。
                                // 不处理的话左键会一直保持按下，而且单指的移动会跟
                                // 滚动手势抢同一批事件。左键的 up 由循环外的兜底统一发，
                                // 与「正常抬手结束」走同一条路，不会漏。
                                if (tapDragHeld && pressed.size >= 2) {
                                    currentHaptics.tick()
                                    scrollWithTwoFingers(
                                        currentScrollSensitivity, currentOnScroll,
                                        pressed.map { it.position.y }.average().toFloat(),
                                    )
                                    break
                                }

                                val change = event.changes.firstOrNull { it.id == down.id }
                                if (change == null) break
                                // 抬手事件里也带着最后一段位移，必须算完再退出。
                                //
                                // 上一版是 `if (!change.pressed) break`，直接丢掉了从
                                // 最后一个 MOVE 到抬手位置之间的这一段 —— 抓包实测：
                                // 140px 的滑动只发出 128px 的位移。表现为指针总是停在
                                // 手指停顿位置的前面一点，精细拖动（拖窗口边缘、选文字）
                                // 会持续偏短。
                                val delta = change.position - lastPos
                                lastPos = change.position
                                if (delta != Offset.Zero) {
                                    touchIndicator.value = change.position
                                    flush(delta.x, delta.y)
                                }
                                if (!change.pressed) break
                                change.consume()
                            }
                            dragging = false
                            touchIndicator.value = null
                        }

                        TouchMode.SCROLL -> {
                            scrollWithTwoFingers(
                                currentScrollSensitivity, currentOnScroll, scrollAnchorY,
                            )
                        }
                    }

                    // 兜底：tap-drag 的左键无论从哪条路径退出都要松开。
                    // 手势被系统打断（来电、通知遮挡）时不会走到任何 break，
                    // 只有这里能保证 Windows 侧不会留下一个永远按住的左键。
                    if (tapDragHeld) {
                        currentOnRelease("left")
                    }
                }
            },
        shape = MaterialTheme.shapes.large,
        // 拖动时切到 surfaceVariant + 主色描边：手指按住的一瞬间整块板变亮，
        // 是「正在拖动」最直观的信号。中央那行大标题因此去掉了 ——
        // 它一直挡在手指落点的位置，拖动时看不见自己在拖到哪。
        color = if (dragging) {
            MaterialTheme.colorScheme.surfaceVariant
        } else {
            MaterialTheme.colorScheme.surface
        },
        tonalElevation = if (dragging) 0.dp else 4.dp,
        border = BorderStroke(
            width = 1.dp,
            color = if (dragging) {
                MaterialTheme.colorScheme.primary
            } else {
                MaterialTheme.colorScheme.outlineVariant
            },
        ),
    ) {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            // 触摸点涟漪：拖动时给出「手指被识别到了」的即时反馈。
            // 状态只在绘制阶段读取，不会引起重组。
            Canvas(modifier = Modifier.fillMaxSize()) {
                touchIndicator.value?.let { pos ->
                    drawCircle(
                        color = rippleColor,
                        radius = 28.dp.toPx(),
                        center = pos,
                    )
                }
            }

            // 手势提示收到最底下：能被第一次打开的人看见，又不挡拖动
            Text(
                text = hint,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 12.dp, start = 24.dp, end = 24.dp),
            )
        }
    }
}
