# Deployment Guide

This guide deploys the Quick Admin Buddy MCP server and its supporting AWS
resources with a single AWS CloudFormation template
(`infrastructure/template.yaml`).

The template provisions the full solution together:

- **AgentCore Runtime** hosting the MCP server container (stateless
  streamable-HTTP on port 8000).
- **AgentCore Gateway** fronting the Runtime, with a Cognito CUSTOM_JWT inbound
  authorizer that gates access (a valid Cognito user token is required to call
  the server). The gateway signs the hop to the Runtime with its own IAM role,
  so the end-user identity is not propagated to the backend.
- **Cognito user pool** (the inbound authorizer / `Cognito_Authorizer`).
- **`Agent_IAM_Identity`** — the least-privilege IAM role assumed by the Runtime
  for outbound calls, limited to the Quick action-connector, knowledge-base, and
  data-source actions the server calls (restricted to this account), a
  read-only `DescribeUser` lookup on this account's Quick users, read access to
  the integration auth secrets, and use of the stack's KMS key.
- **DynamoDB `OnboardingState` table** — durable onboarding state for the single
  shared tenant (this deployment does not isolate state per user).
- **KMS customer-managed key** (rotation enabled) — encrypts the DynamoDB table,
  the stack's Secrets Manager secrets, and the AgentCore CloudWatch log groups
  at rest. The key policy delegates to IAM, so each role that needs the key is
  granted use of it explicitly; a second statement lets the CloudWatch Logs
  service use it, limited to this account's `/aws/vendedlogs/bedrock-agentcore/*`
  log groups.
- **Cognito user pool domain + app client** — the OAuth2 endpoints and
  confidential client used when creating the AWS Quick action connector.
- **Client-secret custom resource + Secrets Manager secret** — reads the Cognito
  app client secret (not exposed by CloudFormation) and stores it securely.
- **Cognito user custom resource + Secrets Manager secret** — creates the
  `quickadminbuddy` user with a generated permanent password (for sign-in during
  the OAuth2 authorization code flow) and stores the credentials securely.
- **CloudWatch log delivery** — delivery sources/destinations/deliveries that
  route the Runtime application and usage logs, the Gateway application logs,
  and the workload-identity (identity) application logs to retained
  CloudWatch log groups.

The AWS Quick MCP action connector is **not** created by this stack. Custom
MCP connectors are not supported for automated (CloudFormation) creation, so the
connector must be created **manually** in AWS Quick using the values the
stack exposes as outputs. See [Step 3](#step-3-create-the-action-connector-manually-in-aws-quick)
below and [auth-model.md](./auth-model.md) for how the pieces fit together.

## Prerequisites

- AWS CLI v2 configured with credentials for the target account and region, with
  permission to create the resources above (CloudFormation, IAM, Cognito,
  DynamoDB, KMS, Amazon Bedrock AgentCore, Lambda, and Secrets Manager).
- Access to the AWS Quick console to create the action connector manually
  after the stack deploys.
- Docker, to build and push the MCP server container image.
- An Amazon ECR repository (or another registry the AgentCore Runtime can pull
  from) in the target account/region.
- The AgentCore service enabled in the target region.

## Step 1: Build and push the container image

The server is packaged by the project's `Dockerfile` (Python 3.10, listens on
port 8000, runs the `quick-admin-buddy-mcp` entry point).

```bash
# From this project's folder (infrastructure/aws-quick-admin-buddy-mcp)
AWS_REGION=eu-west-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REPO=quick-admin-buddy-mcp
IMAGE_TAG=0.1.0
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

# Create the ECR repository (first time only)
aws ecr create-repository --repository-name "${ECR_REPO}" --region "${AWS_REGION}"

# Authenticate Docker to ECR
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Build for the AgentCore Runtime architecture (linux/arm64) and push.
# A plain `docker build` on an x86_64 host produces an amd64 image, which the
# Runtime cannot run.
docker buildx build --platform linux/arm64 -t "${IMAGE_URI}" --load .
docker push "${IMAGE_URI}"
```

Note the resulting `IMAGE_URI`; it is passed to the stack as a parameter.

## Step 2: Deploy the stack

```bash
# The template is larger than CloudFormation's 51,200-byte inline limit, so it
# must be staged in S3. Reuse a bucket or create one (first time only; bucket
# names are globally unique).
export TEMPLATE_BUCKET="cfn-deploy-${AWS_REGION}-${ACCOUNT_ID}"
aws s3 mb "s3://${TEMPLATE_BUCKET}" --region "${AWS_REGION}"

aws cloudformation deploy \
  --region "${AWS_REGION}" \
  --stack-name quick-admin-buddy-mcp \
  --template-file infrastructure/template.yaml \
  --s3-bucket "${TEMPLATE_BUCKET}" \
  --s3-prefix quick-admin-buddy-mcp \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      ContainerImageUri="${IMAGE_URI}" \
      OnboardingStateTableName=OnboardingState \
      TenantId=shared \
      CognitoDomainPrefix="${ACCOUNT_ID}-quickonboarding" \
      QuickSetupBuddySharePrincipals="arn:aws:quicksight:${AWS_REGION}:${ACCOUNT_ID}:user/default/<quick-user>"
```

`--capabilities CAPABILITY_NAMED_IAM` is required because the template creates
named IAM roles (the `Agent_IAM_Identity` role, the gateway execution role, and
the client-secret reader Lambda role). `aws cloudformation deploy` creates the
stack on first run and applies a change set on subsequent runs.

## Stack parameters

| Parameter | Required | Meaning |
|---|---|---|
| `ContainerImageUri` | required | The full image URI (from Step 1) the AgentCore Runtime pulls and runs. |
| `CognitoDomainPrefix` | required | Globally-unique domain prefix for the Cognito hosted UI / OAuth2 endpoints. Forms the authorization and token endpoints (`https://<prefix>.auth.<region>.amazoncognito.com/oauth2/...`) used by the QuickSight action connector. Must be unique across all AWS accounts in the region; lowercase letters, numbers, and hyphens, 3-63 chars. |
| `OnboardingStateTableName` | optional | Name of the DynamoDB table for onboarding state. Must match the server's `ONBOARDING_STATE_TABLE` environment variable (default `OnboardingState`). |
| `TenantId` | optional | The single shared tenant every authenticated caller maps to (default `shared`). This deployment does not propagate per-user identity to the backend; all callers share this tenant's state. Passed to the Runtime as `ONBOARDING_TENANT`. |
| `ActionConnectorName` | optional | Suggested name to use when creating the action connector manually in AWS Quick. Surfaced as the `ActionConnectorSuggestedName` output. |
| `QuickSetupBuddyAgentId` | optional | Id of the "Quick Setup Buddy" AWS Quick agent the stack creates (`AWS::QuickSight::Agent`). Default `quick-setup-buddy`. The id is create-only (changing it replaces the agent), so keep it stable across updates and override it only to deploy a second copy in the same account and region. |
| `QuickSetupBuddySharePrincipals` | optional | Comma-separated Quick user, group, or namespace ARNs to share the "Quick Setup Buddy" agent with (empty shares it with no one but its creator). |

The server reads a small set of environment variables the template wires into
the Runtime:

- `ONBOARDING_STATE_TABLE` — the DynamoDB table name (matches
  `OnboardingStateTableName`).
- `ONBOARDING_TENANT` — the single shared tenant id (matches `TenantId`).

## Stack outputs

After a successful deploy, the stack exposes the identifiers needed to invoke
the server and to configure Cognito clients:

The outputs fall into two groups: identifiers for invoking the server, and the
values needed to create the AWS Quick action connector manually (Step 3).

| Output | Meaning |
|---|---|
| `AgentCoreGatewayEndpoint` | The HTTPS gateway URL the MCP client/skill calls to reach the MCP server. |
| `AgentCoreGatewayArn` | ARN of the AgentCore Gateway. |
| `CognitoUserPoolId` | The user pool the gateway trusts for inbound JWT validation. |
| `ActionConnectorSuggestedName` | Suggested connector name for the manual setup. |
| `ActionConnectorType` | Connector type to select: `MODEL_CONTEXT_PROTOCOL`. |
| `ActionConnectorAuthType` | Auth type to select: `OAUTH2_AUTHORIZATION_CODE`. |
| `ActionConnectorBaseEndpoint` | BaseEndpoint for the connector: the gateway MCP URL, used as-is (it already ends in `/mcp`; appending another `/mcp` makes the gateway return HTTP 400). |
| `ActionConnectorRedirectUrl` | OAuth callback URL (already registered on the Cognito app client). |
| `CognitoUserPoolClientId` | OAuth2 `ClientId` for the connector. |
| `CognitoAuthorizationEndpoint` | OAuth2 `AuthorizationEndpoint`. |
| `CognitoTokenEndpoint` | OAuth2 `TokenEndpoint`. |
| `CognitoClientSecretArn` | Secrets Manager ARN holding the app client secret (retrieve the value for the connector). |
| `CognitoUsername` | Username of the Cognito sign-in user (`quickadminbuddy`). |
| `CognitoUserCredentialsSecretArn` | Secrets Manager ARN holding the user credentials as JSON `{"username","password"}`. |

Retrieve them with:

```bash
aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name quick-admin-buddy-mcp \
  --query "Stacks[0].Outputs" --output table
```

## Step 3: Create the action connector manually in AWS Quick

The AWS Quick MCP custom connector cannot be created by CloudFormation, so
create it by hand using the Step 2 outputs. In the AWS Quick console, add a
new action connector with:

| Connector field | Value (from stack output) |
|---|---|
| Name | `ActionConnectorSuggestedName` |
| Type | `ActionConnectorType` (`MODEL_CONTEXT_PROTOCOL`) |
| Authentication type | `ActionConnectorAuthType` (`OAUTH2_AUTHORIZATION_CODE`) |
| Base endpoint | `ActionConnectorBaseEndpoint` (the gateway URL as-is; it already ends in `/mcp`) |
| Redirect / callback URL | `ActionConnectorRedirectUrl` |
| Authorization endpoint | `CognitoAuthorizationEndpoint` |
| Token endpoint | `CognitoTokenEndpoint` |
| Client ID | `CognitoUserPoolClientId` |
| Client secret | value stored in `CognitoClientSecretArn` (see below) |

Retrieve the client secret from Secrets Manager when prompted for it:

```bash
aws secretsmanager get-secret-value \
  --region "${AWS_REGION}" \
  --secret-id "$(aws cloudformation describe-stacks --region "${AWS_REGION}" \
      --stack-name quick-admin-buddy-mcp \
      --query "Stacks[0].Outputs[?OutputKey=='CognitoClientSecretArn'].OutputValue" \
      --output text)" \
  --query SecretString --output text
```

The `ActionConnectorRedirectUrl` is already registered as an allowed callback on
the Cognito app client, so QuickSight can complete the authorization code
exchange without further Cognito changes.

This is a **user-delegated** (interactive) OAuth2 flow: an admin signs in through
Cognito's hosted UI, and QuickSight exchanges the resulting code for an access
token it presents to the gateway. See [auth-model.md](./auth-model.md) for the
full flow.

### Sign-in user for the OAuth flow

The stack provisions a Cognito user (`quickadminbuddy`, output `CognitoUsername`)
with a generated permanent password, so an admin can complete the hosted-UI
sign-in during the authorization code flow. The credentials are stored as JSON
in Secrets Manager (output `CognitoUserCredentialsSecretArn`); the password is
never exposed through stack outputs or events. Retrieve them with:

```bash
aws secretsmanager get-secret-value \
  --region "${AWS_REGION}" \
  --secret-id "$(aws cloudformation describe-stacks --region "${AWS_REGION}" \
      --stack-name quick-admin-buddy-mcp \
      --query "Stacks[0].Outputs[?OutputKey=='CognitoUserCredentialsSecretArn'].OutputValue" \
      --output text)" \
  --query SecretString --output text
```

The app client permits both the hosted-UI authorization code flow and direct
`USER_PASSWORD_AUTH` (`ALLOW_USER_PASSWORD_AUTH`), so this user can also be used
to script a token for testing. Because the app client is confidential
(`GenerateSecret: true`), a scripted `InitiateAuth` call must include the
computed `SECRET_HASH`.

### Granting other Quick users access to the connector

Connector resource permissions are managed in AWS Quick after creation (also
not automated here). Grant a Quick user the see/edit/manage permission set on the
connector from the console or with the QuickSight `update-action-connector-permissions`
API, using the user's Quick user ARN as the principal.

## Updating the deployment

To ship a new server build, push a new image tag (Step 1) and re-run the same
`aws cloudformation deploy` command with the updated `ContainerImageUri`.
CloudFormation computes and applies the change set.

### Refresh the gateway tool list after a container update (required)

**After every container update you must re-synchronize the gateway target**, or
the gateway will keep serving the tool list it cached from the *previous*
container — new, renamed, or removed tools will not appear.

Why this is needed: the `AgentCoreGatewayTarget` uses an MCP server target in
the default listing mode, which discovers the backend's tools **once, at sync
time, and caches them**. Deploying a new `ContainerImageUri` creates a new
AgentCore Runtime version (the `DEFAULT` endpoint auto-retargets to it, so
traffic reaches the new container), but the gateway target's `Endpoint`
references the Runtime by ID against the `DEFAULT` qualifier — it does not
change — so CloudFormation sees no change to the target and never re-triggers
tool discovery. The cached list goes stale until you sync explicitly.

Run `SynchronizeGatewayTargets` after the stack update completes:

```bash
# Resolve the gateway id and target id from the stack
GATEWAY_ID=$(aws cloudformation describe-stack-resources \
  --region "${AWS_REGION}" --stack-name quick-admin-buddy-mcp \
  --query "StackResources[?LogicalResourceId=='AgentCoreGateway'].PhysicalResourceId" \
  --output text)

# The GatewayTarget physical id is "<gatewayId>|<targetId>"; take the part after the pipe
TARGET_ID=$(aws cloudformation describe-stack-resources \
  --region "${AWS_REGION}" --stack-name quick-admin-buddy-mcp \
  --query "StackResources[?LogicalResourceId=='AgentCoreGatewayTarget'].PhysicalResourceId" \
  --output text | cut -d'|' -f2)

# Force the gateway to re-discover tools from the new container
aws bedrock-agentcore-control synchronize-gateway-targets \
  --region "${AWS_REGION}" \
  --gateway-identifier "${GATEWAY_ID}" \
  --target-id-list "${TARGET_ID}"

# Confirm the sync completed (status READY and a fresh lastSynchronizedAt)
aws bedrock-agentcore-control get-gateway-target \
  --region "${AWS_REGION}" \
  --gateway-identifier "${GATEWAY_ID}" \
  --target-id "${TARGET_ID}" \
  --query "{status:status,lastSynchronizedAt:lastSynchronizedAt}" --output json
```

The target briefly reports `SYNCHRONIZING`, then returns to `READY` with an
updated `lastSynchronizedAt`. The refreshed tool list is served immediately; no
change to the AWS Quick action connector is needed. (If you ever prefer the
gateway to discover tools live on every request instead of caching, recreate the
target with its listing mode set to `DYNAMIC` — at the cost of per-request
discovery latency and tools not appearing in `tools/list` until invoked.)

## Teardown

```bash
aws cloudformation delete-stack --region "${AWS_REGION}" --stack-name quick-admin-buddy-mcp
```

Deleting the stack removes the AgentCore resources, the Cognito user pool and
domain (the `quickadminbuddy` user is deleted by its custom resource on stack
delete), the IAM roles, the client-secret and user-credentials Lambdas with
their Secrets Manager secrets, and the **"Quick Setup Buddy" AWS Quick
agent** the stack created (any sharing granted to it is lost with it).

The following are **not** removed automatically:

- **The AWS Quick action connector**, since it was created manually — delete
  it in the AWS Quick console.
- **Anything the server created in AWS Quick** (action connectors,
  knowledge bases) — these live outside the stack. Disable them with the
  server's `disable_*` tools before teardown, or delete them in the console.
- **The DynamoDB `OnboardingState` table** (`DeletionPolicy: Retain`) — a durable
  store of tenant state.
- **The KMS key** that encrypts that table and the retained log groups
  (`DeletionPolicy: Retain`). Keep it for as long as you keep them: scheduling
  the key for deletion makes the table's data and the logs unreadable.
- **The CloudWatch log groups** for the Runtime, Gateway, and identity logs
  (`DeletionPolicy: Retain`).
- **The Runtime's observability log group**
  (`/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`), which the Runtime
  creates itself on first use, so the stack never owned it.
- **The Lambda log groups** (`/aws/lambda/<stack-name>-*`) of the stack's
  custom-resource functions.

### Redeploying after a teardown

Because the table and log groups are retained, a fresh deploy into the same
account/region fails with "already exists" until you remove them. If you intend
to discard the data, delete them before redeploying:

```bash
aws dynamodb delete-table --region "${AWS_REGION}" --table-name OnboardingState

# Log groups: the identity group has a fixed name and will collide; the Runtime
# and Gateway groups are named after resource ids and are orphaned (not reused).
for lg in $(aws logs describe-log-groups --region "${AWS_REGION}" \
    --log-group-name-prefix /aws/vendedlogs/bedrock-agentcore \
    --query 'logGroups[].logGroupName' --output text); do
  aws logs delete-log-group --region "${AWS_REGION}" --log-group-name "$lg"
done
```

Also remove the old image tags from ECR if you want to restart numbering at
`0.1.0`, and recreate the manual AWS Quick connector from the new stack's
outputs (the old one points at a gateway and Cognito pool that no longer exist).
