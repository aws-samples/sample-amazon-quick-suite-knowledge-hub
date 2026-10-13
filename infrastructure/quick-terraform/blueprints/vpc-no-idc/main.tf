# Blueprint: vpc-no-idc
# VPC-isolated Amazon Quick using native QuickSight identity (no IAM Identity Center).
# Enables PrivateLink (Type 1) and/or a QuickSight VPC connection (Type 2).

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

variable "enable_privatelink" {
  type    = bool
  default = true
}

variable "enable_vpc_connection" {
  type    = bool
  default = true
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "privatelink_service_name" {
  type    = string
  default = ""
}

variable "enable_kms" {
  type    = bool
  default = true
}

variable "tags" {
  type    = map(string)
  default = {}
}

module "quick" {
  source = "../idc-vpc-governed"

  aws_region         = var.aws_region
  account_name       = var.account_name
  notification_email = var.notification_email

  enable_idc = false # native QuickSight identity

  enable_kms          = var.enable_kms
  enable_service_role = true

  enable_privatelink       = var.enable_privatelink
  enable_vpc_connection    = var.enable_vpc_connection
  vpc_id                   = var.vpc_id
  subnet_ids               = var.subnet_ids
  security_group_ids       = var.security_group_ids
  privatelink_service_name = var.privatelink_service_name

  tags = var.tags
}

output "account_name" {
  value = module.quick.account_name
}

output "privatelink_endpoint_id" {
  value = module.quick.privatelink_endpoint_id
}

output "vpc_connection_id" {
  value = module.quick.vpc_connection_id
}

output "kms_key_arn" {
  value = module.quick.kms_key_arn
}
