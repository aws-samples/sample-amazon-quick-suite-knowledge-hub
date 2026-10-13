# Module: account-governance - variables
# Each governance feature has its own enable_* toggle so a blueprint can turn on
# just the controls it needs.

# --- Account settings ------------------------------------------------------
variable "enable_account_settings" {
  description = "Manage account-wide settings (default namespace + termination protection)."
  type        = bool
  default     = false
}

variable "default_namespace" {
  description = "Default QuickSight namespace for the account."
  type        = string
  default     = "default"
}

variable "termination_protection_enabled" {
  description = "Prevent the Amazon Quick account subscription from being deleted."
  type        = bool
  default     = true
}

# --- IP / VPC restrictions -------------------------------------------------
variable "enable_ip_restriction" {
  description = "Manage IP/VPC access restrictions for the account."
  type        = bool
  default     = false
}

variable "ip_restriction_enabled" {
  description = "Master switch for the restriction rules. When false the rules exist but are not enforced."
  type        = bool
  default     = true
}

variable "ip_restriction_rule_map" {
  description = "Map of CIDR block => description. Only listed CIDRs may access Amazon Quick."
  type        = map(string)
  default     = {}
}

variable "vpc_id_restriction_rule_map" {
  description = "Map of VPC ID => description. Access allowed from these VPCs."
  type        = map(string)
  default     = {}
}

variable "vpc_endpoint_id_restriction_rule_map" {
  description = "Map of VPC endpoint ID => description. Access allowed through these endpoints."
  type        = map(string)
  default     = {}
}

# --- IAM policy assignments ------------------------------------------------
variable "iam_policy_assignments" {
  description = <<-EOT
    Map of assignment name => assignment config binding an IAM policy to QuickSight
    identities:
      {
        policy_arn = string                       # IAM managed policy ARN to assign
        status     = optional(string, "ENABLED")  # ENABLED | DISABLED | DRAFT
        namespace  = optional(string, "default")  # QuickSight namespace
        users      = optional(set(string), [])    # QuickSight user names
        groups     = optional(set(string), [])    # QuickSight group names
      }
  EOT
  type = map(object({
    policy_arn = string
    status     = optional(string, "ENABLED")
    namespace  = optional(string, "default")
    users      = optional(set(string), [])
    groups     = optional(set(string), [])
  }))
  default = {}
}

variable "tags" {
  description = "Tags applied to resources that support tagging. (Reserved; current resources are untagged by the provider.)"
  type        = map(string)
  default     = {}
}
