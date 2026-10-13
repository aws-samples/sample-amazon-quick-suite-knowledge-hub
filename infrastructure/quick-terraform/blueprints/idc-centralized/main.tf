# Blueprint: idc-centralized
# The recommended centralized topology: IAM Identity Center integration + account +
# service role + custom-permission governance. No VPC isolation (add vpc-no-idc or
# idc-vpc-governed for that). Presets the full blueprint with VPC disabled.

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

variable "identity_store_id" {
  type = string
}

variable "admin_group_name" {
  type    = string
  default = "QUICK_ADMIN"
}

variable "create_admin_group" {
  type    = bool
  default = true
}

variable "enable_kms" {
  type    = bool
  default = true
}

variable "enable_custom_permissions" {
  type    = bool
  default = true
}

variable "custom_permission_profiles" {
  type = map(object({
    deny_by_default = optional(bool, false)
    preset          = optional(string)
    allow           = optional(list(string), [])
    capabilities    = optional(map(string), {})
  }))
  default = {}
}

variable "custom_permission_role_assignments" {
  type    = map(string)
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}

module "quick" {
  source = "../idc-vpc-governed"

  aws_region         = var.aws_region
  account_name       = var.account_name
  notification_email = var.notification_email

  enable_idc         = true
  identity_store_id  = var.identity_store_id
  admin_group_name   = var.admin_group_name
  create_admin_group = var.create_admin_group

  enable_kms          = var.enable_kms
  enable_service_role = true

  # No VPC in this blueprint.
  enable_privatelink    = false
  enable_vpc_connection = false

  enable_custom_permissions          = var.enable_custom_permissions
  custom_permission_profiles         = var.custom_permission_profiles
  custom_permission_role_assignments = var.custom_permission_role_assignments

  tags = var.tags
}

output "account_name" {
  value = module.quick.account_name
}

output "idc_group_ids" {
  value = module.quick.idc_group_ids
}

output "service_role_arn" {
  value = module.quick.service_role_arn
}

output "kms_key_arn" {
  value = module.quick.kms_key_arn
}

output "custom_permission_profiles" {
  value = module.quick.custom_permission_profiles
}
