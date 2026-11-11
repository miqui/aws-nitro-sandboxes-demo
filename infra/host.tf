locals {
  # Contract with the host/app bundle (see README):
  #   s3://<artifacts_bucket>/<bundle_key> = tarball containing host/ and app/
  #   extracted to /opt/sandbox-demo; /opt/sandbox-demo/host/bootstrap.sh runs as root.
  # NOTE: only $VAR (no braces) is used below so HCL does not interpolate shell variables.
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    exec > >(tee /var/log/sandbox-demo-userdata.log) 2>&1
    TOKEN=$(curl -sS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
    export AWS_DEFAULT_REGION=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
    aws s3 cp s3://${aws_s3_bucket.artifacts.id}/${var.bundle_key} /tmp/sandbox-demo.tgz
    if aws s3 cp s3://${aws_s3_bucket.artifacts.id}/${var.bundle_key}.sha256 /tmp/sandbox-demo.tgz.sha256 2>/dev/null; then
      (cd /tmp && echo "$(cut -d' ' -f1 sandbox-demo.tgz.sha256)  sandbox-demo.tgz" | sha256sum -c -)
    else
      echo "WARNING: no .sha256 object found; bundle integrity not verified"
    fi
    mkdir -p /opt/sandbox-demo
    tar -xzf /tmp/sandbox-demo.tgz -C /opt/sandbox-demo
    exec /opt/sandbox-demo/host/bootstrap.sh
  EOT
}

resource "aws_launch_template" "host" {
  name_prefix            = "${var.project_name}-host-"
  image_id               = "resolve:ssm:${var.ami_ssm_parameter_x86_64}"
  instance_type          = var.instance_type
  ebs_optimized          = true
  update_default_version = true
  user_data              = base64encode(local.user_data)

  iam_instance_profile {
    arn = aws_iam_instance_profile.host.arn
  }

  # The single switch Nitro Sandboxes need on EC2 (changing it later recreates the instance).
  enclave_options {
    enabled = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  monitoring {
    enabled = true
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.root_volume_size_gb
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  network_interfaces {
    associate_public_ip_address = false
    security_groups             = [aws_security_group.host.id]
    delete_on_termination       = true
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.project_name}-host" }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${var.project_name}-host" }
  }
}

resource "aws_instance" "host" {
  # checkov:skip=CKV2_AWS_41:The instance profile is attached through the launch template (aws_launch_template.host).
  subnet_id = aws_subnet.private[0].id

  launch_template {
    id      = aws_launch_template.host.id
    version = aws_launch_template.host.latest_version
  }

  # No key pair, no public IP: access is SSM Session Manager only.
  associate_public_ip_address = false

  enclave_options {
    enabled = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = var.root_volume_size_gb
  }

  monitoring    = true
  ebs_optimized = true

  tags = { Name = "${var.project_name}-host" }

  # The host needs the S3 gateway route and SSM endpoints before it boots.
  depends_on = [
    aws_vpc_endpoint.s3,
    aws_vpc_endpoint.interface,
    aws_iam_role_policy.artifacts_read,
    aws_iam_role_policy.ssm_core,
  ]
}
