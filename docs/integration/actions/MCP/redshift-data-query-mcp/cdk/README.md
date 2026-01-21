# CDK Infrastructure for Amazon Redshift Data Query MCP Integration

This directory contains the AWS CDK infrastructure code for deploying the Amazon Redshift Data Query MCP integration with Amazon Quick Suite.

## Architecture Overview

The CDK stack deploys the following AWS resources:

### Core Components

- **Amazon Bedrock AgentCore Gateway**: MCP protocol gateway for Quick Suite integration
- **AWS Lambda Function**: Processes Redshift queries using AWS LAB MCP Server
- **Amazon Cognito User Pool**: Provides OAuth 2.0 authentication for secure access
- **IAM Roles and Policies**: Implements least-privilege access controls

## File Structure

```
cdk/
 redshift_agentcore_stack.py   # Main CDK stack definition
 README.md                     # This file
 __init__.py                  # Python package initialization
```

## Stack Resources

### RedshiftAgentCoreStack

The main CDK stack creates:

1. **Lambda Execution Role**
   - Permissions for Redshift Data API operations
   - CloudWatch Logs access for monitoring
   - Follows AWS managed policy best practices

2. **Redshift Lambda Function**
   - Runtime: Python 3.12
   - Handler: `redshift_agentcore_lambda.handler`
   - Timeout: 5 minutes
   - Memory: 512 MB
   - Source code from `../tools/` directory

3. **Cognito User Pool**

4. **MCP Gateway**
   - Protocol type: MCP
   - Authorization: Custom JWT (Cognito)
   - Native Bedrock AgentCore integration

5. **Gateway Target**
   - Links MCP gateway to Lambda function
   - Tool schema loaded from JSON configuration
   - IAM role-based authentication

## Deployment

### Prerequisites

Ensure you have the following installed and configured:

- AWS CLI with valid credentials
- AWS CDK v2 (`npm install -g aws-cdk`)
- Python 3.11 or later

### Deploy the Stack

1. **Navigate to the project root**:
   ```bash
   cd docs/use-cases/redshift-data-query-mcp
   ```

2. **Install Python dependencies**:
   ```bash
   uv sync
   ```

3. **Bootstrap CDK (first time only)**:
   ```bash
   cdk bootstrap
   ```

4. **Deploy the stack**:
   ```bash
   cdk deploy
   ```

5. **Save the outputs**: Note the following values for Quick Suite configuration:
   - `GatewayUrl`: MCP gateway endpoint
   - `ClientId`: Cognito client ID
   - `ClientSecret`: Cognito client secret
   - `CognitoTokenUrl`: OAuth token endpoint
   - `UserPoolId`: Cognito user pool identifier

### Cleanup

To remove all deployed resources:

```bash
cdk destroy
```

## Configuration

### Environment Variables

The stack uses the following CDK context values:

- `account`: AWS account ID (auto-detected)
- `region`: AWS region (defaults to us-east-1)

### Customization

You can customize the deployment by modifying `redshift_agentcore_stack.py`:

- **Lambda Configuration**: Adjust memory, timeout, or runtime
- **Cognito Settings**: Modify authentication flow or domain prefix
- **IAM Permissions**: Add or remove service permissions as needed

## Security Considerations

### IAM Permissions

The stack implements least-privilege access:

- Lambda execution role has minimal Redshift permissions
- Gateway role limited to AgentCore operations
- No cross-account access by default

### Authentication

- OAuth 2.0 client credentials flow
- JWT tokens with configurable expiration
- Cognito-managed client secrets

## Monitoring and Troubleshooting

### CloudWatch Integration

The stack automatically creates:
- Lambda function log groups
- CloudWatch metrics for all services
- Error tracking and alerting capabilities

### Common Issues

**Deployment Failures**:
- Verify AWS credentials and permissions
- Check CDK version compatibility
- Ensure unique resource names

**Runtime Errors**:
- Check Lambda function logs in CloudWatch
- Verify IAM permissions for Redshift access
- Confirm tool schema JSON is valid

### Debugging

Enable verbose CDK output:
```bash
cdk deploy --verbose
```

View CloudFormation events:
```bash
aws cloudformation describe-stack-events --stack-name RedshiftAgentCoreStack
```

## Best Practices

### Development

- Use CDK context for environment-specific configuration
- Implement proper error handling in Lambda functions
- Follow AWS naming conventions for resources

### Production

- Enable CloudTrail for audit logging
- Configure backup and disaster recovery
- Implement monitoring and alerting
- Use AWS Secrets Manager for sensitive configuration

## Additional Resources

- [AWS CDK Developer Guide](https://docs.aws.amazon.com/cdk/)
- [Amazon Redshift Data API Documentation](https://docs.aws.amazon.com/redshift/latest/mgmt/data-api.html)
- [AWS Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [Amazon Cognito Developer Guide](https://docs.aws.amazon.com/cognito/)

## Deployment

```bash
uv pip install -e .
cdk deploy --require-approval never
aws cloudformation describe-stacks --stack-name RedshiftAgentCoreStack --query 'Stacks[0].Outputs'
```

## Outputs

The stack provides these outputs for Amazon Quick Suite MCP Actions integration:

- `GatewayUrl`: Amazon Bedrock Agent Runtime endpoint
- `ClientId`: Amazon Cognito client ID
- `ClientSecret`: Amazon Cognito client secret
- `CognitoTokenUrl`: OAuth2 token endpoint
- `UserPoolId`: Amazon Cognito User Pool ID
- `AgentCoreLambdaArn`: AWS Lambda function ARN

## Configuration

### Environment Variables

AWS Lambda function environment variables:

- `LOG_LEVEL`: INFO

### Resource Naming

All AWS resources use the stack name prefix for consistent naming and easy identification.

## Security

- **Least Privilege**: AWS IAM roles with minimal required permissions
- **Authentication**: OAuth2 with Amazon Cognito for MCP Actions
- **Encryption**: All data encrypted in transit and at rest
- **Logging**: Comprehensive Amazon CloudWatch logging for audit trails

## Monitoring

- **Amazon CloudWatch Logs**: AWS Lambda execution logs with 30-day retention
- **Amazon Bedrock Agent Runtime Metrics**: Request/response metrics and throttling
- **AWS Lambda Metrics**: Duration, errors, and concurrency tracking

## Cleanup

To remove all AWS resources created by this stack:

```bash
cdk destroy
```

This command removes all resources created by the AWS CDK stack.
