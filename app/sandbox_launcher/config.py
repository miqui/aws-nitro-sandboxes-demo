"""Sandbox spec validation and Firecracker config generation (pure functions, no I/O).

Limits come from the Nitro Sandboxes / Firecracker docs (see docs/unverified.md):
  * memory >= 64 MiB and a multiple of 2 MiB
  * vcpu_count even unless cpu_overcommit
  * each sandbox vCPUs <= parent vCPUs
  * combined overcommitted sandbox vCPUs <= 5x parent vCPUs (not enforced here)
  * <= 1024 vCPUs
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

MODES = ("confidential", "direct")
MIN_MEM_MIB = 64
MAX_VCPUS = 1024
MAX_TIMEOUT_S = 3600
VSOCK_GUEST_CID = 3
VSOCK_PORT = 5005  # guest agent listens here
GUEST_INIT = "/opt/sandbox/init.sh"  # placed into the rootfs by host/bootstrap.sh


class ConfigError(ValueError):
    """Invalid sandbox request."""


@dataclass(frozen=True)
class SandboxSpec:
    mode: str = "confidential"
    vcpus: int = 2
    mem_mib: int = 512
    timeout_s: int = 30
    cpu_overcommit: bool = False
    debug: bool = False


def validate_spec(spec: SandboxSpec, parent_vcpus: Optional[int] = None) -> None:
    """Raise ConfigError if the spec violates a documented limit."""
    if parent_vcpus is None:
        parent_vcpus = os.cpu_count() or 1
    if parent_vcpus < 1:
        raise ConfigError("parent_vcpus must be >= 1")
    if spec.mode not in MODES:
        raise ConfigError("mode must be one of %s, got %r" % (MODES, spec.mode))
    for name in ("vcpus", "mem_mib", "timeout_s"):
        value = getattr(spec, name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError("%s must be an integer, got %r" % (name, value))
    if spec.vcpus < 1 or spec.vcpus > MAX_VCPUS:
        raise ConfigError("vcpus must be between 1 and %d" % MAX_VCPUS)
    if spec.vcpus % 2 and not spec.cpu_overcommit:
        raise ConfigError("vcpus must be even unless --cpu-overcommit is set")
    if spec.vcpus > parent_vcpus:
        raise ConfigError(
            "vcpus=%d exceeds parent vCPUs (%d)" % (spec.vcpus, parent_vcpus)
        )
    if spec.mem_mib < MIN_MEM_MIB:
        raise ConfigError("mem_mib must be >= %d" % MIN_MEM_MIB)
    if spec.mem_mib % 2:
        raise ConfigError("mem_mib must be a multiple of 2 (MiB)")
    if spec.timeout_s < 1 or spec.timeout_s > MAX_TIMEOUT_S:
        raise ConfigError("timeout_s must be between 1 and %d" % MAX_TIMEOUT_S)


def spec_warnings(spec: SandboxSpec) -> list:
    warnings = []
    if spec.debug:
        warnings.append(
            "debug mode: serial console on and attestation PCRs are zeroed; "
            "never use for anything attested"
        )
    if spec.mode == "direct":
        warnings.append("direct mode: the parent CAN read sandbox memory; no attestation")
    return warnings


def build_firecracker_config(
    spec: SandboxSpec,
    kernel_path: str,
    rootfs_path: str,
    vsock_uds_path: str,
    parent_vcpus: Optional[int] = None,
) -> Dict[str, Any]:
    """Return the dict that is serialised to the `firecracker --config-file` JSON."""
    validate_spec(spec, parent_vcpus)

    enclave: Dict[str, Any] = {}  # confidential mode == empty object (documented default)
    if spec.mode == "direct":
        enclave["direct_mode"] = True
    if spec.cpu_overcommit:
        enclave["cpu_overcommit"] = True
    if spec.debug:
        enclave["debug_mode"] = True

    boot_args = "reboot=k panic=1 init=%s" % GUEST_INIT
    if spec.debug:
        boot_args += " console=ttyS0"

    return {
        "boot-source": {"kernel_image_path": kernel_path, "boot_args": boot_args},
        "drives": [
            {
                "drive_id": "rootfs",
                "path_on_host": rootfs_path,
                "is_root_device": True,
                "is_read_only": False,
            }
        ],
        "machine-config": {"vcpu_count": spec.vcpus, "mem_size_mib": spec.mem_mib},
        "vsock": {"guest_cid": VSOCK_GUEST_CID, "uds_path": vsock_uds_path},
        "enclave": enclave,
    }
