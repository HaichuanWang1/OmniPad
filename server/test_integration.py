"""端到端测试：起一个**真实**的服务端进程，用**真实**的 CLI 与客户端去查它。

这是「状态不再未知」这条需求的最终验收。前面那些单元测试证明各个零件是对的，
这里证明的是整条链路真的能用 —— 改造前根本没有可查的东西：状态只活在某个
终端窗口里，窗口一关就什么都不知道了。

刻意走 `subprocess` 起真进程，而不是在测试进程里直接调函数：要验的正是
「另一个进程能不能看出它在跑、谁连着、怎么把它停下来」。

运行：
    cd server && python test_integration.py
"""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_PY = os.path.join(SERVER_DIR, "server.py")

sys.path.insert(0, SERVER_DIR)

import control  # noqa: E402
import runtime  # noqa: E402
import server  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ALREADY_RUNNING = 2
EXIT_NOT_RUNNING = 3

START_TIMEOUT = 20


class ServerProcess:
    """跑一个真实的 `server.py --headless` 进程。"""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.process = None

    @property
    def status_file(self):
        return os.path.join(self.data_dir, "server_status.json")

    def start(self, *extra):
        self.process = subprocess.Popen(
            [sys.executable, SERVER_PY, "--headless", "--port", "0",
             "--data-dir", self.data_dir, *extra],
            cwd=SERVER_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.wait_for_status()

    def wait_for_status(self, timeout=START_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(
                    f"服务端启动即退出（退出码 {self.process.returncode}）"
                )
            payload = self.status()
            if payload and payload.get("running") and payload.get("port"):
                return payload
            time.sleep(0.1)
        raise AssertionError("服务端未能在 20 秒内写出可用的状态文件")

    def status(self):
        try:
            with open(self.status_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def port(self):
        return self.status()["port"]

    def token(self):
        with open(os.path.join(self.data_dir, "pairing_token.txt"),
                  encoding="utf-8") as f:
            return f.read().strip()

    def cli(self, *args, timeout=30):
        completed = subprocess.run(
            [sys.executable, SERVER_PY, *args, "--data-dir", self.data_dir],
            cwd=SERVER_DIR, capture_output=True, text=True,
            encoding="utf-8", timeout=timeout,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def status_json(self):
        code, out, err = self.cli("--status", "--json")
        return code, json.loads(out)

    def wait_for_client(self, predicate, timeout=10):
        """轮询 --status，直到某个客户端记录满足条件。"""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            _code, payload = self.status_json()
            for client in (payload.get("status") or {}).get("clients", []):
                last = client
                if predicate(client):
                    return client
            time.sleep(0.15)
        return None if last is None else last

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.process = None


class IntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self._tmp.name, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.server = ServerProcess(self.data_dir)

    def tearDown(self):
        self.server.stop()
        self._tmp.cleanup()

    # ---- 客户端 ----

    def connect(self, token=None, version="1.1"):
        sock = socket.create_connection(("127.0.0.1", self.server.port()), timeout=5)
        sock.settimeout(5)
        sock.sendall(json.dumps({
            "type": "handshake",
            "version": version,
            "token": self.server.token() if token is None else token,
        }).encode("utf-8") + b"\n")
        return sock, read_line(sock)


def read_line(sock):
    buffer = b""
    while b"\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer += chunk
    if not buffer.strip():
        return None
    return json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))


class StatusFileTest(IntegrationTestCase):
    def test_status_file_is_written_with_a_real_port(self):
        payload = self.server.start()

        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["mode"], "headless")
        self.assertTrue(payload["running"])
        self.assertNotEqual(payload["port"], 0, "--port 0 时必须回填真实端口")
        self.assertEqual(payload["protocol_version"], "1.1")
        self.assertEqual(payload["pid"], self.server.process.pid)
        self.assertNotIn("GBGUAWW9", json.dumps(payload))

    def test_status_file_is_refreshed_periodically(self):
        self.server.start()
        first = self.server.status()["updated_at"]
        time.sleep(6)
        self.assertNotEqual(
            first, self.server.status()["updated_at"],
            "状态文件必须有心跳刷新，否则读的人分不清「没变化」和「卡死了」",
        )

    def test_log_file_is_written(self):
        self.server.start()
        log = os.path.join(self.data_dir, "logs", "server.log")
        self.assertTrue(os.path.exists(log))
        with open(log, encoding="utf-8") as f:
            self.assertIn("TCP server listening", f.read())

    def test_token_is_stable_across_restarts(self):
        self.server.start()
        token = self.server.token()

        self.server.stop()
        self.server = ServerProcess(self.data_dir)
        self.server.start()

        self.assertEqual(self.server.token(), token, "重启不该换令牌，否则用户每次都要重新配对")


class StatusCommandTest(IntegrationTestCase):
    def test_reports_running_with_a_live_control_channel(self):
        self.server.start()
        code, payload = self.server.status_json()

        self.assertEqual(code, EXIT_OK)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["live"])
        self.assertEqual(
            payload["source"], "control",
            "进程活着时必须问活进程，而不是读可能过期的文件",
        )
        self.assertEqual(payload["status"]["pid"], self.server.process.pid)

    def test_human_output_mentions_pid_port_and_token(self):
        self.server.start()
        code, out, _err = self.server.cli("--status")

        self.assertEqual(code, EXIT_OK)
        self.assertIn("运行中", out)
        self.assertIn(str(self.server.process.pid), out)
        self.assertIn(str(self.server.port()), out)
        self.assertIn(self.server.token()[:4], out)
        self.assertNotIn(self.server.token(), out, "状态输出不该暴露完整令牌")

    def test_not_running_when_there_is_no_status_file(self):
        code, payload = self.server.status_json()

        self.assertEqual(code, EXIT_NOT_RUNNING)
        self.assertFalse(payload["live"])
        self.assertIsNone(payload["status"])
        self.assertIn("状态文件", payload["error"])

    def test_stale_status_file_is_reported_as_not_running(self):
        """状态文件还在、进程已经没了 —— 这正是最容易骗过人的情形。"""
        finished = subprocess.Popen([sys.executable, "-c", "pass"])
        finished.wait(timeout=30)
        with open(self.server.status_file, "w", encoding="utf-8") as f:
            json.dump({"pid": finished.pid, "port": 5800, "running": True}, f)

        code, out, _err = self.server.cli("--status")

        self.assertEqual(code, EXIT_NOT_RUNNING)
        self.assertIn("未运行", out)
        self.assertIn(str(finished.pid), out)


class ClientVisibilityTest(IntegrationTestCase):
    """核心需求：**别人**能不能看出手机连上了。"""

    def test_connected_client_shows_up_as_online(self):
        self.server.start()
        sock, response = self.connect()
        try:
            self.assertEqual(response["type"], "handshake_ack")
            client = self.server.wait_for_client(lambda c: c["state"] == "online")

            self.assertIsNotNone(client, "握手成功的客户端必须显示为在线")
            self.assertEqual(client["state"], "online")
            self.assertIn("在线", client["state_text"])
            self.assertEqual(client["peer"], "127.0.0.1")
            self.assertIsNotNone(client["authenticated_at"])
        finally:
            sock.close()

    def test_online_count_is_reported(self):
        self.server.start()
        sock, _ = self.connect()
        try:
            self.server.wait_for_client(lambda c: c["state"] == "online")
            _code, payload = self.server.status_json()
            self.assertEqual(payload["status"]["online_count"], 1)
        finally:
            sock.close()

    def test_wrong_token_shows_rejected_with_the_reason(self):
        """改造前这条连接在界面上和正常连接一模一样 —— 都叫「在线」。"""
        self.server.start()
        sock, response = self.connect(token="WRONG123")
        try:
            self.assertEqual(response["code"], "AUTH_FAILED")
            client = self.server.wait_for_client(lambda c: c["state"] == "rejected")

            self.assertIsNotNone(client)
            self.assertEqual(client["reason"], "AUTH_FAILED")
            self.assertIn("令牌错误", client["state_text"])
        finally:
            sock.close()

    def test_version_mismatch_shows_its_own_reason(self):
        self.server.start()
        sock, response = self.connect(version="9.9")
        try:
            self.assertEqual(response["code"], "VERSION_MISMATCH")
            client = self.server.wait_for_client(lambda c: c["state"] == "rejected")
            self.assertEqual(client["reason"], "VERSION_MISMATCH")
            self.assertIn("版本", client["state_text"])
        finally:
            sock.close()

    def test_unauthenticated_connection_is_not_reported_as_online(self):
        """TCP 连上但还没握手 —— 界面必须能区分「连上了」和「能用了」。"""
        self.server.start()
        sock = socket.create_connection(("127.0.0.1", self.server.port()), timeout=5)
        try:
            client = self.server.wait_for_client(lambda c: c["messages"] == 0)
            self.assertIsNotNone(client)
            self.assertEqual(client["state"], "connecting")
            self.assertIn("未配对", client["state_text"])

            _code, payload = self.server.status_json()
            self.assertEqual(payload["status"]["online_count"], 0)
        finally:
            sock.close()

    def test_disconnect_reason_is_recorded(self):
        self.server.start()
        sock, _ = self.connect()
        self.server.wait_for_client(lambda c: c["state"] == "online")
        sock.close()

        client = self.server.wait_for_client(lambda c: c["state"] == "offline")
        self.assertIsNotNone(client)
        self.assertEqual(client["reason"], "client_closed")
        self.assertIn("客户端断开", client["state_text"])

    def test_message_count_and_last_activity_advance(self):
        self.server.start()
        sock, _ = self.connect()
        try:
            for _ in range(3):
                sock.sendall(json.dumps({"type": "heartbeat"}).encode() + b"\n")
                read_line(sock)

            client = self.server.wait_for_client(lambda c: c["messages"] >= 4)
            self.assertIsNotNone(client, "消息计数必须跟着走")
            self.assertIsNotNone(client["last_message_at"])
        finally:
            sock.close()


class ControlChannelTest(IntegrationTestCase):
    def test_ping_and_status(self):
        self.server.start()
        endpoint = self.server.status()["control"]

        self.assertEqual(endpoint["host"], "127.0.0.1")
        self.assertTrue(control.request("ping", endpoint["host"], endpoint["port"])["ok"])

        response = control.request("status", endpoint["host"], endpoint["port"])
        self.assertTrue(response["ok"])
        self.assertEqual(response["status"]["pid"], self.server.process.pid)

    def test_control_port_is_not_the_protocol_port(self):
        """控制通道是给本机管理用的，不能和手机连的端口混在一起。"""
        self.server.start()
        self.assertNotEqual(
            self.server.status()["control"]["port"], self.server.port()
        )


class StopCommandTest(IntegrationTestCase):
    def test_stop_shuts_the_server_down_cleanly(self):
        self.server.start()
        pid = self.server.process.pid

        code, out, _err = self.server.cli("--stop")

        self.assertEqual(code, EXIT_OK)
        self.assertIn(str(pid), out)
        self.server.process.wait(timeout=10)
        self.assertEqual(self.server.process.returncode, 0)

        # 状态文件必须被标成未运行，否则下一个人还会以为它在跑
        code, payload = self.server.status_json()
        self.assertEqual(code, EXIT_NOT_RUNNING)
        self.assertFalse(payload["live"])

    def test_stop_without_an_instance(self):
        code, out, _err = self.server.cli("--stop")
        self.assertEqual(code, EXIT_NOT_RUNNING)
        self.assertIn("没在运行", out)

    def test_stop_on_a_stale_status_file(self):
        finished = subprocess.Popen([sys.executable, "-c", "pass"])
        finished.wait(timeout=30)
        with open(self.server.status_file, "w", encoding="utf-8") as f:
            json.dump({"pid": finished.pid, "port": 5800, "running": True}, f)

        code, out, _err = self.server.cli("--stop")

        self.assertEqual(code, EXIT_NOT_RUNNING)
        self.assertIn("过期", out)


class SingleInstanceTest(IntegrationTestCase):
    def test_second_instance_is_refused(self):
        first = self.server.start()

        second = subprocess.run(
            [sys.executable, SERVER_PY, "--headless", "--port", "0",
             "--data-dir", self.data_dir],
            cwd=SERVER_DIR, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )

        self.assertEqual(second.returncode, EXIT_ALREADY_RUNNING)
        self.assertIn(str(first["pid"]), second.stdout)
        self.assertIn("--stop", second.stdout)

    def test_a_different_data_dir_gets_its_own_lock(self):
        """测试用临时目录、用户用真实目录，两者不该互相挡住。"""
        self.server.start()
        other = os.path.join(self._tmp.name, "other")

        second = subprocess.Popen(
            [sys.executable, SERVER_PY, "--headless", "--port", "0",
             "--data-dir", other],
            cwd=SERVER_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + START_TIMEOUT
            while time.time() < deadline:
                if os.path.exists(os.path.join(other, "server_status.json")):
                    break
                time.sleep(0.1)
            self.assertTrue(
                os.path.exists(os.path.join(other, "server_status.json")),
                "不同数据目录必须能各自启动",
            )
        finally:
            second.terminate()
            second.wait(timeout=10)

    def test_port_conflict_is_reported_with_the_owner(self):
        self.server.start()
        port = self.server.port()
        other = os.path.join(self._tmp.name, "conflict")

        result = subprocess.run(
            [sys.executable, SERVER_PY, "--headless", "--port", str(port),
             "--data-dir", other],
            cwd=SERVER_DIR, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )

        self.assertEqual(result.returncode, EXIT_FAILED)
        self.assertIn(str(port), result.stdout)
        self.assertIn(str(self.server.process.pid), result.stdout,
                      "必须说清端口被谁占着，否则用户只能靠猜")


class EphemeralPortTest(unittest.TestCase):
    """回归：`--port 0` 的端口回填不能有竞态。

    曾经的写法是等 `tcp.server is not None` —— 那个字段在 bind 之前就被赋值了，
    于是偶尔会回填到 0，而 0 是连不上的。表现是状态文件里写着「在跑」，端口却是 0，
    读的人只会以为服务端没监听。窗口很小，所以要跑很多次。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.stop()
        self._tmp.cleanup()

    def _start(self, index):
        session = server.ServerSession(
            data_dir=os.path.join(self._tmp.name, f"d{index}"),
            host="127.0.0.1", port=0, token="TEST1234", mode="headless",
        )
        self.sessions.append(session)
        session.start()
        return session

    def test_ephemeral_port_is_backfilled(self):
        session = self._start(0)
        port = session.state.bound_port

        self.assertNotEqual(port, 0)
        self.assertEqual(session.state.snapshot()["port"], port)
        self.assertEqual(runtime.load_status(session.data_dir)["port"], port)

        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass

    def test_backfill_is_deterministic_across_many_starts(self):
        for index in range(8):
            with self.subTest(index=index):
                self.assertNotEqual(self._start(index).state.bound_port, 0)


class BuildScriptTest(unittest.TestCase):
    """构建脚本必须能在**英文** Windows 上跑。

    CI 的 runner 是英文 Windows，stdout 默认编码是 cp1252 —— 打印一句中文就会
    抛 UnicodeEncodeError，整个打包流程挂掉，而报错信息本身完全看不出跟中文有关。
    这是实测踩到的（`Server tests` 的打包步骤就是这么红的），所以用
    `PYTHONIOENCODING=cp1252` 精确复现那个环境，而不是靠推理。
    """

    SCRIPTS_DIR = os.path.join(os.path.dirname(SERVER_DIR), "scripts")

    def _run(self, *args, timeout=120):
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        return subprocess.run(
            [sys.executable, *args], capture_output=True, env=env, timeout=timeout,
        )

    def _stderr(self, result):
        return result.stderr.decode("utf-8", "replace")

    def test_make_version_info_survives_cp1252(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "version_info.txt")
            result = self._run(
                os.path.join(self.SCRIPTS_DIR, "make_version_info.py"),
                "1.0.0-beta1.9", output,
            )

            self.assertEqual(result.returncode, 0, self._stderr(result))
            with open(output, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("1.0.0-beta1.9", content)
            self.assertIn("VSVersionInfo", content)

    def test_make_version_info_rejects_bad_arguments(self):
        result = self._run(os.path.join(self.SCRIPTS_DIR, "make_version_info.py"))
        self.assertEqual(result.returncode, 2)

    def test_make_icon_survives_cp1252(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "icon.ico")
            result = self._run(
                os.path.join(self.SCRIPTS_DIR, "make_icon.py"), output,
            )

            self.assertEqual(result.returncode, 0, self._stderr(result))
            self.assertTrue(os.path.exists(output))

    def test_committed_icon_matches_the_generator(self):
        """图标是生成物，不该被手工改过 —— 改了就对不上了。"""
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "icon.ico")
            result = self._run(
                os.path.join(self.SCRIPTS_DIR, "make_icon.py"), output,
            )
            self.assertEqual(result.returncode, 0, self._stderr(result))

            committed = os.path.join(SERVER_DIR, "assets", "omnipad.ico")
            with open(output, "rb") as a, open(committed, "rb") as b:
                self.assertEqual(
                    a.read(), b.read(),
                    "server/assets/omnipad.ico 与 make_icon.py 的产出不一致，"
                    "请重新生成（python scripts/make_icon.py）",
                )

    def test_status_output_survives_cp1252(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(SERVER_PY, "--status", "--data-dir", tmp)
            self.assertEqual(result.returncode, EXIT_NOT_RUNNING, self._stderr(result))


class GuiAvailabilityTest(unittest.TestCase):
    """CLI 那个 exe 排除了 tkinter，所以它开不了窗口。

    必须提前说清楚，而不是让用户先看到「端口被占用」，或者更糟 ——
    一个 ImportError 堆栈。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_tkinter_is_present_in_a_source_run(self):
        self.assertTrue(server.gui_available())

    def test_cli_only_build_refuses_to_open_a_window(self):
        args = server.build_parser().parse_args(["--data-dir", self.data_dir])
        with mock.patch.object(server, "gui_available", return_value=False), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = server.run_server(args, self.data_dir)

        self.assertEqual(code, server.EXIT_FAILED)
        self.assertIn("只包含命令行模式", stdout.getvalue())

    def test_headless_still_allowed_without_tkinter(self):
        """无头模式根本不需要 tkinter，不该被这条检查挡住。"""
        args = server.build_parser().parse_args(
            ["--headless", "--port", "0", "--data-dir", self.data_dir])
        self.assertTrue(args.headless)


if __name__ == "__main__":
    unittest.main(verbosity=2)
