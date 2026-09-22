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
  filename         = data.archive_file.layer_zip.output_path
  layer_name       = "pptx-layer"
  source_code_hash = data.archive_file.layer_zip.output_base64sha256
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

# S3 bucket for PowerPoint files
resource "aws_s3_bucket" "ppt_bucket" {
  bucket = "ppt-creator-${random_id.bucket_suffix.hex}"
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket_versioning" "ppt_bucket_versioning" {
  bucket = aws_s3_bucket.ppt_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Upload template file to S3
resource "aws_s3_object" "template" {
  count  = fileexists("templates/template.pptx") ? 1 : 0
  bucket = aws_s3_bucket.ppt_bucket.id
  key    = "templates/template.pptx"
  source = "templates/template.pptx"
  etag   = filemd5("templates/template.pptx")
}

# IAM policy for Lambda
resource "aws_iam_role_policy" "lambda_policy" {
  name = "ppt-creator-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.ppt_bucket.arn}/*",
          aws_s3_bucket.ppt_bucket.arn
        ]
      }
    ]
  })
}

# Lambda function
resource "aws_lambda_function" "ppt_creator" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "ppt-creator"
  role            = aws_iam_role.lambda_role.arn
  handler         = "ppt_creator_mcp_lambda.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime         = "python3.11"
  timeout         = 60
  memory_size     = 512

  layers = [aws_lambda_layer_version.pptx_layer.arn]

  environment {
    variables = {
      S3_BUCKET = aws_s3_bucket.ppt_bucket.bucket
      CLOUDFRONT_DOMAIN = aws_cloudfront_distribution.ppt_distribution.domain_name
    }
  }

  depends_on = [aws_iam_role_policy.lambda_policy]
}

# API Gateway
resource "aws_api_gateway_rest_api" "ppt_api" {
  name        = "ppt-creator-api"
  description = "API for PPT Creator Lambda"
}

resource "aws_api_gateway_method" "ppt_post" {
  rest_api_id   = aws_api_gateway_rest_api.ppt_api.id
  resource_id   = aws_api_gateway_rest_api.ppt_api.root_resource_id
  http_method   = "POST"
  authorization = "NONE"
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
      aws_api_gateway_integration.lambda_integration.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Lambda permission for API Gateway
resource "aws_lambda_permission" "api_gw" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ppt_creator.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.ppt_api.execution_arn}/*/*"
}

# CloudFront Origin Access Control
resource "aws_cloudfront_origin_access_control" "ppt_oac" {
  name                              = "ppt-creator-oac"
  description                       = "OAC for PPT Creator S3 bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFront Distribution
resource "aws_cloudfront_distribution" "ppt_distribution" {
  origin {
    domain_name              = aws_s3_bucket.ppt_bucket.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.ppt_oac.id
    origin_id                = "S3-${aws_s3_bucket.ppt_bucket.bucket}"
  }

  enabled = true

  default_cache_behavior {
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "S3-${aws_s3_bucket.ppt_bucket.bucket}"
    compress               = true
    viewer_protocol_policy = "redirect-to-https"

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 3600
    max_ttl     = 86400
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# Update S3 bucket policy to allow CloudFront access
resource "aws_s3_bucket_policy" "ppt_bucket_policy" {
  bucket = aws_s3_bucket.ppt_bucket.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontServicePrincipal"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.ppt_bucket.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.ppt_distribution.arn
          }
        }
      }
    ]
  })
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

output "cloudfront_domain_name" {
  value = aws_cloudfront_distribution.ppt_distribution.domain_name
}

output "api_gateway_url" {
  value = aws_api_gateway_deployment.ppt_deployment.invoke_url
}
