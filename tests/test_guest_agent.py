import base64
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

from tests import REPO_ROOT

import agent  # app/guest/agent.py
from sandbox_launcher import channel

SAMPLES = os.path.join(REPO_ROOT, "app", "samples")


class AgentTests(unittest.TestCase):
    def test_run_code_stdout_and_exit(self):
        r = agent.run_code("print('x'); import sys; sys.exit(3)", 10)
        self.assertEqual((r["exit_code"], r["stdout"].strip(), r["timed_out"]), (3, "x", False))

    def test_timeout_kills_infinite_loop(self):
        with open(os.path.join(SAMPLES, "infinite_loop.py")) as fh:
            code = fh.read()
        r = agent.run_code(code, 1)
        self.assertTrue(r["timed_out"])
        self.assertEqual(r["exit_code"], 124)

    def test_env_passed(self):
        r = agent.run_code("import os; print(os.environ['SANDBOX_X'])", 10, {"SANDBOX_X": "v"})
        self.assertEqual(r["stdout"].strip(), "v")

    def test_output_truncated(self):
        r = agent.run_code("print('a' * 200000)", 10)
        self.assertIn("truncated", r["stdout"])

    def test_both_streams_drained_with_bounded_capture(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import os; "
             "[(os.write(1, b'a' * 8192), os.write(2, b'b' * 8192)) for _ in range(256)]"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        out, err, timed_out = agent._capture(proc, 10)
        self.assertFalse(timed_out)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(out), agent.MAX_OUTPUT + 1)
        self.assertEqual(len(err), agent.MAX_OUTPUT + 1)
        self.assertEqual(agent._clip(out), 'a' * agent.MAX_OUTPUT +
                         "\n[output truncated at %d bytes]" % agent.MAX_OUTPUT)

    def test_timeout_with_descendant_holding_pipes(self):
        r = agent.run_code(
            "import subprocess, sys; subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(30)']); print('started', flush=True)", 1)
        self.assertEqual(r['exit_code'], 124)
        self.assertTrue(r['timed_out'])
        self.assertEqual(r['stdout'], 'started\n')

    def test_wire_compat_with_launcher_channel(self):
        a, b = socket.socketpair()
        t = threading.Thread(target=lambda: agent.handle(b))
        t.start()
        channel.send_msg(a, {"code": base64.b64encode(b"print(40+2)").decode(), "timeout_s": 5})
        resp = channel.recv_msg(a)
        t.join()
        a.close()
        b.close()
        self.assertEqual(resp["stdout"].strip(), "42")
        self.assertEqual(resp["exit_code"], 0)

    def test_bad_request_gets_error_reply(self):
        a, b = socket.socketpair()
        t = threading.Thread(target=lambda: agent.handle(b))
        t.start()
        channel.send_msg(a, {"nope": 1})
        resp = channel.recv_msg(a)
        t.join()
        a.close()
        b.close()
        self.assertEqual(resp["exit_code"], 125)


class SampleTests(unittest.TestCase):
    def test_samples_compile(self):
        for name in ("hello", "isolation_probe", "infinite_loop", "memory_hog"):
            with open(os.path.join(SAMPLES, name + ".py")) as fh:
                compile(fh.read(), name, "exec")

    def test_isolation_probe_emits_json_and_states_limits(self):
        # Run locally (NOT isolated): only asserts shape, not that checks pass.
        env = dict(os.environ, SANDBOX_PARENT_BOOT_ID="x", SANDBOX_EXPECT_VCPUS="2", SANDBOX_EXPECT_MEM_MIB="512")
        out = subprocess.run([sys.executable, os.path.join(SAMPLES, "isolation_probe.py")], env=env,
                             capture_output=True, text=True, timeout=30).stdout
        doc = json.loads(out)
        self.assertEqual(set(doc["checks"]),
                         {"kernel_separate", "no_parent_paths", "no_network", "bounded_resources"})
        self.assertIn("parent cannot read sandbox memory", doc["not_demonstrated"])


if __name__ == "__main__":
    unittest.main()
