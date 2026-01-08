# Quick Chat Agent Embedding Demo - Frontend

This Next.js application provides the frontend interface for the Quick Chat Agent Embedding Demo.

## Prerequisites

Before setting up the frontend, ensure you have completed the following:

1. **Infrastructure Deployment**: Deploy the AWS infrastructure using the deployment script in the `infrastructure/` directory
2. **User Configuration**: Create matching users in Amazon Cognito and AWS IAM Identity Center (see main README.md)
3. **Agent ID Configuration**: Configure your Amazon QuickSuite Agent ID (see main README.md)
4. **Node.js**: Version 18 or later installed
5. **npm**: Package manager installed

## Configuration

The infrastructure deployment creates a `.env.local` file with environment variables. Ensure you have configured your Amazon QuickSuite Agent ID as described in the main README.md file before proceeding.

## Setup and Deployment

### Install Dependencies

```bash
npm install
```

### Start the Development Server

```bash
npm run dev
```

### Access the Application

Open your web browser and navigate to `http://localhost:3000`

## Environment Variables

The infrastructure deployment automatically generates the following environment variables in `.env.local`:

- `NEXT_PUBLIC_API_ENDPOINT`: Amazon API Gateway endpoint URL
- `NEXT_PUBLIC_COGNITO_USER_POOL_ID`: Amazon Cognito User Pool identifier
- `NEXT_PUBLIC_COGNITO_CLIENT_ID`: Amazon Cognito App Client identifier
- `NEXT_PUBLIC_COGNITO_DOMAIN`: Amazon Cognito domain configuration
- `NEXT_PUBLIC_QUICKSUITE_AGENT_ID`: Amazon QuickSuite Agent ID (requires manual configuration)
- `NEXT_PUBLIC_AWS_REGION`: AWS Region where resources are deployed

## Troubleshooting

### Agent ID Configuration Error

If you encounter the error "NEXT_PUBLIC_QUICKSUITE_AGENT_ID is not configured":

- Verify that you have updated the agent ID in `.env.local` following the configuration steps above
- Ensure the agent ID value does not contain the placeholder text

### Authentication Issues

If you experience authentication problems:

- Confirm that you have created matching users in both Amazon Cognito and AWS IAM Identity Center
- Verify that both users use the same email address
- Refer to the main README.md for detailed user setup instructions
