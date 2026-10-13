# Blueprint: idc-vpc-governed
# The full-featured, flag-driven blueprint. Every capability is toggleable, so this
# single blueprint can also express the simpler topologies by turning features off.

variable "aws_region" {
  description = "AWS region to deploy Amazon Quick resources."
  type        = string
  default     = "us-east-1"
}

# --- Account ---------------------------------------------------------------
variable "account_name" {
  description = "Amazon Quick account subscription name."
  type        = string
}

variable "notification_email" {
  description = "Email for Amazon Quick service notifications."
  type        = string
}

variable "edition" {
  description = "ENTERPRISE or ENTERPRISE_AND_Q."
  type        = string
  default     = "ENTERPRISE"
}

# --- Identity Center (toggle) ----------------------------------------------
variable "enable_idc" {
  description = "Integrate with IAM Identity Center. When false, the account uses IAM_AND_QUICKSIGHT auth and IDC groups are skipped."
  type        = bool
  default     = true
}

variable "identity_store_id" {
  description = "IAM Identity Center Identity Store ID. Required when enable_idc = true."
  type        = string
  default     = ""
}

variable "admin_group_name" {
  description = "Display name of the admin IDC group."
  type        = string
  default     = "QUICK_ADMIN"
}

variable "create_admin_group" {
  description = "Create the admin IDC group (false = reference an existing one)."
  type        = bool
  default     = true
}

# --- KMS (toggle) ----------------------------------------------------------
variable "enable_kms" {
  description = "Create a CMK and register it with QuickSight for at-rest encryption."
  type        = bool
  default     = true
}

variable "kms_multi_region" {
  description = "Make the CMK multi-region (needed when IDC home region != Quick region)."
  type        = bool
  default     = false
}

# --- VPC (toggles) ---------------------------------------------------------
variable "enable_privatelink" {
  description = "Type 1: restrict access TO Amazon Quick via a PrivateLink interface endpoint."
  type        = bool
  default     = false
}

variable "enable_vpc_connection" {
  description = "Type 2: create a QuickSight VPC connection to reach private data."
  type        = bool
  default     = false
}

variable "vpc_id" {
  description = "VPC ID for PrivateLink / VPC connection."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subnet IDs for PrivateLink / VPC connection."
  type        = list(string)
  default     = []
}

variable "security_group_ids" {
  description = "Security group IDs for PrivateLink / VPC connection."
  type        = list(string)
  default     = []
}

variable "privatelink_service_name" {
  description = "Region-specific Amazon Quick interface endpoint service name (for PrivateLink)."
  type        = string
  default     = ""
}

# --- Custom permissions (toggle) -------------------------------------------
variable "enable_custom_permissions" {
  description = "Declare custom-permission profiles and bind them to roles."
  type        = bool
  default     = false
}

variable "custom_permission_profiles" {
  description = <<-EOT
    Map of profile name => { deny_by_default, preset, allow, capabilities }.
    See modules/custom-permissions for the posture model and preset names.
  EOT
  type = map(object({
    deny_by_default = optional(bool, false)
    preset          = optional(string)
    allow           = optional(list(string), [])
    capabilities    = optional(map(string), {})
  }))
  default = {}
}

variable "custom_permission_role_assignments" {
  description = "Map of QuickSight role => profile name."
  type        = map(string)
  default     = {}
}

variable "custom_permissions_use_cloudformation" {
  description = "Use the CloudFormation fallback for custom permissions."
  type        = bool
  default     = false
}

# --- Account governance (toggles) ------------------------------------------
variable "enable_account_settings" {
  description = "Manage account-wide settings (default namespace + termination protection)."
  type        = bool
  default     = false
}

variable "termination_protection_enabled" {
  description = "Prevent the Amazon Quick account subscription from being deleted (when enable_account_settings = true)."
  type        = bool
  default     = true
}

variable "enable_ip_restriction" {
  description = "Gate Amazon Quick console & API access by IP CIDR, VPC, or VPC endpoint."
  type        = bool
  default     = false
}

variable "ip_restriction_enabled" {
  description = "Enforce the IP/VPC restriction rules (false = rules exist but are not enforced)."
  type        = bool
  default     = true
}

variable "ip_restriction_rule_map" {
  description = "Map of CIDR block => description. Only listed CIDRs may access Amazon Quick."
  type        = map(string)
  default     = {}
}

variable "vpc_id_restriction_rule_map" {
  description = "Map of VPC ID => description allowed to access Amazon Quick."
  type        = map(string)
  default     = {}
}

variable "vpc_endpoint_id_restriction_rule_map" {
  description = "Map of VPC endpoint ID => description allowed to access Amazon Quick."
  type        = map(string)
  default     = {}
}

variable "iam_policy_assignments" {
  description = <<-EOT
    Map of assignment name => { policy_arn, status, namespace, users, groups }.
    Binds an IAM policy to QuickSight identities. See modules/account-governance.
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

# --- Service role (toggle) -------------------------------------------------
variable "enable_service_role" {
  description = "Create the QuickSight service role (Athena/S3 data access)."
  type        = bool
  default     = true
}

variable "service_role_s3_bucket_arns" {
  description = "S3 bucket ARNs the service role may read."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags applied to all resources that support tagging."
  type        = map(string)
  default     = {}
}
