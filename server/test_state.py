"""state.py 的单元测试：连接状态机与运行状态快照。

这里是「状态不再未知」的核心 —— 改造前 `ClientInfo.status` 只有 connected /
disconnected，而且在**握手之前**就置成 connected，于是没通过令牌校验的连接在
界面上照样显示「在线」。下面逐条盯住新的状态流转。

运行：
    cd server && python test_state.py
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state


class ClientRecordTest(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 10, 5, 12, 0, 0)
        self.record = state.ClientRecord("10.0.0.1", 5000, now=self.t0)

    def test_initial_state_is_connecting_not_online(self):
        """关键回归：TCP 连上 ≠ 能用。握手之前绝不能显示「在线」。"""
        self.assertEqual(self.record.state, state.STATE_CONNECTING)
        self.assertFalse(self.record.is_online)
        self.assertEqual(self.record.addr, "10.0.0.1:5000")
        self.assertIsNone(self.record.authenticated_at)
        self.assertEqual(self.record.messages, 0)

    def test_mark_online_records_authentication_time(self):
        self.record.mark_online(now=self.t0 + timedelta(seconds=1))
        self.assertEqual(self.record.state, state.STATE_ONLINE)
        self.assertTrue(self.record.is_online)
        self.assertEqual(self.record.authenticated_at, self.t0 + timedelta(seconds=1))

    def test_mark_rejected_keeps_the_reason(self):
        self.record.mark_rejected("AUTH_FAILED", now=self.t0 + timedelta(seconds=1))
        self.assertEqual(self.record.state, state.STATE_REJECTED)
        self.assertEqual(self.record.reason, "AUTH_FAILED")
        self.assertFalse(self.record.is_online)

    def test_rejected_survives_the_following_disconnect(self):
        """被拒的连接马上会被服务端关掉。用户要看到的是「为什么被拒」，
        而不是它后来被关掉了这个次要事实。"""
        self.record.mark_rejected("AUTH_FAILED", now=self.t0)
        self.record.mark_offline("rejected", now=self.t0 + timedelta(seconds=1))

        self.assertEqual(self.record.state, state.STATE_REJECTED)
        self.assertEqual(self.record.reason, "AUTH_FAILED")
        self.assertEqual(self.record.disconnected_at, self.t0)

    def test_offline_keeps_the_disconnect_reason(self):
        self.record.mark_online(now=self.t0)
        self.record.mark_offline("idle_timeout", now=self.t0 + timedelta(seconds=20))

        self.assertEqual(self.record.state, state.STATE_OFFLINE)
        self.assertEqual(self.record.reason, "idle_timeout")
        self.assertFalse(self.record.is_online)

    def test_online_clears_a_previous_reason(self):
        record = state.ClientRecord("10.0.0.1", 5001, now=self.t0)
        record.reason = "stale"
        record.mark_online(now=self.t0)
        self.assertIsNone(record.reason)

    def test_note_message_counts_and_timestamps(self):
        self.record.note_message(now=self.t0)
        self.record.note_message(now=self.t0 + timedelta(seconds=2))
        self.assertEqual(self.record.messages, 2)
        self.assertEqual(self.record.last_message_at, self.t0 + timedelta(seconds=2))

    def test_to_dict_is_json_friendly(self):
        self.record.mark_online(now=self.t0)
        self.record.note_message(now=self.t0)
        payload = self.record.to_dict()

        self.assertEqual(payload["state"], state.STATE_ONLINE)
        self.assertEqual(payload["addr"], "10.0.0.1:5000")
        self.assertEqual(payload["messages"], 1)
        self.assertEqual(payload["connected_at"], "2026-10-05T12:00:00")
        self.assertIn("state_text", payload)
        # 必须能被 json.dumps 直接序列化 —— 状态文件就是这么写出去的
        import json
        json.dumps(payload)


class DescribeTest(unittest.TestCase):
    def _record(self, **kwargs):
        record = state.ClientRecord("10.0.0.1", 1)
        for key, value in kwargs.items():
            setattr(record, key, value)
        return record

    def test_online(self):
        self.assertEqual(state.describe(self._record(state=state.STATE_ONLINE)), "在线")

    def test_connecting_is_not_described_as_online(self):
        text = state.describe(self._record(state=state.STATE_CONNECTING))
        self.assertNotIn("在线", text)
        self.assertIn("未配对", text)

    def test_rejected_shows_the_reason(self):
        text = state.describe(
            self._record(state=state.STATE_REJECTED, reason="AUTH_FAILED")
        )
        self.assertIn("令牌错误", text)

    def test_offline_shows_the_reason(self):
        text = state.describe(
            self._record(state=state.STATE_OFFLINE, reason="idle_timeout")
        )
        self.assertIn("空闲超时", text)

    def test_unknown_state_falls_back_to_the_raw_value(self):
        self.assertEqual(state.describe(self._record(state="weird")), "weird")

    def test_every_known_state_has_text(self):
        for value in (state.STATE_CONNECTING, state.STATE_ONLINE,
                      state.STATE_REJECTED, state.STATE_OFFLINE):
            self.assertIn(value, state.STATE_TEXT)


class MaskTokenTest(unittest.TestCase):
    def test_masks_all_but_the_first_four(self):
        self.assertEqual(state.mask_token("GBGUAWW9"), "GBGU****")

    def test_short_token_is_fully_masked(self):
        self.assertEqual(state.mask_token("AB"), "**")
        self.assertEqual(state.mask_token(""), "")

    def test_none_is_safe(self):
        self.assertEqual(state.mask_token(None), "")


class ServerStateTest(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 10, 5, 12, 0, 0)
        self.state = state.ServerState(
            pid=1234, mode="gui", host="0.0.0.0", port=5800,
            protocol_version="1.1", data_dir="C:\\data",
            log_file="C:\\data\\logs\\server.log", token="GBGUAWW9",
            now=self.t0,
        )

    def test_full_lifecycle(self):
        record = self.state.client_connected("127.0.0.1", 60047)
        self.assertEqual(self.state.online_count(), 0)

        self.state.client_message(record.addr)
        self.state.client_authenticated(record.addr)
        self.assertEqual(self.state.online_count(), 1)
        self.assertTrue(record.is_online)

        self.state.client_disconnected(record.addr, "client_closed")
        self.assertEqual(self.state.online_count(), 0)
        self.assertEqual(record.reason, "client_closed")
        self.assertEqual(record.messages, 1)

    def test_rejected_client_is_recorded_with_the_code(self):
        record = self.state.client_connected("10.0.0.9", 5000)
        self.state.client_rejected(record.addr, "AUTH_FAILED")

        self.assertEqual(record.state, state.STATE_REJECTED)
        self.assertEqual(record.reason, "AUTH_FAILED")
        self.assertEqual(self.state.online_count(), 0)

    def test_events_for_unknown_addresses_are_ignored(self):
        """事件可能晚于连接清理到达（断开事件与状态清理是两条线程），不能崩。"""
        self.assertIsNone(self.state.client_authenticated("nope:1"))
        self.assertIsNone(self.state.client_rejected("nope:1", "AUTH_FAILED"))
        self.assertIsNone(self.state.client_disconnected("nope:1", "error"))
        self.assertIsNone(self.state.client_message("nope:1"))

    def test_get_clients_returns_a_snapshot(self):
        self.state.client_connected("127.0.0.1", 1)
        snapshot = self.state.get_clients()
        snapshot.clear()
        self.assertEqual(
            len(self.state.get_clients()), 1,
            "get_clients 返回的必须是副本，否则调用方一改就动到了内部状态",
        )

    def test_clear_clients(self):
        self.state.client_connected("127.0.0.1", 1)
        self.state.clear_clients()
        self.assertEqual(self.state.get_clients(), [])

    def test_bound_port_can_be_backfilled(self):
        """--port 0 时端口由内核分配，必须能回填，否则状态文件里会写 0。"""
        self.state.set_bound_port(51234)
        self.assertEqual(self.state.snapshot()["port"], 51234)

    def test_control_endpoint_defaults_to_none(self):
        self.assertIsNone(self.state.snapshot()["control"])

        self.state.set_control_endpoint("127.0.0.1", 60123)
        self.assertEqual(
            self.state.snapshot()["control"], {"host": "127.0.0.1", "port": 60123}
        )

    def test_running_flag(self):
        self.assertFalse(self.state.snapshot()["running"])
        self.state.set_running(True)
        self.assertTrue(self.state.snapshot()["running"])

    def test_snapshot_shape(self):
        self.state.client_connected("127.0.0.1", 60047)
        payload = self.state.snapshot(now=self.t0)

        for key in ("schema", "pid", "mode", "running", "started_at", "updated_at",
                    "host", "port", "protocol_version", "token_masked",
                    "data_dir", "log_file", "online_count", "control", "clients"):
            self.assertIn(key, payload)

        self.assertEqual(payload["schema"], state.STATUS_SCHEMA)
        self.assertEqual(payload["pid"], 1234)
        self.assertEqual(payload["mode"], "gui")
        self.assertEqual(payload["protocol_version"], "1.1")
        self.assertEqual(payload["updated_at"], "2026-10-05T12:00:00")
        self.assertEqual(len(payload["clients"]), 1)

    def test_snapshot_never_leaks_the_full_token(self):
        payload = self.state.snapshot()
        self.assertEqual(payload["token_masked"], "GBGU****")
        self.assertNotIn("GBGUAWW9", str(payload))

    def test_uptime(self):
        self.assertEqual(self.state.uptime_seconds(now=self.t0), 0)
        self.assertEqual(
            self.state.uptime_seconds(now=self.t0 + timedelta(seconds=90)), 90
        )
        # 时钟回拨不能算出负的运行时间
        self.assertEqual(
            self.state.uptime_seconds(now=self.t0 - timedelta(seconds=90)), 0
        )


class ClientHistoryTest(unittest.TestCase):
    """淘汰规则。键里含源端口，每次重连都是新键，漏淘汰就是内存泄漏。"""

    def setUp(self):
        self._orig_limit = state.MAX_CLIENT_HISTORY
        self.state = state.ServerState(
            pid=1, mode="headless", host="0.0.0.0", port=5800,
            protocol_version="1.1",
        )

    def tearDown(self):
        state.MAX_CLIENT_HISTORY = self._orig_limit

    def _add(self, addr, status=state.STATE_OFFLINE, age_seconds=0):
        """塞一条记录进去，connected_at 用来控制「谁更老」。"""
        peer, _, port = addr.partition(":")
        record = self.state.client_connected(peer, int(port))
        record.state = status
        record.connected_at = datetime.now() - timedelta(seconds=age_seconds)
        return record

    def test_evicts_down_to_the_limit(self):
        self.state.max_history = 3
        for i in range(5):
            self._add(f"c{i}:1", age_seconds=100 - i)
        self.assertEqual(len(self.state.get_clients()), 3)

    def test_offline_records_evicted_before_online_ones(self):
        self.state.max_history = 2
        online = self._add("online:1", status=state.STATE_ONLINE, age_seconds=1000)
        off_old = self._add("off_old:1", age_seconds=500)
        off_new = self._add("off_new:1", age_seconds=1)

        remaining = self.state.get_clients()
        self.assertIn(online, remaining, "在线记录即使最老也应保留")
        self.assertIn(off_new, remaining)
        self.assertNotIn(off_old, remaining, "应优先淘汰离线记录")

    def test_oldest_evicted_first_among_same_status(self):
        self.state.max_history = 2
        oldest = self._add("a:1", age_seconds=300)
        middle = self._add("b:1", age_seconds=200)
        newest = self._add("c:1", age_seconds=100)

        remaining = self.state.get_clients()
        self.assertNotIn(oldest, remaining)
        self.assertIn(middle, remaining)
        self.assertIn(newest, remaining)

    def test_newly_added_record_is_never_evicted(self):
        self.state.max_history = 1
        self._add("old:1", age_seconds=100)
        fresh = self._add("fresh:1")

        self.assertEqual(self.state.get_clients(), [fresh])

    def test_terminates_when_only_the_new_record_remains(self):
        # 上限为 0 时淘汰循环找不到候选。若没有 default=None + break，
        # 这里会死循环 —— 这是最容易写漏的一条路径。
        self.state.max_history = 0
        info = self._add("only:1")

        self.assertEqual(self.state.get_clients(), [info])

    def test_history_stays_bounded_across_many_reconnects(self):
        self.state.max_history = 10
        for i in range(500):
            self._add(f"reconnect-{i}:1", age_seconds=500 - i)

        self.assertEqual(len(self.state.get_clients()), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
