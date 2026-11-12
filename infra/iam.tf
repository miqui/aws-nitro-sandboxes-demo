data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name_prefix        = "${var.project_name}-host-"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

# Minimal Session Manager permissions (instead of the broader AmazonSSMManagedInstanceCore).
# These actions do not support resource-level restriction, hence "*".
# UNVERIFIED: the plan did not confirm this is the strict minimum; if SSM registration
# fails, compare against AmazonSSMManagedInstanceCore.
data "aws_iam_policy_document" "ssm_core" {
  # checkov:skip=CKV_AWS_111:SSM agent actions do not support resource-level constraints.
  # checkov:skip=CKV_AWS_356:SSM agent actions do not support resource-level constraints.
  statement {
    sid = "SessionManager"
    actions = [
      "ssm:UpdateInstanceInformation",
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
      "ec2messages:AcknowledgeMessage",
      "ec2messages:DeleteMessage",
      "ec2messages:FailMessage",
      "ec2messages:GetEndpoint",
      "ec2messages:GetMessages",
      "ec2messages:SendReply",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ssm_core" {
  name   = "ssm-session-manager"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.ssm_core.json
}

# Read-only access to the bundle object only (contract: s3://<bucket>/<bundle_key>[.sha256]).
data "aws_iam_policy_document" "artifacts_read" {
  statement {
    sid     = "ReadBundle"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.artifacts.arn}/sandbox-demo.tgz",
      "${aws_s3_bucket.artifacts.arn}/sandbox-demo.tgz.sha256",
    ]
  }
}

resource "aws_iam_role_policy" "artifacts_read" {
  name   = "artifacts-read"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.artifacts_read.json
}

resource "aws_iam_instance_profile" "host" {
  name_prefix = "${var.project_name}-host-"
  role        = aws_iam_role.host.name
}
