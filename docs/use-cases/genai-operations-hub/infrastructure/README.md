# Infrastructure

AWS CDK infrastructure for GenAI Operations Hub.

## What Gets Deployed

### Base CDK Stack (GenAIOperationsStack)
1. **S3 Bucket** - `genai-ops-bedrock-logs-{account}` for Bedrock logs
2. **S3 Bucket** - `genai-ops-athena-results-{account}` for Athena query results
3. **Glue Database** - `genai_ops_db` for data catalog
4. **Glue Table** - `bedrock_invocation_logs` with schema for Bedrock logs
5. **Athena Workgroup** - `genai-ops-workgroup` for query execution
6. **IAM Role** - QuickSight access to S3 and Athena

### QuickSight Resources (via boto3 script)
7. **Data Source** - Athena connection for QuickSight
8. **Dataset** - Daily Bedrock Invocations (aggregated daily metrics)
9. **Dataset** - Model Performance Metrics (latency and token analysis)
10. **Dataset** - Stop Reason Analysis (completion patterns)

**Note:** QuickSight resources are created via `create_quicksight_resources.py` script to bypass CloudFormation hooks.

## Prerequisites

- AWS CLI configured
- Python 3.8+
- Node.js 14+ (for CDK CLI)
- AWS account with appropriate permissions

## Installation

```bash
# Install CDK CLI globally
npm install -g aws-cdk

# Create Python virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Deployment

### Step 1: Bootstrap CDK

```bash
cdk bootstrap
```

### Step 2: Deploy Base Stack

```bash
cdk deploy GenAIOperationsStack --require-approval never
```

**Time**: ~5 minutes

### Step 3: Grant QuickSight Permissions

**CRITICAL: Do this before creating QuickSight resources**

1. Go to **QuickSight Console**
2. Profile icon → **Manage QuickSight** → **Permissions**
3. **AWS Resources** → **Manage**
4. Check **Amazon Athena** → **Enable write permission for Athena Workgroup**
5. Check **Amazon S3** → Select buckets:
   - `genai-ops-bedrock-logs-<ACCOUNT_ID>`
   - `genai-ops-athena-results-<ACCOUNT_ID>`
6. Click **Update**

### Step 4: Create QuickSight Resources

```bash
pip install boto3
python3 create_quicksight_resources.py 'Admin/your-username'
```

Find your username:
```bash
aws quicksight list-users \
  --aws-account-id $(aws sts get-caller-identity --query Account --output text) \
  --namespace default \
  --region us-east-1
```

**Time**: ~1 minute

### Step 5: View Stack Outputs

```bash
aws cloudformation describe-stacks \
  --stack-name GenAIOperationsStack \
  --query 'Stacks[0].Outputs'
```

## Stack Outputs

After deployment, note these outputs:

- **LogsBucketName** - S3 bucket for Bedrock logs
- **DatabaseName** - Glue database name
- **TableName** - Glue table name
- **AthenaWorkGroup** - Athena workgroup name
- **QuickSightRoleArn** - IAM role ARN for QuickSight

## Customization

### Change Bucket Names

Edit `stacks/genai_ops_stack.py`:

```python
logs_bucket = s3.Bucket(
    self, "BedrockLogsBucket",
    bucket_name="your-custom-bucket-name",  # Change this
    ...
)
```

### Add Encryption

```python
logs_bucket = s3.Bucket(
    self, "BedrockLogsBucket",
    encryption=s3.BucketEncryption.S3_MANAGED,  # Add this
    ...
)
```

### Change Removal Policy

For production, change to `RETAIN`:

```python
logs_bucket = s3.Bucket(
    self, "BedrockLogsBucket",
    removal_policy=RemovalPolicy.RETAIN,  # Change from DESTROY
    auto_delete_objects=False,  # Change from True
    ...
)
```

## Troubleshooting

### QuickSight Data Source Creation Failed

**Error**: `DataSource is in status CREATION_FAILED`

**Cause**: QuickSight lacks permissions to access Athena/S3

**Solution**:
1. Delete failed data source:
   ```bash
   aws quicksight delete-data-source \
     --aws-account-id $(aws sts get-caller-identity --query Account --output text) \
     --data-source-id genai-ops-athena-source \
     --region us-east-1
   ```
2. Grant QuickSight permissions (see Step 3 above)
3. Run script again

### CloudFormation Hook Errors

**Error**: `AWS::EarlyValidation::PropertyValidation` blocks QuickSight resources

**Solution**: Use `create_quicksight_resources.py` script instead of CDK (bypasses hooks)

### Dataset Creation Fails

**Error**: `InvalidParameterValueException`

**Causes**:
- Data source doesn't exist or failed
- QuickSight lacks permissions
- Athena workgroup doesn't exist

**Solution**: Verify Steps 2-4 completed successfully

## Clean Up

```bash
# Empty S3 buckets first
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name GenAIOperationsStack \
  --query 'Stacks[0].Outputs[?OutputKey==`LogsBucketName`].OutputValue' \
  --output text)
aws s3 rm s3://${BUCKET}/ --recursive

# Destroy stack
cdk destroy
```

## Troubleshooting

### Bootstrap Error

```bash
# Ensure you have correct permissions
aws sts get-caller-identity

# Try with explicit account and region
cdk bootstrap aws://123456789012/us-east-1
```

### Deployment Fails

```bash
# View detailed logs
cdk deploy --verbose

# Check CloudFormation events
aws cloudformation describe-stack-events \
  --stack-name GenAIOperationsStack \
  --max-items 20
```

### Bucket Already Exists

If bucket names conflict, either:
1. Delete existing bucket
2. Change bucket name in code
3. Use different AWS account

## Cost Estimate

Resources created by this stack:

| Resource | Cost |
|----------|------|
| S3 Storage | $0.023/GB/month |
| Glue Data Catalog | $1/100k requests |
| Athena Queries | $5/TB scanned |
| IAM Roles | Free |

**Estimated monthly cost**: $2-5 (excluding QuickSight)

## Security

- S3 buckets have versioning disabled (enable for production)
- No encryption enabled by default (add for production)
- IAM role follows least privilege
- Auto-delete enabled for easy cleanup (disable for production)

## Next Steps

After deployment:
1. Upload sample data to S3
2. Follow the [Setup Guide](../docs/0-setup.md) to verify deployment
3. Start building your dashboard with [AI Dashboard Guide](../docs/1-ai-dashboard.md)

See [DEPLOYMENT.md](../DEPLOYMENT.md) for complete instructions.
