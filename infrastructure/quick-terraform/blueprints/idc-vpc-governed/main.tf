# Blueprint: idc-vpc-governed
# Composes the reusable modules under ../../modules via feature flags. Turning flags
# off collapses this to the minimal / non-IDC / non-VPC topologies, so it is the single
# source blueprint behind the simpler ones.

locals {
  auth_method = var.enable_idc ? "IAM_IDENTITY_CENTER" : "IAM_AND_QUICKSIGHT"

  # Validate IDC prerequisites at plan time via a precondition on the account module.
  idc_groups = var.enable_idc ? {
    admin = {
      display_name = var.admin_group_name
      description  = "Amazon Quick admin group"
      create       = var.create_admin_group
    }
  } : {}
}

# --- Identity Center groups (only when enable_idc) -------------------------
module "identity_center" {
  count  = var.enable_idc ? 1 : 0
  source = "../../modules/identity-center"

  identity_store_id = var.identity_store_id
  groups            = local.idc_groups
}

# --- Account subscription + namespace --------------------------------------
module "quick_account" {
  source = "../../modules/quick-account"

  account_name          = var.account_name
  notification_email    = var.notification_email
  edition               = var.edition
  authentication_method = local.auth_method
  admin_group           = var.enable_idc ? [var.admin_group_name] : []
  create_namespace      = true
  namespace             = "default"
  tags                  = var.tags
}

# --- KMS CMK (toggle) ------------------------------------------------------
module "kms" {
  count  = var.enable_kms ? 1 : 0
  source = "../../modules/kms"

  alias                    = "amazon-quick-cmk"
  multi_region             = var.kms_multi_region
  allow_identity_center    = var.enable_idc
  register_with_quicksight = true
  tags                     = var.tags

  depends_on = [module.quick_account]
}

# --- Service role (toggle) -------------------------------------------------
module "service_roles" {
  count  = var.enable_service_role ? 1 : 0
  source = "../../modules/service-roles"

  role_name      = "AmazonQuickServiceRole"
  s3_bucket_arns = var.service_role_s3_bucket_arns
  tags           = var.tags
}

# --- VPC access (toggles) --------------------------------------------------
module "vpc_access" {
  count  = var.enable_privatelink || var.enable_vpc_connection ? 1 : 0
  source = "../../modules/vpc-access"

  enable_privatelink       = var.enable_privatelink
  enable_vpc_connection    = var.enable_vpc_connection
  vpc_id                   = var.vpc_id
  subnet_ids               = var.subnet_ids
  security_group_ids       = var.security_group_ids
  privatelink_service_name = var.privatelink_service_name
  tags                     = var.tags
}

# --- Custom permissions (toggle) -------------------------------------------
module "custom_permissions" {
  count  = var.enable_custom_permissions ? 1 : 0
  source = "../../modules/custom-permissions"

  profiles           = var.custom_permission_profiles
  role_assignments   = var.custom_permission_role_assignments
  use_cloudformation = var.custom_permissions_use_cloudformation
  tags               = var.tags

  depends_on = [module.quick_account]
}

# --- Account governance (toggles) ------------------------------------------
module "account_governance" {
  count  = var.enable_account_settings || var.enable_ip_restriction || length(var.iam_policy_assignments) > 0 ? 1 : 0
  source = "../../modules/account-governance"

  enable_account_settings        = var.enable_account_settings
  default_namespace              = "default"
  termination_protection_enabled = var.termination_protection_enabled

  enable_ip_restriction                = var.enable_ip_restriction
  ip_restriction_enabled               = var.ip_restriction_enabled
  ip_restriction_rule_map              = var.ip_restriction_rule_map
  vpc_id_restriction_rule_map          = var.vpc_id_restriction_rule_map
  vpc_endpoint_id_restriction_rule_map = var.vpc_endpoint_id_restriction_rule_map

  iam_policy_assignments = var.iam_policy_assignments

  tags = var.tags

  depends_on = [module.quick_account]
}
