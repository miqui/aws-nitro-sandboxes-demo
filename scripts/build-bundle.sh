#!/usr/bin/env bash
# Build sandbox-demo.tgz (host/ + app/ + host/artifacts/) on the OPERATOR's machine.
# Needs internet (unless --offline-dir): the target EC2 host has none. Output is uploaded by upload-bundle.sh.
# UNVERIFIED: kernel/rootfs URLs depend on the Firecracker CI artifact layout; pass them explicitly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FC_VERSION="1.18.0"
ARCH="x86_64"
OFFLINE_DIR=""
KERNEL_URL=""
ROOTFS_URL=""
OUTPUT="$REPO_ROOT/dist/sandbox-demo.tgz"
DRY_RUN=0

usage() {
  cat <<EOF
Usage: build-bundle.sh [options]
  --fc-version V     Firecracker release (default $FC_VERSION; must be >= 1.18.0)
  --arch A           x86_64 (default) | aarch64
  --offline-dir DIR  use pre-downloaded files instead of the network. Expected in DIR:
                       firecracker-v<V>-<arch>.tgz, a kernel (bzImage* / vmlinux* / Image*),
                       a rootfs *.squashfs
  --kernel-url URL   Firecracker CI guest kernel (bzImage, DMB-patched); required without --offline-dir
  --rootfs-url URL   Firecracker CI Ubuntu rootfs (.squashfs); required without --offline-dir
  --output FILE      default dist/sandbox-demo.tgz
  --dry-run          print what would happen; no network, no files written
EOF
}

die() { printf 'build-bundle: %s\n' "$*" >&2; exit 1; }
say() { printf 'build-bundle: %s\n' "$*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fc-version) FC_VERSION="${2:?}"; shift 2 ;;
    --arch) ARCH="${2:?}"; shift 2 ;;
    --offline-dir) OFFLINE_DIR="${2:?}"; shift 2 ;;
    --kernel-url) KERNEL_URL="${2:?}"; shift 2 ;;
    --rootfs-url) ROOTFS_URL="${2:?}"; shift 2 ;;
    --output) OUTPUT="${2:?}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

case "$ARCH" in x86_64|aarch64) ;; *) die "unsupported --arch $ARCH" ;; esac
[[ "$(printf '%s\n%s\n' "1.18.0" "$FC_VERSION" | sort -V | head -n1)" == "1.18.0" ]] \
  || die "Firecracker $FC_VERSION < 1.18.0"

FC_TGZ="firecracker-v${FC_VERSION}-${ARCH}.tgz"
FC_URL="https://github.com/firecracker-microvm/firecracker/releases/download/v${FC_VERSION}/${FC_TGZ}"

if [[ -z "$OFFLINE_DIR" && "$DRY_RUN" -eq 0 ]]; then
  [[ -n "$KERNEL_URL" && -n "$ROOTFS_URL" ]] || die "--kernel-url and --rootfs-url are required without --offline-dir"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  say "DRY RUN - no network access, nothing written"
  if [[ -n "$OFFLINE_DIR" ]]; then
    say "would read artifacts from $OFFLINE_DIR: $FC_TGZ, kernel, rootfs .squashfs"
  else
    say "would download $FC_URL"
    say "would download kernel ${KERNEL_URL:-<--kernel-url required>}"
    say "would download rootfs ${ROOTFS_URL:-<--rootfs-url required>}"
  fi
  say "would stage host/ app/ and host/artifacts/{$FC_TGZ,kernel,rootfs.squashfs,MANIFEST,SHA256SUMS}"
  say "would write $OUTPUT"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/stage"
ART="$STAGE/host/artifacts"
mkdir -p "$ART"

if [[ -n "$OFFLINE_DIR" ]]; then
  [[ -d "$OFFLINE_DIR" ]] || die "--offline-dir $OFFLINE_DIR is not a directory"
  [[ -f "$OFFLINE_DIR/$FC_TGZ" ]] || die "missing $OFFLINE_DIR/$FC_TGZ"
  cp "$OFFLINE_DIR/$FC_TGZ" "$ART/$FC_TGZ"
  kernel_src="$(find "$OFFLINE_DIR" -maxdepth 1 -type f \( -name 'bzImage*' -o -name 'vmlinux*' -o -name 'Image*' \) | sort | head -n1)"
  rootfs_src="$(find "$OFFLINE_DIR" -maxdepth 1 -type f -name '*.squashfs' | sort | head -n1)"
  [[ -n "$kernel_src" ]] || die "no kernel (bzImage*/vmlinux*/Image*) in $OFFLINE_DIR"
  [[ -n "$rootfs_src" ]] || die "no *.squashfs rootfs in $OFFLINE_DIR"
  cp "$kernel_src" "$ART/kernel"
  cp "$rootfs_src" "$ART/rootfs.squashfs"
else
  command -v curl >/dev/null 2>&1 || die "curl required"
  curl -fL --retry 3 -o "$ART/$FC_TGZ" "$FC_URL"
  # Release checksum file name is assumed; failure to fetch only skips the cross-check.
  if curl -fsL -o "$WORK/fc.sha256" "${FC_URL}.sha256.txt"; then
    (cd "$ART" && sha256sum -c "$WORK/fc.sha256" >/dev/null) || die "Firecracker checksum mismatch"
    say "Firecracker release checksum verified"
  else
    say "WARNING: no upstream checksum found; recording local sha256 only"
  fi
  curl -fL --retry 3 -o "$ART/kernel" "$KERNEL_URL"
  curl -fL --retry 3 -o "$ART/rootfs.squashfs" "$ROOTFS_URL"
fi

cp -R "$REPO_ROOT/host" "$STAGE/"  # host/ already contains bootstrap.sh; artifacts merged below
cp -R "$REPO_ROOT/app" "$STAGE/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
[[ -f "$STAGE/host/bootstrap.sh" ]] || die "host/bootstrap.sh missing from repo"

cat > "$ART/MANIFEST" <<EOF
FIRECRACKER_VERSION=$FC_VERSION
FIRECRACKER_TGZ=$FC_TGZ
KERNEL_FILE=kernel
ROOTFS_SQUASHFS=rootfs.squashfs
ARCH=$ARCH
EOF
(cd "$ART" && sha256sum "$FC_TGZ" kernel rootfs.squashfs MANIFEST > SHA256SUMS)

mkdir -p "$(dirname "$OUTPUT")"
tar -czf "$OUTPUT" -C "$STAGE" host app
say "wrote $OUTPUT ($(wc -c < "$OUTPUT") bytes)"
