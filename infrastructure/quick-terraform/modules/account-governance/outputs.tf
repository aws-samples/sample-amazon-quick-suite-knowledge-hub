# Module: account-governance - outputs

output "default_namespace" {
  description = "Managed default namespace (null when account settings disabled)."
  value       = var.enable_account_settings ? aws_quicksight_account_settings.this[0].default_namespace : null
}

output "termination_protection_enabled" {
  description = "Whether termination protection is enabled (null when account settings disabled)."
  value       = var.enable_account_settings ? aws_quicksight_account_settings.this[0].termination_protection_enabled : null
}

output "ip_restriction_enabled" {
  description = "Whether IP restrictions are enforced (null when disabled)."
  value       = var.enable_ip_restriction ? aws_quicksight_ip_restriction.this[0].enabled : null
}

output "iam_policy_assignment_names" {
  description = "Names of the IAM policy assignments managed by this module."
  value       = keys(var.iam_policy_assignments)
}
