# Module: custom-permissions
# Declares Amazon Quick custom-permission profiles with three ergonomic layers:
#   1. deny_by_default  - start from "deny everything", then allow-list exceptions.
#   2. preset           - pick a named, ready-made posture (reader_lockdown, etc.).
#   3. capabilities      - raw escape hatch: set individual capability => "DENY".
# Precedence (most specific wins): capabilities > allow > preset > deny_by_default base.
#
# Semantics note: the QuickSight model expresses a capability as DENY or "unset"
# (unset = account default = permitted). "allow" therefore means "leave unset", i.e.
# NOT denied. deny_by_default = true sets all capabilities to DENY except those listed
# in allow (and except any preset/raw overrides).

variable "profiles" {
  description = <<-EOT
    Map of profile name => profile config:
      {
        deny_by_default = optional(bool, false)   # true = deny all 20 capabilities, then allow-list
        preset          = optional(string)        # named posture: reader_lockdown | no_data_exfiltration | author_no_share | strict_pro
        allow           = optional(list(string))  # capability names to leave PERMITTED (unset) - exceptions to a deny base
        capabilities    = optional(map(string))   # raw override: capability name => "DENY" (wins over everything)
      }
    All fields optional; combine them freely.
  EOT
  type = map(object({
    deny_by_default = optional(bool, false)
    preset          = optional(string)
    allow           = optional(list(string), [])
    capabilities    = optional(map(string), {})
  }))
  default = {}
}

variable "role_assignments" {
  description = "Map of QuickSight role (READER|AUTHOR|ADMIN|READER_PRO|AUTHOR_PRO|ADMIN_PRO) => profile name to apply."
  type        = map(string)
  default     = {}
}

variable "namespace" {
  description = "QuickSight namespace for role assignments."
  type        = string
  default     = "default"
}

variable "use_cloudformation" {
  description = "Use a CloudFormation stack (AWS::QuickSight::CustomPermissions) instead of native TF resources. Fallback for older AWS provider versions."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to resources that support tagging."
  type        = map(string)
  default     = {}
}
