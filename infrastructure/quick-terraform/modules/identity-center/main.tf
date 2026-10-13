# Module: identity-center
# Create-or-reference IAM Identity Center groups.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60.0"
    }
  }
}

locals {
  groups_to_create = { for k, g in var.groups : k => g if g.create }
  groups_to_lookup = { for k, g in var.groups : k => g if !g.create }
}

resource "aws_identitystore_group" "this" {
  for_each = local.groups_to_create

  identity_store_id = var.identity_store_id
  display_name      = each.value.display_name
  description       = each.value.description
}

data "aws_identitystore_group" "existing" {
  for_each = local.groups_to_lookup

  identity_store_id = var.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "DisplayName"
      attribute_value = each.value.display_name
    }
  }
}
