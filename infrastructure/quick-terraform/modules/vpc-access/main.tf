# Module: vpc-access
# Type 1 (PrivateLink interface endpoint) and Type 2 (QuickSight VPC connection).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# -----------------------------------------------------------------------------
# Type 1: Interface VPC endpoint (PrivateLink) - restrict access TO Amazon Quick
# -----------------------------------------------------------------------------
resource "aws_vpc_endpoint" "quick" {
  count = var.enable_privatelink ? 1 : 0

  vpc_id              = var.vpc_id
  service_name        = var.privatelink_service_name
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = var.security_group_ids
  private_dns_enabled = true

  tags = merge(var.tags, { Name = "amazon-quick-privatelink" })
}

# -----------------------------------------------------------------------------
# Type 2: QuickSight VPC connection - connect Amazon Quick TO private data
# -----------------------------------------------------------------------------
locals {
  create_connection_role = var.enable_vpc_connection && var.vpc_connection_role_arn == ""
  connection_role_arn    = local.create_connection_role ? aws_iam_role.vpc_connection[0].arn : var.vpc_connection_role_arn
}

data "aws_iam_policy_document" "vpc_connection_assume" {
  count = local.create_connection_role ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["quicksight.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "vpc_connection" {
  count = local.create_connection_role ? 1 : 0

  name               = "${var.vpc_connection_name}-role"
  assume_role_policy = data.aws_iam_policy_document.vpc_connection_assume[0].json
  tags               = var.tags
}

resource "aws_iam_role_policy" "vpc_connection" {
  count = local.create_connection_role ? 1 : 0

  name = "${var.vpc_connection_name}-eni"
  role = aws_iam_role.vpc_connection[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:ModifyNetworkInterfaceAttribute",
          "ec2:DeleteNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeSubnets",
          "ec2:DescribeSecurityGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_quicksight_vpc_connection" "this" {
  count = var.enable_vpc_connection ? 1 : 0

  aws_account_id     = data.aws_caller_identity.current.account_id
  vpc_connection_id  = var.vpc_connection_name
  name               = var.vpc_connection_name
  role_arn           = local.connection_role_arn
  security_group_ids = var.security_group_ids
  subnet_ids         = var.subnet_ids

  tags = var.tags
}
