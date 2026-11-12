<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:f199761905fde79104fa101e453cea80ad3c4c315023a3e5e364293d2ed72ccb -->

# app/ and host/ — sandbox launcher, guest agent, bootstrap

Runs untrusted Python inside a **Nitro Sandbox** (Firecracker >= 1.18 on an AL2023 `kernel6.18 >= 6.18.51-120.162`
parent launched with Nitro Enclaves enabled). Python stdlib only.

> **Status: written and unit-tested offline only. Nothing here has ever booted a real sandbox.**
> See "UNVERIFIED" below.

## Flow

```
operator laptop (internet)           S3 (VPC endpoint)            parent EC2 (no internet)
scripts/build-bundle.sh  ─tgz─►  scripts/upload-bundle.sh ─►  user-data extracts to /opt/sandbox-demo
                                                              └─ host/bootstrap.sh (root)
SSM session ► sudo /opt/sandbox-demo/app/bin/run-sandbox app/samples/hello.py
```

`sandbox-demo.tgz` contains `host/`, `app/` and `host/artifacts/` (Firecracker release tarball, guest kernel,
Ubuntu squashfs rootfs, `MANIFEST`, `SHA256SUMS`). **`build-bundle.sh` downloads these on the operator's machine** and packs them,
because the host has no internet/NAT. Use `--offline-dir DIR` for pre-downloaded files, `--dry-run` to see the plan
without network. The kernel/rootfs URLs must be passed explicitly (`--kernel-url`, `--rootfs-url`; Firecracker CI artifacts,
DMB-patched kernel needed for confidential mode).

```
scripts/build-bundle.sh --kernel-url <bzImage url> --rootfs-url <ubuntu .squashfs url>
scripts/upload-bundle.sh --bucket <artifacts_bucket> --dry-run     # drop --dry-run to really upload (your creds)
```

## Running

```
sudo /opt/sandbox-demo/app/bin/run-sandbox FILE.py [--mode confidential|direct] [--vcpus 2] [--mem-mib 512]
     [--timeout-s 30] [--cpu-overcommit] [--debug] [--dry-run] [--json]
```
- `--dry-run` prints the generated Firecracker config and the commands, executes nothing (works on any machine).
- Default `confidential` emits `"enclave": {}`; `direct` emits `{"direct_mode": true}`; `--debug` adds `debug_mode`
  (serial console, PCRs zeroed). `--cpu-overcommit` is **opt-in here** (the plan listed it default-on; off keeps the
  documented `{}` object and dedicated cores).
- Validation: vcpus even unless overcommit, <= 1024, each sandbox <= parent vCPUs; mem >= 64 MiB and even; timeout 1..3600 s.
- Exit codes: the guest program's; 124 = timeout (sandbox killed); 125 = launcher/boot failure; 2 = bad request.
- Per run: private copy of the rootfs, `firecracker --api-sock … --config-file …`, code sent over vsock (port 5005,
  Firecracker UDS `CONNECT` handshake), result returned as JSON. On exit/timeout firecracker gets SIGTERM then SIGKILL and the run dir is deleted.
- One sandbox per invocation; combined vCPUs across overcommitted sandboxes may be up to 5x parent vCPUs;
  this cross-run budget is not enforced by the launcher. The `jailer` is not used yet.

## Isolation demos (`app/samples/`)

Four of the five demos are checks in `isolation_probe.py` (it reports 1-3 and the vCPU/MemTotal half of 4); the fifth
(timeout kill) is a separate sample, and `memory_hog.py` completes check 4. The probe itself has four checks, not five.

| # | Check | How |
|---|---|---|
| 1 | Guest kernel differs from parent | `isolation_probe.py` compares `boot_id` (release strings may match if the same kernel build is used) |
| 2 | No parent paths, no IMDS | `isolation_probe.py` |
| 3 | No network | `isolation_probe.py` (only `lo`; outbound connect fails; no tap is ever configured) |
| 4 | Bounded CPU/memory | `isolation_probe.py` (vCPU count, MemTotal) and `memory_hog.py` (OOM inside the guest only) |
| 5 | Timeout kill | `infinite_loop.py` with `--timeout-s 5` → exit 124 |

`hello.py` prints guest uname/hostname for a manual comparison with the parent.

**NOT demonstrated: "the parent cannot read sandbox memory".** No tool or test here shows it. In confidential mode AWS documents
it as a property; this repo neither proves nor tests it. Direct mode explicitly allows the parent to read memory.

## Bootstrap (`host/bootstrap.sh`)

Idempotent; checks root, kernel >= 6.18.51-120.162, `/dev/nitro_enclaves` (hard requirement; `/dev/kvm` informational), required tools, and a nonzero boot-time memory pool (`nitro_enclaves.mempool`, verified through sysfs);
verifies `SHA256SUMS`; installs Firecracker to `/usr/local/bin`; places kernel and builds `rootfs.ext4` (squashfs + guest agent + `/opt/sandbox/init.sh`
via `mkfs.ext4 -d`, after executing Python 3 through the guest PATH in a chroot) in `/var/lib/sandbox-demo/images`. `--check-only` runs preflight only. If the memory pool is missing, preflight fails with the
`grubby` command and reboot/retry steps. Wait for `/var/lib/sandbox-demo/.bootstrap-done` before running samples;
inspect `/var/log/sandbox-demo-userdata.log` if it does not appear.

## Phase 2 (`app/phase2/`) — UNVERIFIED skeleton

Parent-side KMS broker: `kms:Decrypt` with `Recipient`. Unit-tested with a fake client only; never run against AWS.
Blocked on a documented way to build a sandbox EIF with known PCR0.

## UNVERIFIED

- Everything that needs a real parent: boot, vsock handshake with sandboxes, `enclave` JSON field names (`direct_mode`, `cpu_overcommit`, `debug_mode`), device node (`/dev/nitro_enclaves` vs `/dev/kvm`), CPU/memory pool config.
- That the Firecracker CI Ubuntu rootfs contains `python3`; that `unsquashfs`/`mkfs.ext4` are installable on the host without internet.
- Firecracker CI artifact URLs and the `.sha256.txt` release checksum file name.
- Using `init=/opt/sandbox/init.sh` as PID 1 (guest `poweroff -f` behaviour).

## Tests

`python3 -m unittest discover` (repo root); shell scripts also checked with `bash -n` and `shellcheck`.


<!-- /coderabbit-ai-generated:v1 -->