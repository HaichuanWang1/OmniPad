"""runtime.py 的单元测试：数据目录、进程存活、单实例、原子写、日志、端口占用。

这些是「状态不再未知」的地基。最容易出错、也最要命的一条在
`test_process_alive.py` 里：Windows 上 `os.kill(pid, 0)` 不是查询，是
TerminateProcess —— 用它做存活检测等于「顺手把服务端杀了」。

运行：
    cd server && python test_runtime.py
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import runtime

NETSTAT_SAMPLE = """
活动连接

  协议  本地地址          外部地址        状态           PID
  TCP    0.0.0.0:22             0.0.0.0:0              LISTENING       3784
  TCP    0.0.0.0:5800           0.0.0.0:0              LISTENING       2832
  TCP    127.0.0.1:5800         127.0.0.1:60047        ESTABLISHED     2832
  TCP    127.0.0.1:60123        0.0.0.0:0              LISTENING       9999
  TCP    [::]:5800              [::]:0                 LISTENING       2832
  TCP    [::1]:49670            [::]:0                 LISTENING       1111
  UDP    0.0.0.0:500             *:*                    1234
  这行是垃圾数据
"""


class DataDirTest(unittest.TestCase):
    def test_explicit_wins_over_everything(self):
        self.assertEqual(
            runtime.resolve_data_dir(
                explicit=r"C:\custom", env="C:\\env", appdata=r"C:\appdata"
            ),
            os.path.abspath(r"C:\custom"),
        )

    def test_env_wins_over_frozen(self):
        self.assertEqual(
            runtime.resolve_data_dir(env="/tmp/omnipad-env", frozen=True,
                                     appdata="/tmp/appdata"),
            os.path.abspath("/tmp/omnipad-env"),
        )

    def test_frozen_uses_appdata(self):
        self.assertEqual(
            runtime.resolve_data_dir(env="", frozen=True, appdata="/tmp/appdata"),
            os.path.join(os.path.abspath("/tmp/appdata"), runtime.APP_DIR_NAME),
        )

    def test_frozen_without_appdata_falls_back_to_home(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("os.path.expanduser", return_value="/home/tester"):
            path = runtime.resolve_data_dir(env="", frozen=True, appdata=None)
        expected = os.path.join(
            os.path.abspath("/home/tester/.config"), runtime.APP_DIR_NAME
        )
        self.assertEqual(path, expected)

    def test_source_run_keeps_the_script_directory(self):
        """源码运行时数据目录仍是 server/ —— 老用户的令牌不会因为升级而搬家。"""
        self.assertEqual(
            runtime.resolve_data_dir(env="", frozen=False),
            os.path.dirname(os.path.abspath(runtime.__file__)),
        )

    def test_env_is_read_from_the_environment_when_not_injected(self):
        with mock.patch.dict(os.environ, {runtime.ENV_DATA_DIR: "/tmp/from-env"}):
            self.assertEqual(
                runtime.resolve_data_dir(frozen=True, appdata="/tmp/appdata"),
                os.path.abspath("/tmp/from-env"),
            )

    def test_path_helpers(self):
        self.assertEqual(runtime.token_file_path("/d"),
                         os.path.join("/d", "pairing_token.txt"))
        self.assertEqual(runtime.status_file_path("/d"),
                         os.path.join("/d", "server_status.json"))
        self.assertEqual(runtime.log_file_path("/d"),
                         os.path.join("/d", "logs", "server.log"))


class AdoptTokenTest(unittest.TestCase):
    """老用户从 `python server_ui.py` 换到 exe 时，令牌要跟过去，不能逼他重新配对。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self._tmp.name, "data")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_adopts_an_existing_token(self):
        source = self._write("pairing_token.txt", "  gbguaww9  \n")
        self.assertEqual(runtime.adopt_token(self.data_dir, [source]), "GBGUAWW9")

        with open(runtime.token_file_path(self.data_dir), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "GBGUAWW9")

    def test_never_overwrites_an_existing_target(self):
        os.makedirs(self.data_dir)
        with open(runtime.token_file_path(self.data_dir), "w", encoding="utf-8") as f:
            f.write("KEEPME12\n")
        source = self._write("pairing_token.txt", "GBGUAWW9")

        self.assertIsNone(runtime.adopt_token(self.data_dir, [source]))
        with open(runtime.token_file_path(self.data_dir), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "KEEPME12")

    def test_skips_missing_and_empty_candidates(self):
        missing = os.path.join(self._tmp.name, "nope.txt")
        empty = self._write("empty.txt", "   \n")

        self.assertIsNone(runtime.adopt_token(self.data_dir, [missing, empty]))
        self.assertFalse(os.path.exists(runtime.token_file_path(self.data_dir)))

    def test_skips_the_target_itself(self):
        os.makedirs(self.data_dir, exist_ok=True)
        target = runtime.token_file_path(self.data_dir)
        with open(target, "w", encoding="utf-8") as f:
            f.write("SAME0001\n")

        # 目标已存在时本来就会提前返回；这里确认不会因为「候选就是目标」而自我复制
        self.assertIsNone(runtime.adopt_token(self.data_dir, [target]))

    def test_falls_through_to_a_later_candidate(self):
        missing = os.path.join(self._tmp.name, "nope.txt")
        good = self._write("good.txt", "FALLBACK\n")
        self.assertEqual(runtime.adopt_token(self.data_dir, [missing, good]), "FALLBACK")


class ProcessAliveTest(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(runtime.process_alive(os.getpid()))

    def test_finished_process_is_not_alive(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=30)
        self.assertFalse(runtime.process_alive(proc.pid))

    def test_bogus_pids_are_not_alive(self):
        for pid in (0, -1, None, "", "abc", 2 ** 40):
            with self.subTest(pid=pid):
                self.assertFalse(runtime.process_alive(pid))

    def test_does_not_kill_anything(self):
        """回归守卫：Windows 上 os.kill(pid, 0) 会真的结束目标进程。

        这里用一个真实存活的子进程做靶子，确认存活检测之后它还活着。
        """
        proc = subprocess.Popen([sys.executable, "-c",
                                 "import time; time.sleep(10)"])
        try:
            self.assertTrue(runtime.process_alive(proc.pid))
            self.assertIsNone(proc.poll(), "存活检测不该把目标进程干掉")
        finally:
            proc.kill()
            proc.wait(timeout=5)


class InstanceLockTest(unittest.TestCase):
    def setUp(self):
        self.name = f"OmniPad-Test-{uuid.uuid4().hex}"
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "lock")

    def tearDown(self):
        self._tmp.cleanup()

    def test_second_acquire_fails(self):
        first = runtime.InstanceLock(self.name, self.path)
        second = runtime.InstanceLock(self.name, self.path)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire(), "第二个实例必须被拦住")
        finally:
            first.release()
            second.release()

    def test_release_allows_reacquire(self):
        first = runtime.InstanceLock(self.name, self.path)
        self.assertTrue(first.acquire())
        first.release()

        second = runtime.InstanceLock(self.name, self.path)
        try:
            self.assertTrue(second.acquire())
        finally:
            second.release()

    def test_different_names_do_not_collide(self):
        a = runtime.InstanceLock(f"{self.name}-a", self.path)
        b = runtime.InstanceLock(f"{self.name}-b", self.path)
        try:
            self.assertTrue(a.acquire())
            self.assertTrue(b.acquire())
        finally:
            a.release()
            b.release()

    def test_context_manager(self):
        with runtime.InstanceLock(self.name, self.path):
            other = runtime.InstanceLock(self.name, self.path)
            self.assertFalse(other.acquire())

        after = runtime.InstanceLock(self.name, self.path)
        try:
            self.assertTrue(after.acquire())
        finally:
            after.release()


class AtomicJsonTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "sub", "server_status.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_missing_directories_and_round_trips(self):
        runtime.write_json_atomic(self.path, {"pid": 1, "中文": "值"})
        self.assertEqual(runtime.read_json(self.path), {"pid": 1, "中文": "值"})

        with open(self.path, encoding="utf-8") as f:
            self.assertIn("中文", f.read(), "状态文件必须保留中文原样")

    def test_overwrites_atomically_and_leaves_no_temp_files(self):
        for i in range(5):
            runtime.write_json_atomic(self.path, {"pid": i})

        self.assertEqual(runtime.read_json(self.path), {"pid": 4})
        leftovers = [n for n in os.listdir(os.path.dirname(self.path))
                     if n.startswith(".status-")]
        self.assertEqual(leftovers, [], "临时文件必须被 os.replace 消费掉")

    def test_read_json_tolerates_missing_and_broken_files(self):
        self.assertIsNone(runtime.read_json(self.path))

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        self.assertIsNone(runtime.read_json(self.path))

    def test_load_status_rejects_non_objects(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        self.assertIsNone(runtime.load_status(os.path.dirname(self.path)))

    def test_status_is_live(self):
        self.assertFalse(runtime.status_is_live(None))
        self.assertFalse(runtime.status_is_live({}))
        self.assertFalse(runtime.status_is_live({"pid": 2 ** 40}))
        self.assertTrue(runtime.status_is_live({"pid": os.getpid()}))


class LoggingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self._tmp.name, "logs", "server.log")
        self.logger = logging.getLogger("OmniPad")

    def tearDown(self):
        runtime.setup_logging(None, console=False)
        self._tmp.cleanup()

    def test_writes_to_the_log_file(self):
        runtime.setup_logging(self.log_path, console=False)
        self.logger.info("hello 中文")

        with open(self.log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("hello 中文", content)
        self.assertIn("INFO", content)

    def test_reconfiguring_does_not_duplicate_handlers(self):
        runtime.setup_logging(self.log_path, console=False)
        runtime.setup_logging(self.log_path, console=False)
        runtime.setup_logging(self.log_path, console=False)

        handlers = [h for h in self.logger.handlers
                    if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertEqual(len(handlers), 1, "重复配置会写出重复的日志行")

    def test_no_console_handler_when_stderr_is_none(self):
        """窗口模式的 exe 被双击启动时 sys.stderr 是 None。

        此时再加 StreamHandler，每条日志都会在 handleError 里被静默吞掉 ——
        与其假装在输出，不如不加。
        """
        with mock.patch.object(sys, "stderr", None):
            runtime.setup_logging(self.log_path, console=True)

        self.assertEqual(
            [h for h in self.logger.handlers if isinstance(h, logging.StreamHandler)
             and not isinstance(h, logging.FileHandler)],
            [],
        )

    def test_console_handler_added_when_stderr_exists(self):
        runtime.setup_logging(self.log_path, console=True)
        self.assertTrue(
            any(isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.FileHandler)
                for h in self.logger.handlers)
        )

    def test_rotation_is_configured(self):
        runtime.setup_logging(self.log_path, console=False)
        handler = next(h for h in self.logger.handlers
                       if isinstance(h, logging.handlers.RotatingFileHandler))
        self.assertEqual(handler.maxBytes, runtime.DEFAULT_LOG_MAX_BYTES)
        self.assertEqual(handler.backupCount, runtime.DEFAULT_LOG_BACKUPS)


class SplitHostPortTest(unittest.TestCase):
    def test_ipv4(self):
        self.assertEqual(runtime.split_host_port("127.0.0.1:5800"), ("127.0.0.1", 5800))

    def test_wildcard(self):
        self.assertEqual(runtime.split_host_port("0.0.0.0:5800"), ("0.0.0.0", 5800))

    def test_ipv6(self):
        self.assertEqual(runtime.split_host_port("[::]:5800"), ("::", 5800))
        self.assertEqual(runtime.split_host_port("[::1]:49670"), ("::1", 49670))

    def test_garbage(self):
        self.assertEqual(runtime.split_host_port(""), ("", None))
        self.assertEqual(runtime.split_host_port("nonsense"), ("nonsense", None))
        self.assertEqual(runtime.split_host_port("1.2.3.4:abc"), ("1.2.3.4", None))


class NetstatTest(unittest.TestCase):
    def test_parses_rows(self):
        rows = runtime.parse_netstat_rows(NETSTAT_SAMPLE)
        self.assertIn(("0.0.0.0:5800", "0.0.0.0:0", "LISTENING", 2832), rows)
        self.assertIn(("127.0.0.1:5800", "127.0.0.1:60047", "ESTABLISHED", 2832), rows)
        self.assertIn(("[::]:5800", "[::]:0", "LISTENING", 2832), rows)

    def test_ignores_headers_udp_and_garbage(self):
        rows = runtime.parse_netstat_rows(NETSTAT_SAMPLE)
        self.assertTrue(all(len(r) == 4 for r in rows))
        self.assertFalse(any("UDP" in str(r) for r in rows))
        self.assertEqual(len(rows), 6)

    def test_listening_pid_prefers_the_listener_over_established(self):
        rows = runtime.parse_netstat_rows(NETSTAT_SAMPLE)
        self.assertEqual(runtime.listening_pid(rows, 5800), 2832)

    def test_listening_pid_is_none_when_nobody_listens(self):
        rows = runtime.parse_netstat_rows(NETSTAT_SAMPLE)
        self.assertIsNone(runtime.listening_pid(rows, 65000))

    def test_listening_pid_falls_back_to_any_row(self):
        """只有 ESTABLISHED 行时也要能给出 PID（例如连接刚建立、监听行被过滤）。"""
        rows = [("127.0.0.1:5800", "127.0.0.1:1", "ESTABLISHED", 42)]
        self.assertEqual(runtime.listening_pid(rows, 5800), 42)

    def test_netstat_failure_is_not_fatal(self):
        with mock.patch("subprocess.run", side_effect=OSError("no netstat")):
            self.assertEqual(runtime.netstat_rows(), [])

    def test_port_owner_uses_injected_rows(self):
        rows = runtime.parse_netstat_rows(NETSTAT_SAMPLE)
        self.assertEqual(runtime.port_owner(5800, rows=rows), 2832)


class LocalAddressesTest(unittest.TestCase):
    def test_collects_and_dedupes_non_loopback_addresses(self):
        fake_sock = mock.MagicMock()
        fake_sock.getsockname.return_value = ("192.168.1.5", 51234)
        addrinfo = [
            (2, 1, 6, "", ("192.168.1.5", 0)),      # 与主地址重复
            (2, 1, 6, "", ("100.64.0.7", 0)),       # Tailscale
            (2, 1, 6, "", ("127.0.0.1", 0)),        # 回环，排除
            (2, 1, 6, "", ("169.254.3.4", 0)),      # 链路本地，排除
        ]
        with mock.patch("runtime.socket.socket", return_value=fake_sock), \
             mock.patch("runtime.socket.getaddrinfo", return_value=addrinfo):
            addresses = runtime.local_ipv4_addresses()

        self.assertEqual(addresses, ["192.168.1.5", "100.64.0.7"])
        self.assertTrue(fake_sock.close.called, "探测 socket 必须被关闭")

    def test_survives_no_network(self):
        fake_sock = mock.MagicMock()
        fake_sock.connect.side_effect = OSError("no route")
        with mock.patch("runtime.socket.socket", return_value=fake_sock), \
             mock.patch("runtime.socket.getaddrinfo", side_effect=OSError("no dns")):
            self.assertEqual(runtime.local_ipv4_addresses(), [])


class FormatUptimeTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(runtime.format_uptime(0), "00:00:00")
        self.assertEqual(runtime.format_uptime(59), "00:00:59")
        self.assertEqual(runtime.format_uptime(3661), "01:01:01")
        self.assertEqual(runtime.format_uptime(-5), "00:00:00")
        self.assertEqual(runtime.format_uptime(3600 * 100), "100:00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
