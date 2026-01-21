# Amazon Quick Suite Bootstrap - Terraform
# This module sets up Amazon Quick Suite with IAM Identity Center integration

terraform {
  required_version = ">= 1.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.0.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.0.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Data sources
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Local values
locals {
  resource_prefix = "QuickSuiteBootstrap"
  
  # Use provided Identity Center values (required)
  instance_arn      = var.identity_center_instance_arn
  identity_store_id = var.identity_center_identity_store_id
}

# =============================================================================
# Lambda Function for Quick Suite Setup
# =============================================================================

# Build Lambda package
resource "null_resource" "lambda_build" {
  triggers = {
    source_hash = filemd5("${path.module}/lambdas/src/custom_resource_handler_for_quick_suite_setup.py")
  }

  provisioner "local-exec" {
    command = <<-EOT
      cd ${path.module}/lambdas
      pip install -r src/requirements.txt -t build/
      cp -r src/* build/
    EOT
  }
}

data "archive_file" "lambda_package" {
  type        = "zip"
  source_dir  = "${path.module}/lambdas/build"
  output_path = "${path.module}/lambdas/quick_suite_setup.zip"

  depends_on = [null_resource.lambda_build]
}

# Lambda execution role
resource "aws_iam_role" "lambda_execution_role" {
  name = "${local.resource_prefix}-LambdaExecutionRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "${local.resource_prefix}-LambdaExecutionRole"
  }
}

# Basic Lambda execution policy
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# QuickSight permissions
resource "aws_iam_role_policy" "quicksight_policy" {
  name = "${local.resource_prefix}-QuickSightPolicy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "quicksight:CreateAccountSubscription",
          "quicksight:DescribeAccountSubscription",
          "quicksight:DeleteAccountSubscription",
          "quicksight:Subscribe"
        ]
        Resource = "arn:aws:quicksight:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "quicksight:CreateNamespace",
          "quicksight:DescribeNamespace",
          "quicksight:DeleteNamespace"
        ]
        Resource = "arn:aws:quicksight:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:namespace/*"
      }
    ]
  })
}

# SSO permissions
resource "aws_iam_role_policy" "sso_policy" {
  name = "${local.resource_prefix}-SSOPolicy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sso:DescribeInstance",
          "sso:ListInstances"
        ]
        Resource = local.instance_arn
      },
      {
        Effect = "Allow"
        Action = [
          "sso:CreateApplication",
          "sso:DeleteApplication",
          "sso:DescribeApplication",
          "sso:CreateApplicationAssignment",
          "sso:DeleteApplicationAssignment",
          "sso:PutApplicationGrant",
          "sso:DeleteApplicationGrant",
          "sso:PutApplicationAuthenticationMethod",
          "sso:PutApplicationAccessScope",
          "sso:ListApplicationAssignments"
        ]
        Resource = "*"
      }
    ]
  })
}

# IAM permissions
resource "aws_iam_role_policy" "iam_policy" {
  name = "${local.resource_prefix}-IAMPolicy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:GetRole",
          "iam:PassRole",
          "iam:ListAttachedRolePolicies",
          "iam:GetPolicy",
          "iam:CreatePolicyVersion",
          "iam:DeletePolicyVersion",
          "iam:GetPolicyVersion",
          "iam:ListPolicyVersions",
          "iam:DeleteRole",
          "iam:ListRoles",
          "iam:CreatePolicy",
          "iam:ListEntitiesForPolicy",
          "iam:ListPolicies",
          "iam:CreateServiceLinkedRole"
        ]
        Resource = "*"
      }
    ]
  })
}

# Identity Store permissions
resource "aws_iam_role_policy" "identity_store_policy" {
  name = "${local.resource_prefix}-IdentityStorePolicy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "identitystore:DescribeUser",
          "identitystore:ListUsers",
          "identitystore:CreateGroup",
          "identitystore:DescribeGroup"
        ]
        Resource = "arn:aws:identitystore::${data.aws_caller_identity.current.account_id}:identitystore/${local.identity_store_id}"
      },
      {
        Effect = "Allow"
        Action = [
          "identitystore:ListGroups",
          "sso-directory:DescribeUser",
          "sso-directory:DescribeGroup"
        ]
        Resource = "*"
      }
    ]
  })
}

# Additional permissions (Organizations, S3, Athena, Directory Service)
resource "aws_iam_role_policy" "additional_policy" {
  name = "${local.resource_prefix}-AdditionalPolicy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "organizations:*",
          "s3:ListAllMyBuckets",
          "athena:ListDataCatalogs",
          "athena:GetDataCatalog",
          "ds:AuthorizeApplication",
          "ds:UnauthorizeApplication",
          "ds:CheckAlias",
          "ds:CreateAlias",
          "ds:DescribeDirectories",
          "ds:DescribeTrusts",
          "ds:DeleteDirectory",
          "ds:CreateIdentityPoolDirectory"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "organizations:ListAWSServiceAccessForOrganization",
          "user-subscriptions:CreateClaim",
          "user-subscriptions:UpdateClaim"
        ]
        Resource = "*"
      }
    ]
  })
}

# Lambda function
resource "aws_lambda_function" "quick_suite_setup" {
  function_name = "${local.resource_prefix}-QuickSuiteSetupFunction"
  role          = aws_iam_role.lambda_execution_role.arn
  handler       = "custom_resource_handler_for_quick_suite_setup.handler"
  runtime       = "python3.12"
  timeout       = 900 # 15 minutes

  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256

  environment {
    variables = {
      LOG_LEVEL = "INFO"
    }
  }

  tags = {
    Name = "${local.resource_prefix}-QuickSuiteSetupFunction"
  }
}

# =============================================================================
# Custom Resource to trigger Quick Suite Setup
# =============================================================================

# Lambda to handle custom resource lifecycle
resource "aws_lambda_invocation" "quick_suite_setup" {
  function_name = aws_lambda_function.quick_suite_setup.function_name

  input = jsonencode({
    RequestType = "Create"
    ResourceProperties = {
      IdentityCenterInstanceArn = local.instance_arn
      IdentityStoreId           = local.identity_store_id
      AccountName               = var.quick_suite_account_name
      AdminEmail                = var.quick_suite_admin_email
      AdminGroupName            = var.quick_suite_admin_group_name
    }
  })

  lifecycle {
    # Re-run when any of these change
    replace_triggered_by = [
      null_resource.force_update
    ]
  }

  depends_on = [
    aws_iam_role_policy.quicksight_policy,
    aws_iam_role_policy.sso_policy,
    aws_iam_role_policy.iam_policy,
    aws_iam_role_policy.identity_store_policy,
    aws_iam_role_policy.additional_policy
  ]
}

# Force update trigger
resource "null_resource" "force_update" {
  triggers = {
    account_name     = var.quick_suite_account_name
    admin_email      = var.quick_suite_admin_email
    admin_group_name = var.quick_suite_admin_group_name
    timestamp        = var.force_update ? timestamp() : "static"
  }
}
