import json
import unittest

from sandbox_launcher import config as cfg
from sandbox_launcher.config import ConfigError, SandboxSpec, build_firecracker_config, validate_spec


def build(spec, parent=8):
    return build_firecracker_config(spec, "/k", "/r", "/v.sock", parent)


class ConfigTests(unittest.TestCase):
    def test_default_is_confidential_empty_enclave_object(self):
        c = build(SandboxSpec())
        self.assertEqual(c["enclave"], {})
        self.assertEqual(c["machine-config"], {"vcpu_count": 2, "mem_size_mib": 512})
        self.assertEqual(c["vsock"], {"guest_cid": 3, "uds_path": "/v.sock"})
        self.assertEqual(c["boot-source"]["kernel_image_path"], "/k")
        self.assertEqual(c["drives"][0]["path_on_host"], "/r")
        self.assertTrue(c["drives"][0]["is_root_device"])
        self.assertNotIn("console=", c["boot-source"]["boot_args"])
        json.dumps(c)  # serialisable

    def test_direct_mode(self):
        self.assertEqual(build(SandboxSpec(mode="direct"))["enclave"], {"direct_mode": True})

    def test_debug_and_overcommit(self):
        c = build(SandboxSpec(debug=True, cpu_overcommit=True, vcpus=3))
        self.assertEqual(c["enclave"], {"cpu_overcommit": True, "debug_mode": True})
        self.assertIn("console=ttyS0", c["boot-source"]["boot_args"])
        self.assertTrue(cfg.spec_warnings(SandboxSpec(debug=True)))

    def test_rejections(self):
        bad = [
            SandboxSpec(mode="weird"),
            SandboxSpec(vcpus=3),  # odd w/o overcommit
            SandboxSpec(vcpus=0),
            SandboxSpec(vcpus=1025, cpu_overcommit=True),
            SandboxSpec(mem_mib=62),
            SandboxSpec(mem_mib=65),
            SandboxSpec(timeout_s=0),
            SandboxSpec(timeout_s=99999),
            SandboxSpec(vcpus="2"),
            SandboxSpec(mem_mib=True),
        ]
        for spec in bad:
            with self.subTest(spec=spec), self.assertRaises(ConfigError):
                validate_spec(spec, 8)

    def test_vcpu_cap_is_5x_parent(self):
        validate_spec(SandboxSpec(vcpus=10), 2)  # exactly 5x
        with self.assertRaises(ConfigError):
            validate_spec(SandboxSpec(vcpus=12), 2)
        validate_spec(SandboxSpec(vcpus=11, cpu_overcommit=True), 3)
        with self.assertRaises(ConfigError):
            validate_spec(SandboxSpec(vcpus=16, cpu_overcommit=True), 3)

    def test_odd_vcpus_ok_with_overcommit(self):
        validate_spec(SandboxSpec(vcpus=3, cpu_overcommit=True), 8)

    def test_boundary_memory(self):
        validate_spec(SandboxSpec(mem_mib=64), 8)


if __name__ == "__main__":
    unittest.main()
