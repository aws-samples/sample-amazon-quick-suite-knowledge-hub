# Amazon Quick Suite Bootstrap - Outputs

output "identity_center_instance_arn" {
  description = "ARN of the IAM Identity Center instance"
  value       = local.instance_arn
}

output "identity_store_id" {
  description = "ID of the Identity Store"
  value       = local.identity_store_id
}

output "lambda_function_arn" {
  description = "ARN of the Quick Suite setup Lambda function"
  value       = aws_lambda_function.quick_suite_setup.arn
}

output "lambda_function_name" {
  description = "Name of the Quick Suite setup Lambda function"
  value       = aws_lambda_function.quick_suite_setup.function_name
}

output "lambda_execution_role_arn" {
  description = "ARN of the Lambda execution role"
  value       = aws_iam_role.lambda_execution_role.arn
}

output "quick_suite_account_name" {
  description = "Name of the Quick Suite account"
  value       = var.quick_suite_account_name
}

output "quick_suite_admin_email" {
  description = "Admin email for Quick Suite"
  value       = var.quick_suite_admin_email
}

output "quick_suite_admin_group_name" {
  description = "Admin group name in Identity Center"
  value       = var.quick_suite_admin_group_name
}

output "setup_result" {
  description = "Result of the Quick Suite setup invocation"
  value       = aws_lambda_invocation.quick_suite_setup.result
  sensitive   = true
}
