# Quick Chat Agent Embedding Demo - Infrastructure

This guide helps you deploy the AWS infrastructure required for the Quick Chat Agent Embedding Demo.

## Prerequisites

- **IAM Identity Center instance** configured in your AWS account
- AWS CLI configured with appropriate permissions
- Node.js 18+ and npm installed
- AWS CDK v2 installed globally (`npm install -g aws-cdk`)

## Deploy Infrastructure

### Option 1: Local Development (Recommended)

```bash
cd infrastructure
./deploy.sh
```

This uses `localhost:3000` as the default domain for development.

### Option 2: Production Deployment

```bash
cd infrastructure
./deploy.sh 'https://yourdomain.com,https://www.yourdomain.com'
```

Specify your production domains as a comma-separated list.

## Deployment Process

The deployment script performs the following actions:

1. Auto-discovers your AWS Identity Center instance ARN
2. Creates a Lambda layer with required Python dependencies
3. Deploys AWS resources using CloudFormation
4. Generates environment configuration for the frontend
5. Installs frontend dependencies

**CloudFormation Stack**: `QuickChatEmbeddingStack`

## Next Steps

After successful deployment:

1. **Create Users**: Create matching users in both Amazon Cognito and AWS IAM Identity Center with the same email address. See the main `README.md` file for detailed user setup steps.

2. **Configure Frontend**: Set up the frontend application. See `fe/README.md` for detailed setup instructions.

## AWS Resources Created

The deployment creates the following AWS resources:

- **AWS Lambda Layer**: Contains Python dependencies (boto3, PyJWT)
- **Amazon API Gateway**: HTTP API with CORS configuration and JWT authorization
- **Amazon Cognito User Pool**: Handles user authentication with app client
- **AWS Lambda Functions**: Implements QuickSuite embedding logic with 10-hour session lifetime
- **AWS IAM Roles**: Provides necessary permissions for all resources
- **AWS IAM Identity Center Integration**: Auto-configured for seamless authentication

## Configuration Files Generated

- **Environment Variables**: `.env.local` file created in the `fe/` directory
- **CloudFormation Outputs**: API endpoints, Cognito configuration, and resource ARNs
