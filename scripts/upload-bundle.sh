#!/usr/bin/env bash
# Upload dist/sandbox-demo.tgz to the artifacts bucket. Use --dry-run to only print the command.
# This repo's tests never run it for real; the operator runs it with their own AWS credentials.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUCKET=""
FILE="$REPO_ROOT/dist/sandbox-demo.tgz"
KEY="sandbox-demo.tgz"
REGION=""
PROFILE=""
DRY_RUN=0

usage() {
  cat <<EOF
Usage: upload-bundle.sh --bucket NAME [--file F] [--region R] [--profile P] [--dry-run]
Uploads F (default dist/sandbox-demo.tgz) to s3://NAME/$KEY
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket) BUCKET="${2:?}"; shift 2 ;;
    --file) FILE="${2:?}"; shift 2 ;;
    --region) REGION="${2:?}"; shift 2 ;;
    --profile) PROFILE="${2:?}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

[[ -n "$BUCKET" ]] || { echo "upload-bundle: --bucket is required" >&2; exit 2; }
cmd=(aws s3 cp "$FILE" "s3://${BUCKET}/${KEY}")
[[ -z "$REGION" ]] || cmd+=(--region "$REGION")
[[ -z "$PROFILE" ]] || cmd+=(--profile "$PROFILE")

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "DRY RUN - not executing:"
  printf '%q ' "${cmd[@]}"; echo
  exit 0
fi
[[ -f "$FILE" ]] || { echo "upload-bundle: $FILE not found (run scripts/build-bundle.sh)" >&2; exit 1; }
"${cmd[@]}"
