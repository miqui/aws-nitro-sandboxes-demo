"""Host side of the vsock channel to the guest agent.

Firecracker exposes guest vsock through a host Unix socket. Host-initiated connection:
connect(uds_path); send "CONNECT <port>\\n"; read "OK <host_port>\\n"; then raw bytes
flow to the guest listener on <port>. (Standard Firecracker vsock behaviour; any
Nitro-Sandbox-specific quirks are UNVERIFIED.)

Framing (shared with app/guest/agent.py): 4-byte big-endian length + UTF-8 JSON.
"""

from __future__ import annotations

import json
import socket
import struct
import time
from typing import Any, Callable, Dict, Optional

MAX_FRAME = 8 * 1024 * 1024


class ChannelError(Exception):
    """Could not talk to the guest agent (boot failure, protocol error)."""


class ChannelTimeout(ChannelError):
    """Guest did not answer in time."""


def send_msg(sock: Any, obj: Dict[str, Any]) -> None:
    data = json.dumps(obj).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ChannelError("message too large")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _recv_exact(sock: Any, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ChannelError("connection closed by peer")
        buf += chunk
    return buf


def recv_msg(sock: Any) -> Dict[str, Any]:
    (length,) = struct.unpack(">I", _recv_exact(sock, 4))
    if length > MAX_FRAME:
        raise ChannelError("frame too large: %d" % length)
    return json.loads(_recv_exact(sock, length).decode("utf-8"))


class VsockUdsChannel:
    """Connected channel; create with `connect`."""

    def __init__(self, sock: Any):
        self._sock = sock

    @classmethod
    def connect(
        cls,
        uds_path: str,
        port: int,
        deadline_s: float,
        alive: Callable[[], bool] = lambda: True,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> "VsockUdsChannel":
        """Retry until the guest agent accepts (guest is still booting) or deadline."""
        end = clock() + deadline_s
        last = "no attempt"
        while clock() < end:
            if not alive():
                raise ChannelError("firecracker exited before the guest agent was reachable")
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.settimeout(2.0)
                s.connect(uds_path)
                s.sendall(("CONNECT %d\n" % port).encode())
                line = b""
                while not line.endswith(b"\n") and len(line) < 64:
                    c = s.recv(1)
                    if not c:
                        break
                    line += c
                if line.startswith(b"OK"):
                    return cls(s)
                last = "unexpected reply %r" % line
            except (OSError, socket.timeout) as exc:
                last = str(exc)
            s.close()
            sleep(0.5)
        raise ChannelError("guest agent not reachable within %ss (%s)" % (deadline_s, last))

    def request(self, payload: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
        self._sock.settimeout(timeout_s)
        try:
            send_msg(self._sock, payload)
            return recv_msg(self._sock)
        except socket.timeout as exc:
            raise ChannelTimeout("no response within %ss" % timeout_s) from exc
        except (OSError, ValueError) as exc:
            raise ChannelError("channel failure: %s" % exc) from exc

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


ChannelFactory = Callable[..., Any]
_ = Optional  # keep typing import used on py3.9
