# Module: service-roles - outputs

output "role_arn" {
  description = "ARN of the QuickSight service role."
  value       = aws_iam_role.service.arn
}

output "role_name" {
  description = "Name of the QuickSight service role."
  value       = aws_iam_role.service.name
}
