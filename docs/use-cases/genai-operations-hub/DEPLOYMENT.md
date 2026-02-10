# Deployment Guide

Complete guide to deploy the GenAI Operations Hub infrastructure.

## Prerequisites

- AWS Account
- AWS CLI configured
- Python 3.8+
- Node.js 14+ (for CDK)
- Bedrock invocation logs enabled

## Step 1: Clone Repository

```bash
git clone <repository-url>
cd genai-operations-hub
```

## Step 2: Install CDK

```bash
npm install -g aws-cdk
```

## Step 3: Set Up Python Environment

```bash
cd infrastructure
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Step 4: Configure AWS Credentials

```bash
aws configure
# Enter your AWS Access Key ID, Secret Access Key, and Region
```

## Step 5: Bootstrap CDK (First Time Only)

```bash
cdk bootstrap aws://ACCOUNT-ID/REGION
```

Replace `ACCOUNT-ID` with your AWS account ID and `REGION` with your target region.

## Step 6: Review Stack

```bash
cdk synth
```

This generates CloudFormation template. Review the output.

## Step 7: Deploy Infrastructure

```bash
cdk deploy
```

Confirm the deployment when prompted. This creates:
- S3 bucket for Bedrock logs
- S3 bucket for Athena results
- Glue database and table
- Athena workgroup
- IAM roles for QuickSight

**Deployment time**: ~5 minutes

## Step 8: Note Outputs

After deployment, save these outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name GenAIOperationsStack \
  --query 'Stacks[0].Outputs'
```

You'll need:
- `LogsBucketName`
- `DatabaseName`
- `TableName`
- `AthenaWorkGroup`
- `QuickSightRoleArn`

## Step 9: Upload Sample Data

```bash
# Get bucket name
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name GenAIOperationsStack \
  --query 'Stacks[0].Outputs[?OutputKey==`LogsBucketName`].OutputValue' \
  --output text)

# Upload logs
cd ..
aws s3 cp sample-data/bedrock-logs/ s3://${BUCKET_NAME}/ --recursive
```

## Step 10: Verify Deployment

```bash
# Check S3 bucket
aws s3 ls s3://${BUCKET_NAME}/

# Check Glue table
aws glue get-table \
  --database-name genai_ops_db \
  --name bedrock_invocation_logs

# Test Athena query
aws athena start-query-execution \
  --query-string "SELECT COUNT(*) FROM bedrock_invocation_logs" \
  --query-execution-context Database=genai_ops_db \
  --result-configuration OutputLocation=s3://${BUCKET_NAME}/athena-results/ \
  --work-group genai-ops-workgroup
```

## Step 11: Enable QuickSight

1. Go to [QuickSight Console](https://quicksight.aws.amazon.com/)
2. Sign up for Enterprise Edition (if not already)
3. Follow Task 2 guide to connect to Athena

## Troubleshooting

### CDK Bootstrap Fails

```bash
# Ensure you have admin permissions
aws sts get-caller-identity

# Try with explicit region
cdk bootstrap aws://ACCOUNT-ID/us-east-1
```

### Deployment Fails

```bash
# Check CloudFormation events
aws cloudformation describe-stack-events \
  --stack-name GenAIOperationsStack \
  --max-items 10

# View detailed error
cdk deploy --verbose
```

### S3 Upload Fails

```bash
# Check bucket exists
aws s3 ls | grep genai-ops

# Verify permissions
aws s3api get-bucket-policy --bucket ${BUCKET_NAME}
```

### Athena Query Fails

- Verify Glue table schema matches log structure
- Check S3 path in table location
- Ensure Athena workgroup is configured

## Clean Up

To remove all resources:

```bash
# Empty S3 buckets first
aws s3 rm s3://${BUCKET_NAME}/ --recursive

# Delete stack
cdk destroy
```

## Cost Estimate

Monthly costs (approximate):
- S3 storage: $0.023/GB (~$2-5 for typical logs)
- Athena queries: $5/TB scanned (~$1-3 for typical usage)
- QuickSight Enterprise: $18/user/month
- Glue Data Catalog: $1/100k requests (~$0.10)

**Total**: ~$20-30/month (excluding QuickSight user fees)

## Security Best Practices

1. **Encrypt S3 buckets**: Enable default encryption
2. **Restrict IAM roles**: Use least privilege
3. **Enable CloudTrail**: Audit API calls
4. **Use VPC endpoints**: For private connectivity
5. **Rotate credentials**: Regularly update access keys

## Next Steps

Proceed to [Setup Guide](docs/0-setup.md) for detailed deployment instructions.
