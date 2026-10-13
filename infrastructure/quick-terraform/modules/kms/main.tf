# Module: kms
# CMK for Amazon Quick with an IDC-aware key policy, plus optional QuickSight registration.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  # Service principals that must use the key for IDC-integrated Amazon Quick.
  # Mirrors the baseline IDC key policy from the Enterprise Governance guidance.
  idc_service_principals = [
    "sso.amazonaws.com",
    "identitystore.amazonaws.com",
  ]
}

data "aws_iam_policy_document" "key" {
  # Root account retains full control (break-glass / administration).
  statement {
    sid       = "EnableRootAccount"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  # QuickSight service use of the key.
  statement {
    sid       = "AllowQuickSight"
    effect    = "Allow"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["quicksight.amazonaws.com"]
    }
  }

  # IDC service principals (only when integrating with Identity Center).
  dynamic "statement" {
    for_each = var.allow_identity_center ? [1] : []
    content {
      sid       = "AllowIdentityCenter"
      effect    = "Allow"
      actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"]
      resources = ["*"]
      principals {
        type        = "Service"
        identifiers = local.idc_service_principals
      }
    }
  }
}

resource "aws_kms_key" "this" {
  description             = "Amazon Quick CMK (${var.alias})"
  multi_region            = var.multi_region
  enable_key_rotation     = var.enable_key_rotation
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.key.json
  tags                    = var.tags
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.alias}"
  target_key_id = aws_kms_key.this.key_id
}

resource "aws_quicksight_key_registration" "this" {
  count = var.register_with_quicksight ? 1 : 0

  aws_account_id = local.account_id

  key_registration {
    key_arn     = aws_kms_key.this.arn
    default_key = true
  }
}
