package com.omnipad.client.ui.input

import com.omnipad.client.network.Keyboard
import com.omnipad.client.network.OmniPadMessage
import com.omnipad.client.network.TextInput

/**
 * 把本地输入框的内容变化翻译成发往服务端的消息序列。
 *
 * 目标是「边打边发」：用户每提交一个字符，电脑上立刻出现，而不是打完一整段再点发送。
 *
 * ## 为什么不能简单地「每变一次就发一次」
 *
 * 输入法在中文/日文等场景下会先给出**未提交的合成文本**（拼音 `nihao` 还在候选框里）。
 * 这时候发出去，电脑上就会先出现一串拼音。所以必须把合成区间排除掉，只发已提交的部分。
 *
 * ## 为什么用差分而不是直接发
 *
 * 本地输入框是「已经发出去的内容」的镜像，用户随时可以退格或选中改写。与其去猜
 * 用户按了什么键，不如直接比较「远端当前应该是什么」和「本地现在是什么」：
 * 算出公共前缀，前缀之后的部分先退格删掉、再把新内容整段发过去。
 *
 * 这样无论是退格、选中替换、还是把光标移到中间插入，结果都正确 —— 因为最终态一致。
 * 远端的光标始终在末尾（我们只会追加），所以退格恰好删掉的是我们发过的那一段。
 *
 * 纯逻辑，不依赖任何 Android / Compose 类型，可直接在 JVM 上单测。
 */
class TextInputTracker {

    /** 已经发到远端的文本，也就是本地输入框应当显示的内容。 */
    var sentText: String = ""
        private set

    fun reset() {
        sentText = ""
    }

    /**
     * 处理一次输入框内容变化。
     *
     * @param text 输入框的完整文本，**包含**尚未提交的合成内容。
     * @param composingStart 合成区间起点（含）；无合成时传 -1。
     * @param composingEnd 合成区间终点（不含）；无合成时传 -1。
     * @return 按顺序应发送的消息；无需发送时为空列表。
     */
    fun onTextChanged(
        text: String,
        composingStart: Int = -1,
        composingEnd: Int = -1,
    ): List<OmniPadMessage> = reconcile(committedPartOf(text, composingStart, composingEnd))

    /**
     * 用户按下回车。
     *
     * 远端输入框在回车后通常会提交并清空，所以本地缓冲也一并清掉；
     * 否则下一次输入会和远端的实际内容对不上。
     */
    fun onEnter(): List<OmniPadMessage> {
        sentText = ""
        return listOf(Keyboard("enter", "press"))
    }

    /**
     * 清空本地缓冲，并把远端已输入的内容一并退格删掉。
     *
     * 语义是「撤销我刚打的这些字」，而不是「只把本地显示擦掉」—— 后者会让两边不一致。
     */
    fun onClear(): List<OmniPadMessage> = reconcile("")

    /** 剔除合成区间，只留下输入法已经提交的文本。 */
    private fun committedPartOf(text: String, start: Int, end: Int): String {
        val valid = start in 0..end && end <= text.length && start < end
        return if (valid) text.removeRange(start, end) else text
    }

    private fun reconcile(target: String): List<OmniPadMessage> {
        if (target == sentText) return emptyList()

        val common = commonPrefixLength(sentText, target)
        // 按 UTF-16 码元计数：Windows 的编辑框同样以码元为单位处理退格，
        // 所以一个 emoji 对应两次退格，与远端行为一致。
        val deletions = sentText.length - common
        val insertion = target.substring(common)

        val messages = ArrayList<OmniPadMessage>(deletions + 1)
        repeat(deletions) { messages += Keyboard("backspace", "press") }
        if (insertion.isNotEmpty()) messages += TextInput(insertion)

        sentText = target
        return messages
    }

    private fun commonPrefixLength(a: String, b: String): Int {
        val max = minOf(a.length, b.length)
        var i = 0
        while (i < max && a[i] == b[i]) i++
        return i
    }
}
