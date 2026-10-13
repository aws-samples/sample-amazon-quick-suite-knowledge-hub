# Module: quick-account
# Creates the Amazon Quick (QuickSight) account subscription + an optional namespace.

variable "account_name" {
  description = "Name for the Amazon Quick account subscription."
  type        = string

  validation {
    condition     = length(var.account_name) >= 1 && length(var.account_name) <= 64
    error_message = "account_name must be between 1 and 64 characters."
  }
}

variable "notification_email" {
  description = "Email address that receives Amazon Quick service notifications."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$", var.notification_email))
    error_message = "notification_email must be a valid email address."
  }
}

variable "edition" {
  description = "Amazon Quick edition. ENTERPRISE_AND_Q enables Q topics; ENTERPRISE is standard."
  type        = string
  default     = "ENTERPRISE"

  validation {
    condition     = contains(["ENTERPRISE", "ENTERPRISE_AND_Q"], var.edition)
    error_message = "edition must be one of: ENTERPRISE, ENTERPRISE_AND_Q."
  }
}

variable "authentication_method" {
  description = "Authentication method for the account. IAM_IDENTITY_CENTER requires IDC; IAM_AND_QUICKSIGHT is the non-IDC path."
  type        = string
  default     = "IAM_IDENTITY_CENTER"

  validation {
    condition     = contains(["IAM_IDENTITY_CENTER", "IAM_AND_QUICKSIGHT", "ACTIVE_DIRECTORY"], var.authentication_method)
    error_message = "authentication_method must be IAM_IDENTITY_CENTER, IAM_AND_QUICKSIGHT, or ACTIVE_DIRECTORY."
  }
}

variable "admin_group" {
  description = "IAM Identity Center admin group name(s) granted account admin. Only used when authentication_method = IAM_IDENTITY_CENTER."
  type        = list(string)
  default     = []
}

variable "author_group" {
  description = "IAM Identity Center author group name(s). Optional."
  type        = list(string)
  default     = []
}

variable "reader_group" {
  description = "IAM Identity Center reader group name(s). Optional."
  type        = list(string)
  default     = []
}

variable "create_namespace" {
  description = "Whether to create a QuickSight namespace in addition to the subscription."
  type        = bool
  default     = true
}

variable "namespace" {
  description = "Namespace name to create when create_namespace = true."
  type        = string
  default     = "default"
}

variable "tags" {
  description = "Tags applied to resources that support tagging."
  type        = map(string)
  default     = {}
}
