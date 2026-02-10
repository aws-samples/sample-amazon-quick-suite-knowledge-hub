#!/usr/bin/env python3
"""
Script to create QuickSight data source and datasets using boto3 API.
This bypasses CloudFormation hooks that block CDK deployment.
"""
import boto3
import sys
import json

def create_quicksight_resources(
    account_id,
    region='us-east-1',
    database_name='genai_ops_db',
    workgroup_name='genai-ops-workgroup',
    quicksight_user='Admin/cmakinen'
):
    """Create QuickSight data source and datasets using boto3 API."""
    
    quicksight = boto3.client('quicksight', region_name=region)
    principal_arn = f"arn:aws:quicksight:{region}:{account_id}:user/default/{quicksight_user}"
    
    print(f"Creating QuickSight resources in account {account_id}...")
    print(f"Principal: {principal_arn}")
    
    # 1. Create Data Source
    print("\n1. Creating Athena data source...")
    try:
        ds_response = quicksight.create_data_source(
            AwsAccountId=account_id,
            DataSourceId='genai-ops-athena-source',
            Name='GenAI-Ops-Athena',
            Type='ATHENA',
            DataSourceParameters={
                'AthenaParameters': {
                    'WorkGroup': workgroup_name
                }
            },
            Permissions=[{
                'Principal': principal_arn,
                'Actions': [
                    'quicksight:DescribeDataSource',
                    'quicksight:DescribeDataSourcePermissions',
                    'quicksight:PassDataSource',
                    'quicksight:UpdateDataSource',
                    'quicksight:DeleteDataSource',
                    'quicksight:UpdateDataSourcePermissions'
                ]
            }]
        )
        print(f"✓ Data source created: {ds_response['Arn']}")
        data_source_arn = ds_response['Arn']
    except quicksight.exceptions.ResourceExistsException:
        print("✓ Data source already exists")
        ds_desc = quicksight.describe_data_source(
            AwsAccountId=account_id,
            DataSourceId='genai-ops-athena-source'
        )
        data_source_arn = ds_desc['DataSource']['Arn']
    
    # 2. Create Dataset - Daily Invocations
    print("\n2. Creating Daily Invocations dataset...")
    try:
        quicksight.create_data_set(
            AwsAccountId=account_id,
            DataSetId='daily-bedrock-invocations',
            Name='Daily Bedrock Invocations',
            ImportMode='DIRECT_QUERY',
            PhysicalTableMap={
                'daily-inv': {
                    'CustomSql': {
                        'DataSourceArn': data_source_arn,
                        'Name': 'daily_invocations',
                        'SqlQuery': f"""
                            SELECT 
                                DATE(from_iso8601_timestamp(timestamp)) as date,
                                modelId,
                                COUNT(*) as invocation_count,
                                AVG(output.outputBodyJson.metrics.latencyMs) as avg_latency_ms,
                                SUM(output.outputBodyJson.usage.inputTokens) as total_input_tokens,
                                SUM(output.outputBodyJson.usage.outputTokens) as total_output_tokens
                            FROM {database_name}.bedrock_invocation_logs
                            GROUP BY DATE(from_iso8601_timestamp(timestamp)), modelId
                        """,
                        'Columns': [
                            {'Name': 'date', 'Type': 'DATETIME'},
                            {'Name': 'modelId', 'Type': 'STRING'},
                            {'Name': 'invocation_count', 'Type': 'INTEGER'},
                            {'Name': 'avg_latency_ms', 'Type': 'DECIMAL'},
                            {'Name': 'total_input_tokens', 'Type': 'INTEGER'},
                            {'Name': 'total_output_tokens', 'Type': 'INTEGER'}
                        ]
                    }
                }
            },
            Permissions=[{
                'Principal': principal_arn,
                'Actions': [
                    'quicksight:DescribeDataSet',
                    'quicksight:DescribeDataSetPermissions',
                    'quicksight:PassDataSet',
                    'quicksight:DescribeIngestion',
                    'quicksight:ListIngestions',
                    'quicksight:UpdateDataSet',
                    'quicksight:DeleteDataSet',
                    'quicksight:CreateIngestion',
                    'quicksight:CancelIngestion',
                    'quicksight:UpdateDataSetPermissions'
                ]
            }]
        )
        print("✓ Daily Invocations dataset created")
    except quicksight.exceptions.ResourceExistsException:
        print("✓ Daily Invocations dataset already exists")
    
    # 3. Create Dataset - Model Performance
    print("\n3. Creating Model Performance dataset...")
    try:
        quicksight.create_data_set(
            AwsAccountId=account_id,
            DataSetId='model-performance-metrics',
            Name='Model Performance Metrics',
            ImportMode='DIRECT_QUERY',
            PhysicalTableMap={
                'model-perf': {
                    'CustomSql': {
                        'DataSourceArn': data_source_arn,
                        'Name': 'model_performance',
                        'SqlQuery': f"""
                            SELECT 
                                modelId,
                                COUNT(*) as total_requests,
                                AVG(output.outputBodyJson.metrics.latencyMs) as avg_latency_ms,
                                MIN(output.outputBodyJson.metrics.latencyMs) as min_latency_ms,
                                MAX(output.outputBodyJson.metrics.latencyMs) as max_latency_ms,
                                AVG(output.outputBodyJson.usage.totalTokens) as avg_tokens
                            FROM {database_name}.bedrock_invocation_logs
                            GROUP BY modelId
                        """,
                        'Columns': [
                            {'Name': 'modelId', 'Type': 'STRING'},
                            {'Name': 'total_requests', 'Type': 'INTEGER'},
                            {'Name': 'avg_latency_ms', 'Type': 'DECIMAL'},
                            {'Name': 'min_latency_ms', 'Type': 'DECIMAL'},
                            {'Name': 'max_latency_ms', 'Type': 'DECIMAL'},
                            {'Name': 'avg_tokens', 'Type': 'DECIMAL'}
                        ]
                    }
                }
            },
            Permissions=[{
                'Principal': principal_arn,
                'Actions': [
                    'quicksight:DescribeDataSet',
                    'quicksight:DescribeDataSetPermissions',
                    'quicksight:PassDataSet',
                    'quicksight:DescribeIngestion',
                    'quicksight:ListIngestions',
                    'quicksight:UpdateDataSet',
                    'quicksight:DeleteDataSet',
                    'quicksight:CreateIngestion',
                    'quicksight:CancelIngestion',
                    'quicksight:UpdateDataSetPermissions'
                ]
            }]
        )
        print("✓ Model Performance dataset created")
    except quicksight.exceptions.ResourceExistsException:
        print("✓ Model Performance dataset already exists")
    
    # 4. Create Dataset - Stop Reason Analysis
    print("\n4. Creating Stop Reason Analysis dataset...")
    try:
        quicksight.create_data_set(
            AwsAccountId=account_id,
            DataSetId='stop-reason-analysis',
            Name='Stop Reason Analysis',
            ImportMode='DIRECT_QUERY',
            PhysicalTableMap={
                'stop-reason': {
                    'CustomSql': {
                        'DataSourceArn': data_source_arn,
                        'Name': 'stop_reason_analysis',
                        'SqlQuery': f"""
                            SELECT 
                                output.outputBodyJson.stopReason as stop_reason,
                                modelId,
                                COUNT(*) as occurrence_count
                            FROM {database_name}.bedrock_invocation_logs
                            GROUP BY output.outputBodyJson.stopReason, modelId
                        """,
                        'Columns': [
                            {'Name': 'stop_reason', 'Type': 'STRING'},
                            {'Name': 'modelId', 'Type': 'STRING'},
                            {'Name': 'occurrence_count', 'Type': 'INTEGER'}
                        ]
                    }
                }
            },
            Permissions=[{
                'Principal': principal_arn,
                'Actions': [
                    'quicksight:DescribeDataSet',
                    'quicksight:DescribeDataSetPermissions',
                    'quicksight:PassDataSet',
                    'quicksight:DescribeIngestion',
                    'quicksight:ListIngestions',
                    'quicksight:UpdateDataSet',
                    'quicksight:DeleteDataSet',
                    'quicksight:CreateIngestion',
                    'quicksight:CancelIngestion',
                    'quicksight:UpdateDataSetPermissions'
                ]
            }]
        )
        print("✓ Stop Reason Analysis dataset created")
    except quicksight.exceptions.ResourceExistsException:
        print("✓ Stop Reason Analysis dataset already exists")
    
    print("\n✅ All QuickSight resources created successfully!")
    print("\nNext steps:")
    print("1. Go to QuickSight Console")
    print("2. Navigate to Datasets")
    print("3. Start building your dashboard with Generative BI")


if __name__ == '__main__':
    # Get AWS account ID
    sts = boto3.client('sts')
    account_id = sts.get_caller_identity()['Account']
    
    # Get parameters from command line or use defaults
    quicksight_user = sys.argv[1] if len(sys.argv) > 1 else 'Admin/cmakinen'
    
    print(f"Using QuickSight user: {quicksight_user}")
    print("If this is incorrect, run: python3 create_quicksight_resources.py 'Admin/your-username'")
    
    create_quicksight_resources(
        account_id=account_id,
        quicksight_user=quicksight_user
    )
