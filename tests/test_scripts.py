import os
import shutil
import subprocess
import tempfile
import unittest

from tests import REPO_ROOT

SCRIPTS = [
    os.path.join(REPO_ROOT, "host", "bootstrap.sh"),
    os.path.join(REPO_ROOT, "scripts", "build-bundle.sh"),
    os.path.join(REPO_ROOT, "scripts", "upload-bundle.sh"),
    os.path.join(REPO_ROOT, "app", "guest", "init.sh"),
]
BOOTSTRAP, BUILD, UPLOAD = SCRIPTS[:3]


def sh(*args, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, **kw)


class ShellSyntaxTests(unittest.TestCase):
    def test_bash_n(self):
        for s in SCRIPTS[:3]:
            with self.subTest(script=s):
                self.assertEqual(sh("bash", "-n", s).returncode, 0)

    def test_init_sh_posix_syntax(self):
        self.assertEqual(sh("sh", "-n", SCRIPTS[3]).returncode, 0)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck not installed")
    def test_shellcheck(self):
        for s in SCRIPTS:
            with self.subTest(script=s):
                p = sh("shellcheck", s)
                self.assertEqual(p.returncode, 0, p.stdout)


class BootstrapTests(unittest.TestCase):
    def helper(self, expr):
        return sh("bash", "-c", 'BOOTSTRAP_SOURCE_ONLY=1 source "%s"; %s' % (BOOTSTRAP, expr))

    def test_version_ge(self):
        cases = [("6.18.51-120.162", "6.18.51-120.162", 0), ("6.18.51-120.161", "6.18.51-120.162", 1),
                 ("6.19.0-1.1", "6.18.51-120.162", 0), ("6.1.100-1.1", "6.18.51-120.162", 1),
                 ("6.18.9-200.1", "6.18.51-120.162", 1), ("1.18.0", "1.18.0", 0), ("1.9.0", "1.18.0", 1)]
        for a, b, rc in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(self.helper("version_ge %s %s" % (a, b)).returncode, rc)

    def test_help(self):
        p = sh("bash", BOOTSTRAP, "--help")
        self.assertEqual(p.returncode, 0)
        self.assertIn("--check-only", p.stdout)

    @unittest.skipIf(os.uname().sysname == "Linux" and os.geteuid() == 0, "would really run on a root Linux box")
    def test_fails_clearly_off_target(self):
        p = sh("bash", BOOTSTRAP, "--check-only")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ERROR", p.stderr)
        self.assertRegex(p.stderr, r"must (run on Linux|run as root)|kernel|nitro_enclaves")

    def test_unknown_arg(self):
        self.assertEqual(sh("bash", BOOTSTRAP, "--bogus").returncode, 2)


class BundleScriptTests(unittest.TestCase):
    def test_build_bundle_dry_run_no_network_no_files(self):
        out = os.path.join(tempfile.mkdtemp(), "x.tgz")
        p = sh("bash", BUILD, "--dry-run", "--output", out, "--kernel-url", "https://k", "--rootfs-url", "https://r")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("DRY RUN", p.stdout)
        self.assertIn("firecracker-v1.18.0-x86_64.tgz", p.stdout)
        self.assertFalse(os.path.exists(out))

    def test_build_bundle_dry_run_offline_dir(self):
        p = sh("bash", BUILD, "--dry-run", "--offline-dir", "/some/dir")
        self.assertIn("/some/dir", p.stdout)

    def test_build_bundle_rejects_old_firecracker(self):
        self.assertNotEqual(sh("bash", BUILD, "--dry-run", "--fc-version", "1.17.0").returncode, 0)

    def test_build_bundle_requires_urls_without_offline(self):
        p = sh("bash", BUILD)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--kernel-url", p.stderr)

    def test_build_bundle_offline_real_pack(self):
        d = tempfile.mkdtemp()
        for name, data in (("firecracker-v1.18.0-x86_64.tgz", b"fc"), ("bzImage-6.18", b"k"), ("ubuntu.squashfs", b"r")):
            with open(os.path.join(d, name), "wb") as fh:
                fh.write(data)
        out = os.path.join(d, "out", "b.tgz")
        p = sh("bash", BUILD, "--offline-dir", d, "--output", out)
        self.assertEqual(p.returncode, 0, p.stderr)
        listing = sh("tar", "-tzf", out).stdout
        for want in ("host/bootstrap.sh", "app/bin/run-sandbox", "app/guest/agent.py",
                     "host/artifacts/MANIFEST", "host/artifacts/SHA256SUMS", "host/artifacts/kernel",
                     "host/artifacts/rootfs.squashfs", "host/artifacts/firecracker-v1.18.0-x86_64.tgz"):
            self.assertIn(want, listing)
        self.assertNotIn("__pycache__", listing)

    def test_upload_dry_run(self):
        p = sh("bash", UPLOAD, "--bucket", "my-bucket", "--dry-run", "--region", "us-east-1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("s3://my-bucket/sandbox-demo.tgz", p.stdout)
        self.assertIn("DRY RUN", p.stdout)

    def test_upload_requires_bucket(self):
        self.assertEqual(sh("bash", UPLOAD, "--dry-run").returncode, 2)


if __name__ == "__main__":
    unittest.main()
