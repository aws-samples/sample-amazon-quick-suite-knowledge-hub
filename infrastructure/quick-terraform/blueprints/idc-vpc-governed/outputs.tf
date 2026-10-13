# Blueprint: idc-vpc-governed - outputs

output "account_name" {
  description = "Amazon Quick account name."
  value       = module.quick_account.account_name
}

output "account_subscription_status" {
  value       = module.quick_account.account_subscription_status
  description = "Account subscription status."
}

output "namespace" {
  value       = module.quick_account.namespace
  description = "Default namespace."
}

output "idc_group_ids" {
  description = "IDC group IDs (empty when enable_idc = false)."
  value       = var.enable_idc ? module.identity_center[0].group_ids : {}
}

output "service_role_arn" {
  description = "QuickSight service role ARN (null when disabled)."
  value       = var.enable_service_role ? module.service_roles[0].role_arn : null
}

output "kms_key_arn" {
  description = "CMK ARN (null when disabled)."
  value       = var.enable_kms ? module.kms[0].key_arn : null
}

output "privatelink_endpoint_id" {
  description = "PrivateLink endpoint ID (null when disabled)."
  value       = var.enable_privatelink ? module.vpc_access[0].privatelink_endpoint_id : null
}

output "vpc_connection_id" {
  description = "QuickSight VPC connection ID (null when disabled)."
  value       = var.enable_vpc_connection ? module.vpc_access[0].vpc_connection_id : null
}

output "custom_permission_profiles" {
  description = "Custom-permission profile names (empty when disabled)."
  value       = var.enable_custom_permissions ? module.custom_permissions[0].profile_names : []
}

output "termination_protection_enabled" {
  description = "Account termination protection state (null when account settings not managed)."
  value       = var.enable_account_settings ? module.account_governance[0].termination_protection_enabled : null
}

output "ip_restriction_enabled" {
  description = "Whether IP/VPC restrictions are enforced (null when disabled)."
  value       = var.enable_ip_restriction ? module.account_governance[0].ip_restriction_enabled : null
}

output "iam_policy_assignment_names" {
  description = "IAM policy assignment names (empty when none configured)."
  value       = length(var.iam_policy_assignments) > 0 ? module.account_governance[0].iam_policy_assignment_names : []
}
