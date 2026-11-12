<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:60fe4f9eea61e702acba90cc426205df772be1eb3ba41901321a95a4755de247 -->

# infra/ — Nitro Sandboxes demo (OpenTofu)

Private-only VPC + one Nitro-Enclaves-enabled parent EC2 host (AL2023 kernel 6.18) that runs
Firecracker >= 1.18 Nitro Sandboxes. Access is **SSM Session Manager only** (no SSH, no public IP,
no IGW, no NAT).

> Status: written and statically validated offline. **Never planned/applied against AWS.**

## Files
| File | Purpose |
|---|---|
| `versions.tf` | tofu >= 1.8, `hashicorp/aws ~> 6.0` |
| `network.tf` | VPC, 3 private subnets (3 AZs), S3 gateway endpoint, interface endpoints ssm/ssmmessages/ec2messages (+ optional logs, kms) |
| `security_groups.tf` | `host` SG (no ingress; egress 443 to endpoints SG + S3 prefix list), `endpoints` SG (443 from host SG only) |
| `iam.tf` | host role: inline minimal Session Manager actions + `s3:GetObject` on the bundle object only |
| `s3.tf` | artifacts bucket: public access block, versioning, SSE-S3, TLS-only policy, lifecycle |
| `host.tf` | launch template (`resolve:ssm:` AL2023 6.18 AMI, `enclave_options`, IMDSv2 hop 1, encrypted gp3, no public IP) + instance |
| `kms.tf` | phase 2 only: key whose `Decrypt` is allowed only when `kms:RecipientAttestation:ImageSha384` = pinned PCR0 |
| `tests/policy_test.sh` | offline static invariant checks (regex; limited) |

## Bundle contract (infra <-> host/app)
1. Operator uploads `sandbox-demo.tgz` (containing `host/` and `app/`) to `s3://<artifacts_bucket>/sandbox-demo.tgz`
   (optionally `sandbox-demo.tgz.sha256`, verified by user-data if present). Upload is done by a script, not tofu.
2. Instance user-data (inline, tiny) copies it to `/tmp`, extracts to `/opt/sandbox-demo`, then `exec`s
   `/opt/sandbox-demo/host/bootstrap.sh` as root (installs Firecracker >= 1.18, kernel/rootfs, `app/`).
   Log: `/var/log/sandbox-demo-userdata.log`.
3. Because the instance has no NAT, **everything bootstrap needs must be inside the bundle** (or S3 objects the role may read: only `<bundle_key>` and `<bundle_key>.sha256` are readable).
4. The host may boot before the bundle exists; user-data then fails. Upload first, then `apply`, or re-run: `sudo cloud-init clean --logs && sudo reboot`, or run the user-data steps by hand via SSM.
5. Run: `sudo /opt/sandbox-demo/app/bin/run-sandbox <file.py>` via SSM.

Chicken-and-egg: the bucket is created by this stack. Suggested order: `tofu apply -target=aws_s3_bucket.artifacts` (+ upload), then full `tofu apply`; or apply fully and reboot the host after upload.

## Variables
| Name | Default | Notes |
|---|---|---|
| `project_name` | `nitro-sbx` | name prefix |
| `region` | `us-east-1` | |
| `instance_type` | `m6i.2xlarge` | **UNVERIFIED** Enclaves-capable; validation rejects `.metal` and `t*` |
| `architecture` | `x86_64` | only value allowed |
| `ami_ssm_parameter_x86_64` | `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.18-x86_64` | from Firecracker/EC2 docs |
| `vpc_cidr` | `10.20.0.0/16` | /24 subnets via `cidrsubnet` |
| `root_volume_size_gb` | 30 | |
| `enable_logs_endpoint` | false | extra hourly cost |
| `enable_phase2_kms` | false | adds KMS key, role policy, KMS interface endpoint |
| `kms_allowed_image_pcr0` | `""` | 96 hex chars, non-zero; required when phase 2 on (precondition) |

Outputs: `region`, `artifacts_bucket`, `instance_id`, `ssm_start_session_command`, `run_sandbox_command`, `kms_key_arn`.

## Running it (Miguel, later — nothing here has been executed against AWS)
```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# 0. Pre-flight: confirm instance_type supports Nitro Enclaves (see UNVERIFIED below)
tofu init
tofu plan -out plan.tfplan      # needs AWS credentials; reads AZ list
tofu apply plan.tfplan
tofu output -raw ssm_start_session_command
tofu destroy                    # when done
```
Cost: 1 x m6i.2xlarge on-demand, 30 GiB gp3, and **3 AZs x 3 interface endpoints (9 ENIs billed hourly)** — the endpoints often cost more than the idle host. S3 gateway endpoint is free. Destroy when finished.

## Offline checks
```bash
tofu fmt -check -recursive && tofu init -backend=false && tofu validate   # no AWS creds
bash tests/policy_test.sh
tflint --init && tflint                                                   # if installed
checkov -d . --framework terraform                                        # 0 failed; skips documented inline
```

## UNVERIFIED items
- `m6i.2xlarge` supports Nitro Enclaves (plan could not read the full table).
- Exact tflint-ruleset-aws version in `.tflint.hcl`; `~> 6.0` provider resolved to latest 6.x at init.
- Minimal inline SSM policy is sufficient for registration (fallback: `AmazonSSMManagedInstanceCore`).
- Both `aws_launch_template` and `aws_instance` carry `enclave_options`/metadata/EBS settings; how the provider reconciles an instance that sets overrides alongside a launch template was not exercised.
- Phase 2: building a sandbox EIF and reading its PCR0 (no documented recipe found); KMS condition behaviour is documented, but the sandbox attestation flow end to end was not exercised. The root-account statement is deliberately limited (no `kms:*`); verify no lock-out.
- Bootstrap verifies a nonzero boot-time memory reservation (`nitro_enclaves.mempool` and sysfs counters).
  If absent it fails preflight with configuration, reboot, and retry steps; `allocator.yaml` alone is insufficient.
- No AWS call or `plan`/`apply` was run, so IAM, endpoint, SG and AMI-resolution behaviour is untested.

## Production checklist (infra-specific)
- Remote state with locking + encryption; no local state.
- Bucket: SSE-KMS with CMK, `force_destroy = false`, access logging, optionally restrict to the VPC endpoint (`aws:SourceVpce`) once uploads come from inside the VPC.
- Add an S3 endpoint policy and VPC endpoint policies; enable VPC Flow Logs and CloudTrail data events for the bucket.
- Use a customer-managed KMS key for EBS; pin the AMI ID instead of `resolve:ssm` latest for reproducibility.
- Verify bundle integrity with a signature (not just a same-bucket sha256).
- Auto Scaling / multiple hosts if >1 needed (single instance is a SPOF); Session Manager session logging to S3/CloudWatch.
- Prefer AWS-managed or least-privilege per-operator IAM for `ssm:StartSession`.
- Phase 2: pin PCR0 (and optionally PCR1/2/8) per release; rotate; alarm on KMS `Decrypt` denials; never use debug-mode sandboxes.
- Run tflint, checkov/trivy and a real `plan` review in CI before any apply.


<!-- /coderabbit-ai-generated:v1 -->