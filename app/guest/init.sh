#!/bin/sh
# PID 1 inside the sandbox (boot arg init=/opt/sandbox/init.sh). Mount pseudo-fs, run agent.
# UNVERIFIED: not booted on a real Nitro Sandbox yet.
mount -t proc proc /proc 2>/dev/null
mount -t sysfs sys /sys 2>/dev/null
mount -t devtmpfs dev /dev 2>/dev/null
mount -t tmpfs tmp /tmp 2>/dev/null
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
exec python3 /opt/sandbox/agent.py --poweroff
