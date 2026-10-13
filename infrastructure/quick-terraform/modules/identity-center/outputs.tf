# Module: identity-center - outputs

output "group_ids" {
  description = "Map of logical name => Identity Store group ID (created or looked up)."
  value = merge(
    { for k, g in aws_identitystore_group.this : k => g.group_id },
    { for k, g in data.aws_identitystore_group.existing : k => g.group_id },
  )
}

output "group_display_names" {
  description = "Map of logical name => group display name."
  value       = { for k, g in var.groups : k => g.display_name }
}
