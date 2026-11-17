---
category: Financial Services
description: "Finance dashboard with embedded Quick Sight analytics and Quick chat AI assistant using Cognito + IAM Identity Center authentication"
---

# Finance Dashboard with Quick Sight and Quick Chat Embedding

A web-based financial analytics dashboard for AnyCompany that embeds Quick Sight dashboards and the Quick chat AI assistant from AWS Quick into a React application. Users sign in through Amazon Cognito and IAM Identity Center (IDC).

This solution shows how to build a custom branded analytics experience that combines your own React UI with embedded Quick Sight visuals and conversational AI. Every embed request is authenticated through a Cognito → IDC → Quick Sight identity chain.

## Architecture

The React frontend gets an ID token from Cognito and exchanges it for embed URLs through the embedding API. A separate metrics API serves the KPI data.

```text
┌─────────────────────────────────────────────────────────────┐
│  CloudFront + S3                                            │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  React Frontend                                       │  │
│  │  ├── KPI Cards (Revenue, Expenses, Profit, Margin)    │  │
│  │  ├── Chart.js Visualizations                          │  │
│  │  └── QuickSight Overlay (Dashboard + QuickChat)       │  │
│  └──────────────┬────────────────────────────────────────┘  │
│                 │                                            │
│     ┌───────────▼───────────┐                               │
│     │  Cognito Hosted UI    │                               │
│     │  (Implicit Flow)      │                               │
│     └───────────┬───────────┘                               │
│                 │ id_token                                   │
│     ┌───────────▼───────────┐                               │
│     │  Embedding API        │                               │
│     │  (API Gateway HTTP)   │                               │
│     └───────────┬───────────┘                               │
│                 │                                            │
│     ┌───────────▼───────────┐                               │
│     │  Lambda (Python)      │                               │
│     │  ├─ CreateTokenWithIAM│──► IAM Identity Center        │
│     │  ├─ AssumeRole        │──► QuickSuite Role            │
│     │  └─ GenerateEmbedUrl  │──► QuickSight                 │
│     └───────────────────────┘                               │
│                                                              │
│  ┌──────────────────┐  ┌──────────────┐                     │
│  │  DynamoDB         │  │  API Gateway │                     │
│  │  (Metrics Data)   │◄─┤  REST API    │◄── Lambda (Node.js)│
│  └──────────────────┘  └──────────────┘                     │
└─────────────────────────────────────────────────────────────┘
```

### Components

The solution has these parts:

- **Frontend**: React 18 (CRA) with Chart.js visualizations and Quick Sight Embedding SDK
- **Metrics API**: Lambda (Node.js 18) + DynamoDB + API Gateway REST
- **Embedding API**: Lambda (Python 3.11) + API Gateway HTTP API with JWT auth
- **Auth Chain**: Cognito → IAM Identity Center (Trusted Token Issuer) → STS AssumeRole → Quick Sight
- **Infrastructure**: AWS CDK v2 (JavaScript): S3, CloudFront (OAC), DynamoDB, Lambda, API Gateway
- **Quick Sight**: Embedded dashboard + Quick chat AI assistant via `GenerateEmbedUrlForRegisteredUser`

## Features

The dashboard includes:

- 4 KPI cards with trend indicators (Revenue, Expenses, Net Profit, Profit Margin)
- 5 interactive charts (Revenue trends, Profit margin, Cash flow, Expense breakdown, Quarterly comparison)
- Embedded Quick Sight dashboard with full interactivity
- Quick chat AI assistant for natural language queries against dashboard data
- Single API call returns both dashboard and chat embed URLs (avoids JWT reuse errors)
- Mock data fallback for local development without AWS backend

## Quick Start

Deploy the backend first, then the embedding API, then the frontend.

### 1. Clone Repository (Sparse Checkout)

Clone only this use case's files:

```bash
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub.git
cd sample-amazon-quick-suite-knowledge-hub
git sparse-checkout set docs/use-cases/finance-dashboard-embedding
```

### 2. Deploy Infrastructure

Deploy the CDK stack from the `infrastructure` folder:

```bash
cd docs/use-cases/finance-dashboard-embedding/infrastructure
npm install
npx cdk bootstrap
npx cdk deploy
```

This deploys: DynamoDB table, metrics Lambda, API Gateway, S3 bucket, CloudFront distribution, and auto-seeds sample data.

### 3. Deploy Embedding API

The Quick chat embedding Lambda and API Gateway are deployed via a separate CDK stack. See the [Finance Agent Setup Guide](./quicksuite-setup/FINANCE_AGENT_SETUP_GUIDE.md) for full instructions on:

- Cognito User Pool creation
- IAM Identity Center configuration
- Trusted Token Issuer setup
- Embedding Lambda deployment

### 4. Configure and Deploy Frontend

Set the environment values, build the app, and upload it to the S3 bucket:

```bash
cd frontend
cp .env.example .env
# Edit .env with your API URLs and Cognito config
npm install
npm run build
aws s3 sync build/ s3://YOUR_BUCKET_NAME --delete
aws cloudfront create-invalidation --distribution-id YOUR_DIST_ID --paths "/*"
```

## Identity Chain Setup

The Quick Sight embedding requires an identity chain across Cognito, IAM Identity Center, and Quick Sight:

```text
Cognito User Pool ──(email claim)──► IAM Identity Center ──(group)──► QuickSight
```

### Adding Users

Each user must exist in Cognito, IAM Identity Center, and Quick Sight with the same email:

```bash
# 1. Cognito
aws cognito-idp admin-create-user \
  --user-pool-id YOUR_POOL_ID \
  --username "user@example.com" \
  --user-attributes Name=email,Value="user@example.com" Name=email_verified,Value=true \
  --temporary-password "TempPass123!"

# 2. IAM Identity Center
aws identitystore create-user \
  --identity-store-id YOUR_STORE_ID \
  --user-name "user@example.com" \
  --display-name "User Name" \
  --name '{"FamilyName":"Name","GivenName":"User"}' \
  --emails '[{"Value":"user@example.com","Type":"work","Primary":true}]'

# 3. Add to QuickSight group (auto-syncs to QuickSight)
aws identitystore create-group-membership \
  --identity-store-id YOUR_STORE_ID \
  --group-id YOUR_QS_GROUP_ID \
  --member-id '{"UserId":"USER_ID_FROM_STEP_2"}'
```

## Configuration

The frontend and the embedding Lambda each read their own environment variables.

### Frontend Environment Variables (`frontend/.env`)

| Variable | Description |
|----------|-------------|
| `REACT_APP_API_URL` | Metrics API Gateway URL |
| `REACT_APP_QUICKCHAT_API_ENDPOINT` | Embedding API Gateway URL |
| `REACT_APP_COGNITO_USER_POOL_ID` | Cognito User Pool ID |
| `REACT_APP_COGNITO_CLIENT_ID` | Cognito App Client ID |
| `REACT_APP_COGNITO_DOMAIN` | Cognito Hosted UI domain |
| `REACT_APP_QUICKSUITE_AGENT_ID` | Quick chat agent ID |
| `REACT_APP_QUICKSIGHT_DASHBOARD_ID` | Quick Sight Dashboard ID |

### Embedding Lambda Environment Variables

| Variable | Description |
|----------|-------------|
| `IDC_APP_CLIENT_ID` | IAM Identity Center application ARN |
| `QUICKSUITE_ROLE_ARN` | IAM role for Quick Sight API calls |
| `QUICKSIGHT_USER_ARN` | Quick Sight registered user ARN |
| `ALLOWED_DOMAINS` | Comma-separated allowed embedding domains |
| `DASHBOARD_ID` | Quick Sight dashboard ID |
| `AWS_ACCOUNT_ID` | AWS account ID |
| `COGNITO_ISSUER` | Cognito user pool issuer URL used to verify the incoming ID token's signature, for example `https://cognito-idp.<region>.amazonaws.com/<userPoolId>`. **Required**: the ID token is rejected when unset. |
| `COGNITO_APP_CLIENT_ID` | Cognito app client id the ID token's `aud`/`client_id` claim must match (audience check). |

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| CORS preflight 500 | API Gateway missing Lambda invoke permission | Add `lambda:InvokeFunction` resource policy |
| JWT is already redeemed | Same token used for multiple `CreateTokenWithIAM` calls | Use `embedType: 'both'` for single API call |
| User does not exist | Email mismatch across Cognito, IDC, and Quick Sight | Verify the email matches in Cognito, IDC, and Quick Sight |
| AccessDenied on AssumeRole | Wrong role ARN or missing `sts:SetContext` | Check Lambda env vars and IAM policies |

## Project Structure

The folder separates the frontend, the metrics and embedding APIs, and the CDK stack:

```text
finance-dashboard-embedding/
├── frontend/                  # React CRA app
│   ├── src/
│   │   ├── components/        # Dashboard, Charts, ChatPopup
│   │   ├── services/          # API layer with mock fallback
│   │   └── App.js             # Root component
│   └── .env.example           # Environment template
├── backend/
│   ├── lambda/                # Metrics API (Node.js)
│   │   ├── getMetrics.js      # GET /metrics handler
│   │   └── seedData.js        # DynamoDB seeder
│   └── lambda-tte/            # Embedding API (Python)
│       └── index.py           # Token exchange + embed URL generation
├── infrastructure/
│   └── lib/
│       └── finance-dashboard-stack.js  # CDK stack
└── quicksuite-setup/          # Setup guides and reference data
```

## License

This library is licensed under the MIT-0 License. See the LICENSE file for details.
