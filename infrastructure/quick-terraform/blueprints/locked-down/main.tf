# Blueprint: locked-down
# Secure-by-default governance posture. Ships hardened out of the box:
#   - READER role gets a deny-by-default profile (view only; allow-list exceptions).
#   - AUTHOR role gets the author_no_share preset (can build, cannot share externally).
#   - KMS CMK enabled, service role enabled.
# IDC optional (default on). VPC off here - combine with vpc-no-idc / idc-vpc-governed
# for network isolation. Extend custom_permission_profiles to add more postures.

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "account_name" {
  type = string
}

variable "notification_email" {
  type = string
}

variable "enable_idc" {
  type    = bool
  default = true
}

variable "identity_store_id" {
  description = "Required when enable_idc = true."
  type        = string
  default     = ""
}

variable "admin_group_name" {
  type    = string
  default = "QUICK_ADMIN"
}

variable "reader_allow" {
  description = "Capabilities to leave PERMITTED for the locked-down READER profile (exceptions to deny-by-default). e.g. [\"export_to_pdf\"]."
  type        = list(string)
  default     = []
}

variable "extra_profiles" {
  description = "Additional custom-permission profiles to merge in (same schema as the module)."
  type = map(object({
    deny_by_default = optional(bool, false)
    preset          = optional(string)
    allow           = optional(list(string), [])
    capabilities    = optional(map(string), {})
  }))
  default = {}
}

variable "extra_role_assignments" {
  description = "Additional role => profile assignments to merge in."
  type        = map(string)
  default     = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  # Hardened defaults: a deny-by-default reader profile + an author_no_share profile.
  default_profiles = {
    LockedReader = {
      deny_by_default = true
      allow           = var.reader_allow # everything else denied
    }
    AuthorNoShare = {
      preset = "author_no_share"
    }
  }

  default_role_assignments = {
    READER = "LockedReader"
    AUTHOR = "AuthorNoShare"
  }

  profiles         = merge(local.default_profiles, var.extra_profiles)
  role_assignments = merge(local.default_role_assignments, var.extra_role_assignments)
}

module "quick" {
  source = "../idc-vpc-governed"

  aws_region         = var.aws_region
  account_name       = var.account_name
  notification_email = var.notification_email

  enable_idc        = var.enable_idc
  identity_store_id = var.identity_store_id
  admin_group_name  = var.admin_group_name

  enable_kms          = true
  enable_service_role = true

  # No VPC in this blueprint (compose with the VPC blueprints if needed).
  enable_privatelink    = false
  enable_vpc_connection = false

  enable_custom_permissions          = true
  custom_permission_profiles         = local.profiles
  custom_permission_role_assignments = local.role_assignments

  tags = var.tags
}

output "account_name" {
  value = module.quick.account_name
}

output "custom_permission_profiles" {
  value = module.quick.custom_permission_profiles
}

output "kms_key_arn" {
  value = module.quick.kms_key_arn
}

output "service_role_arn" {
  value = module.quick.service_role_arn
}
