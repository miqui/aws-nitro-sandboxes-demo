import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tests import REPO_ROOT

from sandbox_launcher import cli, config as cfg

RUN_SANDBOX = os.path.join(REPO_ROOT, "app", "bin", "run-sandbox")
HELLO = os.path.join(REPO_ROOT, "app", "samples", "hello.py")


class CliTests(unittest.TestCase):
    def dry(self, *extra):
        return subprocess.run([sys.executable, RUN_SANDBOX, HELLO, "--dry-run", "--parent-vcpus", "4",
                               "--state-dir", "/tmp/nonexistent-state", *extra],
                              capture_output=True, text=True)

    def test_dry_run_prints_config_and_commands_without_executing(self):
        p = self.dry()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("firecracker --api-sock", p.stdout)
        self.assertIn("--config-file", p.stdout)
        self.assertIn('"enclave": {}', p.stdout)
        self.assertIn("CONNECT 5005", p.stdout)
        self.assertFalse(os.path.exists("/tmp/nonexistent-state"))  # nothing created

    def test_dry_run_direct_mode(self):
        p = self.dry("--mode", "direct", "--vcpus", "4", "--mem-mib", "256")
        self.assertIn('"direct_mode": true', p.stdout)
        self.assertIn("parent CAN read", p.stdout)

    def test_dry_run_config_block_is_valid_json(self):
        out = self.dry().stdout
        start = out.index("{")
        end = out.index("\n}\n", start) + 2
        conf = json.loads(out[start:end])
        self.assertEqual(conf["machine-config"]["vcpu_count"], 2)

    def test_invalid_request_exit_2(self):
        p = self.dry("--vcpus", "6")  # > parent vCPUs (4)
        self.assertEqual(p.returncode, 2)
        self.assertIn("exceeds parent", p.stderr)

    def test_missing_file_exit_2(self):
        out, err = io.StringIO(), io.StringIO()
        rc = cli.main(["/no/such/file.py", "--parent-vcpus", "4"], out=out, err=err)
        self.assertEqual(rc, 2)

    def test_real_run_uses_injected_launcher_and_propagates_exit_code(self):
        from sandbox_launcher.launcher import Launcher, RunResult

        class L(Launcher):
            def run(self, spec, code):
                return RunResult("r", 7, stdout="out\n", stderr="err\n")

        l = L(state_dir="/tmp/x", parent_vcpus=4)
        out, err = io.StringIO(), io.StringIO()
        rc = cli.main([HELLO, "--parent-vcpus", "4"], launcher=l, out=out, err=err)
        self.assertEqual((rc, out.getvalue(), err.getvalue()), (7, "out\n", "err\n"))


if __name__ == "__main__":
    unittest.main()
