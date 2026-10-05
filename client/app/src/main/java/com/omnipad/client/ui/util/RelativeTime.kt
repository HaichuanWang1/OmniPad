package com.omnipad.client.ui.util

/** 历史记录的相对时间档位。 */
enum class RelativeBucket { JUST_NOW, MINUTES, HOURS, DAYS }

data class RelativeTime(val bucket: RelativeBucket, val amount: Int)

/**
 * 把时间戳换算成「刚刚 / 12 分钟前 / 3 小时前 / 2 天前」。
 *
 * 纯函数，与文案资源解耦，便于单测 —— 边界（59 秒、60 分、24 小时）最容易写错，
 * 而这类错误在界面上要等很久才看得出来。
 */
object RelativeTimeFormatter {

    fun format(nowMs: Long, thenMs: Long): RelativeTime {
        val deltaSeconds = (nowMs - thenMs) / 1000
        // 负数说明记录的时间戳在未来（改过系统时间、或时钟回拨），当成刚刚处理，
        // 而不是显示成「-3 小时前」。
        if (deltaSeconds < 60) return RelativeTime(RelativeBucket.JUST_NOW, 0)

        val minutes = deltaSeconds / 60
        if (minutes < 60) return RelativeTime(RelativeBucket.MINUTES, minutes.toInt())

        val hours = minutes / 60
        if (hours < 24) return RelativeTime(RelativeBucket.HOURS, hours.toInt())

        return RelativeTime(RelativeBucket.DAYS, (hours / 24).toInt())
    }
}
