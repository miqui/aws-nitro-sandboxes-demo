"""Process runner abstraction so tests never start a real firecracker."""

from __future__ import annotations

import subprocess
from typing import List, Optional


class SubprocessRunner:
    """Real runner: starts a child process, logs stdout+stderr to a file."""

    def start(self, argv: List[str], log_path: str) -> "SubprocessHandle":
        log = open(log_path, "wb")
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        finally:
            log.close()
        return SubprocessHandle(proc)


class SubprocessHandle:
    def __init__(self, proc: subprocess.Popen):
        self._p = proc

    def poll(self) -> Optional[int]:
        return self._p.poll()

    def terminate(self) -> None:
        if self._p.poll() is None:
            self._p.terminate()

    def kill(self) -> None:
        if self._p.poll() is None:
            self._p.kill()

    def wait(self, timeout: float) -> Optional[int]:
        try:
            return self._p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
