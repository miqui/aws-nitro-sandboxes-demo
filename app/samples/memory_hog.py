"""Allocates memory until the guest OOM-kills it (or MemoryError). Bounded by --mem-mib, not parent RAM.

Do NOT run this directly on a workstation. Optional arg: cap in MiB (default 8192).
"""
import sys

cap = int(sys.argv[1]) if len(sys.argv) > 1 else 8192
chunks = []
for i in range(cap):
    chunks.append(bytearray(1024 * 1024))
    for j in range(0, len(chunks[-1]), 4096):
        chunks[-1][j] = 1  # touch pages so they are really committed
    if i % 64 == 0:
        print("allocated %d MiB" % (i + 1), flush=True)
print("allocated cap without dying: %d MiB" % cap)
