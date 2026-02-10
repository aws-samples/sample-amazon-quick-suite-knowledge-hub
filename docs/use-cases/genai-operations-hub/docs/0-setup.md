# Setup Guide

Complete setup instructions for the GenAI Operations Hub.

## Overview

Deploy the infrastructure and configure QuickSight to analyze Bedrock invocation logs.

**Duration:** 15-20 minutes

---

## Prerequisites

- AWS Account with Bedrock invocation logs enabled
- QuickSight Enterprise Edition subscription
- AWS CLI configured with appropriate credentials
- Python 3.8+ and Node.js 14+ installed

---

## Part 1: Deploy Infrastructure

### Step 1: Install Dependencies

```bash
cd infrastructure
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install boto3

# Install AWS CDK CLI globally (if not already installed)
npm install -g aws-cdk
```

### Step 2: Bootstrap CDK (One-time setup)

```bash
cdk bootstrap
```

**Troubleshooting:** If bootstrap fails with CloudFormation hook errors, try a different region:
```bash
cdk bootstrap aws://ACCOUNT-ID/us-west-2
```

### Step 3: Deploy the Stack

```bash
cdk deploy GenAIOperationsStack --require-approval never
```

This creates:
- S3 buckets for logs and query results
- Glue database and table
- Athena workgroup
- IAM roles and policies

**Expected time:** ~5 minutes

---

## Part 2: Upload Sample Data

### Step 4: Copy Sample Logs to S3

```bash
# Get the bucket name from CloudFormation outputs
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name GenAIOperationsStack \
  --query 'Stacks[0].Outputs[?OutputKey==`LogsBucketName`].OutputValue' \
  --output text)

# Upload sample data
cd ../sample-data
aws s3 cp bedrock-logs/ s3://$BUCKET/ --recursive
```

### Step 5: Test Athena Query

```bash
aws athena start-query-execution \
  --query-string "SELECT COUNT(*) FROM bedrock_invocation_logs" \
  --query-execution-context Database=genai_ops_db \
  --result-configuration OutputLocation=s3://${BUCKET}/athena-results/ \
  --work-group genai-ops-workgroup
```

---

## Part 3: Configure QuickSight

### Step 6: Find Your QuickSight Username

```bash
aws quicksight list-users \
  --aws-account-id $(aws sts get-caller-identity --query Account --output text) \
  --namespace default \
  --region us-east-1
```

Your username will be in the format: `<IAMRoleName>/<SessionName>` (e.g., `Admin/your-session-name`)

**Important:** Save this username - you'll need it in the next steps!

### Step 7: Grant QuickSight Permissions

**CRITICAL: Do this BEFORE creating QuickSight resources**

1. Go to the **Amazon QuickSight Console**
2. Click your profile icon (top right) → **Manage QuickSight**
3. Under **Permissions** in the left menu, click on **AWS Resources**
4. Configure permissions:
   - ✅ Check **Amazon Athena**
   - ✅ Enable **Write permission for Athena Workgroup**
   - ✅ Check **Amazon S3**
   - ✅ Click **Select S3 buckets**
5. Select both buckets created in the previous step:
   - `genai-ops-bedrock-logs-<ACCOUNT_ID>`
   - `genai-ops-athena-results-<ACCOUNT_ID>`
6. Click **Finish** → **Update**

**Critical Step:** If you skip this, QuickSight resource creation will fail with permission errors.

### Step 8: Create QuickSight Resources

Use the boto3 script to create data sources and datasets:

```bash
cd ../infrastructure
python3 create_quicksight_resources.py '<IAMRoleName>/<SessionName>'
```

Replace `'<IAMRoleName>/<SessionName>'` with your actual username from Step 6.

This script creates:
- Athena data source
- Three datasets with pre-configured SQL queries:
  - Daily Bedrock Invocations
  - Model Performance Metrics
  - Stop Reason Analysis

**Expected time:** ~1 minute

**Troubleshooting:** If you see `DataSource is in status CREATION_FAILED`:
1. This means Step 7 permissions weren't granted properly
2. Delete the failed data source:
   ```bash
   aws quicksight delete-data-source \
     --aws-account-id $(aws sts get-caller-identity --query Account --output text) \
     --data-source-id genai-ops-athena-source \
     --region us-east-1
   ```
3. Go back to Step 7 and verify both Athena and S3 permissions with both buckets selected
4. Re-run the script

### Step 9: Verify QuickSight Resources

1. Go to the **QuickSight Console**
2. Click **Datasets** in the left navigation menu
3. Verify these three datasets exist:
   - ✅ Daily Bedrock Invocations
   - ✅ Model Performance Metrics
   - ✅ Stop Reason Analysis
4. Click each dataset to preview the data

**Note:** Each dataset should show sample data from your Bedrock logs. If you see "No data", verify the sample data was uploaded in Step 4.

---

## Validation Checklist

- ✓ CDK stack deployed successfully
- ✓ S3 buckets created (check CloudFormation outputs)
- ✓ Sample data uploaded to S3
- ✓ Athena query executes without errors
- ✓ QuickSight username identified
- ✓ S3 and Athena permissions granted
- ✓ Data source created successfully
- ✓ All three datasets visible in QuickSight
- ✓ Datasets show sample data when previewed

---

## What You've Accomplished

✅ Deployed infrastructure with CDK  
✅ Uploaded sample Bedrock logs to S3  
✅ Configured QuickSight permissions  
✅ Created data sources and datasets  
✅ Verified data connectivity

---

## Next Steps

Continue to [Task 1: AI Dashboard](1-ai-dashboard.md) to build AI-powered visualizations!
