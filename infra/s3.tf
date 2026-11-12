data "aws_caller_identity" "current" {}

# Artifacts bucket. The bundle is uploaded by a script (scripts/upload-bundle.sh), not by tofu.
resource "aws_s3_bucket" "artifacts" {
  # checkov:skip=CKV_AWS_18:Demo bucket; access logging needs a second log bucket. In the production checklist.
  # checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for the demo. In the production checklist.
  # checkov:skip=CKV_AWS_145:SSE-S3 is deliberate for the demo; CMK is in the production checklist.
  # checkov:skip=CKV2_AWS_62:No event consumers in this demo.
  bucket = "${var.project_name}-artifacts-${data.aws_caller_identity.current.account_id}-${var.region}"
  # Demo convenience: lets `tofu destroy` remove a non-empty bucket. Set false for production.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # SSE-S3; use aws:kms + CMK for production (see README checklist)
    }
  }
}

data "aws_iam_policy_document" "artifacts" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket     = aws_s3_bucket.artifacts.id
  policy     = data.aws_iam_policy_document.artifacts.json
  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket     = aws_s3_bucket.artifacts.id
  depends_on = [aws_s3_bucket_versioning.artifacts]

  rule {
    id     = "expire-old-versions-and-abort-mpu"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
