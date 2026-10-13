# Blueprint: minimal
# Just the Amazon Quick account subscription (+ default namespace). No IDC, no VPC,
# no KMS, no custom permissions. Good for a quick evaluation account.
# Implemented as a thin preset over the modules so behavior stays consistent.

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "account_name" {
  type = string
}

variable "notification_email" {
  type = string
}

variable "edition" {
  type    = string
  default = "ENTERPRISE"
}

variable "tags" {
  type    = map(string)
  default = {}
}

module "quick_account" {
  source = "../../modules/quick-account"

  account_name          = var.account_name
  notification_email    = var.notification_email
  edition               = var.edition
  authentication_method = "IAM_AND_QUICKSIGHT" # non-IDC
  create_namespace      = true
  namespace             = "default"
  tags                  = var.tags
}

output "account_name" {
  value = module.quick_account.account_name
}

output "account_subscription_status" {
  value = module.quick_account.account_subscription_status
}

output "namespace" {
  value = module.quick_account.namespace
}
