"""server_ui 的纯逻辑测试。

界面本身没法自动化测，但界面里那些**会算错**的部分都抽成了纯函数：表格行怎么排、
状态文字怎么写、日志缓冲什么时候丢旧行。改造前这些逻辑混在 Tk 回调里，
只能靠人眼盯着窗口看 —— 而「在线」写成假的正是这么漏过去的。

真建一个 Tk 窗口的用例默认跳过（CI 没有桌面会话），用 OMNIPAD_GUI_TEST=1 打开。

运行：
    cd server && python test_server_ui.py
    $env:OMNIPAD_GUI_TEST=1; python test_server_ui.py
"""
import logging
import os
import queue
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server_ui
import state as state_module

RUN_GUI = os.environ.get("OMNIPAD_GUI_TEST") == "1"


def make_record(peer="127.0.0.1", port=5000, state=state_module.STATE_ONLINE,
                connected_at=None, messages=0, reason=None):
    record = state_module.ClientRecord(peer, port, now=connected_at)
    record.state = state
    record.messages = messages
    record.reason = reason
    if state == state_module.STATE_ONLINE:
        record.authenticated_at = record.connected_at
    return record


class LogBufferTest(unittest.TestCase):
    """Tk 的 Text 控件不会自己丢旧行，跑一整天就是几十兆内存。"""

    def test_does_not_drop_before_the_limit(self):
        buffer = server_ui.LogBuffer(max_lines=3)
        self.assertEqual([buffer.add() for _ in range(3)], [0, 0, 0])
        self.assertEqual(buffer.line_count, 3)

    def test_drops_one_line_per_addition_after_the_limit(self):
        buffer = server_ui.LogBuffer(max_lines=3)
        for _ in range(3):
            buffer.add()
        self.assertEqual([buffer.add() for _ in range(4)], [1, 1, 1, 1])
        self.assertEqual(buffer.line_count, 3)

    def test_stays_bounded_over_a_long_run(self):
        buffer = server_ui.LogBuffer(max_lines=100)
        for _ in range(10_000):
            buffer.add()
        self.assertEqual(buffer.line_count, 100)

    def test_clear(self):
        buffer = server_ui.LogBuffer(max_lines=2)
        buffer.add()
        buffer.clear()
        self.assertEqual(buffer.line_count, 0)
        self.assertEqual(buffer.add(), 0)

    def test_default_limit_is_the_documented_one(self):
        self.assertEqual(server_ui.LogBuffer().max_lines, server_ui.MAX_LOG_LINES)


class FormatTimeTest(unittest.TestCase):
    def test_formats_a_datetime(self):
        self.assertEqual(
            server_ui.format_time(datetime(2026, 10, 5, 9, 8, 7)), "09:08:07"
        )

    def test_none_becomes_a_dash(self):
        self.assertEqual(server_ui.format_time(None), "-")


class ClientRowTagTest(unittest.TestCase):
    def test_every_state_has_a_colour(self):
        for value in (state_module.STATE_ONLINE, state_module.STATE_CONNECTING,
                      state_module.STATE_REJECTED, state_module.STATE_OFFLINE):
            with self.subTest(state=value):
                self.assertIn(server_ui.client_row_tag(make_record(state=value)),
                              server_ui.STATE_COLORS)

    def test_unknown_state_falls_back(self):
        self.assertEqual(
            server_ui.client_row_tag(make_record(state="weird")),
            state_module.STATE_OFFLINE,
        )


class ClientRowsTest(unittest.TestCase):
    def test_online_records_come_first(self):
        old = datetime(2026, 10, 5, 10, 0, 0)
        offline = make_record(port=1, state=state_module.STATE_OFFLINE,
                              connected_at=old)
        online = make_record(port=2, state=state_module.STATE_ONLINE,
                             connected_at=old + timedelta(minutes=5))

        rows = server_ui.client_rows([offline, online])
        self.assertEqual(rows[0][0], online.addr)
        self.assertEqual(rows[1][0], offline.addr)

    def test_row_shape(self):
        record = make_record(messages=7,
                             connected_at=datetime(2026, 10, 5, 12, 0, 0))
        record.last_message_at = record.connected_at
        row = server_ui.client_rows([record])[0]

        self.assertEqual(len(row), 6)
        addr, status, connected, last, messages, tag = row
        self.assertEqual(addr, record.addr)
        self.assertEqual(status, "在线")
        self.assertEqual(connected, "12:00:00")
        self.assertEqual(last, "12:00:00")
        self.assertEqual(messages, "7")
        self.assertEqual(tag, state_module.STATE_ONLINE)

    def test_rejected_record_shows_the_reason(self):
        record = make_record(state=state_module.STATE_REJECTED, reason="AUTH_FAILED")
        _addr, status, *_ = server_ui.client_rows([record])[0]
        self.assertIn("令牌错误", status)

    def test_never_online_record_is_not_shown_as_online(self):
        record = make_record(state=state_module.STATE_CONNECTING)
        _addr, status, *_ = server_ui.client_rows([record])[0]
        self.assertNotIn("在线", status)

    def test_empty(self):
        self.assertEqual(server_ui.client_rows([]), [])


class SessionSummaryTest(unittest.TestCase):
    def test_no_payload(self):
        self.assertEqual(server_ui.session_summary(None), "未运行")

    def test_no_clients(self):
        self.assertIn("等待手机连接",
                      server_ui.session_summary({"online_count": 0, "clients": []}))

    def test_online_count(self):
        text = server_ui.session_summary({
            "online_count": 2,
            "clients": [{}, {}, {}],
        })
        self.assertIn("2 台在线", text)
        self.assertIn("3 条记录", text)

    def test_history_without_online(self):
        text = server_ui.session_summary({"online_count": 0, "clients": [{}]})
        self.assertIn("无设备在线", text)


class TrayTooltipTest(unittest.TestCase):
    def test_waiting(self):
        self.assertIn("等待连接", server_ui.tray_tooltip({"port": 5800,
                                                        "online_count": 0}))

    def test_online(self):
        text = server_ui.tray_tooltip({"port": 5800, "online_count": 2})
        self.assertIn("2 台在线", text)
        self.assertIn("5800", text)

    def test_no_payload(self):
        self.assertEqual(server_ui.tray_tooltip(None), "OmniPad 服务端")


class PortOwnerTextTest(unittest.TestCase):
    def test_free_port(self):
        with mock.patch("server_ui.runtime.port_owner", return_value=None):
            self.assertIn("空闲", server_ui.port_owner_text(5800))

    def test_owned_by_someone_else(self):
        with mock.patch("server_ui.runtime.port_owner", return_value=999999):
            self.assertIn("999999", server_ui.port_owner_text(5800))
            self.assertIn("占用", server_ui.port_owner_text(5800))

    def test_owned_by_us(self):
        with mock.patch("server_ui.runtime.port_owner", return_value=os.getpid()):
            self.assertIn("本进程", server_ui.port_owner_text(5800))

    def test_no_port(self):
        self.assertIn("空闲", server_ui.port_owner_text(0))


class QueueHandlerTest(unittest.TestCase):
    def test_records_land_in_the_queue(self):
        target = queue.Queue()
        handler = server_ui.QueueHandler(target)
        handler.emit(logging.LogRecord("OmniPad", logging.INFO, "", 0, "hi",
                                       None, None))
        self.assertEqual(target.get_nowait().getMessage(), "hi")


class OpenPathTest(unittest.TestCase):
    def test_missing_path_returns_false_instead_of_raising(self):
        self.assertFalse(server_ui.open_path(os.path.join(
            os.path.dirname(__file__), "definitely-missing-file-xyz")))


class DpiTest(unittest.TestCase):
    def test_does_not_raise(self):
        server_ui.enable_dpi_awareness()      # 返回值与平台有关，不断言


@unittest.skipUnless(RUN_GUI, "需要桌面会话；用 OMNIPAD_GUI_TEST=1 打开")
class LiveGuiTest(unittest.TestCase):
    """真的把窗口搭起来。

    纯逻辑测试证明不了「控件名写错了」这类问题 —— 那些只有在真的构造一次
    界面时才会暴露。
    """

    def setUp(self):
        import server
        self._tmp = __import__("tempfile").TemporaryDirectory()
        self.session = server.ServerSession(
            data_dir=self._tmp.name, host="127.0.0.1", port=0,
            token="TEST1234", mode="gui",
        )
        self.app = server_ui.ServerApp(self.session)

    def tearDown(self):
        try:
            self.app.root.destroy()
        except Exception:
            pass
        self.session.stop()
        self._tmp.cleanup()

    def test_widgets_exist(self):
        for name in ("status_dot", "status_label", "address_label", "token_label",
                     "client_tree", "log_text", "uptime_label", "version_label"):
            self.assertTrue(hasattr(self.app, name), f"缺少控件 {name}")

    def test_status_bar_is_not_squeezed_out(self):
        """回归：状态栏曾经被日志区挤成一条缝。

        Tk 的 packer 按**打包顺序**分配空间，日志区 expand=True 又排在状态栏
        前面，于是状态栏只剩下几个像素 —— 文字全被裁掉，界面上看不出有这一栏。
        """
        self.app.root.deiconify()
        self.app.root.update()
        self.assertGreater(
            self.app.uptime_label.winfo_height(), 10,
            "状态栏被挤掉了，检查 _build_ui 里的打包顺序",
        )

    def test_client_table_uses_the_dark_theme(self):
        """回归：Windows 原生 ttk 主题会无视 Treeview 背景色，表格是一块白板。"""
        from tkinter import ttk
        self.assertEqual(ttk.Style().theme_use(), "clam")
        self.assertEqual(
            str(ttk.Style().lookup("Client.Treeview", "background")).lower(),
            server_ui.BG.lower(),
        )

    def test_refresh_methods_run(self):
        self.app._refresh_header()
        self.app._refresh_clients()
        self.app._refresh_clients()

    def test_hide_to_tray_and_restore(self):
        """「最小化到托盘」必须真的能回来，否则用户就找不回窗口了。"""
        self.app.root.update()
        self.assertTrue(self.app.root.winfo_viewable())

        self.app._hide_to_tray()
        self.app.root.update()
        self.assertFalse(self.app.root.winfo_viewable(), "窗口应当被藏起来")

        self.app._handle_tray_command(server_ui.TRAY_SHOW)
        self.app.root.update()
        self.assertTrue(self.app.root.winfo_viewable(), "托盘菜单必须能把窗口叫回来")

    def test_log_lines_are_rendered_and_trimmed(self):
        self.app.log_text.config(state="normal")
        for i in range(server_ui.MAX_LOG_LINES + 20):
            self.app.log_buffer.add()
            self.app.log_text.insert("end", f"line {i}\n")
        self.app.log_text.config(state="disabled")
        self.assertLessEqual(self.app.log_buffer.line_count,
                             server_ui.MAX_LOG_LINES)

    def test_self_check_dialog_builds(self):
        # 自检里会跑一次 netstat，这里只确认它不抛异常
        server_ui.port_owner_text(1)

    def test_shutdown_cancels_timers_and_is_idempotent(self):
        """关闭时必须撤掉定时回调。

        destroy() 之后 Tcl 解释器还在，排队的 after 回调照样会触发，
        然后去碰已经销毁的控件 —— 会甩出一堆 TclError。
        """
        self.app._poll()
        self.app._tick()
        self.assertIsNotNone(self.app._poll_id)
        self.assertIsNotNone(self.app._tick_id)

        self.app._shutdown()

        self.assertTrue(self.app._closing)
        self.assertIsNone(self.app._poll_id)
        self.assertIsNone(self.app._tick_id)
        self.app._shutdown()      # 第二次必须是空操作，不能抛


if __name__ == "__main__":
    unittest.main(verbosity=2)
