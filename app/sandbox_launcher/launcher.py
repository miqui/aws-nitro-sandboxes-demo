"""Launcher: per-run rootfs copy, firecracker --config-file, vsock exec, timeout, cleanup."""

from __future__ import annotations

import base64
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import config as cfg
from .channel import ChannelError, ChannelTimeout, VsockUdsChannel
from .runner import SubprocessRunner

DEFAULT_STATE_DIR = "/var/lib/sandbox-demo"
EXIT_TIMEOUT = 124
EXIT_LAUNCHER_ERROR = 125
RESPONSE_GRACE_S = 5.0


@dataclass
class RunResult:
    run_id: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_s: float = 0.0
    error: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class Plan:
    run_id: str
    run_dir: str
    config_path: str
    rootfs_copy: str
    uds_path: str
    api_sock: str
    log_path: str
    config: Dict[str, Any]
    commands: List[str]


def _read_boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id") as fh:
            return fh.read().strip()
    except OSError:
        return ""


class Launcher:
    def __init__(
        self,
        state_dir: str = DEFAULT_STATE_DIR,
        runner: Any = None,
        channel_factory: Callable[..., Any] = VsockUdsChannel.connect,
        copy_file: Callable[[str, str], Any] = shutil.copyfile,
        parent_vcpus: Optional[int] = None,
        firecracker_bin: str = "firecracker",
        kernel_path: Optional[str] = None,
        rootfs_path: Optional[str] = None,
        boot_timeout_s: float = 60.0,
        keep_run_dir: bool = False,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
        clock: Callable[[], float] = time.monotonic,
        parent_release: Optional[str] = None,
        parent_boot_id: Optional[str] = None,
    ):
        self.state_dir = state_dir
        self.runner = runner or SubprocessRunner()
        self.channel_factory = channel_factory
        self.copy_file = copy_file
        self.parent_vcpus = parent_vcpus if parent_vcpus is not None else (os.cpu_count() or 1)
        self.firecracker_bin = firecracker_bin
        self.kernel_path = kernel_path or os.path.join(state_dir, "images", "kernel")
        self.rootfs_path = rootfs_path or os.path.join(state_dir, "images", "rootfs.ext4")
        self.boot_timeout_s = boot_timeout_s
        self.keep_run_dir = keep_run_dir
        self.id_factory = id_factory
        self.clock = clock
        self.parent_release = parent_release if parent_release is not None else os.uname().release
        self.parent_boot_id = parent_boot_id if parent_boot_id is not None else _read_boot_id()

    # ---- planning (no side effects; used by --dry-run) ----
    def plan(self, spec: cfg.SandboxSpec) -> Plan:
        cfg.validate_spec(spec, self.parent_vcpus)
        run_id = self.id_factory()
        run_dir = os.path.join(self.state_dir, "runs", run_id)
        rootfs_copy = os.path.join(run_dir, "rootfs.ext4")
        uds = os.path.join(run_dir, "vsock.sock")
        api = os.path.join(run_dir, "api.sock")
        conf = os.path.join(run_dir, "config.json")
        log = os.path.join(run_dir, "firecracker.log")
        config = cfg.build_firecracker_config(spec, self.kernel_path, rootfs_copy, uds, self.parent_vcpus)
        commands = [
            "mkdir -m 0700 -p %s" % run_dir,
            "cp %s %s" % (self.rootfs_path, rootfs_copy),
            "%s --api-sock %s --config-file %s" % (self.firecracker_bin, api, conf),
            "# host connects to %s, sends 'CONNECT %d', then exchanges framed JSON (code in, result out)"
            % (uds, cfg.VSOCK_PORT),
            "# on exit/timeout: terminate firecracker (SIGTERM, then SIGKILL), rm -rf %s" % run_dir,
        ]
        return Plan(run_id, run_dir, conf, rootfs_copy, uds, api, log, config, commands)

    # ---- execution ----
    def run(self, spec: cfg.SandboxSpec, code: str) -> RunResult:
        plan = self.plan(spec)
        notes = cfg.spec_warnings(spec)
        started = self.clock()
        proc = None
        channel = None
        result: RunResult
        try:
            os.makedirs(plan.run_dir, mode=0o700, exist_ok=True)
            self.copy_file(self.rootfs_path, plan.rootfs_copy)
            with open(plan.config_path, "w") as fh:
                json.dump(plan.config, fh, indent=2)
            argv = [self.firecracker_bin, "--api-sock", plan.api_sock, "--config-file", plan.config_path]
            proc = self.runner.start(argv, plan.log_path)
            channel = self.channel_factory(
                plan.uds_path,
                cfg.VSOCK_PORT,
                self.boot_timeout_s,
                alive=lambda: proc.poll() is None,
            )
            request = {
                "code": base64.b64encode(code.encode("utf-8")).decode("ascii"),
                "timeout_s": spec.timeout_s,
                "env": {
                    "SANDBOX_PARENT_KERNEL": self.parent_release,
                    "SANDBOX_PARENT_BOOT_ID": self.parent_boot_id,
                    "SANDBOX_EXPECT_VCPUS": str(spec.vcpus),
                    "SANDBOX_EXPECT_MEM_MIB": str(spec.mem_mib),
                },
            }
            resp = channel.request(request, spec.timeout_s + RESPONSE_GRACE_S)
            result = RunResult(
                run_id=plan.run_id,
                exit_code=int(resp.get("exit_code", EXIT_LAUNCHER_ERROR)),
                stdout=str(resp.get("stdout", "")),
                stderr=str(resp.get("stderr", "")),
                timed_out=bool(resp.get("timed_out", False)),
            )
        except ChannelTimeout:
            notes.append("guest did not answer within timeout; firecracker killed")
            result = RunResult(plan.run_id, EXIT_TIMEOUT, timed_out=True)
        except (ChannelError, OSError) as exc:
            result = RunResult(plan.run_id, EXIT_LAUNCHER_ERROR, error=str(exc))
            tail = self._log_tail(plan.log_path)
            if tail:
                notes.append("firecracker log tail: " + tail)
        finally:
            if channel is not None:
                channel.close()
            if proc is not None:
                self._stop(proc)
            if not self.keep_run_dir:
                shutil.rmtree(plan.run_dir, ignore_errors=True)
        result.duration_s = round(self.clock() - started, 3)
        result.notes = notes
        return result

    @staticmethod
    def _stop(proc: Any) -> None:
        proc.terminate()
        if proc.wait(5.0) is None:
            proc.kill()
            proc.wait(5.0)

    @staticmethod
    def _log_tail(path: str, limit: int = 1000) -> str:
        try:
            with open(path, "rb") as fh:
                return fh.read()[-limit:].decode("utf-8", "replace")
        except OSError:
            return ""
