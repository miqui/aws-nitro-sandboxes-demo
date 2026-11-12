<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:3eb32669fdf409c8408922756eb1ef8e74637522bbe751daf1211591d23c574f -->

# aws-nitro-sandboxes-demo

Run untrusted Python inside an **AWS Nitro Sandbox** (a Firecracker microVM backed by Nitro hardware isolation) on a
private, SSM-only EC2 host, deployed with OpenTofu.

> **Status: written and checked offline only.** Nothing here has been planned or applied against AWS, and no sandbox
> has ever booted. See [What is verified](#what-is-verified-and-what-is-not).

## Architecture

```
 Operator ──SSM Session Manager──► ssm / ssmmessages / ec2messages interface endpoints
                                              │            (no public IP, no SSH, no IGW, no NAT)
 ┌───────────────────── VPC (3 private subnets, 3 AZs) ──────────────────────────────────────────┐
 │  sg-endpoints (443 from sg-host)        S3 gateway endpoint ──► artifacts bucket (private)    │
 │  ┌──────── Parent EC2 (AL2023 kernel 6.18, Nitro Enclaves enabled, IMDSv2) ────────────────┐  │
 │  │ sg-host: egress 443 to endpoints + S3 only, no ingress                                    │  │
 │  │ sandbox_launcher (run-sandbox) ──► firecracker >= 1.18 ──/dev/nitro_enclaves──► Nitro HV  │  │
 │  │     │ config: "enclave": {}   rootfs (virtio-blk)   vsock (UDS "CONNECT 5005")            │  │
 │  │     ▼                                                                                     │  │
 │  │  ┌────────────── Nitro Sandbox (own VM; no network device is configured) ──────────────┐  │  │
 │  │  │ guest kernel ─► init.sh ─► guest agent ─► runs untrusted.py ─► JSON result ─vsock──►│  │  │
 │  │  └─────────────────────────────────────────────────────────────────────────────────────┘  │  │
 │  └───────────────────────────────────────────────────────────────────────────────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Nitro Sandbox vs Nitro Enclave.** Same primitive (Nitro Enclaves driver, EIF format, NSM device). An enclave gets
dedicated cores and memory, only a vsock to the parent, and no storage or network. A sandbox runs a normal Linux
guest under Firecracker with virtio block/vsock/net devices, can time-share parent cores, and is launched in
*confidential* mode (parent cannot read its memory; attestation available) or *direct* mode (parent can read memory;
no attestation). The mode is fixed at launch.

## What is verified and what is not

**Verified (offline, in this repo):** `tofu fmt/validate` (OpenTofu 1.13.9, AWS provider 6.x), tflint, checkov
(108 passed, 0 failed, 9 skipped; each skip is justified inline), `shellcheck`, `bash -n`, static policy tests, 50 unit
tests, and dry-runs of the launcher and the bundle scripts.

**UNVERIFIED (needs a real AWS account / host):**
- That `m6i.2xlarge` supports Nitro Enclaves (the full instance table was not read). Check before applying.
- Sandbox EIF / PCR0 for phase 2 (KMS attestation): no documented recipe to build a sandbox EIF with known PCRs was found.
- The `enclave` Firecracker JSON field names (`direct_mode`, `debug_mode`, `cpu_overcommit`) and the device node
  (`/dev/nitro_enclaves` vs `/dev/kvm`).
- The vsock handshake between launcher and guest agent; `init=/opt/sandbox/init.sh` as PID 1.
- That the Firecracker CI Ubuntu rootfs ships `python3`, and that the Firecracker CI artifact URLs and checksum file
  names are as assumed (you pass the URLs explicitly).
- That the minimal inline SSM IAM policy is sufficient; how the provider reconciles instance-level overrides with the
  launch template; the behaviour of IAM, endpoints, SGs and AMI resolution (no AWS call was ever made).
- **Not demonstrated at all:** "the parent cannot read sandbox memory" (AWS documents it for confidential mode; this
  repo neither proves nor tests it).
- Isolation probe: it has four checks (kernel/boot id, no parent paths/IMDS, no network, vCPU/mem limits). The "five
  demos" are those four plus the timeout-kill sample `infinite_loop.py`; `memory_hog.py` complements the limits check.
- `--cpu-overcommit` is **off by default** (opt-in flag); the original plan said on.

## Layout

| Path | Contents |
|---|---|
| `infra/` | OpenTofu: VPC, endpoints, SGs, IAM, S3 artifacts bucket, launch template + host, optional phase-2 KMS. See `infra/README.md` |
| `host/bootstrap.sh` | Runs on the host from user-data: preflight, installs Firecracker/kernel/rootfs from the bundle |
| `app/` | `sandbox_launcher` (Python stdlib), guest agent, samples, `phase2/kms_broker.py` (skeleton). See `app/README.md` |
| `scripts/` | `build-bundle.sh` (operator machine, needs internet), `upload-bundle.sh` |
| `tests/` | 50 `unittest` tests |
| `.github/workflows/ci.yml` | Keyless CI: jobs `validate` and `test` |

## Variables (`infra/variables.tf`)

| Name | Default | Notes |
|---|---|---|
| `project_name` | `nitro-sbx` | name prefix |
| `region` | `us-east-1` | |
| `instance_type` | `m6i.2xlarge` | UNVERIFIED Enclaves support; `.metal` and `t*` rejected |
| `architecture` | `x86_64` | only value allowed |
| `ami_ssm_parameter_x86_64` | AL2023 kernel-6.18 parameter | resolved at launch |
| `vpc_cidr` | `10.20.0.0/16` | |
| `root_volume_size_gb` | `30` | |
| `enable_logs_endpoint` | `false` | extra hourly cost |
| `enable_phase2_kms` | `false` | KMS key + endpoint; needs `kms_allowed_image_pcr0` |
| `kms_allowed_image_pcr0` | `""` | 96 hex chars when phase 2 is on |

## Cost

Regional estimate for us-east-1: **3 AZs x 3 interface endpoints (9 ENIs billed hourly)** cost about $0.09/hour,
versus about $0.384/hour for the default Linux `m6i.2xlarge` on-demand host. The host costs more while running;
30 GiB gp3 storage and data processing are additional costs. The S3 gateway endpoint is free. No NAT gateway. Destroy when done.

## Security posture

- No public IP, no SSH key, no IGW/NAT; access via SSM Session Manager only.
- Host SG: no ingress; egress 443 to the endpoints SG and S3 prefix list only. Endpoints SG: 443 from host SG only.
- IMDSv2 required, hop limit 1; encrypted gp3 root volume; default SG has no rules.
- Host role: minimal Session Manager actions plus `s3:GetObject` on the bundle object only. No credentials baked in.
- Bucket: public access block, versioning, SSE-S3, TLS-only policy.
- Sandbox: no network device configured; code travels over vsock; per-run rootfs copy deleted afterwards; timeout kill.
- Deliberate demo shortcuts (see inline `checkov:skip` comments): SSE-S3 rather than CMK, no bucket access logging or
  replication, no VPC flow logs, `force_destroy = true`.

## Deploy guide (for Miguel; this spends money, so read it first)

Prerequisites: AWS credentials for a sandbox account, `tofu` >= 1.8, AWS CLI + Session Manager plugin.

1. Confirm the instance type supports Nitro Enclaves in your region (UNVERIFIED default).
2. Build the bundle on your laptop (needs internet; pass Firecracker CI kernel/rootfs URLs):
   `scripts/build-bundle.sh --kernel-url <bzImage URL> --rootfs-url <ubuntu .squashfs URL>`
3. `cd infra && cp terraform.tfvars.example terraform.tfvars && tofu init`
4. Create only the bucket first: `tofu apply -target=aws_s3_bucket.artifacts`
5. Upload: `cd .. && scripts/upload-bundle.sh --bucket "$(cd infra && tofu output -raw artifacts_bucket)"`
6. Full apply: `cd infra && tofu apply` (review the plan first).
7. Connect: `eval "$(tofu output -raw ssm_start_session_command)"`. Wait for `sudo test -f /var/lib/sandbox-demo/.bootstrap-done` to succeed before running samples.
   If the marker does not appear or bootstrap fails, inspect `sudo cat /var/log/sandbox-demo-userdata.log`.
   If preflight reports no memory pool, follow its kernel-parameter and reboot steps, reconnect,
   and rerun `sudo /opt/sandbox-demo/host/bootstrap.sh`; wait for the marker again.
8. Run a sample on the host: `sudo /opt/sandbox-demo/app/bin/run-sandbox /opt/sandbox-demo/app/samples/hello.py`
   (also `isolation_probe.py`, `memory_hog.py`, `infinite_loop.py --timeout-s 5` -> exit 124).
9. Tear down: `exit` the SSM session, return to the local repository’s `infra/` directory, and run `tofu destroy` there.

If the host booted before the upload, user-data failed: upload, then `sudo cloud-init clean --logs && sudo reboot`.

## Run manually

Only commands actually executed while preparing this repo (macOS, OpenTofu v1.13.9, Python 3).

```
$ (cd infra && tofu fmt -check -recursive && tofu init -backend=false -input=false && tofu validate)
Success! The configuration is valid.

$ python3 -m unittest discover
..................................................
----------------------------------------------------------------------
Ran 50 tests in 3.938s

OK

$ scripts/build-bundle.sh --dry-run --kernel-url https://example.invalid/bzImage --rootfs-url https://example.invalid/rootfs.squashfs
build-bundle: DRY RUN - no network access, nothing written
build-bundle: would download https://github.com/firecracker-microvm/firecracker/releases/download/v1.18.0/firecracker-v1.18.0-x86_64.tgz
build-bundle: would download kernel https://example.invalid/bzImage
build-bundle: would download rootfs https://example.invalid/rootfs.squashfs
build-bundle: would stage host/ app/ and host/artifacts/{firecracker-v1.18.0-x86_64.tgz,kernel,rootfs.squashfs,MANIFEST,SHA256SUMS}
build-bundle: would write <repo>/dist/sandbox-demo.tgz

$ python3 app/bin/run-sandbox app/samples/hello.py --dry-run
# DRY RUN - nothing executed
# firecracker config (/var/lib/sandbox-demo/runs/93966ddf110b/config.json):
{
  "boot-source": {
    "kernel_image_path": "/var/lib/sandbox-demo/images/kernel",
    "boot_args": "reboot=k panic=1 init=/opt/sandbox/init.sh"
  },
  "drives": [
    {
      "drive_id": "rootfs",
      "path_on_host": "/var/lib/sandbox-demo/runs/93966ddf110b/rootfs.ext4",
      "is_root_device": true,
      "is_read_only": false
    }
  ],
  "machine-config": {
    "vcpu_count": 2,
    "mem_size_mib": 512
  },
  "vsock": {
    "guest_cid": 3,
    "uds_path": "/var/lib/sandbox-demo/runs/93966ddf110b/vsock.sock"
  },
  "enclave": {}
}
# commands:
mkdir -m 0700 -p /var/lib/sandbox-demo/runs/93966ddf110b
cp /var/lib/sandbox-demo/images/rootfs.ext4 /var/lib/sandbox-demo/runs/93966ddf110b/rootfs.ext4
firecracker --api-sock /var/lib/sandbox-demo/runs/93966ddf110b/api.sock --config-file /var/lib/sandbox-demo/runs/93966ddf110b/config.json
# host connects to /var/lib/sandbox-demo/runs/93966ddf110b/vsock.sock, sends 'CONNECT 5005', then exchanges framed JSON (code in, result out)
# on exit/timeout: terminate firecracker (SIGTERM, then SIGKILL), rm -rf /var/lib/sandbox-demo/runs/93966ddf110b
# code file: app/samples/hello.py (sent over vsock, not written to the rootfs)
```

## Production checklist

- Infra bar: private subnets in 3 AZs (already), a security group per role, least-privilege IAM per operator and per role.
- No baked credentials: instance profile only; sign and verify the bundle (not just a same-bucket sha256).
- Remote encrypted state with locking; pinned AMI ID; real `tofu plan` review in CI.
- Bucket: SSE-KMS with CMK, `force_destroy = false`, access logging, replication, endpoint policies, VPC flow logs, CloudTrail data events.
- More than one host (single instance is a SPOF); Session Manager session logging.
- Use the `jailer`, and a cross-run vCPU budget (combined overcommitted sandboxes must stay within 5x parent vCPUs).
- Phase 2: pin PCR0 per release, alarm on KMS `Decrypt` denials, never use debug mode.
- Prove, rather than assume, memory confidentiality before relying on it.

## License

See `LICENSE`.


<!-- /coderabbit-ai-generated:v1 -->