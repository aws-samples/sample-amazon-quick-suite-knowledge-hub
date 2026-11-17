# Deployment Guide

Complete guide to deploy the GenAI Operations Hub infrastructure.

## Prerequisites

You need the following before you deploy:

- AWS Account
- AWS CLI configured
- Python 3.8+
- Node.js 14+ (for CDK)
- Bedrock invocation logs enabled

## Step 1: Clone Repository

Clone the repository and change to the sample folder:

```bash
git clone <repository-url>
cd genai-operations-hub
```

## Step 2: Install CDK

Install the AWS CDK CLI globally:

```bash
npm install -g aws-cdk
```

## Step 3: Set Up Python Environment

Create a virtual environment and install the requirements:

```bash
cd infrastructure
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Step 4: Configure AWS Credentials

Set the credentials and Region the AWS CLI and CDK use:

```bash
aws configure
# Enter your AWS Access Key ID, Secret Access Key, and Region
```

## Step 5: Bootstrap CDK (First Time Only)

Bootstrap the account and Region once before the first deployment:

```bash
cdk bootstrap aws://ACCOUNT-ID/REGION
```

Replace `ACCOUNT-ID` with your AWS account ID and `REGION` with your target Region.

## Step 6: Review Stack

Synthesize the stack:

```bash
cdk synth
```

This generates the CloudFormation template. Review the output before you deploy.

## Step 7: Deploy Infrastructure

Deploy the stack:

```bash
cdk deploy
```

Confirm the deployment when prompted. This creates:

- S3 bucket for Bedrock logs
- S3 bucket for Athena results
- Glue database and table
- Athena workgroup
- IAM roles for Quick Sight

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

Look up the logs bucket, generate the sample logs, and upload them:

```bash
# Get bucket name
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name GenAIOperationsStack \
  --query 'Stacks[0].Outputs[?OutputKey==`LogsBucketName`].OutputValue' \
  --output text)

# Generate and upload logs
cd infrastructure/data-generators
python3 generate_all_data.py
cd ../sample-data
aws s3 cp bedrock-logs/ s3://${BUCKET_NAME}/ --recursive
```

## Step 10: Verify Deployment

Check the bucket contents and the Glue table, then run a test query in Athena:

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

## Step 11: Enable Quick Sight

Quick Sight reads the logs through Athena:

1. Go to [Quick Sight Console](https://quicksight.aws.amazon.com/)
2. Sign up for Enterprise Edition (if not already)
3. Follow Task 2 guide to connect to Athena

## Troubleshooting

Each entry below lists commands that help you find the cause of a failure.

### CDK Bootstrap Fails

Check which identity you're using, then retry with an explicit Region:

```bash
# Ensure you have admin permissions
aws sts get-caller-identity

# Try with explicit region
cdk bootstrap aws://ACCOUNT-ID/us-east-1
```

### Deployment Fails

Read the latest stack events, or rerun the deployment with verbose output:

```bash
# Check CloudFormation events
aws cloudformation describe-stack-events \
  --stack-name GenAIOperationsStack \
  --max-items 10

# View detailed error
cdk deploy --verbose
```

### S3 Upload Fails

Confirm that the bucket exists and check its policy:

```bash
# Check bucket exists
aws s3 ls | grep genai-ops

# Verify permissions
aws s3api get-bucket-policy --bucket ${BUCKET_NAME}
```

### Athena Query Fails

Check the table and the workgroup:

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
- Quick Sight Enterprise: $18/user/month
- Glue Data Catalog: $1/100k requests (~$0.10)

**Total**: ~$20-30/month (excluding Quick Sight user fees)

## Security Best Practices

Apply these practices before you use the sample with real logs:

1. **Encrypt S3 buckets**: Enable default encryption
2. **Restrict IAM roles**: Use least privilege
3. **Enable CloudTrail**: Audit API calls
4. **Use VPC endpoints**: For private connectivity
5. **Rotate credentials**: Regularly update access keys

## Next Steps

Proceed to [Setup Guide](0-setup.md) for detailed deployment instructions.
