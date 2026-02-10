#!/bin/bash
set -e

echo "🧹 Cleaning up GenAI Operations Hub..."

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION="us-east-1"

# Delete QuickSight datasets
echo "1. Deleting QuickSight datasets..."
aws quicksight delete-data-set --aws-account-id $ACCOUNT --data-set-id daily-bedrock-invocations --region $REGION 2>/dev/null || echo "  ✓ Dataset daily-bedrock-invocations already deleted"
aws quicksight delete-data-set --aws-account-id $ACCOUNT --data-set-id model-performance-metrics --region $REGION 2>/dev/null || echo "  ✓ Dataset model-performance-metrics already deleted"
aws quicksight delete-data-set --aws-account-id $ACCOUNT --data-set-id stop-reason-analysis --region $REGION 2>/dev/null || echo "  ✓ Dataset stop-reason-analysis already deleted"

# Delete QuickSight data source
echo "2. Deleting QuickSight data source..."
aws quicksight delete-data-source --aws-account-id $ACCOUNT --data-source-id genai-ops-athena-source --region $REGION 2>/dev/null || echo "  ✓ Data source already deleted"

# Empty S3 buckets
echo "3. Emptying S3 buckets..."
LOGS_BUCKET="genai-ops-bedrock-logs-${ACCOUNT}"
RESULTS_BUCKET="genai-ops-athena-results-${ACCOUNT}"

aws s3 rm s3://${LOGS_BUCKET}/ --recursive 2>/dev/null || echo "  ✓ Logs bucket already empty"
aws s3 rm s3://${RESULTS_BUCKET}/ --recursive 2>/dev/null || echo "  ✓ Results bucket already empty"

# Delete CDK stack
echo "4. Deleting CDK stack..."
cd infrastructure
cdk destroy GenAIOperationsStack --force

echo "✅ Cleanup complete!"
