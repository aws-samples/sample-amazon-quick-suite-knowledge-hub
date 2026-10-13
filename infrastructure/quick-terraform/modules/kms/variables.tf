# Module: kms
# Customer managed key (CMK) for Amazon Quick / QuickSight at-rest encryption,
# optionally multi-region (required when IDC home region != Quick region), and
# registration of the key with QuickSight.

variable "alias" {
  description = "KMS key alias (without the 'alias/' prefix)."
  type        = string
  default     = "amazon-quick-cmk"
}

variable "multi_region" {
  description = "Create a multi-region key. Required when IDC home region differs from the Quick deployment region."
  type        = bool
  default     = false
}

variable "enable_key_rotation" {
  description = "Enable automatic annual key rotation."
  type        = bool
  default     = true
}

variable "register_with_quicksight" {
  description = "Register this CMK with QuickSight via aws_quicksight_key_registration."
  type        = bool
  default     = true
}

variable "allow_identity_center" {
  description = "Add SSO / Identity Store / managed-apps service principals to the key policy (needed for IDC-integrated Quick)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to the key."
  type        = map(string)
  default     = {}
}
