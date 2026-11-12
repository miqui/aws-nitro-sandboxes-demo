#!/usr/bin/env python3
"""Guest-side agent: listens on vsock, runs submitted Python with a timeout, returns the result.

Stdlib only; this single file is copied into the guest rootfs by host/bootstrap.sh.
Protocol (same as sandbox_launcher.channel): 4-byte big-endian length + JSON.
  request : {"code": <base64>, "timeout_s": int, "env": {str: str}}
  response: {"exit_code": int, "stdout": str, "stderr": str, "timed_out": bool}
UNVERIFIED: never run inside a real Nitro Sandbox yet.
"""
import argparse
import base64
import json
import os
import selectors
import time
import signal
import socket
import struct
import subprocess
import sys
import tempfile

PORT = 5005
MAX_OUTPUT = 64 * 1024
MAX_FRAME = 8 * 1024 * 1024


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def recv_msg(sock):
    (length,) = struct.unpack(">I", _recv_exact(sock, 4))
    if length > MAX_FRAME:
        raise ValueError("frame too large")
    return json.loads(_recv_exact(sock, length).decode("utf-8"))


def send_msg(sock, obj):
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _clip(data):
    text = data[:MAX_OUTPUT].decode("utf-8", "replace")
    if len(data) > MAX_OUTPUT:
        text += "\n[output truncated at %d bytes]" % MAX_OUTPUT
    return text


def _capture(proc, timeout_s):
    """Drain both pipes, keeping one extra byte for _clip's truncation marker."""
    buffers = [bytearray(), bytearray()]
    deadline = time.monotonic() + timeout_s
    timed_out = False
    with selectors.DefaultSelector() as selector:
        for stream, buf in zip((proc.stdout, proc.stderr), buffers):
            selector.register(stream, selectors.EVENT_READ, buf)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if not timed_out and remaining <= 0:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for key, _ in selector.select(None if timed_out else max(0, remaining)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                else:
                    key.data.extend(chunk[:max(0, MAX_OUTPUT + 1 - len(key.data))])
        try:
            proc.wait(timeout=None if timed_out else max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    return buffers[0], buffers[1], timed_out


def run_code(code, timeout_s, extra_env=None, python=None):
    """Run `code` with `python -I`; kill the whole process group on timeout."""
    python = python or sys.executable
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (extra_env or {}).items()})
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "untrusted.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(code)
        proc = subprocess.Popen(
            [python, "-I", path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=d,
            env=env,
            start_new_session=True,
        )
        out, err, timed_out = _capture(proc, timeout_s)
        code_rc = 124 if timed_out else proc.returncode
        return {"exit_code": code_rc, "stdout": _clip(out), "stderr": _clip(err), "timed_out": timed_out}


def handle(conn, python=None):
    try:
        req = recv_msg(conn)
        code = base64.b64decode(req["code"]).decode("utf-8")
        timeout_s = max(1, min(int(req.get("timeout_s", 30)), 3600))
        resp = run_code(code, timeout_s, req.get("env"), python)
    except Exception as exc:  # report any failure to the host instead of dying silently
        resp = {"exit_code": 125, "stdout": "", "stderr": "agent error: %r" % (exc,), "timed_out": False}
    send_msg(conn, resp)


def poweroff():
    os.sync()
    # constant argv, no shell, no user input; host also kills firecracker anyway
    subprocess.call(["poweroff", "-f"])


def serve(listener, once=True, python=None, on_done=None):
    while True:
        conn, _ = listener.accept()
        with conn:
            handle(conn, python)
        if once:
            if on_done:
                on_done()
            return


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--python", default=None)
    ap.add_argument("--poweroff", action="store_true", help="power off the VM after one job")
    args = ap.parse_args(argv)
    if not hasattr(socket, "AF_VSOCK"):
        print("AF_VSOCK not available", file=sys.stderr)
        return 1
    ls = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    ls.bind((socket.VMADDR_CID_ANY, args.port))
    ls.listen(1)
    serve(ls, once=True, python=args.python, on_done=poweroff if args.poweroff else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
