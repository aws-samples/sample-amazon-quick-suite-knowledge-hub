# Authentication and authorization model

The AWS Quick Admin Buddy MCP server uses two independent identity boundaries:

- **Inbound** — who may call the server. Amazon Cognito authenticates users; the
  AgentCore Gateway validates the token and **gates access** before any request
  reaches the server. This is access control only: a valid Cognito user token is
  required to call the server, but the user's identity is **not** propagated to
  the backend.
- **Outbound** — what the server may do to AWS Quick. An AWS Identity and
  Access Management (IAM) role, `<stack-name>-Agent-IAM-Identity` (the runtime
  role), allows only the specific AWS Quick admin actions the server needs
  (listed in [Outbound authorization](#outbound-authorization-runtime-role-to-aws-quick)).

The two are deliberately separate: the inbound Cognito identity gates access; the
outbound IAM identity is the server's own scoped role and is never derived from,
or substituted by, caller credentials.

This deployment intentionally does **not** propagate per-user identity to the
backend. All authenticated callers map to a **single shared tenant** (see
[Shared tenant](#shared-tenant-no-per-user-propagation)). The pattern is
Cognito `CUSTOM_JWT` inbound gating, IAM Signature Version 4 (SigV4) to the
runtime, and a shared service identity downstream.

## Inbound authentication (Cognito → AgentCore Gateway)

The AgentCore Gateway is configured with a **CUSTOM_JWT inbound authorizer** that
trusts the Cognito user pool provisioned by the CloudFormation template.

- A request that arrives with a **valid** Cognito access token is forwarded to the
  MCP server on the AgentCore Runtime.
- A request that arrives with a **missing or invalid** token is **rejected at the
  gateway with an authorization error and is never forwarded** to the server. The
  server never sees unauthenticated traffic.
- The gateway signs the gateway→runtime hop with **its own IAM role**
  (the `GATEWAY_IAM_ROLE` credential provider on the gateway target), so the
  runtime only ever sees the gateway's machine identity — **never the end user**.
  No request headers (in particular, `Authorization`) are forwarded to the
  runtime; `Authorization` is a restricted header that AgentCore does not allow
  a gateway target to forward, and no interceptor is configured to re-inject
  identity.

This inbound path is platform configuration on the gateway, not server code. The
server assumes it is always running behind the gateway and that any request it
receives came from an authenticated caller.

### How the Quick connector obtains a token (OAuth2 authorization code)

The AWS Quick MCP action connector is created **manually** in AWS Quick
(custom MCP connectors are not supported for automated creation), using the
OAuth2 endpoints and app client this stack provisions. Once created, it
authenticates to the gateway using the **OAuth2 authorization code grant**
against the same Cognito user pool the gateway trusts:

1. AWS Quick redirects the admin to the Cognito **authorization endpoint**
   (`https://<prefix>.auth.<region>.amazoncognito.com/oauth2/authorize`) to sign
   in and consent.
2. Cognito redirects back to the AWS Quick callback
   (`https://<region>.quicksight.aws.amazon.com/sn/oauthcallback`) with an
   authorization code. The Cognito app client registers this exact URL as an
   allowed callback.
3. AWS Quick exchanges the code at the Cognito **token endpoint**
   (`.../oauth2/token`) using the app client id and secret, receiving an access
   token.
4. AWS Quick calls the gateway MCP endpoint (the gateway URL, which already
   ends in `/mcp`) presenting that token, which the gateway validates with its
   CUSTOM_JWT authorizer as above.

The app client id and secret identify the confidential client. Cognito generates
the secret and CloudFormation does not expose it, so a Lambda-backed custom
resource reads it via `cognito-idp:DescribeUserPoolClient` and stores it in
Secrets Manager. The stack exposes the client id, the OAuth2 endpoints, the base
endpoint, and the callback URL as outputs, and the secret is retrieved from
Secrets Manager when configuring the connector by hand (see the manual-setup
steps in [deployment-guide.md](./deployment-guide.md)).

This is a user-delegated (interactive) flow: a person authenticates through the
Cognito hosted UI. The point of the flow is to prove that a valid Cognito user
is calling; the backend doesn't use the user's identity, and the flow doesn't
change the outbound identity.

The stack creates a Cognito sign-in user (`quickadminbuddy`) with a generated
permanent password stored in Secrets Manager, so the administrator who sets up
the account has credentials for the hosted-UI sign-in. The app client allows
only the hosted-UI authorization code grant and refresh tokens, so a token
can't be obtained with the client credentials and the password alone. See
the "Sign-in user" section of [deployment-guide.md](./deployment-guide.md) for
how to read the credentials.

### Shared tenant (no per-user propagation)

Because the gateway does not propagate the end-user identity to the runtime,
every authenticated caller maps to a **single shared tenant**. The server reads
that tenant from the `ONBOARDING_TENANT` environment variable (set from the
`TenantId` template parameter, default `shared`) and uses it for all
`OnboardingState` reads and writes. No tool accepts a tenant argument, so a
caller can neither select nor spoof a tenant — there is exactly one.

This is a deliberate design choice: the skill is meant for setting up an AWS
Quick account, so the requirement is to authenticate that a valid AWS Quick
administrator is calling, not to isolate state per individual user. If per-user isolation is ever required,
it would need a different architecture (for example, invoking the runtime
directly with the user token rather than through an IAM-signed gateway hop, or an
AgentCore OBO/interceptor mechanism) — the current gateway-fronted design does
not carry the user identity to the backend.

## Outbound authorization (runtime role to AWS Quick)

The server's calls to the AWS Quick admin APIs are signed (SigV4) with the
`<stack-name>-Agent-IAM-Identity` role, the AgentCore Runtime execution role. The
boto3 client is created without explicit credentials, so it uses the Runtime's
role automatically — there are no static or caller-supplied credentials in the
server.

The server creates **two** kinds of AWS Quick integrations — action
connectors ([`CreateActionConnector`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateActionConnector.html)) and knowledge bases ([`CreateKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateKnowledgeBase.html))
— so the role grants the admin actions for both, plus read access to the Secrets
Manager secrets that hold integration auth parameters, and nothing else:

Action connectors:

- [`CreateActionConnector`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateActionConnector.html)
- `DescribeActionConnector`
- `DescribeActionConnectorPermissions`
- `ListActionConnectors`
- `SearchActionConnectors`
- `DeleteActionConnector`
- `UpdateActionConnectorPermissions`

Knowledge bases:

- [`CreateKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateKnowledgeBase.html)
- `DescribeKnowledgeBase`
- `ListKnowledgeBases`
- `DeleteKnowledgeBase`
- `UpdateKnowledgeBasePermissions`

Data sources (a knowledge base is created in two steps: the data source first,
then the knowledge base that references it):

- `CreateDataSource`
- `DescribeDataSource`
- `DeleteDataSource`
- `PassDataSource`

The AWS Quick actions above are further restricted with an
`aws:ResourceAccount` condition so they apply to this account only.

Users (read-only lookup):

- `DescribeUser` — on this account's AWS Quick user ARNs only
  (`arn:<partition>:quicksight:*:<account>:user/*`; any Region, because AWS
  Quick users live in the account's identity Region). Before granting a connector or
  knowledge base to `share_with` / `owner`, the server checks the user exists so
  it can report "user does not exist" (for example an email passed instead of
  the username) instead of the generic `InvalidParameterValueException`.
  If the lookup is not permitted, the check is skipped.

Secrets (auth parameters supplied by reference):

- `secretsmanager:GetSecretValue` and `secretsmanager:DescribeSecret` — scoped
  by naming convention to admin-supplied auth secrets (`quick-admin-buddy/*`).
  The stack's own Cognito secrets are not readable by the runtime role.
  `DescribeSecret` returns a secret's ARN without its value, which is all the
  server needs where it forwards a secret by reference (OneDrive knowledge
  bases).
- The tools accept secret values only through these secrets. A secret value
  passed inline as a tool argument is rejected with `validation_failure`
  (`data.error_type: inline_secret_not_allowed`), so credentials don't pass
  through the chat or the model context.

State and encryption:

- `dynamodb:GetItem` and `dynamodb:UpdateItem` — on the `OnboardingState`
  table only.
- `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey*`, `kms:DescribeKey` — on
  the stack's customer-managed key only, and only when used through DynamoDB.
  (The key policy delegates to IAM and has no wildcard principal; its only
  other statement lets the Amazon CloudWatch Logs service principal use the key
  for this account's AgentCore vended-log groups and the stack's Lambda log
  groups.)

**Fail-fast, no fallback.** If authentication with the runtime role fails
(for example, credential resolution fails), the server fails the operation
immediately and returns a `Tool_Result` with Result_Status `internal_error`
describing the authentication failure. It does **not** attempt any fallback
credentials.

## End-to-end request flow

```text
Onboarding skill / MCP client
        │  MCP over HTTPS + Cognito JWT
        ▼
AgentCore Gateway
        │  validate JWT against Cognito user pool (ACCESS GATING ONLY)
        │  ├─ invalid / missing → reject (never forwarded)
        │  └─ valid → forward, signed to the runtime with the gateway IAM role
        │            (end-user identity is NOT propagated)
        ▼
MCP Server (AgentCore Runtime)
        │  resolve the single shared tenant from ONBOARDING_TENANT
        │  run domain logic (catalog, suite gating, validation, idempotency)
        ├─ read / write shared-tenant state ───────► DynamoDB OnboardingState
        └─ configure connectors (SigV4, runtime role, allowed actions only)
                                              ──────► AWS Quick Admin API
```

1. The client calls the gateway over HTTPS, presenting a Cognito JWT.
2. The gateway validates the JWT. Invalid or missing tokens are rejected before
   reaching the server; valid tokens are forwarded to the runtime over an
   IAM-signed (SigV4) hop that carries the gateway's machine identity, not the
   user's.
3. The server resolves the single shared tenant from `ONBOARDING_TENANT` and runs
   the requested tool's domain logic.
4. State reads and writes go to the shared tenant's row in the DynamoDB table.
5. Connector and knowledge-base operations call the AWS Quick admin API,
   signed with the runtime role and limited to the allowed action
   set above. On any auth failure, the operation returns `internal_error` with
   no fallback.

For the tool-level result contract, see [tool-reference.md](./tool-reference.md);
for how these resources are provisioned, see
[deployment-guide.md](./deployment-guide.md).
