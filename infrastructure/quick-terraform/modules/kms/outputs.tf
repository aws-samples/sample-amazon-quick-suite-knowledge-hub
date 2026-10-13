# Module: kms - outputs

output "key_arn" {
  description = "ARN of the CMK."
  value       = aws_kms_key.this.arn
}

output "key_id" {
  description = "ID of the CMK."
  value       = aws_kms_key.this.key_id
}

output "alias" {
  description = "Alias of the CMK (with alias/ prefix)."
  value       = aws_kms_alias.this.name
}
