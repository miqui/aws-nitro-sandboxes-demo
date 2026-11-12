import os
import shutil
import socket
import tempfile
import threading
import unittest

from sandbox_launcher import config as cfg
from sandbox_launcher.channel import ChannelError, ChannelTimeout, VsockUdsChannel
from sandbox_launcher.launcher import Launcher


class FakeProc:
    def __init__(self, stubborn=False):
        self.terminated = False
        self.killed = False
        self.stubborn = stubborn

    def poll(self):
        return 0 if (self.terminated or self.killed) else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout):
        return None if (self.stubborn and not self.killed) else 0


class FakeRunner:
    def __init__(self, proc=None):
        self.proc = proc or FakeProc()
        self.calls = []

    def start(self, argv, log_path):
        self.calls.append((argv, log_path))
        return self.proc


class FakeChannel:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.sent, self.closed = response, exc, None, False

    def request(self, payload, timeout_s):
        self.sent = (payload, timeout_s)
        if self.exc:
            raise self.exc
        return self.response

    def close(self):
        self.closed = True


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "images"))
        with open(os.path.join(self.tmp, "images", "rootfs.ext4"), "wb") as fh:
            fh.write(b"ROOTFS")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def launcher(self, runner, channel, connect_exc=None, **kw):
        def factory(uds, port, deadline, alive=None):
            self.factory_args = (uds, port, deadline)
            if connect_exc:
                raise connect_exc
            return channel

        return Launcher(state_dir=self.tmp, runner=runner, channel_factory=factory, parent_vcpus=4,
                        id_factory=lambda: "run1", parent_release="6.18.x", parent_boot_id="pb", **kw)

    def runs(self):
        d = os.path.join(self.tmp, "runs")
        return os.listdir(d) if os.path.isdir(d) else []

    def test_success_path(self):
        runner = FakeRunner()
        ch = FakeChannel({"exit_code": 0, "stdout": "hi\n", "stderr": "", "timed_out": False})
        res = self.launcher(runner, ch).run(cfg.SandboxSpec(timeout_s=7), "print('hi')")
        self.assertEqual((res.exit_code, res.stdout, res.timed_out), (0, "hi\n", False))
        argv = runner.calls[0][0]
        self.assertEqual(argv[0], "firecracker")
        self.assertIn("--config-file", argv)
        self.assertEqual(self.factory_args[1], cfg.VSOCK_PORT)
        payload, tmo = ch.sent
        self.assertEqual(payload["timeout_s"], 7)
        self.assertGreater(tmo, 7)
        self.assertEqual(payload["env"]["SANDBOX_PARENT_BOOT_ID"], "pb")
        self.assertTrue(ch.closed and runner.proc.terminated)
        self.assertEqual(self.runs(), [])  # per-run dir (incl. rootfs copy) removed

    def test_rootfs_is_copied_per_run_not_shared(self):
        seen = {}

        def copy(src, dst):
            seen["src"], seen["dst"] = src, dst
            shutil.copyfile(src, dst)

        ch = FakeChannel({"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False})
        self.launcher(FakeRunner(), ch, copy_file=copy).run(cfg.SandboxSpec(), "x")
        self.assertNotEqual(seen["src"], seen["dst"])
        self.assertIn("run1", seen["dst"])

    def test_config_file_contents_written_for_firecracker(self):
        captured = {}
        runner = FakeRunner()
        orig = runner.start

        def start(argv, log):
            import json
            with open(argv[argv.index("--config-file") + 1]) as fh:
                captured.update(json.load(fh))
            return orig(argv, log)

        runner.start = start
        ch = FakeChannel({"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False})
        self.launcher(runner, ch).run(cfg.SandboxSpec(mode="direct"), "x")
        self.assertEqual(captured["enclave"], {"direct_mode": True})

    def test_timeout_kills_and_cleans_up(self):
        runner = FakeRunner(FakeProc(stubborn=True))
        ch = FakeChannel(exc=ChannelTimeout("slow"))
        res = self.launcher(runner, ch).run(cfg.SandboxSpec(timeout_s=1), "while 1: pass")
        self.assertTrue(res.timed_out)
        self.assertEqual(res.exit_code, 124)
        self.assertTrue(runner.proc.terminated and runner.proc.killed)  # escalated to SIGKILL
        self.assertEqual(self.runs(), [])

    def test_guest_reported_timeout(self):
        ch = FakeChannel({"exit_code": 124, "stdout": "", "stderr": "", "timed_out": True})
        res = self.launcher(FakeRunner(), ch).run(cfg.SandboxSpec(), "x")
        self.assertTrue(res.timed_out)
        self.assertEqual(res.exit_code, 124)

    def test_boot_failure_cleans_up_and_reports(self):
        runner = FakeRunner()
        res = self.launcher(runner, None, connect_exc=ChannelError("agent never came up")).run(cfg.SandboxSpec(), "x")
        self.assertEqual(res.exit_code, 125)
        self.assertIn("agent never came up", res.error)
        self.assertTrue(runner.proc.terminated)
        self.assertEqual(self.runs(), [])

    def test_unexpected_error_still_cleans_up(self):
        runner = FakeRunner()
        ch = FakeChannel(exc=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self.launcher(runner, ch).run(cfg.SandboxSpec(), "x")
        self.assertTrue(runner.proc.terminated)
        self.assertEqual(self.runs(), [])

    def test_invalid_spec_starts_nothing(self):
        runner = FakeRunner()
        with self.assertRaises(cfg.ConfigError):
            self.launcher(runner, FakeChannel()).run(cfg.SandboxSpec(vcpus=40), "x")  # > 5x4
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.runs(), [])

    def test_keep_run_dir(self):
        ch = FakeChannel({"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False})
        self.launcher(FakeRunner(), ch, keep_run_dir=True).run(cfg.SandboxSpec(), "x")
        self.assertEqual(self.runs(), ["run1"])


class UdsChannelTests(unittest.TestCase):
    """VsockUdsChannel against a fake Firecracker vsock UDS speaking the CONNECT handshake."""

    def test_connect_handshake_and_request(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "v.sock")
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(path)
        srv.listen(1)
        got = {}

        def serve():
            conn, _ = srv.accept()
            line = b""
            while not line.endswith(b"\n"):
                line += conn.recv(1)
            got["hello"] = line
            conn.sendall(b"OK 1073741824\n")
            from sandbox_launcher.channel import recv_msg, send_msg
            got["req"] = recv_msg(conn)
            send_msg(conn, {"exit_code": 0, "stdout": "ok", "stderr": "", "timed_out": False})
            conn.close()

        t = threading.Thread(target=serve)
        t.start()
        ch = VsockUdsChannel.connect(path, 5005, 5)
        resp = ch.request({"code": "eA=="}, 5)
        ch.close()
        t.join()
        srv.close()
        self.assertEqual(got["hello"], b"CONNECT 5005\n")
        self.assertEqual(resp["stdout"], "ok")

    def test_connect_gives_up_when_process_dead(self):
        with self.assertRaises(ChannelError):
            VsockUdsChannel.connect("/nonexistent.sock", 5005, 5, alive=lambda: False)

    def test_connect_deadline(self):
        t = [0.0]

        def clock():
            t[0] += 1.0
            return t[0]

        with self.assertRaises(ChannelError):
            VsockUdsChannel.connect("/nonexistent.sock", 5005, 3, sleep=lambda s: None, clock=clock)


if __name__ == "__main__":
    unittest.main()
