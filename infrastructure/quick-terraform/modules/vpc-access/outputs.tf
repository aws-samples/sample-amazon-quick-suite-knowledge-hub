# Module: vpc-access - outputs

output "privatelink_endpoint_id" {
  description = "ID of the PrivateLink interface endpoint (null when disabled)."
  value       = var.enable_privatelink ? aws_vpc_endpoint.quick[0].id : null
}

output "vpc_connection_id" {
  description = "ID of the QuickSight VPC connection (null when disabled)."
  value       = var.enable_vpc_connection ? aws_quicksight_vpc_connection.this[0].vpc_connection_id : null
}

output "vpc_connection_arn" {
  description = "ARN of the QuickSight VPC connection (null when disabled)."
  value       = var.enable_vpc_connection ? aws_quicksight_vpc_connection.this[0].arn : null
}
