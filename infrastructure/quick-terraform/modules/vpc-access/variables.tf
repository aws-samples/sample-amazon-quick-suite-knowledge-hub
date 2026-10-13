# Module: vpc-access
# Two independent VPC capabilities from the governance guidance:
#   Type 1 - restrict access TO Amazon Quick via an interface VPC endpoint (PrivateLink).
#   Type 2 - connect Amazon Quick TO private data sources via a QuickSight VPC connection.

variable "enable_privatelink" {
  description = "Type 1: create an interface VPC endpoint (PrivateLink) that restricts access TO Amazon Quick."
  type        = bool
  default     = false
}

variable "enable_vpc_connection" {
  description = "Type 2: create a QuickSight VPC connection so Quick can reach private data sources."
  type        = bool
  default     = false
}

variable "vpc_id" {
  description = "VPC ID used for the endpoint / connection."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subnet IDs for the interface endpoint / VPC connection (>= 2 in different AZs)."
  type        = list(string)
  default     = []
}

variable "security_group_ids" {
  description = "Security group IDs attached to the endpoint / VPC connection."
  type        = list(string)
  default     = []
}

variable "privatelink_service_name" {
  description = "Service name for the Amazon Quick interface endpoint (region-specific)."
  type        = string
  default     = ""
}

variable "vpc_connection_name" {
  description = "Name for the QuickSight VPC connection (Type 2)."
  type        = string
  default     = "amazon-quick-vpc-connection"
}

variable "vpc_connection_role_arn" {
  description = "IAM role ARN QuickSight assumes to create ENIs for the VPC connection. Leave empty to let the module create one."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to resources that support tagging."
  type        = map(string)
  default     = {}
}
