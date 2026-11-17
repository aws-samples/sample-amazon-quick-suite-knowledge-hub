# Quick Chat Agent Embedding Demo - Infrastructure

This guide helps you deploy the AWS infrastructure required for the Quick Chat Agent Embedding Demo.

## Prerequisites

The deployment script looks up your IAM Identity Center instance automatically, so the instance must exist before you start. Check that you have each item below:

- **IAM Identity Center instance** configured in your AWS account
- AWS CLI configured with appropriate permissions
- Node.js 18+ and npm installed
- AWS CDK v2 installed globally (`npm install -g aws-cdk`)

## Deploy Infrastructure

Run `deploy.sh` with the domains that will host the frontend. The script uses them as the allowed embedding domains.

### Option 1: Local Development (Recommended)

Run the script without arguments:

```bash
cd infrastructure
./deploy.sh
```

This uses `localhost:3000` as the default domain for development.

### Option 2: Production Deployment

Pass your domains to the script:

```bash
cd infrastructure
./deploy.sh 'https://yourdomain.com,https://www.yourdomain.com'
```

Specify your production domains as a comma-separated list.

## Deployment Process

The deployment script performs the following actions:

1. Auto-discovers your IAM Identity Center instance ARN
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
- **AWS Lambda Functions**: Implements Quick embedding logic with 10-hour session lifetime
- **AWS IAM Roles**: Provides necessary permissions for all resources
- **AWS IAM Identity Center Integration**: Auto-configured for trusted identity propagation

## Configuration Files Generated

The deployment also writes configuration for the next steps:

- **Environment Variables**: `.env.local` file created in the `fe/` directory
- **CloudFormation Outputs**: API endpoints, Cognito configuration, and resource ARNs
