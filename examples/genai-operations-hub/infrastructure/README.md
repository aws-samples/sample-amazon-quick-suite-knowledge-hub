# Infrastructure

AWS CDK infrastructure for GenAI Operations Hub.

## What Gets Deployed

Deployment has two parts: a CDK stack for storage, catalog, and query resources, and a boto3 script for the Quick Sight resources.

### Base CDK Stack (GenAIOperationsStack)

The CDK stack creates:

1. **S3 Bucket** - `genai-ops-bedrock-logs-{account}` for Bedrock logs
2. **S3 Bucket** - `genai-ops-athena-results-{account}` for Athena query results
3. **Glue Database** - `genai_ops_db` for data catalog
4. **Glue Table** - `bedrock_invocation_logs` with schema for Bedrock logs
5. **Athena Workgroup** - `genai-ops-workgroup` for query execution
6. **IAM Role** - Quick Sight access to S3 and Athena

### Quick Sight Resources (via boto3 script)

The script then creates:

1. **Data Source** - Athena connection for Quick Sight
2. **Dataset** - Daily Bedrock Invocations (aggregated daily metrics)
3. **Dataset** - Model Performance Metrics (latency and token analysis)
4. **Dataset** - Stop Reason Analysis (completion patterns)

**Note:** Quick Sight resources are created via `create_quicksight_resources.py` script to bypass CloudFormation hooks.

## Prerequisites

You need the following before you deploy:

- AWS CLI configured
- Python 3.8+
- Node.js 14+ (for CDK CLI)
- AWS account with appropriate permissions

## Installation

Install the CDK CLI and the Python dependencies in a virtual environment:

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

Run these steps in order. Step 3 is a console step that the script in Step 4 depends on.

### Step 1: Bootstrap CDK

Bootstrap the account and Region once:

```bash
cdk bootstrap
```

### Step 2: Deploy Base Stack

Deploy the CDK stack:

```bash
cdk deploy GenAIOperationsStack --require-approval never
```

**Time**: ~5 minutes

### Step 3: Grant Quick Sight Permissions

**Important:** Do this step before you create the Quick Sight resources.

1. Go to **Quick Sight Console**
2. Profile icon → **Manage Quick Sight** → **Permissions**
3. **AWS Resources** → **Manage**
4. Check **Amazon Athena** → **Enable write permission for Athena Workgroup**
5. Check **Amazon S3** → Select buckets:
   - `genai-ops-bedrock-logs-<ACCOUNT_ID>`
   - `genai-ops-athena-results-<ACCOUNT_ID>`
6. Choose **Update**

### Step 4: Create Quick Sight Resources

Run the script with your Quick Sight user name:

```bash
pip install boto3
python3 create_quicksight_resources.py 'Admin/your-username'
```

If you don't know your user name, list the users in the `default` namespace:

```bash
aws quicksight list-users \
  --aws-account-id $(aws sts get-caller-identity --query Account --output text) \
  --namespace default \
  --region us-east-1
```

**Time**: ~1 minute

### Step 5: View Stack Outputs

Print the stack outputs:

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
- **QuickSightRoleArn** - IAM role ARN for Quick Sight

## Customization

Each change below is an edit to `stacks/genai_ops_stack.py`.

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

Add an `encryption` setting to the bucket:

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

These errors come from the Quick Sight resource script and the datasets.

### Quick Sight Data Source Creation Failed

**Error**: `DataSource is in status CREATION_FAILED`

**Cause**: Quick Sight lacks permissions to access Athena/S3

**Solution**:

1. Delete failed data source:

   ```bash
   aws quicksight delete-data-source \
     --aws-account-id $(aws sts get-caller-identity --query Account --output text) \
     --data-source-id genai-ops-athena-source \
     --region us-east-1
   ```

2. Grant Quick Sight permissions (see Step 3 above)
3. Run script again

### CloudFormation Hook Errors

**Error**: `AWS::EarlyValidation::PropertyValidation` blocks Quick Sight resources

**Solution**: Use `create_quicksight_resources.py` script instead of CDK (bypasses hooks)

### Dataset Creation Fails

**Error**: `InvalidParameterValueException`

**Causes**:

- Data source doesn't exist or failed
- Quick Sight lacks permissions
- Athena workgroup doesn't exist

**Solution**: Verify that Steps 2 to 4 completed successfully.

## Clean Up

Empty the logs bucket, then destroy the stack:

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

## CDK Troubleshooting

These errors come from CDK bootstrap and deployment.

### Bootstrap Error

Confirm your credentials, then bootstrap with an explicit account and Region:

```bash
# Ensure you have correct permissions
aws sts get-caller-identity

# Try with explicit account and region
cdk bootstrap aws://123456789012/us-east-1
```

### Deployment Fails

Rerun with verbose output and read the stack events:

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

**Estimated monthly cost**: $2-5 (excluding Quick Sight)

## Security

The stack uses sample defaults. Change them before you use it in production:

- S3 buckets have versioning disabled (enable for production)
- No encryption enabled by default (add for production)
- IAM role follows least privilege
- Auto-delete enabled so cleanup removes the buckets (disable for production)

## Next Steps

After deployment:

1. Upload sample data to S3
2. Follow the [Setup Guide](../docs/0-setup.md) to verify deployment
3. Start building your dashboard with [AI Dashboard Guide](../docs/1-ai-dashboard.md)

See [DEPLOYMENT.md](../DEPLOYMENT.md) for complete instructions.
