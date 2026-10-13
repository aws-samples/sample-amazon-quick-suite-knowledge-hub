# Module: quick-account
# Native Terraform provisioning of the Amazon Quick (QuickSight) account subscription
# and (optionally) a namespace. Replaces the earlier Lambda-based bootstrap with the
# first-class aws_quicksight_account_subscription resource.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60.0"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # IDC group assignment only applies to the IAM_IDENTITY_CENTER auth method.
  is_idc = var.authentication_method == "IAM_IDENTITY_CENTER"
}

resource "aws_quicksight_account_subscription" "this" {
  aws_account_id        = data.aws_caller_identity.current.account_id
  account_name          = var.account_name
  authentication_method = var.authentication_method
  edition               = var.edition
  notification_email    = var.notification_email

  # IDC group wiring (only meaningful for the Identity Center auth method).
  admin_group  = local.is_idc && length(var.admin_group) > 0 ? var.admin_group : null
  author_group = local.is_idc && length(var.author_group) > 0 ? var.author_group : null
  reader_group = local.is_idc && length(var.reader_group) > 0 ? var.reader_group : null

  # Subscription creation is asynchronous; allow generous time.
  timeouts {
    create = "30m"
    delete = "30m"
  }
}

resource "aws_quicksight_namespace" "this" {
  count = var.create_namespace ? 1 : 0

  aws_account_id = data.aws_caller_identity.current.account_id
  namespace      = var.namespace
  identity_store = "QUICKSIGHT"

  tags = var.tags

  depends_on = [aws_quicksight_account_subscription.this]
}
