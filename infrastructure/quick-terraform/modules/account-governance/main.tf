# Module: account-governance
# Account-wide Amazon Quick admin controls, each behind its own toggle:
#   1. account_settings     - default namespace + termination protection.
#   2. ip_restriction        - allow/deny console & API access by IP CIDR, VPC, or VPC endpoint.
#   3. iam_policy_assignment - bind an IAM policy to QuickSight users/groups in a namespace.

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
  account_id = data.aws_caller_identity.current.account_id
}

# -----------------------------------------------------------------------------
# Account settings: default namespace + termination protection
# -----------------------------------------------------------------------------
resource "aws_quicksight_account_settings" "this" {
  count = var.enable_account_settings ? 1 : 0

  aws_account_id                 = local.account_id
  default_namespace              = var.default_namespace
  termination_protection_enabled = var.termination_protection_enabled

  timeouts {
    create = "15m"
    update = "15m"
  }
}

# -----------------------------------------------------------------------------
# IP / VPC restrictions: gate console & API access
# -----------------------------------------------------------------------------
resource "aws_quicksight_ip_restriction" "this" {
  count = var.enable_ip_restriction ? 1 : 0

  aws_account_id = local.account_id
  enabled        = var.ip_restriction_enabled

  ip_restriction_rule_map              = var.ip_restriction_rule_map
  vpc_id_restriction_rule_map          = var.vpc_id_restriction_rule_map
  vpc_endpoint_id_restriction_rule_map = var.vpc_endpoint_id_restriction_rule_map
}

# -----------------------------------------------------------------------------
# IAM policy assignments: attach an IAM policy to QuickSight identities
# -----------------------------------------------------------------------------
resource "aws_quicksight_iam_policy_assignment" "this" {
  for_each = var.iam_policy_assignments

  aws_account_id    = local.account_id
  assignment_name   = each.key
  assignment_status = each.value.status
  namespace         = each.value.namespace
  policy_arn        = each.value.policy_arn

  identities {
    user  = each.value.users
    group = each.value.groups
  }
}
