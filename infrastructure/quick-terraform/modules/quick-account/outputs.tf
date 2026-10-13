# Module: quick-account - outputs

output "account_name" {
  description = "Name of the Amazon Quick account subscription."
  value       = aws_quicksight_account_subscription.this.account_name
}

output "account_subscription_status" {
  description = "Status of the account subscription."
  value       = aws_quicksight_account_subscription.this.account_subscription_status
}

output "authentication_method" {
  description = "Authentication method configured on the account."
  value       = aws_quicksight_account_subscription.this.authentication_method
}

output "namespace" {
  description = "Namespace name (null when create_namespace = false)."
  value       = var.create_namespace ? aws_quicksight_namespace.this[0].namespace : null
}

output "namespace_arn" {
  description = "ARN of the created namespace (null when create_namespace = false)."
  value       = var.create_namespace ? aws_quicksight_namespace.this[0].arn : null
}
