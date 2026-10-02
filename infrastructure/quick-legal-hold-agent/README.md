# Quick Legal Hold Agent

A deployable **legal-hold management agent for Amazon Quick**. From inside Amazon Quick
chat, a Legal administrator can search identities, place and release legal holds on
users and groups, and list active holds - with every action attributed to the individual
admin who performed it (chain-of-custody).

The agent's tools are exposed to Amazon Quick over the **Model Context Protocol (MCP)**
through an **Amazon Bedrock AgentCore Gateway**, secured with **OAuth 2.0 per-user login
(3LO)** through Amazon Cognito. Amazon Quick connects to it as an **MCP connector**. When a
hold is placed, it is written to an Amazon DynamoDB table that a preservation data path
(Amazon Data Firehose to a filter AWS Lambda function to a write-once-read-many (WORM)
Amazon S3 bucket) reads, so held users' chat records are automatically preserved to an
Object Lock bucket.

This is a sample intended for evaluation and as a starting point. Review it against your
own security and compliance requirements before using it with production data.

## Contents

1. [Architecture](#architecture)
2. [What gets created](#what-gets-created)
3. [The agent's tools](#the-agents-tools)
4. [Prerequisites](#prerequisites)
5. [Step 1 - Deploy the agent backend](#step-1---deploy-the-agent-backend)
6. [Step 2 - Connect the agent to Amazon Quick](#step-2---connect-the-agent-to-amazon-quick)
7. [Step 3 - Create the Legal Hold agent in Amazon Quick](#step-3---create-the-legal-hold-agent-in-amazon-quick)
8. [Step 4 - Validate](#step-4---validate)
9. [Cleanup](#cleanup)
10. [Repository layout](#repository-layout)
11. [Contributing](#contributing)
12. [License](#license)

## Architecture

![Quick Legal Hold Agent architecture. Data path (top): a user under legal hold's Amazon Quick prompts and AI outputs flow as CHAT_LOGS to Amazon CloudWatch vended logs, then to Amazon Data Firehose and a filter AWS Lambda function that keeps only active-hold users (matched by Amazon QuickSight ARN) and partitions by group and user, into an Amazon S3 Object Lock (WORM) bucket encrypted by an AWS KMS key for e-discovery retrieval. Control path (bottom): a Legal admin asks in Amazon Quick, which connects through an OAuth 2.0 and Amazon Cognito MCP connector to an Amazon Bedrock AgentCore Gateway exposing the search_identities, list_group_members, place_hold, release_hold, and list_holds tools, which directly invokes the Hold Manager AWS Lambda function that resolves users and expands groups through AWS IAM Identity Center and idempotently writes and releases holds in an Amazon DynamoDB held-user table. The filter function looks up user_arn in that same Amazon DynamoDB table.](images/quick_legal_hold_architecture.png)

```
Amazon Quick (Legal Hold agent, MCP connector)
      |  1. per-user OAuth login (Cognito hosted UI, authorization_code / 3LO)
      |  2. MCP JSON-RPC + Bearer JWT
      v
AgentCore Gateway (MCP, CUSTOM_JWT authorizer) --REQUEST interceptor--> injects caller identity
      |  direct Lambda invoke (tool name in clientContext)
      v
Hold Manager Lambda --> DynamoDB holds table --> Firehose --> filter Lambda --> S3 WORM (Object Lock)
      \_ (optional) IAM Identity Center: read-only resolve/expand users and groups
```

- **Control path:** Amazon Quick -> Gateway -> Hold Manager Lambda -> DynamoDB.
- **Data path:** chat logs -> Firehose -> filter (keep only active-hold users) -> WORM S3.
- Placing a hold via the agent immediately governs what the data path preserves.

## What gets created

All resources share the prefix `quick-legalhold-mcp-`.

**Control path (the agent)**
- `quick-legalhold-mcp-hold-manager` AWS Lambda function - the five tools.
- `quick-legalhold-mcp-interceptor` AWS Lambda function - REQUEST interceptor that injects caller identity.
- `quick-legalhold-mcp-gateway-role` - Gateway execution role.
- `AWS::BedrockAgentCore::Gateway` `quick-legalhold-mcp-gw` (MCP, CUSTOM_JWT) + Lambda target.

**Preservation data path**
- AWS Key Management Service (AWS KMS) customer managed key (alias `quick-legalhold-mcp-cmk`), key rotation enabled.
- Amazon S3 WORM bucket `quick-legalhold-mcp-worm-<account>` - Object Lock (GOVERNANCE),
  versioned, SSE-KMS, public access blocked.
- Amazon DynamoDB table `quick-legalhold-mcp-users` (partition key `user_arn`, on-demand).
- Amazon Data Firehose stream `quick-legalhold-mcp-stream-dp` -> WORM bucket, with filter
  AWS Lambda function `quick-legalhold-mcp-filter` (keeps only active-hold `user_arn`s).
- A delivery-wiring custom resource that binds the chat-logs vended-log delivery to the stream.

**Authentication (Amazon Cognito)**
- User pool `quick-legalhold-mcp-pool` + hosted-UI domain + resource server `holds-api`
  (scope `invoke`).
- App client `quick-legalhold-mcp-3lo` (authorization_code / 3LO; scopes
  `openid email profile holds-api/invoke`; confidential client with secret; callbacks =
  Quick's `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback` and
  `http://localhost:8765/callback`).
- A sample end-user `legal-admin-test` whose password is stored in AWS Secrets Manager
  (`quick-legalhold-mcp-3lo-testuser`) for evaluation only.

Authentication is **per-user (3LO) only**, so every hold is attributed to the individual
logged-in Legal admin.

## The agent's tools

| Tool | Purpose | Required args |
|---|---|---|
| `search_identities` | Search AWS IAM Identity Center users and groups by substring (IDC mode) | `query` (optional) |
| `list_group_members` | Expand an AWS IAM Identity Center group to its member users | `group_name` |
| `place_hold` | Place an active hold on a user or (expanded) group; one item per `user_arn` | `type`, `name`, `matter_id` (+ optional `custodian`) |
| `release_hold` | Set matching holds to `status=released` | `type`, `name` |
| `list_holds` | List all current hold items | none |

## Prerequisites

- **Node.js** 18 or later, **npm**, **AWS CLI v2**, and **Python 3**, all on your PATH.
- AWS credentials for your target account (`aws configure`, IAM Identity Center / SSO, or
  environment variables). The deploy script verifies these with `aws sts get-caller-identity`.
- Permissions to create the resources listed above and to run `cdk bootstrap` if the
  environment is not already bootstrapped (the script bootstraps only if needed).
- An **Amazon Quick** account where you can add MCP connectors and create agents (admin access).

## Step 1 - Deploy the agent backend

```bash
git clone <this-repo>
cd quick-legal-hold-agent
./scripts/deploy.sh
# optional overrides:
#   AWS_REGION=us-west-2 ./scripts/deploy.sh
#   QUICK_REDIRECT_URI=<your-quick-callback> ./scripts/deploy.sh
```

`scripts/deploy.sh` runs the full pipeline and is idempotent:

1. Preflight - verify `node`, `npm`, `aws`, `python3`.
2. Identity - `aws sts get-caller-identity`.
3. `npm install`.
4. CDK bootstrap - only if the `CDKToolkit` stack is missing.
5. `cdk diff` (informational).
6. `cdk deploy --require-approval never`.
7. Write the AWS CloudFormation outputs to `outputs/deployment-outputs.json`.

There are no manual post-deploy steps for the backend; the stack creates all of its own
infrastructure.

<details>
<summary>Manual equivalent</summary>

```bash
npm install
npx cdk diff
npx cdk deploy --require-approval never
# override Quick redirect URI:
# npx cdk deploy -c quickRedirectUri=<uri> --require-approval never
```
</details>

## Step 2 - Connect the agent to Amazon Quick

This step registers the deployed AgentCore Gateway as an **MCP connector** in Amazon Quick.

> **CLI availability:** Amazon Quick exposes `aws quicksight create-action-connector`, but
> its connector `--type` values are HTTP and SaaS integrations (`GENERIC_HTTP`,
> `SALESFORCE_CRM`, `JIRA_CLOUD`, `AMAZON_S3`, and similar) - there is **no MCP connector
> type**. So an MCP connector cannot be created from the CLI today and must be added in the
> Amazon Quick console. Everything up to the paste is automated below.

Run the helper to print exact, ready-to-paste values pulled live from your deployed stack
(it also retrieves the app client secret from Cognito):

```bash
./scripts/quick-connector-info.sh
```

It prints the following and writes `outputs/quick-connector.json`:

| Field | Source |
|---|---|
| MCP Gateway URL | `GatewayUrl` output |
| Auth method | OAuth 2.0 - authorization code (3LO) |
| Authorize URL | `ThreeLoAuthorizeUrl` output |
| Token URL | `ThreeLoTokenUrl` output |
| OIDC discovery URL | `CognitoDiscoveryUrl` output |
| Client ID | `ThreeLoClientId` output |
| Client secret | Retrieved from Cognito (confidential client) |
| Scopes | `openid email profile holds-api/invoke` |
| Redirect URI | `ThreeLoRedirectUri` (Quick's `.../sn/oauthcallback`) |

**Your deployed connector values.** Read them from this stack's CloudFormation
**Outputs** (Console → CloudFormation → the `quick-legalhold-mcp` stack →
Outputs, or `aws cloudformation describe-stacks`). Every field the connector
needs is an output — including the client secret (`ThreeLoClientSecret`):

| Field | Stack output |
|---|---|
| MCP Gateway URL | `GatewayUrl` |
| Auth method | OAuth 2.0 - authorization code (3LO) |
| Authorize URL | `ThreeLoAuthorizeUrl` |
| Token URL | `ThreeLoTokenUrl` |
| OIDC discovery URL | `CognitoDiscoveryUrl` |
| Client ID | `ThreeLoClientId` |
| Client secret | `ThreeLoClientSecret` |
| Scopes | `ThreeLoScopes` (`openid email profile holds-api/invoke`) |
| Redirect URI | `ThreeLoRedirectUri` (Quick's `.../sn/oauthcallback`) |

Or run `./scripts/quick-connector-info.sh` to print them ready-to-paste and
write `outputs/quick-connector.json`.

In the Amazon Quick console:

1. Open Amazon Quick and go to the left nav **Integrations** (Admins may find it under
   **Manage Quick > Connectors**). Select the **Actions** tab.
2. Under **Set up a new integration**, find the **Model Context Protocol (MCP)** tile and
   choose the plus (**+**) sign.
3. On the **Create integration** page, enter a **Name** (for example
   `Legal Hold Tools`), an optional description, and paste the **MCP Gateway URL** from the
   table above. Choose **Next**.
4. For the authentication method choose **OAuth 2.0 - authorization code**, then paste the
   **Authorize URL**, **Token URL** (or the **OIDC discovery URL**), **Client ID**,
   **Client secret**, and **Scopes** `openid email profile holds-api/invoke`.
5. Confirm the **Redirect URI** shown matches Quick's callback
   (`https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`). If your account uses a
   different regional callback, redeploy with `-c quickRedirectUri=<uri>` and re-run the helper.
6. Choose **Create and continue.** Quick performs OAuth protected-resource-metadata
   discovery, then runs MCP `initialize` and `tools/list` and surfaces the five tools
   (`search_identities`, `list_group_members`, `place_hold`, `release_hold`, `list_holds`).
7. Complete the **Authenticate** step: you are redirected to the Cognito hosted UI to sign
   in as a Legal admin. The registry/connector does not store your credentials; each admin
   logs in with their own.
8. **Share** the connector with your Legal-admin role or user group so only Legal can invoke
   holds. If you skip sharing, only you can use it and teammates will not see the tools.

Each Legal admin authenticates individually through the Cognito hosted UI the first time
they use the agent, and their holds are attributed to them.

**Tip (optional): make the connector a pre-configured card.** If you publish the Gateway to
an **AWS Agent Registry** that is linked to your Amazon Quick account, its MCP record appears as a
pre-configured connector card on the Connectors page, so admins create the connector from
the card instead of typing every field. See
[AWS Agent Registry integration](https://docs.aws.amazon.com/quick/latest/userguide/aws-agent-registry-integration.html).

## Step 3 - Create the Legal Hold agent in Amazon Quick

After the connector exists (Step 2), create the agent and attach the connector as an action.
This can be done in the console or with the AWS CLI.

### Option A - AWS CLI

`aws quicksight create-agent` creates the agent and can attach action connectors by ARN via
`--action-connectors`. Get your connector's ARN first (from the console connector details or
by listing connectors), then create the agent:

> **The agent prompt goes in `--custom-prompt-input`**, not in `--welcome-message` or
> `--starter-prompts` (those are only the greeting and the suggested-prompt chips). The
> instructions live in the tagged-union field `NewPrompt.CustomInstructions`. Because the
> prompt is long, put it in a JSON file and pass it with `file://` rather than inline.

First, save the agent prompt (the full text in the [Agent prompt](#agent-prompt) section
below) into a `NewPrompt.CustomInstructions` JSON file:

```bash
cat > custom-prompt.json <<'JSON'
{
  "NewPrompt": {
    "CustomInstructions": "You are the Legal Hold Assistant for the Legal and e-discovery team. You help authorized Legal administrators place, release, and review legal holds on Amazon Quick users and groups by calling the connected hold-management tools. You never manage holds any other way.\n\nTools available to you:\n- search_identities(query): find IAM Identity Center users and groups by name substring. Use this first when the admin refers to a person or team by name and you need to confirm the exact identity before acting.\n- list_group_members(group_name): expand a group to its member users. Use this to show the admin exactly who a group hold will cover before you place it.\n- place_hold(type, name, matter_id): place an active hold on a user or group. type is \"user\" or \"group\". For a group, the tool expands membership and writes one hold per member user. matter_id is the legal matter the hold belongs to.\n- release_hold(type, name): set the matching holds to released.\n- list_holds(): list all current holds.\n\nHow to behave:\n1. Confirm before you write. Before calling place_hold or release_hold, restate the target and, for a hold, the matter_id, and ask the admin to confirm. Do not place or release a hold until they confirm.\n2. Resolve names first. If the admin gives a name rather than an exact identifier, call search_identities (and list_group_members for a group) and show what you found. If a name is ambiguous or returns no match, ask the admin to clarify rather than guessing.\n3. Always require a matter_id before placing a hold. If the admin does not provide one, ask for it. Never invent a matter_id.\n4. Groups. When the target is a group, expand it with list_group_members, show the members, and only after confirmation call place_hold with type \"group\". Report how many members were covered.\n5. Never place a hold on yourself, and never place holds on everyone or on wildcard targets. If asked to do either, decline and ask for a specific, scoped target.\n6. Report back precisely. After any action, report the resolved user_arns, the matter_id, the recorded custodian (the signed-in admin), and the resulting status. If a hold already existed, say so instead of implying a new one was created.\n7. Read-only on the directory. Identity resolution is read-only; you cannot modify IAM Identity Center users or groups. Say so if asked.\n8. Fail closed. If a tool returns an error or you cannot resolve a target, stop and report the error plainly. Do not fabricate a success, an ARN, or a hold that was not written.\n9. Stay in scope. You only handle legal-hold search, place, release, and list. For anything else, tell the admin it is outside your scope.\n\nEvery hold you place is attributed to the signed-in administrator for chain-of-custody, so be accurate and deliberate.",
    "Identity": "A precise, security-conscious Legal Hold assistant for the Legal and e-discovery team.",
    "Tone": "Professional, concise, and deliberate."
  }
}
JSON
```

Then create the agent, attaching the connector **and** the prompt:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Find the MCP connector's ARN (created in Step 2).
aws quicksight list-action-connectors \
  --aws-account-id "$ACCOUNT_ID" --region us-east-1 \
  --query "ActionConnectorSummaries[].{Name:Name,Arn:Arn}" --output table

# Create the agent, attach the connector as an action, and set the prompt.
aws quicksight create-agent \
  --aws-account-id "$ACCOUNT_ID" --region us-east-1 \
  --agent-id legal-hold-assistant \
  --name "Legal Hold Assistant" \
  --description "Search identities and place/release/list legal holds." \
  --action-connectors "arn:aws:quicksight:us-east-1:${ACCOUNT_ID}:actionconnector/<your-connector-id>" \
  --custom-prompt-input file://custom-prompt.json \
  --agent-lifecycle PUBLISHED \
  --welcome-message "Ask me to search identities or place, release, and list legal holds." \
  --starter-prompts "List all current holds" "Search for users named smith"
```

Notes:
- `--agent-id` must match `[0-9a-zA-Z-_.+]+`; `--name` is 1-50 characters.
- `--custom-prompt-input` is a tagged union: use `NewPrompt` (as above) for a fresh prompt,
  or `ExistingPrompt` to reference a saved model-profile prompt. `NewPrompt.CustomInstructions`
  is the agent's system prompt; `Identity`, `Tone`, `OutputStyle`, and `ResponseLength` are
  optional refinements. `--welcome-message` and `--starter-prompts` are NOT the prompt, they
  are only the greeting and the suggested-prompt chips.
- Use `--agent-lifecycle PREVIEW` to stage the agent, then re-run with `PUBLISHED` (or use
  `update-agent`) when ready.
- To change the prompt later, use `aws quicksight update-agent --custom-prompt-input file://custom-prompt.json ...`.
- To restrict who can use the agent, attach spaces with `--spaces <space-arn> ...` and manage
  sharing/permissions so only the Legal-admin role has access.
- If you created the agent before the connector existed, attach it later with
  `aws quicksight update-agent --action-connectors <connector-arn> ...`.

### Option B - Amazon Quick console

1. Go to **Agents** and choose **Create agent** (or **Create custom agent**).
2. Name it, for example **Legal Hold Assistant**.
3. Under **Actions**, add the MCP connector from Step 2 so the agent can call the five hold tools.
4. Paste the **agent prompt** below into the agent's **Instructions** field.
5. Restrict the agent's visibility and sharing to the Legal-admin role or group.
6. Save and publish the agent to the Legal team's Amazon Quick workspace.

### Agent prompt

Paste this into the agent's **Instructions** (system prompt). It tells the agent how and
when to use each of the five tools, and enforces confirm-before-write and chain-of-custody
behavior.

```text
You are the Legal Hold Assistant for the Legal and e-discovery team. You help authorized
Legal administrators place, release, and review legal holds on Amazon Quick users and
groups by calling the connected hold-management tools. You never manage holds any other way.

Tools available to you:
- search_identities(query): find IAM Identity Center users and groups by name substring.
  Use this first when the admin refers to a person or team by name and you need to confirm
  the exact identity before acting.
- list_group_members(group_name): expand a group to its member users. Use this to show the
  admin exactly who a group hold will cover before you place it.
- place_hold(type, name, matter_id): place an active hold on a user or group. type is
  "user" or "group". For a group, the tool expands membership and writes one hold per
  member user. matter_id is the legal matter the hold belongs to.
- release_hold(type, name): set the matching holds to released.
- list_holds(): list all current holds.

How to behave:
1. Confirm before you write. Before calling place_hold or release_hold, restate the target
   (user or group) and, for a hold, the matter_id, and ask the admin to confirm. Do not
   place or release a hold until they confirm.
2. Resolve names first. If the admin gives a name rather than an exact identifier, call
   search_identities (and list_group_members for a group) and show what you found. If a
   name is ambiguous or returns no match, ask the admin to clarify rather than guessing.
3. Always require a matter_id before placing a hold. If the admin does not provide one, ask
   for it. Never invent a matter_id.
4. Groups. When the target is a group, expand it with list_group_members, show the members,
   and only after confirmation call place_hold with type "group". Report how many members
   were covered.
5. Never place a hold on yourself, and never place holds on everyone or on wildcard targets.
   If asked to do either, decline and ask for a specific, scoped target.
6. Report back precisely. After any action, report the resolved user_arns, the matter_id,
   the recorded custodian (the signed-in admin), and the resulting status. If a hold already
   existed, say so instead of implying a new one was created.
7. Read-only on the directory. Identity resolution is read-only; you cannot modify IAM
   Identity Center users or groups. Say so if asked.
8. Fail closed. If a tool returns an error or you cannot resolve a target, stop and report
   the error plainly. Do not fabricate a success, an ARN, or a hold that was not written.
9. Stay in scope. You only handle legal-hold search, place, release, and list. For anything
   else, tell the admin it is outside your scope.

Every hold you place is attributed to the signed-in administrator for chain-of-custody, so
be accurate and deliberate.
```

> Tip: keep a short **welcome message** such as "Ask me to search identities or place,
> release, and list legal holds," and a couple of **starter prompts** like
> "List all current holds" and "Search for users named smith."

Example prompts a Legal admin can use in chat:

- "Search for users named *smith*."
- "Place a hold on the group *Finance-EMEA* for matter M-2026-0142."
- "List all current holds."
- "Release the hold on user *jdoe*."

## Step 4 - Validate

Test the agent from inside Amazon Quick — the same way real Legal admins will use it.
In the Quick chat with the Legal Hold agent:

1. Ask it to **`list holds`** (expect an empty list on a fresh deploy).
2. Ask it to **`place a hold`** on the sample test user (or any name).
3. Ask it to **`list holds`** again to confirm the hold now appears.
4. Optionally ask it to **`release the hold`** and list again.

Because the connector uses per-user 3LO login, each hold is attributed to the
signed-in admin — you can confirm this in the DynamoDB `quick-legalhold-mcp-users`
table (the `custodian` / `custodian_source` attributes reflect the logged-in user).

Real IAM Identity Center users and groups are only ever resolved read-only; holds
are written keyed on the resolved user ARN.

## Cleanup

```bash
./scripts/teardown.sh
```

This clears S3 Object Lock legal holds, empties the WORM bucket, then runs `cdk destroy`.
It removes only this stack's resources (KMS, S3, DynamoDB, Firehose, Lambdas, the Cognito
pool / app client / sample user / secret, and the Gateway and target). The bucket uses
Object Lock in GOVERNANCE mode (never COMPLIANCE), so an administrator can remove holds and
versions and everything is deletable. The script is safe to re-run.

## Repository layout

```
quick-legal-hold-agent/
├── bin/app.ts                              # CDK app entry
├── lib/quick-legalhold-mcp-stack.ts        # Self-contained stack: KMS + S3 + DynamoDB + Firehose + filter
│                                           #   + Gateway (3LO) + interceptor + Cognito + sample user
├── lambda/
│   ├── hold_manager_mcp/handler.py         # 5 tools; custodian from injected caller identity
│   ├── filter/handler.py                   # Firehose transform: keep only active-hold user_arns
│   ├── interceptor/handler.py              # REQUEST interceptor: decode JWT, inject _caller
│   └── delivery_wiring/handler.py          # vended-log delivery binding (custom resource)
├── scripts/
│   ├── deploy.sh                           # one-command deploy (preflight, identity, install, bootstrap, deploy, outputs)
│   ├── quick-connector-info.sh             # print Amazon Quick MCP connector values (3LO)
│   └── teardown.sh                         # WORM cleanup + cdk destroy (this stack only)
├── outputs/                                # generated at deploy time (gitignored)
├── images/                                 # architecture diagram used in this README
├── cdk.json / package.json / tsconfig.json / .npmrc
└── README.md
```

## Contributing

See [How to Contribute](/HOW-TO-CONTRIBUTE.md).

## License

This library is licensed under the MIT-0 License. See the [LICENSE](/LICENSE)
file.
