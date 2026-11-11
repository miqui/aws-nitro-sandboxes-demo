output "region" {
  value = var.region
}

output "architecture" {
  description = "Parent CPU architecture (only x86_64 is wired in this demo)."
  value       = var.architecture
}

output "artifacts_bucket" {
  description = "Upload sandbox-demo.tgz (and optional .sha256) here."
  value       = aws_s3_bucket.artifacts.id
}

output "instance_id" {
  value = aws_instance.host.id
}

output "ssm_start_session_command" {
  description = "Open a shell on the host (needs the Session Manager plugin)."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.host.id}"
}

output "run_sandbox_command" {
  description = "Run on the host after bootstrap finished."
  value       = "sudo /opt/sandbox-demo/app/bin/run-sandbox /opt/sandbox-demo/app/samples/hello.py"
}

output "kms_key_arn" {
  description = "Phase 2 key ARN (null unless enable_phase2_kms)."
  value       = one(aws_kms_key.sandbox_secret[*].arn)
}
