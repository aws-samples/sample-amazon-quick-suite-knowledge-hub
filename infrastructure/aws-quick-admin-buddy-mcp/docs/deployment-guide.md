# Deployment guide

This guide deploys the AWS Quick Admin Buddy MCP server and its supporting AWS
resources with a single AWS CloudFormation template
(`infrastructure/template.yaml`).

The template creates the full solution together. Every resource that supports
tags carries the tag `Project=QuickAdminBuddy` for cost tracking.

- An Amazon Bedrock AgentCore Runtime hosting the MCP server container
  (stateless streamable HTTP on port 8000).
- An AgentCore Gateway in front of the Runtime, with an Amazon Cognito
  `CUSTOM_JWT` inbound authorizer: a valid Cognito access token is required to
  call the server. The gateway signs the hop to the Runtime with its own AWS
  Identity and Access Management (IAM) role, so the end-user identity is not
  passed to the backend.
- A Cognito user pool (the inbound authorizer's identity source). It allows only
  administrator-created users.
- The `<stack-name>-Agent-IAM-Identity` role (the runtime role), which the
  Runtime uses for outbound calls. It can call only the AWS Quick
  action-connector, knowledge-base, and data-source operations the server uses
  (restricted to this account), look up this account's AWS Quick users
  ([`DescribeUser`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DescribeUser.html)), read Secrets Manager secrets named `quick-admin-buddy/*`,
  and use the stack's AWS KMS key through DynamoDB.
- The `OnboardingState` Amazon DynamoDB table, which holds the onboarding state
  for the single shared tenant (this deployment does not isolate state per
  user).
- An AWS Key Management Service (AWS KMS) customer managed key (rotation turned
  on). It encrypts the DynamoDB table, the stack's Secrets Manager secrets, and
  the stack's Amazon CloudWatch Logs log groups. The key policy delegates to IAM,
  so each role that needs the key is granted it explicitly; a second statement
  lets the CloudWatch Logs service use it, limited to this account's
  `/aws/vendedlogs/bedrock-agentcore/*` and `/aws/lambda/<stack-name>/*` log
  groups.
- A Cognito user pool domain and app client: the OAuth 2.0 endpoints and the
  confidential client used when you create the AWS Quick action connector. The
  client allows only the hosted-UI authorization code grant and refresh
  tokens.
- A client-secret custom resource and Secrets Manager secret. The custom
  resource reads the Cognito app client secret (CloudFormation does not expose
  it) and stores it in Secrets Manager, which holds the only copy.
- A Cognito user custom resource and Secrets Manager secret. The custom resource
  creates the `quickadminbuddy` user with a generated permanent password (for
  the sign-in during the OAuth 2.0 authorization code flow) and stores the
  credentials in Secrets Manager.
- CloudWatch Logs delivery: delivery sources, destinations, and deliveries that
  route the Runtime application and usage logs, the Gateway application logs,
  and the workload-identity application logs to retained log groups. The three
  AWS Lambda functions behind the custom resources write to their own retained
  log groups under `/aws/lambda/<stack-name>/`. Every log group has a 90-day
  retention period and is encrypted with the stack's key.

The stack does not create the AWS Quick MCP action connector. Custom MCP
connectors can't be created with CloudFormation, so you create the connector by
hand in AWS Quick with the values the stack exposes as outputs. See
[Step 3](#step-3-create-the-action-connector-in-aws-quick) and
[auth-model.md](./auth-model.md) for how the pieces fit together.

## Prerequisites

You need the following:

- AWS Command Line Interface (AWS CLI) version 2, configured with credentials
  for the target account and Region, with permission to create the resources
  listed at the start of this guide (CloudFormation, IAM, Cognito, DynamoDB, AWS
  KMS, Amazon Bedrock AgentCore, AWS Lambda, Secrets Manager, and CloudWatch
  Logs).
- An AWS Quick account with an Enterprise subscription, where you can use the
  AWS Quick console to create the action connector after the stack deploys.
- Docker, to build and push the MCP server container image.
- An Amazon Elastic Container Registry (Amazon ECR) repository (or another
  registry the AgentCore Runtime can pull from) in the target account and
  Region.
- The AgentCore service available in the target Region.

## Step 1: Build and push the container image

The project's `Dockerfile` packages the server: Python 3.12, an unprivileged
user, port 8000, and the `quick-admin-buddy-mcp` entry point.

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

Note the resulting `IMAGE_URI`; you pass it to the stack as a parameter.

## Step 2: Deploy the stack

```bash
# The template is larger than the CloudFormation inline limit of 51,200 bytes,
# so it must be staged in Amazon S3. Reuse a bucket or create one (first time
# only; bucket names are globally unique).
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
named IAM roles (the runtime role, the gateway execution role, and the roles
for the custom-resource Lambda functions). `aws cloudformation deploy` creates
the stack on the first run and applies a change set on later runs.

The Runtime, Gateway, and workload-identity log group names are fixed, so you
can deploy one stack per account and Region.

## Stack parameters

| Parameter | Required | Meaning |
|---|---|---|
| `ContainerImageUri` | required | The full image URI (from Step 1) the AgentCore Runtime pulls and runs. |
| `CognitoDomainPrefix` | required | Globally unique domain prefix for the Cognito hosted UI and OAuth 2.0 endpoints. Forms the authorization and token endpoints (`https://<prefix>.auth.<region>.amazoncognito.com/oauth2/...`) used by the AWS Quick action connector. Must be unique across all AWS accounts in the Region; lowercase letters, numbers, and hyphens, 3-63 characters. |
| `OnboardingStateTableName` | optional | Name of the DynamoDB table for onboarding state. Must match the server's `ONBOARDING_STATE_TABLE` environment variable (default `OnboardingState`). |
| `TenantId` | optional | The single shared tenant every authenticated caller maps to (default `shared`). This deployment does not pass per-user identity to the backend; all callers share this tenant's state. Passed to the Runtime as `ONBOARDING_TENANT`. |
| `ActionConnectorName` | optional | Suggested name to use when you create the action connector in AWS Quick. Surfaced as the `ActionConnectorSuggestedName` output. |
| `QuickSetupBuddyAgentId` | optional | ID of the "Quick Setup Buddy" AWS Quick chat agent the stack creates (`AWS::QuickSight::Agent`). Default `quick-setup-buddy`. The ID is create-only (changing it replaces the chat agent), so keep it stable across updates. |
| `QuickSetupBuddySharePrincipals` | optional | Comma-separated AWS Quick user, group, or namespace Amazon Resource Names (ARNs) to share the "Quick Setup Buddy" chat agent with (empty shares it with no one but its creator). |

The server reads these environment variables once at startup. The template sets
the first two on the Runtime, and the container image sets the third:

| Variable | Default | Meaning |
|---|---|---|
| `ONBOARDING_STATE_TABLE` | `OnboardingState` | The DynamoDB table name (matches `OnboardingStateTableName`). |
| `ONBOARDING_TENANT` | `shared` | The single shared tenant ID (matches `TenantId`). |
| `MCP_BIND_HOST` | `127.0.0.1` | The interface the server listens on. The image sets `0.0.0.0` so the Runtime's proxy can reach it inside the container; no host port is published. |

A variable that isn't set takes its default. A value that is set but invalid,
for example an empty table name or a name with characters the template's
`AllowedPattern` doesn't allow, stops the server at startup with an error that
names the variable. The server also stops at startup if no AWS Region is
configured; the AgentCore Runtime provides one.

## Stack outputs

The outputs fall into two groups: identifiers for invoking the server, and the
values you need to create the AWS Quick action connector (Step 3).

| Output | Meaning |
|---|---|
| `AgentCoreGatewayEndpoint` | The HTTPS gateway URL the MCP client (the skill, through the connector) calls to reach the MCP server. |
| `AgentCoreGatewayArn` | ARN of the AgentCore Gateway. |
| `CognitoUserPoolId` | The user pool the gateway trusts for inbound JWT validation. |
| `ActionConnectorSuggestedName` | Suggested connector name. |
| `ActionConnectorType` | API value of the connector type, `MODEL_CONTEXT_PROTOCOL`, for scripted setup. In the console, choose **Model Context Protocol (MCP)**. |
| `ActionConnectorAuthType` | API value of the auth type, `OAUTH2_AUTHORIZATION_CODE`, for scripted setup. In the console, choose **User authentication (OAuth)**. |
| `ActionConnectorBaseEndpoint` | The connector's MCP server endpoint: the gateway MCP URL, used unchanged (it already ends in `/mcp`; appending another `/mcp` makes the gateway return HTTP 400). |
| `ActionConnectorRedirectUrl` | OAuth callback URL (already registered on the Cognito app client). |
| `CognitoUserPoolClientId` | OAuth 2.0 client ID for the connector. |
| `CognitoAuthorizationEndpoint` | OAuth 2.0 authorization URL. |
| `CognitoTokenEndpoint` | OAuth 2.0 token URL. |
| `CognitoClientSecretArn` | Secrets Manager ARN holding the app client secret (read the value for the connector). |
| `CognitoUsername` | User name of the Cognito sign-in user (`quickadminbuddy`). |
| `CognitoUserCredentialsSecretArn` | Secrets Manager ARN holding the user credentials as JSON `{"username","password"}`. |

Read them with:

```bash
aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name quick-admin-buddy-mcp \
  --query "Stacks[0].Outputs" --output table
```

## Step 3: Create the action connector in AWS Quick

CloudFormation can't create the AWS Quick MCP custom connector, so create it by
hand with the Step 2 outputs. These steps follow
[Set up MCP integration](https://docs.aws.amazon.com/quick/latest/userguide/mcp-integration.html#mcp-integration-setup):

1. In the AWS Quick console, choose **Connectors**, choose the **Create for
   your team** tab, and then choose **Model Context Protocol (MCP)**.
1. For **Name**, enter the `ActionConnectorSuggestedName` output. For **MCP
   server endpoint**, enter the `ActionConnectorBaseEndpoint` output unchanged.
   For **Connection type**, choose **Public network**. Then choose **Next**.
1. Choose **User authentication (OAuth)**, and enter **Client ID**
   (`CognitoUserPoolClientId`), **Client Secret** (the value in
   `CognitoClientSecretArn`, see the following command), **Token URL**
   (`CognitoTokenEndpoint`), **Authorization URL**
   (`CognitoAuthorizationEndpoint`), and **Redirect URL**
   (`ActionConnectorRedirectUrl`).
1. Choose **Create and continue**, review the integration, and choose **Next**.

Read the client secret from Secrets Manager when the console asks for it:

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
the Cognito app client, so AWS Quick can complete the authorization code
exchange without further Cognito changes.

This is a user-delegated (interactive) OAuth 2.0 flow: an administrator signs
in through the Cognito hosted UI, and AWS Quick exchanges the resulting code for
an access token it presents to the gateway. See [auth-model.md](./auth-model.md)
for the full flow.

### Sign-in user for the OAuth flow

The stack creates a Cognito user (`quickadminbuddy`, output `CognitoUsername`)
with a generated permanent password, so an administrator can complete the
hosted-UI sign-in during the authorization code flow. The credentials are stored
as JSON in Secrets Manager (output `CognitoUserCredentialsSecretArn`); stack
outputs and events contain the secret ARN, not the password.

The user is shared by the administrators who onboard the account. The skill and
this backend help administrators set up AWS Quick before regular users get
access, to speed up the initial deployment, and are meant to be removed once the
account is ready to onboard regular users (see [Teardown](#teardown)). The user is
created so that you can sign in right after the stack is deployed.

Read the credentials with:

```bash
aws secretsmanager get-secret-value \
  --region "${AWS_REGION}" \
  --secret-id "$(aws cloudformation describe-stacks --region "${AWS_REGION}" \
      --stack-name quick-admin-buddy-mcp \
      --query "Stacks[0].Outputs[?OutputKey=='CognitoUserCredentialsSecretArn'].OutputValue" \
      --output text)" \
  --query SecretString --output text
```

The app client allows only the hosted-UI authorization code grant (and refresh
tokens), so there is no direct user name and password sign-in
([`InitiateAuth`](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_InitiateAuth.html) with `USER_PASSWORD_AUTH`) that bypasses the hosted UI.

### Give other AWS Quick users access to the connector

You manage connector permissions in AWS Quick after creation. Grant an AWS Quick
user the see, edit, and manage permission set on the connector from the console
or with the `update-action-connector-permissions` API, using the user's AWS
Quick user ARN as the principal.

## Updating the deployment

To ship a new server build, push a new image tag (Step 1) and run the same
`aws cloudformation deploy` command again with the updated `ContainerImageUri`.
CloudFormation computes and applies the change set.

### Refresh the gateway tool list after a container update (required)

After every container update, synchronize the gateway target again. Otherwise
the gateway keeps serving the tool list it cached from the previous container,
and new, renamed, or removed tools don't appear.

This step is needed because the `AgentCoreGatewayTarget` uses an MCP server
target in the default listing mode, which discovers the backend's tools at sync
time and caches them. Deploying a new `ContainerImageUri` creates a new
AgentCore Runtime version (the `DEFAULT` endpoint retargets to it, so traffic
reaches the new container), but the gateway target's `Endpoint` references the
Runtime by ID against the `DEFAULT` qualifier and doesn't change. CloudFormation
therefore sees no change to the target and never starts tool discovery, and the
cached list goes stale until you sync explicitly.

Run [`SynchronizeGatewayTargets`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_SynchronizeGatewayTargets.html) after the stack update completes:

```bash
# Resolve the gateway ID and target ID from the stack
GATEWAY_ID=$(aws cloudformation describe-stack-resources \
  --region "${AWS_REGION}" --stack-name quick-admin-buddy-mcp \
  --query "StackResources[?LogicalResourceId=='AgentCoreGateway'].PhysicalResourceId" \
  --output text)

# The GatewayTarget physical ID is "<gatewayId>|<targetId>"; take the part after the pipe
TARGET_ID=$(aws cloudformation describe-stack-resources \
  --region "${AWS_REGION}" --stack-name quick-admin-buddy-mcp \
  --query "StackResources[?LogicalResourceId=='AgentCoreGatewayTarget'].PhysicalResourceId" \
  --output text | cut -d'|' -f2)

# Make the gateway discover the tools of the new container
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
updated `lastSynchronizedAt`.

### Sync the connector in AWS Quick

The gateway sync covers the gateway side only. AWS Quick doesn't update a custom
MCP connector's tool list on its own; per
[MCP sync](https://docs.aws.amazon.com/quick/latest/userguide/mcp-integration.html#mcp-integration-sync),
the connector owner must trigger a sync. Open the connector's details page in
AWS Quick, choose **Sync**, and sign in again when AWS Quick asks. Without it, a
new or renamed tool doesn't show up in AWS Quick.

If you ever prefer the gateway to discover tools live on every request instead
of caching them, recreate the target with its listing mode set to `DYNAMIC`.
The cost is per-request discovery latency, and tools don't appear in
`tools/list` until they are invoked.

## Logs and debugging

At startup the server writes one JSON log line (`"event": "startup"`) with the
settings it resolved: the table name, the tenant, and the bind host. None of
them is secret. Each tool call then writes one JSON log line to the Runtime's log group
(`/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`) with the tool name, the
connector or extension key, the result status (and `error_type` when there is
one), the AWS request IDs of the AWS calls the tool made, and the duration. The
line never contains tool arguments, result messages, or secret values. Use the
AWS request IDs to find a failed `Create*` call in AWS CloudTrail or when you
contact AWS Support. The gateway's application log group
(`/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/<gateway-id>`)
records every request that reaches the gateway.

The gateway returns generic errors to callers, because `ExceptionLevel` is left
unset in the template. While you debug a deployment, you can set
`ExceptionLevel: DEBUG` on the `AgentCoreGateway` resource and update the stack,
so gateway responses include the detailed cause (for example, a target
connection or authentication failure). Remove it again afterwards, because
detailed errors work against the server's own sanitization of results.

## Teardown

```bash
aws cloudformation delete-stack --region "${AWS_REGION}" --stack-name quick-admin-buddy-mcp
```

Deleting the stack removes the AgentCore resources, the Cognito user pool and
domain (the `quickadminbuddy` user is deleted by its custom resource on stack
delete), the IAM roles, the custom-resource Lambda functions, the stack's
Secrets Manager secrets, and the "Quick Setup Buddy" AWS Quick chat agent the
stack created (any sharing granted to it is lost with it).

The following are not removed automatically:

- The AWS Quick action connector, because you created it by hand. Delete it in
  the AWS Quick console.
- Anything the server created in AWS Quick (action connectors and knowledge
  bases), because these live outside the stack. Turn them off with the server's
  `disable_*` tools before teardown, or delete them in the console.
- The DynamoDB `OnboardingState` table (`DeletionPolicy: Retain`), which holds
  the tenant state.
- The AWS KMS key that encrypts that table and the retained log groups
  (`DeletionPolicy: Retain`). Keep it for as long as you keep them: scheduling
  the key for deletion makes the table's data and the logs unreadable.
- The CloudWatch Logs log groups for the Runtime, Gateway, and identity logs,
  and the Lambda log groups under `/aws/lambda/<stack-name>/`
  (`DeletionPolicy: Retain`).
- The Runtime's observability log group
  (`/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`), which the Runtime
  creates itself on first use, so the stack never owned it.

### Redeploying after a teardown

Because the table and log groups are retained, a fresh deploy into the same
account and Region fails with "already exists" until you remove them. If you
intend to discard the data, delete them before redeploying:

```bash
aws dynamodb delete-table --region "${AWS_REGION}" --table-name OnboardingState

# Log groups: the identity and Lambda groups have fixed names and collide; the
# Runtime and Gateway groups are named after resource IDs and are orphaned.
for prefix in /aws/vendedlogs/bedrock-agentcore "/aws/lambda/quick-admin-buddy-mcp/"; do
  for lg in $(aws logs describe-log-groups --region "${AWS_REGION}" \
      --log-group-name-prefix "$prefix" \
      --query 'logGroups[].logGroupName' --output text); do
    aws logs delete-log-group --region "${AWS_REGION}" --log-group-name "$lg"
  done
done
```

Also remove the old image tags from Amazon ECR if you want to restart numbering
at `0.1.0`, and create the AWS Quick connector again from the new stack's
outputs (the old one points at a gateway and Cognito user pool that no longer
exist).
