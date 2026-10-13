# Module: service-roles
# QuickSight service role assumed by the quicksight.amazonaws.com service principal.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60.0"
    }
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["quicksight.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "service" {
  name               = var.role_name
  description        = "Service role for Amazon Quick"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "athena" {
  count = var.attach_athena_access ? 1 : 0

  role       = aws_iam_role.service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSQuicksightAthenaAccess"
}

data "aws_iam_policy_document" "s3" {
  count = length(var.s3_bucket_arns) > 0 ? 1 : 0

  statement {
    sid       = "ListBuckets"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = var.s3_bucket_arns
  }
  statement {
    sid       = "ReadObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = [for arn in var.s3_bucket_arns : "${arn}/*"]
  }
}

resource "aws_iam_role_policy" "s3" {
  count = length(var.s3_bucket_arns) > 0 ? 1 : 0

  name   = "${var.role_name}-S3Access"
  role   = aws_iam_role.service.id
  policy = data.aws_iam_policy_document.s3[0].json
}
