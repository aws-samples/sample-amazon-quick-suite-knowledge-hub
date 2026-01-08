# Bedrock KB Retrieval MCP CDK Infrastructure

AWS CDK infrastructure for Bedrock KB Retrieval MCP - Amazon Bedrock Knowledge Base MCP integration.

## Architecture

The CDK stack (`KBDirectStack`) deploys:

- **AgentCore Gateway**: Bedrock AgentCore Gateway with Lambda target
- **Lambda Function**: Bedrock Knowledge Base retrieval handler
- **Cognito User Pool**: OAuth2 authentication for QuickSuite MCP Actions
- **IAM Roles**: Least-privilege permissions for Amazon Bedrock access
- **CloudWatch**: Logging and monitoring

## Stack Components

### AgentCore Gateway

- **Type**: Amazon Bedrock AgentCore Gateway
- **Target**: Lambda function for MCP tool execution
- **Authentication**: Cognito User Pool with OAuth2
- **Protocol**: Model Context Protocol (MCP)

### Lambda Function

- **Runtime**: Python 3.12
- **Handler**: `kb_agentcore_lambda.handler`
- **Memory**: 512 MB
- **Timeout**: 5 minutes
- **Concurrency**: 10 reserved executions

### Authentication

- **Type**: Cognito User Pool with OAuth2
- **Flow**: Client credentials for service-to-service
- **Password Policy**: Strong requirements
- **Client Secret**: Generated for QuickSuite integration

### IAM Permissions

- `bedrock:ListKnowledgeBases`
- `bedrock:GetKnowledgeBase`
- `bedrock:ListDataSources`
- `bedrock:GetDataSource`
- `bedrock:Retrieve`
- `bedrock:RetrieveAndGenerate`
- `bedrock:InvokeModel`

## Deployment

```bash
# Install dependencies
pip install -r requirements.txt

# Deploy stack
cdk deploy --require-approval never

# Get outputs
aws cloudformation describe-stacks --stack-name KBDirectStack --query 'Stacks[0].Outputs'
```

## Outputs

The stack provides these outputs for QuickSuite MCP Actions integration:

- `GatewayUrl`: AgentCore Gateway endpoint
- `ClientId`: Cognito client ID
- `ClientSecret`: Cognito client secret
- `CognitoTokenUrl`: OAuth2 token endpoint
- `UserPoolId`: Cognito User Pool ID
- `AgentCoreLambdaArn`: Lambda function ARN

## Configuration

### Environment Variables

Lambda function environment:

- `LOG_LEVEL`: INFO
- `POWERTOOLS_SERVICE_NAME`: kb-direct-agentcore

### Resource Naming

All resources use the stack name prefix for consistent naming and easy identification.

## Security

- **Least Privilege**: IAM roles with minimal required permissions
- **Authentication**: OAuth2 with Cognito for MCP Actions
- **Encryption**: All data encrypted in transit and at rest
- **Logging**: Comprehensive CloudWatch logging for audit trails

## Monitoring

- **CloudWatch Logs**: Lambda execution logs with 30-day retention
- **AgentCore Metrics**: Request/response metrics and throttling
- **Lambda Metrics**: Duration, errors, and concurrency tracking

## Cleanup

```bash
cdk destroy
```

This removes all resources created by the stack.
