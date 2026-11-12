#!/usr/bin/env bash
# Offline static policy checks on infra/*.tf (regex only, python3 stdlib). Not a substitute for
# tflint/checkov/trivy or a real plan: it only guards a few invariants from regressing.
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 - "$dir" <<'PY'
import re, sys, pathlib

d = pathlib.Path(sys.argv[1])
src = {p.name: p.read_text() for p in d.glob("*.tf")}
allsrc = "\n".join(src.values())
# strip comments so documentation text cannot trip or satisfy checks
code = re.sub(r'(?m)(#|//).*$', '', allsrc)
fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


h = re.sub(r'(?m)(#|//).*$', '', src["host.tf"])
s = re.sub(r'(?m)(#|//).*$', '', src["s3.tf"])

check("no 0.0.0.0/0 or ::/0 CIDR in any .tf", not re.search(r'0\.0\.0\.0/0|::/0', code))
check("no ingress rule resource targets the host SG",
      not re.search(r'aws_vpc_security_group_ingress_rule"\s+"[^"]*"\s*\{[^}]*\n\s*security_group_id\s*=\s*aws_security_group\.host\.id', code))
check("no inline ingress blocks", not re.search(r'(?m)^\s*ingress\s*\{', code))
check("no associate_public_ip_address = true", not re.search(r'associate_public_ip_address\s*=\s*true', code))
check("host sets associate_public_ip_address = false (LT + instance)",
      len(re.findall(r'associate_public_ip_address\s*=\s*false', h)) >= 2)
check("no map_public_ip_on_launch = true", not re.search(r'map_public_ip_on_launch\s*=\s*true', code))
check("no internet/NAT gateway or EIP", not re.search(r'resource\s+"aws_(internet_gateway|nat_gateway|eip)"', code))
check("no key_name", not re.search(r'(?m)^\s*key_name\s*=', code))
check("IMDSv2 required (LT + instance), never optional",
      len(re.findall(r'http_tokens\s*=\s*"required"', h)) >= 2 and not re.search(r'http_tokens\s*=\s*"optional"', code))
check("IMDS hop limit 1 (LT + instance)", len(re.findall(r'http_put_response_hop_limit\s*=\s*1\b', h)) >= 2)
check("EBS encrypted in launch template, never false",
      len(re.findall(r'encrypted\s*=\s*true', h)) == 1 and not re.search(r'encrypted\s*=\s*false', code))
check("S3 public access block has all four flags set true",
      all(re.search(k + r'\s*=\s*true', s) for k in
          ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")))
check("S3 TLS-only deny policy", "aws:SecureTransport" in s)
check("S3 versioning enabled", re.search(r'status\s*=\s*"Enabled"\s*\n\s*}', s) is not None)
check("enclave_options enabled (LT + instance)",
      len(re.findall(r'enclave_options\s*\{\s*enabled\s*=\s*true\s*\}', h)) >= 2)
check("no local-exec / provisioners", not re.search(r'local-exec|provisioner\s+"', code))
check("no AWS access key literals", not re.search(r'AKIA[0-9A-Z]{16}', allsrc))
check("no wildcard-only IAM action", not re.search(r'actions\s*=\s*\[\s*"\*"\s*\]', code))
print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
PY
