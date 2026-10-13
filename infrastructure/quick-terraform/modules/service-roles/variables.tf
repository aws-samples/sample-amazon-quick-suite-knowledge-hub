# Module: service-roles
# IAM service role(s) that Amazon Quick / QuickSight assumes to reach AWS data sources.

variable "role_name" {
  description = "Name of the QuickSight service role."
  type        = string
  default     = "AmazonQuickServiceRole"
}

variable "attach_athena_access" {
  description = "Attach the managed AWSQuicksightAthenaAccess policy."
  type        = bool
  default     = true
}

variable "s3_bucket_arns" {
  description = "Optional list of S3 bucket ARNs the service role may read (data sources + manifests). Empty = no inline S3 policy."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags applied to the role."
  type        = map(string)
  default     = {}
}
