# Module: identity-center
# Creates (or references) IAM Identity Center groups used by Amazon Quick for
# role-based access (admin / author / reader).

variable "identity_store_id" {
  description = "IAM Identity Center Identity Store ID (d-xxxxxxxxxx). Get with: aws sso-admin list-instances."
  type        = string

  validation {
    condition     = length(var.identity_store_id) > 0
    error_message = "identity_store_id is required."
  }
}

variable "groups" {
  description = <<-EOT
    Map of logical name => group config to create in the Identity Store.
    Each entry: { display_name = string, description = optional(string) }.
    Set create = false on an entry to look up an existing group instead of creating it.
  EOT
  type = map(object({
    display_name = string
    description  = optional(string, "Managed by Amazon Quick Terraform")
    create       = optional(bool, true)
  }))
  default = {}
}
