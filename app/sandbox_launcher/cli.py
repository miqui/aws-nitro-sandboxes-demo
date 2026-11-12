"""Command line for run-sandbox."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import config as cfg
from .launcher import DEFAULT_STATE_DIR, EXIT_LAUNCHER_ERROR, Launcher


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run-sandbox",
        description="Run an untrusted Python file inside a Nitro Sandbox (Firecracker).",
    )
    p.add_argument("file", help="Python file to execute inside the sandbox")
    p.add_argument("--mode", choices=cfg.MODES, default="confidential")
    p.add_argument("--vcpus", type=int, default=2)
    p.add_argument("--mem-mib", type=int, default=512)
    p.add_argument("--timeout-s", type=int, default=30)
    p.add_argument("--cpu-overcommit", action="store_true", help="allow odd vCPU counts / time-share parent cores")
    p.add_argument("--debug", action="store_true", help="serial console on; attestation PCRs zeroed")
    p.add_argument("--state-dir", default=os.environ.get("SANDBOX_DEMO_STATE", DEFAULT_STATE_DIR))
    p.add_argument("--kernel", help="guest kernel (default: <state-dir>/images/kernel)")
    p.add_argument("--rootfs", help="rootfs template (default: <state-dir>/images/rootfs.ext4)")
    p.add_argument("--firecracker", default="firecracker")
    p.add_argument("--parent-vcpus", type=int, help="override detected parent vCPU count (for the 5x cap)")
    p.add_argument("--boot-timeout-s", type=float, default=60.0)
    p.add_argument("--keep-run-dir", action="store_true")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument("--dry-run", action="store_true", help="print config + commands; execute nothing")
    return p


def main(argv: Optional[List[str]] = None, launcher: Optional[Launcher] = None, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    spec = cfg.SandboxSpec(
        mode=args.mode,
        vcpus=args.vcpus,
        mem_mib=args.mem_mib,
        timeout_s=args.timeout_s,
        cpu_overcommit=args.cpu_overcommit,
        debug=args.debug,
    )
    if launcher is None:
        launcher = Launcher(
            state_dir=args.state_dir,
            parent_vcpus=args.parent_vcpus,
            firecracker_bin=args.firecracker,
            kernel_path=args.kernel,
            rootfs_path=args.rootfs,
            boot_timeout_s=args.boot_timeout_s,
            keep_run_dir=args.keep_run_dir,
        )
    try:
        plan = launcher.plan(spec)
    except cfg.ConfigError as exc:
        print("run-sandbox: invalid request: %s" % exc, file=err)
        return 2

    if args.dry_run:
        print("# DRY RUN - nothing executed", file=out)
        for w in cfg.spec_warnings(spec):
            print("# WARNING: " + w, file=out)
        print("# firecracker config (%s):" % plan.config_path, file=out)
        print(json.dumps(plan.config, indent=2), file=out)
        print("# commands:", file=out)
        for c in plan.commands:
            print(c, file=out)
        print("# code file: %s (sent over vsock, not written to the rootfs)" % args.file, file=out)
        return 0

    try:
        with open(args.file, encoding="utf-8") as fh:
            code = fh.read()
    except OSError as exc:
        print("run-sandbox: cannot read %s: %s" % (args.file, exc), file=err)
        return 2

    result = launcher.run(spec, code)
    if args.json:
        print(json.dumps(result.__dict__, indent=2), file=out)
    else:
        out.write(result.stdout)
        err.write(result.stderr)
        for n in result.notes:
            print("# note: " + n, file=err)
        if result.error:
            print("run-sandbox: %s" % result.error, file=err)
        if result.timed_out:
            print("run-sandbox: TIMED OUT after %ss" % spec.timeout_s, file=err)
    return result.exit_code if result.exit_code is not None else EXIT_LAUNCHER_ERROR
