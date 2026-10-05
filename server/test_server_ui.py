"""server_ui 的纯逻辑测试。

Tkinter 的界面部分没法自动化测，但客户端历史的淘汰规则是纯逻辑，而且它管的是一张
会随重连不断增长的字典 —— 键里含源端口，每次重连都是新键，漏淘汰就是内存泄漏。
这里不创建 Tk 根窗口，只测那部分；导入 server_ui 本身不需要显示器。
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server_ui


class GetLanIpTest(unittest.TestCase):
    def test_returns_a_dotted_quad(self):
        # 无外网时会回落到 127.0.0.1，两种都算正常。
        self.assertRegex(server_ui.get_lan_ip(), r"^\d{1,3}(\.\d{1,3}){3}$")

    def test_falls_back_to_loopback_and_closes_socket(self):
        with mock.patch("server_ui.socket.socket") as fake_socket:
            fake_socket.return_value.connect.side_effect = OSError("no route")
            self.assertEqual(server_ui.get_lan_ip(), "127.0.0.1")
            # finally 分支必须关掉 socket，否则每次调用都漏一个 fd
            self.assertTrue(fake_socket.return_value.close.called)


class ClientInfoTest(unittest.TestCase):
    def test_initial_state(self):
        info = server_ui.ClientInfo(("10.0.0.1", 5000))
        self.assertEqual(info.addr, ("10.0.0.1", 5000))
        self.assertEqual(info.status, "connected")
        self.assertIsNone(info.disconnected_at)
        self.assertIsInstance(info.connected_at, datetime)


class ClientHistoryTest(unittest.TestCase):
    """`_remember_client` 的淘汰规则。"""

    def setUp(self):
        self._orig_limit = server_ui.MAX_CLIENT_HISTORY
        self.server = server_ui.TcpServer()

    def tearDown(self):
        server_ui.MAX_CLIENT_HISTORY = self._orig_limit

    def _add(self, key, status="disconnected", age_seconds=0):
        """塞一条记录进去，connected_at 用来控制「谁更老」。"""
        info = server_ui.ClientInfo(("10.0.0.1", 1))
        info.status = status
        info.connected_at = datetime.now() - timedelta(seconds=age_seconds)
        self.server._remember_client(key, info)
        return info

    def test_remembers_client(self):
        info = self._add("a:1")
        self.assertEqual(self.server.get_clients(), [info])

    def test_get_clients_returns_a_snapshot(self):
        self._add("a:1")
        snapshot = self.server.get_clients()
        snapshot.clear()
        self.assertEqual(
            len(self.server.get_clients()), 1,
            "get_clients 返回的必须是副本，否则调用方一改就动到了内部状态",
        )

    def test_evicts_down_to_the_limit(self):
        server_ui.MAX_CLIENT_HISTORY = 3
        for i in range(5):
            self._add(f"c{i}:1", age_seconds=100 - i)
        self.assertEqual(len(self.server.get_clients()), 3)

    def test_offline_records_evicted_before_connected_ones(self):
        server_ui.MAX_CLIENT_HISTORY = 2
        online = self._add("online:1", status="connected", age_seconds=1000)
        off_old = self._add("off_old:1", status="disconnected", age_seconds=500)
        off_new = self._add("off_new:1", status="disconnected", age_seconds=1)

        remaining = self.server.get_clients()
        self.assertIn(online, remaining, "在线记录即使最老也应保留")
        self.assertIn(off_new, remaining)
        self.assertNotIn(off_old, remaining, "应优先淘汰离线记录")

    def test_oldest_evicted_first_among_same_status(self):
        server_ui.MAX_CLIENT_HISTORY = 2
        oldest = self._add("a:1", age_seconds=300)
        middle = self._add("b:1", age_seconds=200)
        newest = self._add("c:1", age_seconds=100)

        remaining = self.server.get_clients()
        self.assertNotIn(oldest, remaining)
        self.assertIn(middle, remaining)
        self.assertIn(newest, remaining)

    def test_newly_added_record_is_never_evicted(self):
        server_ui.MAX_CLIENT_HISTORY = 1
        self._add("old:1", age_seconds=100)
        fresh = self._add("fresh:1")

        self.assertEqual(self.server.get_clients(), [fresh])

    def test_terminates_when_only_the_new_record_remains(self):
        # 上限为 0 时淘汰循环找不到候选。若没有 default=None + break，
        # 这里会死循环 —— 这是最容易写漏的一条路径。
        server_ui.MAX_CLIENT_HISTORY = 0
        info = self._add("only:1")

        self.assertEqual(self.server.get_clients(), [info])

    def test_history_stays_bounded_across_many_reconnects(self):
        server_ui.MAX_CLIENT_HISTORY = 10
        for i in range(500):
            self._add(f"reconnect-{i}:1", age_seconds=500 - i)

        self.assertEqual(len(self.server.get_clients()), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
