#!/usr/bin/env bash
# Idempotent host bootstrap for the Nitro Sandboxes demo parent (AL2023, no internet).
# Run as root by EC2 user-data after extracting sandbox-demo.tgz to /opt/sandbox-demo.
# Everything installed comes from host/artifacts/ inside the tarball (see scripts/build-bundle.sh).
# UNVERIFIED: never executed on a real instance.
set -euo pipefail

ROOT="${SANDBOX_DEMO_ROOT:-/opt/sandbox-demo}"
STATE_DIR="${SANDBOX_DEMO_STATE:-/var/lib/sandbox-demo}"
ART_DIR="${SANDBOX_DEMO_ARTIFACTS:-$ROOT/host/artifacts}"
BIN_DIR="${SANDBOX_DEMO_BIN_DIR:-/usr/local/bin}"
MIN_KERNEL="6.18.51-120.162"
ROOTFS_EXTRA_MIB=512

log() { printf '[bootstrap] %s\n' "$*"; }
warn() { printf '[bootstrap] WARNING: %s\n' "$*" >&2; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

# version_ge A B : true if A >= B (version sort)
version_ge() {
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

# kernel_core : running kernel without the .amzn2023.<arch> suffix
kernel_core() {
  uname -r | sed -E 's/\.amzn[0-9]+.*$//'
}

manifest_get() {
  local key="$1"
  [[ -r "$ART_DIR/MANIFEST" ]] || die "missing $ART_DIR/MANIFEST (bundle built without build-bundle.sh?)"
  sed -n "s/^${key}=//p" "$ART_DIR/MANIFEST" | head -n1
}

usage() {
  cat <<EOF
Usage: bootstrap.sh [--check-only]
  --check-only  run the preflight checks (kernel, devices, tools) and exit
Env overrides: SANDBOX_DEMO_ROOT SANDBOX_DEMO_STATE SANDBOX_DEMO_ARTIFACTS SANDBOX_DEMO_BIN_DIR
EOF
}

check_memory_pool() {
  local total
  total="$(cat /sys/class/misc/nitro_enclaves/mem_pool/total 2>/dev/null)" || total=0
  if ! [[ "$total" =~ ^[0-9]+$ ]] || [[ "$total" -eq 0 ]] \
      || ! grep -Eq '(^| )nitro_enclaves.mempool=[^ ]+' /proc/cmdline; then
    die "No verified boot-time Nitro Sandbox memory pool. On AL2023 run: sudo grubby --update-kernel=ALL --args='nitro_enclaves.mempool=4G'; sudo reboot. After reboot verify /proc/cmdline and /sys/class/misc/nitro_enclaves/mem_pool/total (>0), then rerun sudo $ROOT/host/bootstrap.sh. Size the pool for your workload and parent memory; allocator.yaml is not sufficient."
  fi
  log "boot-time sandbox memory pool: $total bytes"
}

preflight() {
  [[ "$(uname -s)" == "Linux" ]] || die "must run on Linux (the AL2023 parent instance); this is $(uname -s)"
  [[ "$(id -u)" -eq 0 ]] || die "must run as root"
  local k
  k="$(kernel_core)"
  version_ge "$k" "$MIN_KERNEL" \
    || die "kernel $(uname -r) is older than required $MIN_KERNEL (use AMI al2023-ami-kernel-6.18-*, then 'dnf upgrade kernel')"
  log "kernel $(uname -r) >= $MIN_KERNEL OK"

  # Nitro Enclaves driver provides the sandbox device. UNVERIFIED which device node sandboxes need;
  # the plan says /dev/nitro_enclaves (same driver as Enclaves).
  if [[ ! -e /dev/nitro_enclaves ]]; then
    modprobe nitro_enclaves 2>/dev/null || true
  fi
  [[ -e /dev/nitro_enclaves ]] \
    || die "/dev/nitro_enclaves missing: instance must be launched with Nitro Enclaves enabled (enclave_options.enabled=true) on a supported non-metal type"
  log "/dev/nitro_enclaves present"
  if [[ -e /dev/kvm ]]; then
    log "/dev/kvm present (informational)"
  else
    warn "/dev/kvm absent (expected on non-metal EC2; sandboxes are believed to use /dev/nitro_enclaves instead - UNVERIFIED)"
  fi
  check_memory_pool

  local tool
  for tool in tar sha256sum mkfs.ext4 unsquashfs install find chroot; do
    command -v "$tool" >/dev/null 2>&1 \
      || die "required tool '$tool' not found (AL2023: dnf install e2fsprogs squashfs-tools; needs the AL2023 repo via the S3 VPC endpoint, UNVERIFIED)"
  done
  command -v python3 >/dev/null 2>&1 || die "python3 not found"
}

verify_artifacts() {
  [[ -d "$ART_DIR" ]] || die "artifact dir $ART_DIR missing; rebuild the bundle with scripts/build-bundle.sh"
  [[ -r "$ART_DIR/SHA256SUMS" ]] || die "missing $ART_DIR/SHA256SUMS"
  (cd "$ART_DIR" && sha256sum -c SHA256SUMS >/dev/null) || die "artifact checksum mismatch in $ART_DIR"
  log "artifact checksums OK"
}

install_firecracker() {
  local ver tgz tmp bin
  ver="$(manifest_get FIRECRACKER_VERSION)"
  tgz="$ART_DIR/$(manifest_get FIRECRACKER_TGZ)"
  [[ -n "$ver" ]] || die "FIRECRACKER_VERSION missing in MANIFEST"
  version_ge "$ver" "1.18.0" || die "Firecracker $ver < 1.18.0 (required for Nitro Sandboxes)"
  if [[ -x "$BIN_DIR/firecracker" ]] && "$BIN_DIR/firecracker" --version 2>/dev/null | grep -q "$ver"; then
    log "firecracker $ver already installed"
    return
  fi
  tmp="$(mktemp -d)"
  tar -xzf "$tgz" -C "$tmp"
  bin="$(find "$tmp" -type f -name 'firecracker-v*' ! -name '*.debug' ! -name '*.sha256*' | head -n1)"
  [[ -n "$bin" ]] || { rm -rf "$tmp"; die "no firecracker binary found inside $tgz"; }
  install -m 0755 "$bin" "$BIN_DIR/firecracker"
  rm -rf "$tmp"
  log "installed firecracker $ver to $BIN_DIR/firecracker"
}

place_kernel() {
  local src dst="$STATE_DIR/images/kernel"
  src="$ART_DIR/$(manifest_get KERNEL_FILE)"
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    log "guest kernel already in place"
    return
  fi
  install -m 0644 "$src" "$dst"
  log "placed guest kernel at $dst"
}

build_rootfs() {
  local squash stamp want dst="$STATE_DIR/images/rootfs.ext4" tmp mib
  squash="$ART_DIR/$(manifest_get ROOTFS_SQUASHFS)"
  stamp="$STATE_DIR/images/rootfs.stamp"
  want="python-path-check-v1-$(cat "$squash" "$ROOT/app/guest/agent.py" "$ROOT/app/guest/init.sh" | sha256sum | cut -d' ' -f1)"
  if [[ -f "$dst" && -f "$stamp" && "$(cat "$stamp")" == "$want" ]]; then
    log "rootfs already built for these inputs"
    return
  fi
  tmp="$(mktemp -d)"
  unsquashfs -f -d "$tmp/tree" "$squash" >/dev/null
  mkdir -p "$tmp/tree/opt/sandbox"
  install -m 0755 "$ROOT/app/guest/agent.py" "$tmp/tree/opt/sandbox/agent.py"
  install -m 0755 "$ROOT/app/guest/init.sh" "$tmp/tree/opt/sandbox/init.sh"
  if ! chroot "$tmp/tree" /bin/sh -c '
      export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
      exec python3 -I -c "import sys; assert sys.version_info.major == 3"'; then
    rm -rf "$tmp"
    die "rootfs has no usable Python 3 interpreter on the guest PATH"
  fi
  mib=$(( $(du -sm "$tmp/tree" | cut -f1) + ROOTFS_EXTRA_MIB ))
  rm -f "$dst.tmp"
  truncate -s "${mib}M" "$dst.tmp"
  mkfs.ext4 -q -F -L rootfs -d "$tmp/tree" "$dst.tmp"
  mv "$dst.tmp" "$dst"
  printf '%s\n' "$want" > "$stamp"
  rm -rf "$tmp"
  log "built $dst (${mib} MiB)"
}

main() {
  case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --check-only) preflight; log "preflight OK"; exit 0 ;;
    "") ;;
    *) usage >&2; exit 2 ;;
  esac
  rm -f "$STATE_DIR/.bootstrap-done"
  preflight
  verify_artifacts
  install -d -m 0755 "$STATE_DIR" "$STATE_DIR/images"
  install -d -m 0700 "$STATE_DIR/runs"
  install_firecracker
  place_kernel
  build_rootfs
  chmod 0755 "$ROOT/app/bin/run-sandbox"
  date -u +%FT%TZ > "$STATE_DIR/.bootstrap-done"
  log "done. Try: sudo $ROOT/app/bin/run-sandbox $ROOT/app/samples/hello.py"
}

# Allow tests to source the helper functions without running anything.
if [[ "${BOOTSTRAP_SOURCE_ONLY:-0}" == "1" ]]; then
  # shellcheck disable=SC2317
  return 0 2>/dev/null || exit 0
fi
main "$@"
