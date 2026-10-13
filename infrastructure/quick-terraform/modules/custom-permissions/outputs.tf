# Module: custom-permissions - outputs

output "profile_arns" {
  description = "Map of profile name => ARN (native path) or CloudFormation stack output ARN (fallback path)."
  value = var.use_cloudformation ? {
    for k, s in aws_cloudformation_stack.custom_permissions : k => s.outputs["Arn"]
    } : {
    for k, p in aws_quicksight_custom_permissions.this : k => p.arn
  }
}

output "profile_names" {
  description = "List of custom-permission profile names managed by this module."
  value       = keys(var.profiles)
}

output "resolved_denies" {
  description = "Map of profile name => final list of DENIED capabilities (after deny_by_default + preset + allow + raw merge). Useful for review."
  value       = { for k, v in local.final_deny : k => sort(tolist(v)) }
}

output "available_presets" {
  description = "Names of the built-in preset postures that can be referenced via profile.preset."
  value       = keys(local.presets)
}
