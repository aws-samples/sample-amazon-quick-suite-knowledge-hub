---
description: MCP backend for the Admin Onboarding Buddy skill. Twelve tools that set up AWS Quick action connectors and knowledge bases, on Amazon Bedrock AgentCore with one CloudFormation template.
tags: MCP, AgentCore, Onboarding
---

# AWS Quick Admin Buddy MCP

The backend for the **Admin Onboarding Buddy** skill for AWS Quick. It is a
Model Context Protocol (MCP) server that automates AWS Quick connector setup.
An administrator chats with the skill, and the skill calls this server's tools
to create and share AWS Quick integrations.

The server exposes twelve onboarding tools that create two kinds of AWS
Quick integrations:

- **Action connectors** ([`CreateActionConnector`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateActionConnector.html))
- **Knowledge bases** ([`CreateKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateKnowledgeBase.html))

Each integration uses either a Custom OAuth app or Service-to-Service OAuth.
Its auth parameters are supplied inline or read from AWS Secrets Manager. When
parameters are missing, the tools report them back together with the Secrets
Manager JSON contract, and they surface `Create*` API errors for
troubleshooting. Two further tools track the client-side AWS Quick
**extensions** (Office add-ins and the browser extension) with a status the
admin attests to, because extensions have no Quick admin API.

The server runs on **Amazon Bedrock AgentCore Runtime** behind an **AgentCore
Gateway** secured with Amazon Cognito (OAuth 2.0 authorization code). It ships
as a container image plus a single AWS CloudFormation template.

## Contents

1. [How it fits with the skill](#how-it-fits-with-the-skill)
1. [Architecture](#architecture)
1. [What gets created](#what-gets-created)
1. [The MCP tools](#the-mcp-tools)
1. [Prerequisites](#prerequisites)
1. [Get the code](#get-the-code)
1. [Step 1: Build and push the container image](#step-1-build-and-push-the-container-image)
1. [Step 2: Deploy the stack](#step-2-deploy-the-stack)
1. [Step 3: Connect the server to AWS Quick](#step-3-connect-the-server-to-aws-quick)
1. [Update the stack with a change set](#update-the-stack-with-a-change-set)
1. [Development](#development)
1. [Cleanup](#cleanup)
1. [Security](#security)
1. [Repository layout](#repository-layout)
1. [Contributing](#contributing)
1. [License](#license)

## How it fits with the skill

This project is the backend of the official **Quick Admin Onboarding Buddy**
skill (`admin-onboarding-buddy`), published in the Amazon Quick official skill
catalog under
[Amazon-Quick-official-catalog/skills/amazon-quick](https://github.com/Amazon-Quick/Amazon-Quick-official-catalog/tree/main/skills/amazon-quick).
The skill reaches it through the **Admin Onboarding Buddy Endpoint** MCP action
connector, and it can't apply any change in AWS Quick without this backend
deployed.

The skill walks an AWS Quick administrator through onboarding: choosing the
productivity suite (Microsoft 365 or Google Workspace), enabling action
connectors and knowledge bases, sharing them with users, and tracking
extensions. The skill holds the conversation and the step-by-step guidance.
This backend makes the actual AWS Quick admin API calls.

The skill bundles a copy of
[`infrastructure/template.yaml`](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/infrastructure/aws-quick-admin-buddy-mcp/infrastructure/template.yaml)
(as `assets/template.yaml`), and this folder is the source for the container
image that the template deploys. To use the skill, deploy this backend,
register it in AWS Quick as an MCP action connector (Step 3), and then install
the skill from the official catalog.

## Architecture

![AWS Quick Admin Buddy MCP architecture. On the left, the Quick Admin Onboarding Buddy skill runs in Quick Desktop. (1) It calls the Admin Onboarding Buddy Endpoint, a custom MCP action connector in AWS Quick. (2) The connector forwards the call to an Amazon Bedrock AgentCore Gateway, which passes it to the AgentCore Runtime container. (3) The runtime retrieves configuration and auth secrets from AWS Secrets Manager. (4) It calls the AWS Quick API, whose create operations add action connectors and knowledge bases to AWS Quick, connected to the Microsoft (Outlook, SharePoint, OneDrive, OneNote) or Google (Gmail, Docs, Calendar, Meet, Slides, Sheets, Chat) productivity suite. (5) The runtime persists each status in the OnboardingState Amazon DynamoDB table. Everything except Quick Desktop and the productivity suite runs in your AWS account.](images/quick-admin-buddy-architecture.png)

The numbers match the diagram:

1. **Skill to connector.** The Admin Onboarding Buddy skill runs in Quick
   Desktop and calls the backend's tools through the **Admin Onboarding Buddy
   Endpoint**, a custom MCP action connector in AWS Quick. The connector signs
   the admin in through the Amazon Cognito hosted UI (OAuth 2.0 authorization
   code) and sends the access token with every call.
1. **Connector to AgentCore.** The **AgentCore Gateway** validates the token
   with its `CUSTOM_JWT` authorizer (Cognito discovery URL and allowed app
   client) and rejects requests without a valid token. It calls the
   **AgentCore Runtime** container with SigV4, using its own IAM role, so the
   Runtime can only be invoked with IAM credentials.
1. **Secrets.** The MCP server reads the integration's configuration and auth
   parameters from **AWS Secrets Manager** when the admin passes a secret name.
1. **AWS Quick API.** The server calls the AWS Quick admin APIs as the
   least-privilege `Agent_IAM_Identity` role to create, share, and delete
   **action connectors** and **knowledge bases** for the Microsoft 365 or
   Google Workspace productivity suite.
1. **State.** The server records each integration's status in the
   **OnboardingState** Amazon DynamoDB table, so the skill can resume and
   summarize the onboarding.

The end-user identity is **not** propagated to the backend. The deployment
serves one shared tenant. See
[Authentication & Authorization Model](docs/auth-model.md) for details.

## What gets created

One CloudFormation stack (`infrastructure/template.yaml`) creates:

- **AgentCore Runtime** that hosts the MCP server container (stateless
  streamable HTTP, port 8000, `linux/arm64`).
- **AgentCore Gateway** and gateway target in front of the Runtime, with a
  Cognito `CUSTOM_JWT` inbound authorizer.
- **Amazon Cognito** user pool (admin-created users only), resource server
  (`quick-admin-buddy/invoke` scope), hosted-UI domain, and confidential app
  client whose callback is the AWS Quick OAuth callback. Custom resources
  store the client secret and a generated sign-in user's credentials in
  Secrets Manager.
- **IAM roles**: `Agent_IAM_Identity` for the Runtime's outbound calls (limited
  to the Quick action-connector, knowledge-base, and data-source actions the
  server calls in this account, plus a read-only `DescribeUser` lookup), the
  gateway execution role, and roles for the custom-resource Lambda functions.
- **DynamoDB** `OnboardingState` table (retained on delete).
- **AWS KMS** customer-managed key with rotation enabled (retained on delete).
  It encrypts the table, the stack's secrets, and the log groups.
- **CloudWatch Logs** delivery for the Runtime, Gateway, and workload-identity
  logs (log groups retained on delete).
- A **"Quick Setup Buddy" AWS Quick agent**, optionally shared with the
  principals you pass in `QuickSetupBuddySharePrincipals`.

The AWS Quick MCP action connector itself is **not** created by the stack,
because custom MCP connectors can't be created with CloudFormation. You create
it by hand in Step 3.

## The MCP tools

| Tool | Purpose |
| --- | --- |
| `list_connector_catalog` | List the supported integrations and the configuration each one needs |
| `set_productivity_suite` | Record the tenant's productivity suite (Microsoft 365 or Google Workspace), which gates the catalog |
| `enable_action_connector` | Create an AWS Quick action connector |
| `disable_action_connector` | Delete an action connector the server created |
| `enable_kb_integration` | Create an AWS Quick knowledge base (and its data source) |
| `disable_kb_integration` | Delete a knowledge base the server created |
| `share_action_connector` | Share an action connector with AWS Quick users or groups |
| `share_kb_integration` | Share a knowledge base with AWS Quick users or groups |
| `get_tenant_configuration` | Return the tenant's recorded configuration |
| `get_onboarding_summary` | Summarize onboarding progress across connectors, knowledge bases, and extensions |
| `list_extensions` | List the AWS Quick extensions and their attested status |
| `set_extension_status` | Record the admin-attested status of an extension |

Inputs, outputs, every `Result_Status` value, and the connector catalog are in
the [Tool Reference](docs/tool-reference.md).

## Prerequisites

- **AWS CLI v2** with credentials for the target account and Region, and
  permission to create the resources listed above (CloudFormation, IAM,
  Cognito, DynamoDB, KMS, Amazon Bedrock AgentCore, Lambda, Secrets Manager,
  CloudWatch Logs, Amazon ECR, and AWS Quick agents).
- **Docker** with `buildx`, to build a `linux/arm64` image.
- An **AWS Quick** account where you are an administrator and can add MCP
  action connectors.
- Amazon Bedrock AgentCore available in the target Region.
- **Python 3.10 or later**, only to run the tests locally.

## Get the code

```bash
git clone https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub.git
cd sample-amazon-quick-suite-knowledge-hub/infrastructure/aws-quick-admin-buddy-mcp
```

Run every command below from this folder.

## Step 1: Build and push the container image

AgentCore Runtime pulls the server image from Amazon ECR and runs on AWS
Graviton, so build for `linux/arm64`. A plain `docker build` on an x86_64 host
produces an amd64 image that the Runtime can't run.

```bash
export AWS_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPO=quick-admin-buddy-mcp
export IMAGE_TAG=0.1.0
export IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

# 1. Create the ECR repository (first time only)
aws ecr create-repository \
  --repository-name "${ECR_REPO}" \
  --region "${AWS_REGION}" \
  --image-scanning-configuration scanOnPush=true

# 2. Authenticate Docker to ECR
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin \
      "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# 3. Build for the AgentCore Runtime architecture and push
docker buildx build --platform linux/arm64 -t "${IMAGE_URI}" --load .
docker push "${IMAGE_URI}"
```

The image is built from the project
[`Dockerfile`](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/infrastructure/aws-quick-admin-buddy-mcp/Dockerfile).
It targets Python 3.10 and runs the server on port 8000 inside the container.
You don't need to run the container locally. If you do, publish the port to
loopback only (`docker run -p 127.0.0.1:8000:8000 ...`), because the server has
no authentication of its own; the Gateway provides it in AWS.

## Step 2: Deploy the stack

The template is larger than CloudFormation's 51,200-byte inline limit, so
`aws cloudformation deploy` must stage it in an S3 bucket with `--s3-bucket`.

```bash
# 1. Reuse a bucket for CloudFormation templates, or create one (first time
#    only; bucket names are globally unique)
export TEMPLATE_BUCKET="cfn-deploy-${AWS_REGION}-${ACCOUNT_ID}"
aws s3 mb "s3://${TEMPLATE_BUCKET}" --region "${AWS_REGION}"

# 2. Deploy. --template-file is the local path; the CLI uploads it to the
#    bucket and prefix given by --s3-bucket and --s3-prefix.
aws cloudformation deploy \
  --region "${AWS_REGION}" \
  --stack-name quick-admin-buddy-mcp \
  --template-file infrastructure/template.yaml \
  --s3-bucket "${TEMPLATE_BUCKET}" \
  --s3-prefix quick-admin-buddy-mcp \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      ContainerImageUri="${IMAGE_URI}" \
      CognitoDomainPrefix="${ACCOUNT_ID}-quickonboarding" \
      QuickSetupBuddySharePrincipals="arn:aws:quicksight:${AWS_REGION}:${ACCOUNT_ID}:user/default/<quick-user-name>"
```

- `CognitoDomainPrefix` (required) is a globally unique, lowercase prefix for
  the Cognito OAuth 2.0 endpoints.
- `QuickSetupBuddySharePrincipals` (optional) is a comma-separated list of
  AWS Quick user, group, or namespace ARNs to share the "Quick Setup Buddy"
  agent with. Leave it out to keep the agent visible only to its creator.

The [Deployment Guide](docs/deployment-guide.md) lists every parameter and
output.

## Step 3: Connect the server to AWS Quick

Create an **MCP action connector** in the AWS Quick console that points at
the Gateway. Read the values from the stack outputs:

```bash
aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name quick-admin-buddy-mcp \
  --query "Stacks[0].Outputs"
```

| Connector field | Stack output |
| --- | --- |
| Name | `ActionConnectorSuggestedName` |
| Type | `ActionConnectorType` (`MODEL_CONTEXT_PROTOCOL`) |
| MCP server endpoint | `ActionConnectorBaseEndpoint` (the Gateway URL as is; it already ends in `/mcp`) |
| Authentication | `ActionConnectorAuthType` (`OAUTH2_AUTHORIZATION_CODE`) |
| Client ID | `CognitoUserPoolClientId` |
| Client secret | Read from the Secrets Manager secret in `CognitoClientSecretArn` |
| Authorization URL | `CognitoAuthorizationEndpoint` |
| Token URL | `CognitoTokenEndpoint` |
| Redirect URL | `ActionConnectorRedirectUrl` |

When AWS Quick asks you to sign in, use the Cognito user in
`CognitoUsername`. Its password is in the Secrets Manager secret in
`CognitoUserCredentialsSecretArn`. The
[Deployment Guide](docs/deployment-guide.md) has the full connector steps, the
commands to read both secrets, and how to give other AWS Quick users access
to the connector.

After the connector is created, install the Admin Onboarding Buddy skill in
AWS Quick and start the onboarding conversation.

## Update the stack with a change set

To ship a new build, push a new `IMAGE_TAG` and re-run the Step 2 command with
the new `ContainerImageUri`. After a container update, refresh the Gateway's
tool list as described in the
[Deployment Guide](docs/deployment-guide.md).

Use an explicit change set instead when you want to review what will change
(replacements, deletions, IAM changes) before anything is applied:

```bash
export STACK_NAME=quick-admin-buddy-mcp
export CHANGE_SET_NAME="update-$(date +%Y%m%d%H%M%S)"

# 0. The raw create-change-set API caps an inline template at 51,200 bytes, so
#    upload the template to S3 and reference it with --template-url.
aws s3 cp infrastructure/template.yaml \
  "s3://${TEMPLATE_BUCKET}/quick-admin-buddy-mcp/template.yaml" \
  --region "${AWS_REGION}"
export TEMPLATE_URL="https://${TEMPLATE_BUCKET}.s3.${AWS_REGION}.amazonaws.com/quick-admin-buddy-mcp/template.yaml"

# 1. Create the change set (does not modify the stack)
aws cloudformation create-change-set \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --change-set-name "${CHANGE_SET_NAME}" \
  --change-set-type UPDATE \
  --template-url "${TEMPLATE_URL}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
      ParameterKey=ContainerImageUri,ParameterValue="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/quick-admin-buddy-mcp:NEW_TAG" \
      ParameterKey=CognitoDomainPrefix,UsePreviousValue=true \
      ParameterKey=OnboardingStateTableName,UsePreviousValue=true \
      ParameterKey=ActionConnectorName,UsePreviousValue=true \
      ParameterKey=TenantId,UsePreviousValue=true \
      ParameterKey=QuickSetupBuddyAgentId,UsePreviousValue=true \
      ParameterKey=QuickSetupBuddySharePrincipals,UsePreviousValue=true

# 2. Wait for the diff, then review it
aws cloudformation wait change-set-create-complete \
  --region "${AWS_REGION}" --stack-name "${STACK_NAME}" --change-set-name "${CHANGE_SET_NAME}"
aws cloudformation describe-change-set \
  --region "${AWS_REGION}" --stack-name "${STACK_NAME}" --change-set-name "${CHANGE_SET_NAME}"

# 3. Execute it once the diff looks correct, and wait for the update
aws cloudformation execute-change-set \
  --region "${AWS_REGION}" --stack-name "${STACK_NAME}" --change-set-name "${CHANGE_SET_NAME}"
aws cloudformation wait stack-update-complete \
  --region "${AWS_REGION}" --stack-name "${STACK_NAME}"
```

- Keep `UsePreviousValue=true` for every parameter you are not changing.
  Otherwise CloudFormation resets it to the template default.
- If the change set reports `didn't contain changes`, there is nothing to
  update. Delete it with `delete-change-set`.
- Don't execute a change set that shows `Replacement: True` on a stateful
  resource such as the DynamoDB table or the Cognito user pool. Replacement
  deletes and recreates the resource.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
cfn-lint infrastructure/template.yaml
```

The tests are unit tests plus Hypothesis property-based tests. They run at a
minimum of 100 Hypothesis examples through the `quick_admin_buddy` profile in
`tests/conftest.py`. DynamoDB tests use `moto`, so no AWS account is needed.

## Cleanup

```bash
aws cloudformation delete-stack --region "${AWS_REGION}" --stack-name quick-admin-buddy-mcp
```

Some resources are kept or live outside the stack, and you remove them
yourself:

- The AWS Quick MCP action connector you created in Step 3.
- Any action connectors and knowledge bases the server created in AWS Quick.
  Disable them with the `disable_*` tools before teardown, or delete them in
  the console.
- The DynamoDB table, the KMS key, and the CloudWatch log groups, which are
  retained (`DeletionPolicy: Retain`).
- The ECR repository and the template bucket.

The [Deployment Guide](docs/deployment-guide.md) lists the retained resources
in detail and explains how to redeploy into the same account afterwards.

## Security

- **Inbound access** requires a valid Cognito access token, checked by the
  Gateway's `CUSTOM_JWT` authorizer. The Cognito user pool only allows users
  that an administrator creates.
- **Gateway to Runtime** traffic is signed with SigV4. The Runtime has no
  unauthenticated entry point, and no container port is published.
- **Outbound calls** use the least-privilege `Agent_IAM_Identity` role, scoped
  to this account.
- **Secrets** (the Cognito client secret, the sign-in user's password, and the
  integration credentials) are kept in Secrets Manager and encrypted with the
  stack's KMS key. Stack outputs expose secret ARNs, never secret values. No
  credentials are stored in this repository.
- **Single tenant.** The caller's identity is not passed to the backend, so
  everyone who can use the connector acts on the same AWS Quick account.
  Share the connector only with the administrators who should onboard it.
- This sample uses Amazon Cognito so that it works out of the box. In your own
  deployment you can point the Gateway's `CUSTOM_JWT` authorizer at the
  OIDC identity provider your organization already uses.

See [Authentication & Authorization Model](docs/auth-model.md) for the full
model.

## Repository layout

```text
aws-quick-admin-buddy-mcp/
├── src/quick_admin_buddy_mcp/
│   ├── server.py              # MCP entry point: the 12 tools, streamable HTTP
│   ├── domain/                # Catalog, suite gating, validation, Tool_Result
│   ├── quick/                 # AWS Quick admin API client (boto3), Secrets Manager
│   └── state/                 # Onboarding state persistence (in memory and DynamoDB)
├── infrastructure/
│   └── template.yaml          # Single CloudFormation template for the whole backend
├── docs/
│   ├── tool-reference.md      # Tool inputs, outputs, Result_Status values, catalogs
│   ├── deployment-guide.md    # Parameters, outputs, connector setup, updates, teardown
│   └── auth-model.md          # Inbound, gateway-to-runtime, and outbound auth
├── tests/                     # Unit and Hypothesis property-based tests
├── images/                    # Architecture diagram used in this README
├── Dockerfile                 # linux/arm64 image for AgentCore Runtime
└── pyproject.toml             # Package metadata, dependencies, pytest and ruff config
```

> License, contribution, and code-of-conduct files live at the **repository
> root**, not inside this project.

## Contributing

See [How to Contribute](/HOW-TO-CONTRIBUTE.md).

## License

This library is licensed under the MIT-0 License. See the [LICENSE](/LICENSE)
file.
