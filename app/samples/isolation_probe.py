"""Isolation probe: runs INSIDE the sandbox. Prints one JSON document with per-check results.

Checks (the 5th, timeout kill, is demonstrated by infinite_loop.py):
  1 kernel_separate   : boot_id/kernel differ from the parent's (env passed by the launcher)
  2 no_parent_paths   : parent paths and the IMDS are absent/unreachable
  3 no_network        : only loopback exists and outbound connects fail
  4 bounded_resources : vCPU count and MemTotal match what was requested

It does NOT, and cannot, show that the parent is unable to read sandbox memory.
"""
import json
import os
import socket


def read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def check_kernel():
    boot_id = read("/proc/sys/kernel/random/boot_id")
    parent_boot = os.environ.get("SANDBOX_PARENT_BOOT_ID", "")
    parent_rel = os.environ.get("SANDBOX_PARENT_KERNEL", "")
    rel = os.uname().release
    ok = bool(boot_id) and bool(parent_boot) and boot_id != parent_boot
    return {
        "ok": ok,
        "guest_release": rel,
        "parent_release": parent_rel,
        "same_release_string": rel == parent_rel,
        "guest_boot_id": boot_id,
        "parent_boot_id": parent_boot,
        "note": "boot_id differing proves a distinct kernel instance; release strings may legitimately match",
    }


def check_paths():
    present = [p for p in ("/opt/sandbox-demo", "/var/lib/sandbox-demo", "/home/ssm-user", "/home/ec2-user") if os.path.exists(p)]
    imds = "reachable"
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("169.254.169.254", 80))
    except OSError as exc:
        imds = "unreachable (%s)" % exc.__class__.__name__
    finally:
        s.close()
    return {"ok": not present and imds.startswith("unreachable"), "parent_paths_present": present, "imds": imds}


def check_network():
    try:
        ifaces = sorted(os.listdir("/sys/class/net"))
    except OSError:
        ifaces = []
    connect = "connected"
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("1.1.1.1", 443))
    except OSError as exc:
        connect = "failed (%s)" % exc.__class__.__name__
    finally:
        s.close()
    return {"ok": set(ifaces) <= {"lo"} and connect.startswith("failed"), "interfaces": ifaces, "outbound": connect}


def check_resources():
    mem_kib = None
    for line in (read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            mem_kib = int(line.split()[1])
    exp_cpu = int(os.environ.get("SANDBOX_EXPECT_VCPUS", "0") or 0)
    exp_mem = int(os.environ.get("SANDBOX_EXPECT_MEM_MIB", "0") or 0)
    cpus = os.cpu_count()
    mem_mib = mem_kib // 1024 if mem_kib else None
    # MemTotal is below the VM size (kernel reserve); accept 50%..100%.
    mem_ok = bool(exp_mem and mem_mib and 0.5 * exp_mem <= mem_mib <= exp_mem)
    return {"ok": bool(exp_cpu) and cpus == exp_cpu and mem_ok, "cpus": cpus, "expected_cpus": exp_cpu,
            "mem_total_mib": mem_mib, "expected_mem_mib": exp_mem}


checks = {
    "kernel_separate": check_kernel(),
    "no_parent_paths": check_paths(),
    "no_network": check_network(),
    "bounded_resources": check_resources(),
}
print(json.dumps({"checks": checks, "all_ok": all(c["ok"] for c in checks.values()),
                  "not_demonstrated": "parent cannot read sandbox memory"}, indent=2))
