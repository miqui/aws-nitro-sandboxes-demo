# Phase 2 only (enable_phase2_kms = true). UNVERIFIED end to end: the plan found no AWS recipe
# for building a sandbox EIF and reading its PCR0, so the pinned value must come from the operator.
# Verified (KMS docs): condition key kms:RecipientAttestation:ImageSha384 = PCR0; applies to
# Decrypt/GenerateDataKey*/GenerateRandom/DeriveSharedSecret. Debug-mode sandboxes have zeroed PCRs.

data "aws_iam_policy_document" "kms_key" {
  count = var.enable_phase2_kms ? 1 : 0

  # Management without Decrypt: IAM policies in this account may administer the key,
  # but "kms:*" is intentionally not granted here.
  statement {
    sid = "AccountKeyManagementNoDecrypt"
    actions = [
      "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*", "kms:Update*",
      "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:TagResource", "kms:UntagResource",
      "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion", "kms:Encrypt",
    ]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid       = "DecryptOnlyForAttestedSandbox"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.host.arn]
    }

    condition {
      test     = "StringEqualsIgnoreCase"
      variable = "kms:RecipientAttestation:ImageSha384"
      values   = [var.kms_allowed_image_pcr0]
    }
  }
}

resource "aws_kms_key" "sandbox_secret" {
  count = var.enable_phase2_kms ? 1 : 0

  description             = "${var.project_name}: secret released only to an attested Nitro Sandbox"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.kms_key[0].json

  lifecycle {
    precondition {
      condition     = var.kms_allowed_image_pcr0 != ""
      error_message = "kms_allowed_image_pcr0 must be set (96 hex chars) when enable_phase2_kms = true."
    }
  }
}

resource "aws_kms_alias" "sandbox_secret" {
  count = var.enable_phase2_kms ? 1 : 0

  name          = "alias/${var.project_name}-sandbox-secret"
  target_key_id = aws_kms_key.sandbox_secret[0].key_id
}

# Identity side: the host role may call Decrypt on this key (the parent relays the guest's
# attestation document as the Recipient parameter). The key policy's PCR0 condition gates it.
data "aws_iam_policy_document" "kms_decrypt" {
  count = var.enable_phase2_kms ? 1 : 0

  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.sandbox_secret[0].arn]
  }
}

resource "aws_iam_role_policy" "kms_decrypt" {
  count = var.enable_phase2_kms ? 1 : 0

  name   = "kms-decrypt-attested"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.kms_decrypt[0].json
}
