---
category: Financial Services
description: "Finance dashboard with embedded QuickSight analytics and QuickChat AI assistant using Cognito + IAM Identity Center authentication"
---

# Finance Dashboard with QuickSight & QuickChat Embedding

A web-based financial analytics dashboard for AnyCompany that embeds Amazon QuickSight dashboards and QuickChat AI assistant into a React application, using Cognito and IAM Identity Center for authentication.

This solution demonstrates how to build a custom branded analytics experience that combines your own React UI with embedded QuickSight visuals and conversational AI — all secured through a Cognito → IDC → QuickSight identity chain.

## Architecture

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

- **Frontend**: React 18 (CRA) with Chart.js visualizations and QuickSight Embedding SDK
- **Metrics API**: Lambda (Node.js 18) + DynamoDB + API Gateway REST
- **Embedding API**: Lambda (Python 3.11) + API Gateway HTTP API with JWT auth
- **Auth Chain**: Cognito → IAM Identity Center (Trusted Token Issuer) → STS AssumeRole → QuickSight
- **Infrastructure**: AWS CDK v2 (JavaScript) — S3, CloudFront (OAC), DynamoDB, Lambda, API Gateway
- **QuickSight**: Embedded dashboard + QuickChat AI assistant via `GenerateEmbedUrlForRegisteredUser`

## Features

- 4 KPI cards with trend indicators (Revenue, Expenses, Net Profit, Profit Margin)
- 5 interactive charts (Revenue trends, Profit margin, Cash flow, Expense breakdown, Quarterly comparison)
- Embedded QuickSight dashboard with full interactivity
- QuickChat AI assistant for natural language queries against dashboard data
- Single API call returns both dashboard and chat embed URLs (avoids JWT reuse errors)
- Mock data fallback for local development without AWS backend

## Quick Start

### 1. Clone Repository (Sparse Checkout)

```bash
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub.git
cd sample-amazon-quick-suite-knowledge-hub
git sparse-checkout set docs/use-cases/finance-dashboard-embedding
```

### 2. Deploy Infrastructure

```bash
cd docs/use-cases/finance-dashboard-embedding/infrastructure
npm install
npx cdk bootstrap
npx cdk deploy
```

This deploys: DynamoDB table, metrics Lambda, API Gateway, S3 bucket, CloudFront distribution, and auto-seeds sample data.

### 3. Deploy Embedding API

The QuickChat embedding Lambda and API Gateway are deployed via a separate CDK stack. See the [Embedding Setup Guide](./EMBEDDING_SETUP.md) for full instructions on:

- Cognito User Pool creation
- IAM Identity Center configuration
- Trusted Token Issuer setup
- Embedding Lambda deployment

### 4. Configure and Deploy Frontend

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

The QuickSight embedding requires a 3-service identity chain:

```text
Cognito User Pool ──(email claim)──► IAM Identity Center ──(group)──► QuickSight
```

### Adding Users

Each user must exist in all three services with the same email:

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

### Frontend Environment Variables (`frontend/.env`)

| Variable | Description |
|----------|-------------|
| `REACT_APP_API_URL` | Metrics API Gateway URL |
| `REACT_APP_QUICKCHAT_API_ENDPOINT` | Embedding API Gateway URL |
| `REACT_APP_COGNITO_USER_POOL_ID` | Cognito User Pool ID |
| `REACT_APP_COGNITO_CLIENT_ID` | Cognito App Client ID |
| `REACT_APP_COGNITO_DOMAIN` | Cognito Hosted UI domain |
| `REACT_APP_QUICKSUITE_AGENT_ID` | QuickChat Agent ID |
| `REACT_APP_QUICKSIGHT_DASHBOARD_ID` | QuickSight Dashboard ID |

### Embedding Lambda Environment Variables

| Variable | Description |
|----------|-------------|
| `IDC_APP_CLIENT_ID` | IAM Identity Center application ARN |
| `QUICKSUITE_ROLE_ARN` | IAM role for QuickSight API calls |
| `QUICKSIGHT_USER_ARN` | QuickSight registered user ARN |
| `ALLOWED_DOMAINS` | Comma-separated allowed embedding domains |
| `DASHBOARD_ID` | QuickSight dashboard ID |
| `AWS_ACCOUNT_ID` | AWS account ID |

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| CORS preflight 500 | API Gateway missing Lambda invoke permission | Add `lambda:InvokeFunction` resource policy |
| JWT is already redeemed | Same token used for multiple `CreateTokenWithIAM` calls | Use `embedType: 'both'` for single API call |
| User does not exist | Email mismatch across Cognito/IDC/QuickSight | Verify email matches in all three services |
| AccessDenied on AssumeRole | Wrong role ARN or missing `sts:SetContext` | Check Lambda env vars and IAM policies |

## Project Structure

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
