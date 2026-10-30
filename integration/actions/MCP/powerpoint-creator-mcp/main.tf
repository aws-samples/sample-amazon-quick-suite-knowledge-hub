terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-1"
}

variable "presigned_url_expiry_seconds" {
  description = "Lifetime in seconds of the pre-signed download URL returned for each generated presentation"
  type        = number
  default     = 3600

  validation {
    condition     = var.presigned_url_expiry_seconds >= 60 && var.presigned_url_expiry_seconds <= 43200
    error_message = "presigned_url_expiry_seconds must be between 60 and 43200 (12 hours)."
  }
}

variable "presentation_retention_days" {
  description = "Number of days generated presentations are kept in S3 before they are deleted"
  type        = number
  default     = 7

  validation {
    condition     = var.presentation_retention_days >= 1
    error_message = "presentation_retention_days must be at least 1."
  }
}

variable "access_token_validity_minutes" {
  description = "Lifetime in minutes of the OAuth access tokens issued to Amazon Quick"
  type        = number
  default     = 60

  validation {
    condition     = var.access_token_validity_minutes >= 5 && var.access_token_validity_minutes <= 1440
    error_message = "access_token_validity_minutes must be between 5 and 1440."
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Build layer using null_resource
resource "null_resource" "build_layer" {
  provisioner "local-exec" {
    command = "mkdir -p layer/python && pip install python-pptx -t layer/python --platform manylinux2014_x86_64 --only-binary=:all:"
  }

  triggers = {
    requirements = filemd5("${path.module}/requirements.txt")
  }
}

data "archive_file" "layer_zip" {
  type        = "zip"
  source_dir  = "layer"
  output_path = "pptx-layer.zip"
  depends_on  = [null_resource.build_layer]
}

resource "aws_lambda_layer_version" "pptx_layer" {
  filename            = data.archive_file.layer_zip.output_path
  layer_name          = "pptx-layer"
  source_code_hash    = data.archive_file.layer_zip.output_base64sha256
  compatible_runtimes = ["python3.11"]
}

# Lambda function zip
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "src/ppt_creator_mcp_lambda.py"
  output_path = "lambda_function.zip"
}

# IAM role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "ppt-creator-lambda-role"

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
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# S3 bucket for templates and generated presentations (private, no public or CDN access)
resource "aws_s3_bucket" "ppt_bucket" {
  bucket = "ppt-creator-${random_id.bucket_suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "ppt_bucket" {
  bucket = aws_s3_bucket.ppt_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "ppt_bucket" {
  bucket = aws_s3_bucket.ppt_bucket.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ppt_bucket" {
  bucket = aws_s3_bucket.ppt_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "ppt_bucket_versioning" {
  bucket = aws_s3_bucket.ppt_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "ppt_bucket" {
  bucket     = aws_s3_bucket.ppt_bucket.id
  depends_on = [aws_s3_bucket_versioning.ppt_bucket_versioning]

  rule {
    id     = "expire-generated-presentations"
    status = "Enabled"

    filter {
      prefix = "presentations/"
    }

    expiration {
      days = var.presentation_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Deny any non-TLS access to the bucket
resource "aws_s3_bucket_policy" "ppt_bucket_policy" {
  bucket     = aws_s3_bucket.ppt_bucket.id
  depends_on = [aws_s3_bucket_public_access_block.ppt_bucket]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.ppt_bucket.arn,
          "${aws_s3_bucket.ppt_bucket.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# Upload template file to S3
resource "aws_s3_object" "template" {
  count  = fileexists("templates/template.pptx") ? 1 : 0
  bucket = aws_s3_bucket.ppt_bucket.id
  key    = "templates/template.pptx"
  source = "templates/template.pptx"
  etag   = filemd5("templates/template.pptx")
}

# IAM policy for Lambda: read templates, write/read generated presentations only
resource "aws_iam_role_policy" "lambda_policy" {
  name = "ppt-creator-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "WriteFunctionLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = [
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/ppt-creator",
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/ppt-creator:*"
        ]
      },
      {
        Sid      = "ReadTemplates"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.ppt_bucket.arn}/templates/*"]
      },
      {
        Sid      = "ListTemplates"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.ppt_bucket.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["templates/*"]
          }
        }
      },
      {
        Sid    = "WriteAndPresignPresentations"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = ["${aws_s3_bucket.ppt_bucket.arn}/presentations/*"]
      }
    ]
  })
}

# Lambda function
resource "aws_lambda_function" "ppt_creator" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "ppt-creator"
  role             = aws_iam_role.lambda_role.arn
  handler          = "ppt_creator_mcp_lambda.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 512

  layers = [aws_lambda_layer_version.pptx_layer.arn]

  environment {
    variables = {
      S3_BUCKET                    = aws_s3_bucket.ppt_bucket.bucket
      PRESIGNED_URL_EXPIRY_SECONDS = tostring(var.presigned_url_expiry_seconds)
    }
  }

  depends_on = [aws_iam_role_policy.lambda_policy]
}

# Amazon Cognito: OAuth 2.0 client credentials for Amazon Quick service authentication
resource "aws_cognito_user_pool" "ppt_pool" {
  name = "ppt-creator-${random_id.bucket_suffix.hex}"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }
}

resource "aws_cognito_user_pool_domain" "ppt_domain" {
  domain       = "ppt-creator-${random_id.bucket_suffix.hex}"
  user_pool_id = aws_cognito_user_pool.ppt_pool.id
}

resource "aws_cognito_resource_server" "ppt_api" {
  identifier   = "ppt-creator"
  name         = "PPT Creator MCP API"
  user_pool_id = aws_cognito_user_pool.ppt_pool.id

  scope {
    scope_name        = "invoke"
    scope_description = "Invoke the PPT Creator MCP server"
  }
}

resource "aws_cognito_user_pool_client" "quick_client" {
  name         = "amazon-quick-ppt-creator"
  user_pool_id = aws_cognito_user_pool.ppt_pool.id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_scopes                 = aws_cognito_resource_server.ppt_api.scope_identifiers
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]

  access_token_validity = var.access_token_validity_minutes
  token_validity_units {
    access_token = "minutes"
  }

  enable_token_revocation       = true
  prevent_user_existence_errors = "ENABLED"
}

# API Gateway
resource "aws_api_gateway_rest_api" "ppt_api" {
  name        = "ppt-creator-api"
  description = "API for PPT Creator Lambda"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_authorizer" "cognito" {
  name            = "ppt-creator-cognito"
  rest_api_id     = aws_api_gateway_rest_api.ppt_api.id
  type            = "COGNITO_USER_POOLS"
  provider_arns   = [aws_cognito_user_pool.ppt_pool.arn]
  identity_source = "method.request.header.Authorization"
}

resource "aws_api_gateway_method" "ppt_post" {
  rest_api_id          = aws_api_gateway_rest_api.ppt_api.id
  resource_id          = aws_api_gateway_rest_api.ppt_api.root_resource_id
  http_method          = "POST"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  authorization_scopes = aws_cognito_resource_server.ppt_api.scope_identifiers
}

resource "aws_api_gateway_integration" "lambda_integration" {
  rest_api_id = aws_api_gateway_rest_api.ppt_api.id
  resource_id = aws_api_gateway_rest_api.ppt_api.root_resource_id
  http_method = aws_api_gateway_method.ppt_post.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.ppt_creator.invoke_arn
}

resource "aws_api_gateway_deployment" "ppt_deployment" {
  depends_on = [
    aws_api_gateway_integration.lambda_integration,
  ]

  rest_api_id = aws_api_gateway_rest_api.ppt_api.id
  stage_name  = "prod"

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_method.ppt_post.id,
      aws_api_gateway_method.ppt_post.authorization,
      aws_api_gateway_method.ppt_post.authorization_scopes,
      aws_api_gateway_authorizer.cognito.id,
      aws_api_gateway_integration.lambda_integration.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_method_settings" "ppt_throttling" {
  rest_api_id = aws_api_gateway_rest_api.ppt_api.id
  stage_name  = aws_api_gateway_deployment.ppt_deployment.stage_name
  method_path = "*/*"

  settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
    data_trace_enabled     = false
  }
}

# Lambda permission for API Gateway (POST on the root resource only)
resource "aws_lambda_permission" "api_gw" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ppt_creator.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.ppt_api.execution_arn}/*/POST/"
}

# Outputs
output "lambda_function_name" {
  value = aws_lambda_function.ppt_creator.function_name
}

output "layer_arn" {
  value = aws_lambda_layer_version.pptx_layer.arn
}

output "s3_bucket_name" {
  value = aws_s3_bucket.ppt_bucket.bucket
}

output "api_gateway_url" {
  value = aws_api_gateway_deployment.ppt_deployment.invoke_url
}

output "cognito_token_url" {
  description = "OAuth 2.0 token endpoint to configure in Amazon Quick service authentication"
  value       = "https://${aws_cognito_user_pool_domain.ppt_domain.domain}.auth.${data.aws_region.current.name}.amazoncognito.com/oauth2/token"
}

output "cognito_client_id" {
  description = "OAuth 2.0 client ID to configure in Amazon Quick service authentication"
  value       = aws_cognito_user_pool_client.quick_client.id
}

output "cognito_client_secret" {
  description = "OAuth 2.0 client secret to configure in Amazon Quick service authentication"
  value       = aws_cognito_user_pool_client.quick_client.client_secret
  sensitive   = true
}
