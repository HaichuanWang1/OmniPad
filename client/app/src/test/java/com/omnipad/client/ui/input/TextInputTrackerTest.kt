package com.omnipad.client.ui.input

import com.omnipad.client.network.Keyboard
import com.omnipad.client.network.OmniPadMessage
import com.omnipad.client.network.TextInput
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 实时键盘的差分逻辑。
 *
 * 这里的每一条都对应一个真实会出问题的场景：拼音还在候选框里就发出去、
 * 退格退多了、选中替换后远端内容对不上。
 */
class TextInputTrackerTest {

    private val tracker = TextInputTracker()

    /** 断言消息序列，同时把每条消息的 JSON 拼出来便于看失败详情。 */
    private fun assertMessages(expected: List<OmniPadMessage>, actual: List<OmniPadMessage>) {
        assertEquals(
            expected.map { it.toJson() },
            actual.map { it.toJson() },
        )
    }

    @Test
    fun `初始状态没有任何待发送内容`() {
        assertEquals("", tracker.sentText)
        assertTrue(tracker.onTextChanged("").isEmpty())
    }

    @Test
    fun `逐字输入只发增量`() {
        assertMessages(listOf(TextInput("h")), tracker.onTextChanged("h"))
        assertMessages(listOf(TextInput("e")), tracker.onTextChanged("he"))
        assertMessages(listOf(TextInput("l")), tracker.onTextChanged("hel"))
        assertEquals("hel", tracker.sentText)
    }

    @Test
    fun `一次粘贴多个字只发一条消息`() {
        assertMessages(listOf(TextInput("你好，世界")), tracker.onTextChanged("你好，世界"))
    }

    @Test
    fun `退格发一次 backspace`() {
        tracker.onTextChanged("hello")
        assertMessages(listOf(Keyboard("backspace", "press")), tracker.onTextChanged("hell"))
        assertEquals("hell", tracker.sentText)
    }

    @Test
    fun `连删多个字符发对应次数的 backspace`() {
        tracker.onTextChanged("hello")
        assertMessages(
            List(5) { Keyboard("backspace", "press") },
            tracker.onTextChanged(""),
        )
        assertEquals("", tracker.sentText)
    }

    @Test
    fun `选中替换等价于删掉再补上`() {
        tracker.onTextChanged("hello world")
        // 选中 "world" 打成 "there"
        assertMessages(
            List(5) { Keyboard("backspace", "press") } + TextInput("there"),
            tracker.onTextChanged("hello there"),
        )
        assertEquals("hello there", tracker.sentText)
    }

    @Test
    fun `光标移到中间插入也能让远端收敛到同一结果`() {
        tracker.onTextChanged("hello")
        // 在 "he" 之后插入 "X"
        assertMessages(
            List(3) { Keyboard("backspace", "press") } + TextInput("Xllo"),
            tracker.onTextChanged("heXllo"),
        )
        assertEquals("heXllo", tracker.sentText)
    }

    // ---- 输入法合成态 ----

    @Test
    fun `拼音还在候选框里时不发任何内容`() {
        assertTrue(tracker.onTextChanged("n", composingStart = 0, composingEnd = 1).isEmpty())
        assertTrue(tracker.onTextChanged("ni", composingStart = 0, composingEnd = 2).isEmpty())
        assertTrue(tracker.onTextChanged("nihao", composingStart = 0, composingEnd = 5).isEmpty())
        assertEquals("", tracker.sentText)
    }

    @Test
    fun `合成提交后只发提交结果`() {
        tracker.onTextChanged("nihao", composingStart = 0, composingEnd = 5)
        assertMessages(listOf(TextInput("你好")), tracker.onTextChanged("你好"))
        assertEquals("你好", tracker.sentText)
    }

    @Test
    fun `在已有文本后面继续用输入法`() {
        tracker.onTextChanged("hi ")
        tracker.onTextChanged("hi n", composingStart = 3, composingEnd = 4)
        tracker.onTextChanged("hi ni", composingStart = 3, composingEnd = 5)
        assertMessages(listOf(TextInput("你")), tracker.onTextChanged("hi 你"))
        assertEquals("hi 你", tracker.sentText)
    }

    @Test
    fun `合成中途取消不产生任何消息`() {
        tracker.onTextChanged("abc")
        tracker.onTextChanged("abcx", composingStart = 3, composingEnd = 4)
        // 用户按 Esc 取消候选，合成文本消失
        assertTrue(tracker.onTextChanged("abc").isEmpty())
        assertEquals("abc", tracker.sentText)
    }

    @Test
    fun `合成区间越界时按无合成处理而不是崩溃`() {
        assertMessages(listOf(TextInput("abc")), tracker.onTextChanged("abc", 1, 99))
        assertMessages(listOf(TextInput("d")), tracker.onTextChanged("abcd", -1, -1))
        // start == end 是空区间，等同于无合成
        assertMessages(listOf(TextInput("e")), tracker.onTextChanged("abcde", 4, 4))
    }

    // ---- 回车与清空 ----

    @Test
    fun `回车发送 enter 并清空本地缓冲`() {
        tracker.onTextChanged("hello")
        assertMessages(listOf(Keyboard("enter", "press")), tracker.onEnter())
        assertEquals("", tracker.sentText)
    }

    @Test
    fun `回车之后继续输入从空开始而不是补发旧内容`() {
        tracker.onTextChanged("hello")
        tracker.onEnter()
        assertMessages(listOf(TextInput("n")), tracker.onTextChanged("n"))
        assertEquals("n", tracker.sentText)
    }

    @Test
    fun `清空会把远端已输入的内容一并退格删掉`() {
        tracker.onTextChanged("abc")
        assertMessages(List(3) { Keyboard("backspace", "press") }, tracker.onClear())
        assertEquals("", tracker.sentText)
    }

    @Test
    fun `本来就没有内容时清空不发消息`() {
        assertTrue(tracker.onClear().isEmpty())
    }

    @Test
    fun `reset 之后旧内容不会被当成已发送`() {
        tracker.onTextChanged("abc")
        tracker.reset()
        assertMessages(listOf(TextInput("a")), tracker.onTextChanged("a"))
    }

    // ---- 边界 ----

    @Test
    fun `内容没变化时不发消息`() {
        tracker.onTextChanged("abc")
        assertTrue(tracker.onTextChanged("abc").isEmpty())
    }

    @Test
    fun `emoji 按 UTF-16 码元计数退格`() {
        // Windows 编辑框同样按码元退格，一个 emoji 需要两次退格才能删掉
        tracker.onTextChanged("😀")
        assertMessages(List(2) { Keyboard("backspace", "press") }, tracker.onTextChanged(""))
    }

    @Test
    fun `保留 emoji 只补后面的字符`() {
        tracker.onTextChanged("😀")
        assertMessages(listOf(TextInput("a")), tracker.onTextChanged("😀a"))
    }

    @Test
    fun `大量文本的整段替换不会串位`() {
        val original = "a".repeat(500)
        val replacement = "b".repeat(300)
        tracker.onTextChanged(original)
        val messages = tracker.onTextChanged(replacement)
        assertEquals(500 + 1, messages.size)
        assertEquals(
            List(500) { Keyboard("backspace", "press") } + TextInput(replacement),
            messages,
        )
    }
}
