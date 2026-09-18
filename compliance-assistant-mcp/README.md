---
category: Use Case
description: "Multi-agent compliance analysis via Amazon Quick using CrewAI, Bedrock AgentCore Runtime, and MCP"
---

# Compliance Assistant — Multi-Agent Analysis via Amazon Quick

Multi-agent compliance analysis exposed as an MCP server to Amazon Quick through Amazon Bedrock AgentCore Gateway. Users ask compliance questions in natural language, and a 3-agent CrewAI pipeline analyzes regulations, drafts policies, and recommends AWS solutions — orchestrated asynchronously to respect Quick's 60-second MCP timeout.

**Components:**

- **AgentCore Gateway** — MCP server endpoint with Cognito OAuth (created by CDK)
- **Lambda Function** — Orchestrator: starts jobs, polls status, retrieves reports (created by CDK)
- **AgentCore Runtime** — Runs the CrewAI crew (3 agents, 3-5 min per analysis)
- **CDK Stack** — DynamoDB, S3, Lambda, Cognito, Gateway, Gateway Target, IAM (Python)
- **Bedrock Agent** — Foundation model for compliance research (Nova Pro v1)

## 🎯 Purpose

- **Regulatory Analysis on Demand** — Ask Quick to analyze PCI DSS, HIPAA, NIST CSF, ISO 27001, SOX, GDPR, etc.
- **Multi-Agent Pipeline** — 3 specialized CrewAI agents work sequentially: Compliance Analyst → Compliance Specialist → Solutions Architect
- **Async Job Pattern** — Every MCP tool call returns in < 5 seconds; the full pipeline runs in the background (3-5 minutes)
- **Managed Infrastructure** — AgentCore Runtime handles container builds, scaling, and health checks — no Docker or ECS required

## 📁 Project Structure

```text
compliance-assistant-mcp/
├── README.md
├── pyproject.toml                         # Python project config (CrewAI dependencies)
├── LICENSE
├── cdk/                                   # CDK infrastructure (Python)
│   ├── app.py                             # CDK app entry point
│   ├── compliance_assistant_stack.py      # DynamoDB, S3, Lambda, Cognito, Gateway, Target
│   ├── cdk.json
│   └── requirements.txt                   # CDK Python dependencies
├── runtime/                               # AgentCore Runtime + Lambda handler
│   ├── agent.py                           # CrewAI entry point (deployed via agentcore CLI)
│   ├── handler.py                         # Lambda handler (3 MCP tools, bundled by CDK)
│   └── requirements.txt                   # Python dependencies for Runtime
├── scripts/
│   └── create_bedrock_agent.py            # Creates Bedrock Agent + alias
├── src/compliance_assistant/              # CrewAI crew source
│   ├── __init__.py
│   ├── crew.py                            # Crew definition (3 agents, 3 tasks)
│   ├── main.py                            # Local CLI runner
│   └── config/
│       ├── agents.yaml                    # Agent role/goal/backstory
│       └── tasks.yaml                     # Task descriptions and expected outputs
├── knowledge/
│   └── user_preference.txt
└── images/
    └── architecture.png
```

## 🚀 Quick Start

Deployment is 4 steps: clone → create Bedrock Agent → deploy AgentCore Runtime → deploy CDK stack. The CDK stack creates everything else (DynamoDB, S3, Lambda, Cognito, Gateway).

### Prerequisites

- AWS account with [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html) available in your target region
- AWS CLI v2 configured with credentials (`aws configure`)
- Python 3.10+ (required by AgentCore Runtime and CDK)
- Node.js 18+ (required by the AWS CDK CLI)
- AWS CDK CLI: `npm install -g aws-cdk`
- AgentCore CLI: `pip install bedrock-agentcore-starter-toolkit` (requires Python 3.10+)
- Amazon Nova Pro v1 is automatically available in supported regions — no manual model access setup required

**Required IAM Permissions:** CloudFormation, Lambda, IAM, DynamoDB, S3, Cognito, Bedrock, Bedrock AgentCore

### Step 1 — Clone

```bash
git clone --filter=blob:none --sparse \
  https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub.git
cd sample-amazon-quick-suite-knowledge-hub
git sparse-checkout set docs/use-cases/compliance-assistant-mcp
cd docs/use-cases/compliance-assistant-mcp
```

### Step 2 — Create Bedrock Agent

Set your target region and run the script:

```bash
export AWS_REGION=us-east-1   # any region with Bedrock AgentCore support

python3 scripts/create_bedrock_agent.py
```

Save the output values:

```text
AGENT_ID      = XXXXXXXXXX
AGENT_ALIAS_ID = YYYYYYYYYY
```

### Step 3 — Deploy AgentCore Runtime

The AgentCore Runtime runs the CrewAI crew in a managed container. No Docker needed — the CLI uses CodeBuild.

```bash
# Prepare deployment directory
mkdir -p /tmp/agentcore-deploy
cp runtime/agent.py /tmp/agentcore-deploy/
cp runtime/requirements.txt /tmp/agentcore-deploy/
cp -r src/compliance_assistant /tmp/agentcore-deploy/compliance_assistant

cd /tmp/agentcore-deploy

# Create Python 3.10+ venv and install toolkit
python3.10 -m venv .venv && source .venv/bin/activate
pip install bedrock-agentcore-starter-toolkit

# Configure
agentcore configure \
  --entrypoint agent.py \
  --name compliance-crew-runtime \
  --region $AWS_REGION \
  --idle-timeout 900 \
  --max-lifetime 28800 \
  --non-interactive

# Deploy with environment variables
agentcore deploy \
  --env AWS_REGION_NAME=$AWS_REGION \
  --env MODEL=bedrock/us.amazon.nova-pro-v1:0 \
  --env AGENT_ID=<YOUR_AGENT_ID> \
  --env AGENT_ALIAS_ID=<YOUR_AGENT_ALIAS_ID>
```

Get the Runtime ARN:

```bash
agentcore status --verbose
```

Note the ARN, e.g.: `arn:aws:bedrock-agentcore:$AWS_REGION:<ACCOUNT_ID>:runtime/compliance_crew_runtime-xxxxxxxx`

Also note the Runtime IAM role name (needed in Step 5):

```bash
aws iam list-roles --query "Roles[?contains(RoleName,'AgentCoreSDKRuntime')].RoleName" --output text
```

### Step 4 — Deploy CDK Stack

This single command creates DynamoDB, S3, Lambda, Cognito (OAuth), AgentCore Gateway, and the Gateway Target with all 3 MCP tool schemas.

```bash
cd <path-to>/compliance-assistant-mcp/cdk

# Create a virtual environment and install CDK dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Get your account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Bootstrap CDK (first time per account/region)
cdk bootstrap aws://$ACCOUNT_ID/$AWS_REGION

# Deploy
cdk deploy \
  -c runtimeArn="<YOUR_RUNTIME_ARN>" \
  -c agentId="<YOUR_AGENT_ID>" \
  -c agentAliasId="<YOUR_AGENT_ALIAS_ID>"
```

> **Troubleshooting CDK Bootstrap:** If bootstrap fails with an S3 bucket conflict (e.g., a stale `cdk-hnb659fds-assets-*` bucket from a previous bootstrap), use a custom qualifier:
>
> ```bash
> cdk bootstrap --qualifier myid --toolkit-stack-name CDKToolkit-myid aws://$ACCOUNT_ID/$AWS_REGION
> ```
>
> Then add `-c "@aws-cdk/core:bootstrapQualifier=myid"` to all `cdk deploy` / `cdk destroy` commands.

The stack outputs everything you need for Quick integration:

| Output | Description |
|--------|-------------|
| `GatewayUrl` | MCP Server endpoint URL |
| `CognitoClientId` | OAuth2 Client ID |
| `CognitoTokenUrl` | OAuth2 token endpoint |
| `CognitoScopes` | OAuth2 scopes |

To retrieve the Client Secret (not shown in CloudFormation outputs):

```bash
# Get the User Pool ID and Client ID from stack outputs
aws cloudformation describe-stacks \
  --stack-name ComplianceAssistantV2 \
  --query 'Stacks[0].Outputs' \
  --region $AWS_REGION

# Then describe the client to get the secret
aws cognito-idp describe-user-pool-client \
  --user-pool-id <COGNITO_USER_POOL_ID> \
  --client-id <COGNITO_CLIENT_ID> \
  --region $AWS_REGION \
  --query 'UserPoolClient.ClientSecret' \
  --output text
```

### Step 5 — Grant Runtime Access to DynamoDB and S3

The AgentCore Runtime runs under its own IAM role (created by the `agentcore` CLI). It needs permissions to update job status in DynamoDB and upload reports to S3.

```bash
# Ensure AWS_REGION is set (same region used in Steps 2-4)
export AWS_REGION=us-east-1

# Get the Runtime role name (from Step 3)
RUNTIME_ROLE=$(aws iam list-roles \
  --query "Roles[?contains(RoleName,'AgentCoreSDKRuntime')].RoleName" \
  --output text --region $AWS_REGION)

echo "Runtime role: $RUNTIME_ROLE"

# Get resource names from CDK stack outputs
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Add DynamoDB + S3 + Bedrock Agent permissions
aws iam put-role-policy \
  --role-name "$RUNTIME_ROLE" \
  --policy-name ComplianceAssistantDataAccess \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Sid\": \"DynamoDBAccess\",
        \"Effect\": \"Allow\",
        \"Action\": [\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:UpdateItem\",\"dynamodb:Query\"],
        \"Resource\": \"arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/compliance-assistant-v2-jobs\"
      },
      {
        \"Sid\": \"S3ReportAccess\",
        \"Effect\": \"Allow\",
        \"Action\": [\"s3:PutObject\",\"s3:GetObject\"],
        \"Resource\": \"arn:aws:s3:::compliance-assistant-v2-reports-${ACCOUNT_ID}/reports/*\"
      },
      {
        \"Sid\": \"BedrockAgentAccess\",
        \"Effect\": \"Allow\",
        \"Action\": [\"bedrock:InvokeAgent\"],
        \"Resource\": [
          \"arn:aws:bedrock:${AWS_REGION}:${ACCOUNT_ID}:agent/<YOUR_AGENT_ID>\",
          \"arn:aws:bedrock:${AWS_REGION}:${ACCOUNT_ID}:agent-alias/<YOUR_AGENT_ID>/*\"
        ]
      }
    ]
  }"
```

## 🔧 Available MCP Tools

| Tool | Description | Returns |
|------|-------------|---------|
| `start_compliance_analysis` | Kicks off analysis for a regulatory topic | `job_id`, status |
| `get_analysis_status` | Polls job progress | status, progress message |
| `get_analysis_report` | Retrieves completed markdown report from S3 | Full report |

## 🎨 Quick Integration

From the CDK stack outputs (Step 4), gather these values:

- `GatewayUrl` — MCP Server endpoint
- `CognitoClientId` — OAuth2 Client ID
- Client Secret — retrieved via CLI (see Step 4)
- `CognitoTokenUrl` — OAuth2 token endpoint
- `CognitoScopes` — OAuth2 scopes

### Configure MCP Action

1. Navigate to **Integrations** in Amazon Quick
2. Click **Actions** → **+** button for **Model Context Protocol**
3. Fill in:
   - **Name**: Compliance Assistant
   - **Description**: Multi-agent compliance analysis using CrewAI and Bedrock AgentCore
   - **MCP Server Endpoint**: Paste `GatewayUrl`
   - Click **Next**
4. Configure authentication:
   - Select **Service Authentication** → **Service-to-service OAuth**
   - **Client ID** → Paste `CognitoClientId`
   - **Client Secret** → Paste the secret retrieved via CLI
   - **Token URL** → Paste `CognitoTokenUrl`
5. Click **Create and Continue** → **Next** → **Next**

### Create Quick Agent

1. Navigate to **Agents** → **Create agent**
2. Configure:
   - **Agent name**: Compliance Assistant
   - **Description**: Multi-agent compliance analysis for regulatory frameworks
3. Add Agent Instructions:

```text
You are a Compliance Assistant with access to a multi-agent analysis pipeline.

CAPABILITIES:
• Analyze regulatory frameworks (PCI DSS, HIPAA, NIST CSF, ISO 27001, SOX, GDPR)
• Draft organizational compliance policies
• Recommend AWS implementation solutions

WORKFLOW:
1. When asked to analyze a compliance topic, call start_compliance_analysis with the topic
2. The analysis takes 3-5 minutes. Inform the user and offer to check status
3. When asked for status, call get_analysis_status with the job_id
4. When the status is COMPLETED, call get_analysis_report to retrieve the full report
5. Present the report with key findings highlighted

RESPONSE STYLE:
• Acknowledge the request and confirm the analysis has started
• Provide the job_id so the user can reference it
• When presenting reports, summarize key findings first, then show the full report
• Offer follow-up analysis on related topics
```

<!-- markdownlint-disable MD029 -->
4. Scroll to **Actions** → **Add action** → Select **Compliance Assistant**
5. Click **Save**

### Test the Agent

```text
"Run a compliance analysis on PCI DSS 4.0 requirements for banking organizations"
"Check the status of my analysis"
"Get the compliance report"
```

## 💡 Example Queries

**Regulatory framework analysis:**

```text
"Run a compliance analysis on PCI DSS 4.0 for banking organizations"
"Analyze HIPAA requirements for healthcare data in cloud environments"
"What are the latest NIST CSF 2.0 changes for financial services"
"Analyze ISO 27001:2022 requirements for technology companies"
"SOX compliance requirements for publicly traded fintech companies"
"GDPR data protection requirements for US companies with EU customers"
```

**Industry-specific compliance:**

```text
"Compliance requirements for open banking APIs under PSD2"
"Anti-money laundering (AML) technology requirements for banks"
"FFIEC cybersecurity assessment requirements for community banks"
"DORA (Digital Operational Resilience Act) requirements for financial institutions"
```

**Cross-framework analysis:**

```text
"Compare PCI DSS and NIST CSF controls for payment processing"
"Overlapping requirements between HIPAA and SOX for healthcare companies"
```

**AWS-specific compliance architecture:**

```text
"AWS compliance architecture for FedRAMP High workloads"
"Cloud security controls for PCI DSS scope reduction on AWS"
```

## 🐛 Troubleshooting

**CDK deploy fails on Gateway or Target:**

- Ensure your region supports Amazon Bedrock AgentCore. Check [regional availability](https://docs.aws.amazon.com/general/latest/gr/bedrock.html).
- The `AWS::BedrockAgentCore::Gateway` and `AWS::BedrockAgentCore::GatewayTarget` CloudFormation resource types must be available in your region.

**MCP Authentication Issues:**

- Verify OAuth2 credentials in Quick MCP Actions configuration
- Retrieve the client secret via CLI (see Step 4) — it's not shown in CloudFormation outputs
- Ensure client secret is copied without leading/trailing spaces

**Analysis Returns No Data or Fails:**

- Check Lambda CloudWatch Logs: `/aws/lambda/compliance-assistant-v2-gateway-target`
- Verify the AgentCore Runtime is running: `agentcore status --verbose`
- Check DynamoDB table for job status: look for `FAILED` entries with `error_message`

**Cold Start Timeouts:**

- First invocation after idle period may take longer (AgentCore Runtime cold start)
- The Lambda handler retries up to 3 times with backoff on cold-start errors
- To warm up: `agentcore invoke '{"job_id":"warmup","topic":"test"}'`

**Quick Shows "Tool call timed out":**

- This means a single MCP call exceeded 60 seconds — should not happen with the async pattern
- Check that the Lambda is returning immediately from `start_compliance_analysis`
- Verify `FUNCTION_NAME` environment variable is set correctly for self-invocation

## 🧹 Cleanup

Remove all deployed resources in reverse order:

```bash
# 1. Delete CDK stack (Gateway, Cognito, Lambda, DynamoDB, S3)
cd cdk && cdk destroy

# 2. Delete Runtime IAM policy
RUNTIME_ROLE=$(aws iam list-roles \
  --query "Roles[?contains(RoleName,'AgentCoreSDKRuntime')].RoleName" \
  --output text --region $AWS_REGION)
aws iam delete-role-policy --role-name "$RUNTIME_ROLE" \
  --policy-name ComplianceAssistantDataAccess

# 3. Delete AgentCore Runtime
cd /tmp/agentcore-deploy && source .venv/bin/activate
agentcore delete

# 4. Delete Bedrock Agent
aws bedrock-agent delete-agent \
  --agent-id <AGENT_ID> --region $AWS_REGION
```

**Cost Considerations:** This solution incurs costs for Lambda invocations, DynamoDB reads/writes, S3 storage, AgentCore Runtime compute, Bedrock model invocations, and Cognito authentication. Monitor usage in AWS Cost Explorer.

## 🔒 Security

- **IAM Roles** — Lambda and Runtime use least-privilege IAM permissions
- **Encryption** — S3 uses server-side encryption; DynamoDB uses AWS-managed encryption
- **Authentication** — OAuth 2.0 with Cognito (client credentials flow, no user passwords)
- **Data Retention** — DynamoDB items auto-expire via TTL (24 hours); S3 reports expire after 30 days
- **Network** — All communication uses TLS; no public endpoints exposed beyond the Gateway
- **Credentials** — Never commit `gateway_config_v2.json` or `.env` to version control

## 📝 License

This library is licensed under the MIT-0 License. See the repository [LICENSE](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/LICENSE).
