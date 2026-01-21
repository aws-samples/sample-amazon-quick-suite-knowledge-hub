# Amazon Quick Suite Bootstrap - Variables

variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-1"
}

variable "identity_center_instance_arn" {
  description = "ARN of IAM Identity Center instance (required). Get with: aws sso-admin list-instances --region <your-idc-region>"
  type        = string
  
  validation {
    condition     = length(var.identity_center_instance_arn) > 0
    error_message = "identity_center_instance_arn is required. Run: aws sso-admin list-instances --region <region>"
  }
}

variable "identity_center_identity_store_id" {
  description = "Identity Store ID (required). Get with: aws sso-admin list-instances --region <your-idc-region>"
  type        = string
  
  validation {
    condition     = length(var.identity_center_identity_store_id) > 0
    error_message = "identity_center_identity_store_id is required. Run: aws sso-admin list-instances --region <region>"
  }
}

variable "identity_center_region" {
  description = "AWS region where Identity Center is located"
  type        = string
  default     = "us-west-2"
}

variable "quick_suite_account_name" {
  description = "Name for the Quick Suite account"
  type        = string
  default     = "QuickSuiteBootstrap"

  validation {
    condition     = length(var.quick_suite_account_name) >= 1 && length(var.quick_suite_account_name) <= 64
    error_message = "Account name must be between 1 and 64 characters."
  }
}

variable "quick_suite_admin_email" {
  description = "Email address for the Quick Suite admin user"
  type        = string
  default     = "admin@example.com"

  validation {
    condition     = can(regex("^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$", var.quick_suite_admin_email))
    error_message = "Must be a valid email address."
  }
}

variable "quick_suite_admin_group_name" {
  description = "Name of the admin group in Identity Center"
  type        = string
  default     = "QUICK_SUITE_ADMIN"

  validation {
    condition     = length(var.quick_suite_admin_group_name) >= 1 && length(var.quick_suite_admin_group_name) <= 128
    error_message = "Group name must be between 1 and 128 characters."
  }
}

variable "force_update" {
  description = "Set to true to force re-run of the Quick Suite setup"
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
