---
category: Capability
description: "AI-powered document generation (docx, pdf, pptx, xlsx, HTML) for AWS Quick using MCP Gateway with AgentCore Runtime and Code Interpreter"
---

# Document Generation MCP with AgentCore Runtime

AI-powered document generation for AWS Quick. Creates professional
docx, pdf, pptx, xlsx, and HTML files using a Strands SDK agent with
Claude Sonnet on Bedrock and AgentCore Code Interpreter.

## What You Get With This Solution

A chat-to-document pipeline that produces real, downloadable files, not text
you copy-paste and reformat. The agent writes and executes Python code in a
sandboxed Code Interpreter using real libraries (openpyxl, python-docx,
reportlab, python-pptx), so the output includes:

- Formatted Word documents with headings, tables, bullet points, and page numbers
- Multi-sheet Excel workbooks with formulas, conditional formatting, and charts
- PowerPoint decks with slide layouts, themes, and data visualizations
- PDF reports with professional typography, headers, footers, and page breaks
- Interactive HTML prototypes with CSS and working JavaScript

You download the output file from the chat in one step, ready to
attach to an email or present in a meeting.

### Sample Outputs

These were generated entirely by the agent from natural language prompts, with no manual editing:

| File | Prompt Summary |
|------|---------------|
| [Employee_Performance_Tracking.xlsx](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/examples/document-generation-mcp-agentcore-runtime/samples/Employee_Performance_Tracking.xlsx) | Employee performance spreadsheet with quarterly KPI scores, weighted averages, and distribution charts |
| [Cloud_Migration_Business_Proposal.pdf](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/examples/document-generation-mcp-agentcore-runtime/samples/Cloud_Migration_Business_Proposal.pdf) | Cloud migration business proposal with executive summary, cost analysis, and timeline |
| [dataflow-landing-page.html](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/examples/document-generation-mcp-agentcore-runtime/samples/dataflow-landing-page.html) | Product landing page with responsive layout, feature cards, and pricing tiers |

### What You Can Ask

These prompts show the kind of request each file type handles.

**Spreadsheets (xlsx):**

- "Build a project budget tracker spreadsheet with cost categories, monthly actuals vs. forecast, variance formulas, and a burn-down chart"
- "Create an employee performance tracking spreadsheet with quarterly KPI scores, weighted averages, and distribution charts"
- "Generate a sales pipeline spreadsheet with deal stages, win probability, weighted revenue, and a funnel chart"

**Presentations (pptx):**

- "Create a 15-slide Q4 business review PowerPoint with revenue charts, regional breakdowns, and key metrics"
- "Build an architecture decision record PowerPoint comparing 3 approaches with pros/cons tables and a recommendation slide"

**Documents (docx):**

- "Write a technical design Word document for a microservices migration with architecture diagrams described in tables, risk matrix, and timeline"
- "Generate an onboarding guide Word document with checklists, role-specific sections, and a 30-60-90 day plan table"

**PDFs (pdf):**

- "Create a professional invoice PDF with line items, tax calculations, and company branding"
- "Build a compliance audit report PDF with findings table, severity ratings, and remediation timeline"

**Web prototypes (frontend-design):**

- "Design a dashboard landing page HTML with a sidebar nav, metric cards, and a responsive data table"
- "Create a pricing page HTML with three tiers, feature comparison grid, and toggle between monthly/annual"

### The Key Difference

The agent doesn't only write text. It writes and executes Python code in a
sandboxed Code Interpreter. That means it can use real libraries (openpyxl,
python-docx, reportlab, python-pptx) to produce files with:

- Formulas and cell references (not just static numbers)
- Conditional formatting and data validation
- Charts generated from actual data
- Proper document styling, fonts, and page layout
- Multi-sheet/multi-slide structure
- Interactive HTML with working JavaScript

The document generation skills for docx, pdf, pptx, and xlsx are inspired by
[Anthropic's open-source skills](https://github.com/anthropics/skills/tree/main/skills).
This sample adapts them to run on AgentCore Runtime with Code Interpreter and adds
tool call budgeting and base64 capture hooks for reliability.

## Architecture

Quick calls the gateway's MCP tools. Lambda functions start the job and return its result, while the agent runs on AgentCore Runtime and a Step Function tracks it.

```text
AWS Quick (Chat)
    │
    ▼
AgentCore Gateway (MCP tools)  ← CDK-managed: Gateway + Cognito + Lambdas
    │
    ├── create_document ──→  Lambda (submit_job)
    │                            │
    │                            ├─ 1. Create job record in DynamoDB (SUBMITTED)
    │                            ├─ 2. Invoke AgentCore Runtime (fire-and-forget)
    │                            ├─ 3. Start Step Function polling loop
    │                            └─ 4. Return job_id immediately
    │
    │                        AgentCore Runtime (runs independently, no timeout)
    │                        ┌──────────────────────────────────┐
    │                        │  Strands SDK Agent                │
    │                        │  Model: Claude Sonnet 4.6         │
    │                        │         (cross-region profile)    │
    │                        │  Tool: Code Interpreter            │
    │                        │                                    │
    │                        │  Generates Python code             │
    │                        │  → executes in sandbox             │
    │                        │  → produces .docx/.pdf/…           │
    │                        │  → direct-persists to S3 + DynamoDB│
    │                        └──────────────────────────────────┘
    │
    │                        Step Function (polling loop)
    │                        ┌──────────────────────────────────┐
    │                        │  Wait 30s                         │
    │                        │    → CheckAgentStatus Lambda      │
    │                        │      → COMPLETED? → MarkCompleted │
    │                        │      → still running? → loop back │
    │                        │      → error? → MarkFailed        │
    │                        │  Timeout: 45 minutes              │
    │                        └──────────────────────────────────┘
    │
    └── get_document_job_result ──→  Lambda (get_result)
                                       │
                                       ▼
                                   DynamoDB → CloudFront URL
                                       │
                                       ▼
                                   Returns download link to chat
                                   (CloudFront → S3, clean URLs
                                    that work on corporate networks)
```

## Key Design Decisions

Each choice below addresses a limit of running long document jobs behind a chat interface:

- **Strands SDK**: agent framework running on AgentCore Runtime
- **Claude Sonnet 4.6** (`us.anthropic.claude-sonnet-4-6`): cross-region inference profile on Bedrock
- **AgentCore Code Interpreter**: isolated sandbox for Python code execution
- **Tool call budget**: MaxToolCallsHook limits the agent to 20 code executions to prevent runaway loops
- **Base64 capture hook**: captures file output in real-time before conversation trimming
- **Fire-and-forget invocation**: submit_job invokes the agent with a 10s read timeout and doesn't wait for completion. The agent runs independently on AgentCore Runtime with no Lambda timeout constraint
- **Direct-persist**: the agent uploads the result to S3 and marks the job COMPLETED in DynamoDB before returning, so the result is persisted regardless of any downstream timeouts
- **Step Function polling loop**: polls DynamoDB every 30s to detect when the agent finishes; 45-minute timeout as a safety net
- **Async submit/poll**: works around Quick's 60-second MCP timeout
- **S3 + CloudFront**: file delivery via clean `*.cloudfront.net` URLs that aren't blocked by corporate proxies (S3 presigned URLs are often blocked)
- **CDK-managed Gateway**: Gateway, Cognito, CloudFront, and the rest of the infrastructure in a single CDK stack

## Prerequisites

Before you start, make sure you have:

1. **AWS CLI v2** installed and configured with credentials for your target account
2. **Python 3.12+**: the agent and CDK stack both use Python
3. **Node.js 18+** and **npm**: required for the AWS CDK CLI (`npx cdk`)
4. **Bedrock model access**: enable `anthropic.claude-sonnet-4-6` (or the cross-region
   inference profile `us.anthropic.claude-sonnet-4-6`) in the
   [Bedrock console](https://console.aws.amazon.com/bedrock/home#/modelaccess)
   for your account and Region
5. **AgentCore CLI**: installed via `pip install bedrock-agentcore[starter-toolkit]`
   (handled by `requirements.txt` in Step 0)

## Project Structure

The code is split between the agent, the CDK stack, and the Lambda handlers:

```text
agentcore_runtime/         Strands agent deployed to AgentCore Runtime
  agent.py                 Agent code (Claude Sonnet + Code Interpreter + hooks)
  requirements.txt         Python dependencies for the agent
  create-iam-role.sh       IAM role setup for AgentCore Runtime

cdk/                       CDK infrastructure (Python)
  app.py                   CDK app entry point
  document_skills_stack.py Stack: DynamoDB, S3, CloudFront, Lambdas, Step Function, Gateway, Cognito
  requirements.txt         Python CDK dependencies
  cdk.json                 CDK app config (runs `python3 app.py`)

lambdas/
  submit_job/              Accepts request, invokes agent (fire-and-forget), starts polling loop
  check_agent_status/      Polls DynamoDB to detect when agent finishes
  get_job_result/          Reads DynamoDB status, returns presigned S3 download URL
  update_job/              Updates DynamoDB job status (used by Step Function)

gateway/
  openapi-spec.yaml        MCP tool definitions (reference documentation)

samples/                   Sample outputs generated by the agent
```

## Deployment

Deployment runs in order from Step 0 to Step 3, and each step depends on the previous one.

Set your target Region for every step:

```bash
export AWS_REGION=us-east-1   # or your preferred region
```

### Step 0: Set up Python environment

Create a virtual environment and install the AgentCore CLI and CDK dependencies:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r cdk/requirements.txt
```

This installs:

- `bedrock-agentcore[starter-toolkit]`: provides the `agentcore` CLI for deploying the agent
- `aws-cdk-lib` and `constructs`: Python CDK libraries for the infrastructure stack

Verify the CLI is available:

```bash
agentcore --help
```

### Step 1: Create IAM role for AgentCore Runtime

The agent needs an IAM role that allows it to invoke Bedrock models, use
Code Interpreter, and write CloudWatch logs.

```bash
cd agentcore_runtime
chmod +x create-iam-role.sh
./create-iam-role.sh
cd ..
```

The script outputs a Role ARN like:

```text
arn:aws:iam::<account-id>:role/DocumentSkillsAgentCoreRole
```

Save this ARN for Step 2.

**What the role allows:**

- `bedrock:InvokeModel` / `bedrock:InvokeModelWithResponseStream`: call Claude Sonnet (cross-region inference profiles)
- `bedrock-agentcore:StartCodeInterpreterSession`, `InvokeCodeInterpreter`, `StopCodeInterpreterSession`, and related actions: Code Interpreter sessions (managed + custom)
- `s3:PutObject` / `dynamodb:UpdateItem`: direct-persist (agent writes results to S3 + DynamoDB)
- `logs:CreateLogGroup` / `logs:PutLogEvents`: CloudWatch logging

### Step 2: Deploy the agent to AgentCore Runtime

Deploy the agent with the AgentCore CLI:

```bash
source .venv/bin/activate
agentcore deploy
```

On first run, the CLI will interactively prompt you:

```text
Where should we create your new agent?
> 1. Create a new agent
Agent name: document_skills_agent
Region: us-east-1
Execution role ARN: <paste the role ARN from Step 1>
```

It creates `.bedrock_agentcore.yaml` with your settings and deploys the agent.
This file is gitignored because it contains account-specific config.

On success, you'll see:

```text
✅ Agent deployed successfully
Agent ARN: arn:aws:bedrock-agentcore:<region>:<account>:runtime/document_skills_agent-XxxYyyZzz
```

Note the **Runtime ID** (the part after `runtime/`, for example `document_skills_agent-XxxYyyZzz`).
You'll need it in Step 3.

For later deploys after code changes, run `agentcore deploy`. It doesn't prompt again.

### Step 3: Deploy CDK infrastructure

The CDK stack creates the DynamoDB table, S3 bucket, Lambda functions,
Step Function, AgentCore Gateway, and Cognito authorizer in one deploy.

```bash
cd cdk

# Install the CDK CLI (if not already installed globally)
npm install -g aws-cdk

# Bootstrap CDK in your account/region (first time only)
cdk bootstrap aws://<account-id>/$AWS_REGION

# Deploy the stack, passing the Runtime ID from Step 2
cdk deploy --parameters AgentCoreRuntimeId=<runtime-id-from-step-2>
```

Example with a real runtime ID:

```bash
cdk deploy --parameters AgentCoreRuntimeId=document_skills_agent-XxxYyyZzz
```

CDK will show you the resources it plans to create and ask for confirmation.
Type `y` to proceed.

On success, the stack outputs all the values you need for Quick:

```text
Outputs:
QuickSuiteDocumentSkills.McpUrl        = https://...gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
QuickSuiteDocumentSkills.TokenUrl      = https://docskills-XXXXXXXX.auth.<region>.amazoncognito.com/oauth2/token
QuickSuiteDocumentSkills.ClientId      = <YOUR_CLIENT_ID>
QuickSuiteDocumentSkills.Scope         = document-skills-gateway/invoke
QuickSuiteDocumentSkills.UserPoolId    = <region>_XxxYyy
QuickSuiteDocumentSkills.GatewayId     = ...
QuickSuiteDocumentSkills.SubmitJobFnArn    = arn:aws:lambda:...
QuickSuiteDocumentSkills.GetJobResultFnArn = arn:aws:lambda:...
QuickSuiteDocumentSkills.DocsBucketName    = document-skills-output-...
QuickSuiteDocumentSkills.JobsTableName     = document-skill-jobs
QuickSuiteDocumentSkills.StateMachineArn   = arn:aws:states:...
```

To get the **Client Secret** (not included in stack outputs because Cognito
doesn't expose it as a CloudFormation attribute), run:

```bash
aws cognito-idp describe-user-pool-client \
  --user-pool-id <UserPoolId-from-output> \
  --client-id <ClientId-from-output> \
  --query 'UserPoolClient.ClientSecret' \
  --output text --region $AWS_REGION
```

```bash
cd ..
```

**What gets created:**

- **DynamoDB table** (`document-skill-jobs`): tracks job status, TTL-enabled
- **S3 bucket** (`document-skills-output-<account>-<region>`): stores generated files, 7-day auto-expiry
- **CloudFront distribution**: serves download URLs via `*.cloudfront.net` (avoids corporate proxy blocks on S3 presigned URLs)
- **Lambda functions**: submit_job (invokes agent + starts polling), check_agent_status, update_job, get_job_result
- **Step Function** (`document-skill-orchestrator`): polling loop: Wait → Check → Choice (loop or done)
- **AgentCore Gateway** (`document-skills-gateway`): MCP gateway with two tool targets
- **Cognito User Pool**: OAuth2 authorizer with `client_credentials` grant for the gateway

### Configure Quick

Using the CDK stack outputs (and the Client Secret from the command above):

1. Go to **Quick Admin → MCP Actions → Add MCP Server**
2. Fill in:

| Setting | Value |
|---------|-------|
| MCP URL | `McpUrl` from stack output |
| Token URL | `TokenUrl` from stack output |
| Client ID | `ClientId` from stack output |
| Client Secret | from `describe-user-pool-client` command |
| Scope | `Scope` from stack output (`document-skills-gateway/invoke`) |

The auth uses the OAuth2 `client_credentials` flow. Quick requests a token
from the Cognito token URL using the client ID + secret. It then passes that JWT
in the `Authorization` header when calling the MCP gateway.

1. Save and test by asking Quick to create a document

**If you need to retrieve these values later:**

```bash
# All values except Client Secret
aws cloudformation describe-stacks \
  --stack-name QuickSuiteDocumentSkills \
  --query 'Stacks[0].Outputs' \
  --output table --region $AWS_REGION

# Client Secret
aws cognito-idp describe-user-pool-client \
  --user-pool-id <UserPoolId> \
  --client-id <ClientId> \
  --query 'UserPoolClient.ClientSecret' \
  --output text --region $AWS_REGION
```

## Create a Custom Agent with Document Skills

You can create a custom Quick agent that uses the document generation
MCP tools. The key behavior: after submitting a job, the agent should
automatically poll for the result instead of asking the user what to do next.

### Agent Setup

Create the agent in Quick:

1. Go to **Quick Admin → Custom Agents → Create Agent**
2. Configure the agent with the MCP server you set up in the previous section
3. Use the system prompt below

### Recommended System Prompt

Paste this prompt into the agent's instructions:

```text
You are a document creation assistant. You help users create professional
documents (Word, PDF, PowerPoint, Excel, and HTML) from natural language
descriptions.

You have access to two tools:
- create_document: Submit a document creation job
- get_document_job_result: Check job status and get the download link

WORKFLOW — follow this exactly for every document request:

1. Determine the skill_type from the user's request:
   - Word document → "docx"
   - PDF → "pdf"
   - PowerPoint/presentation/deck → "pptx"
   - Excel/spreadsheet → "xlsx"
   - HTML/web page/landing page → "frontend-design"

2. Call create_document with the skill_type, a detailed prompt based on
   the user's request, and an appropriate filename.

3. After the job is submitted, tell the user EXACTLY this:

   "Your document is being generated. This can take 3-8 minutes for
   complex documents.

   To check status, type: **check status {job_id}**"

   Replace {job_id} with the actual job ID returned by create_document.

4. When the user sends "check status <job_id>", call get_document_job_result
   with that job_id.
   - If status is COMPLETED: present the download link to the user.
   - If status is SUBMITTED or PROCESSING: tell the user the document is
     still being generated and to try again in a minute.
   - If status is FAILED: tell the user what went wrong and offer to retry.

IMPORTANT RULES:
- After calling create_document, do NOT try to poll or call
  get_document_job_result on your own. You MUST wait for the user to
  ask for status.
- Always give the user the exact "check status {job_id}" query to copy.
- When COMPLETED, always present the download link clearly.
- If the user asks to "check status" without a job_id, ask them for it.
- You can handle multiple document requests in one conversation.
```

### How It Works

With this prompt, the agent flow looks like:

```text
User: "Create a Q4 business review PowerPoint with revenue charts"
  │
  Agent: calls create_document(skill_type="pptx", prompt="...", filename="Q4_Review.pptx")
  │
  Agent: "Your document is being generated. This can take 3-8 minutes
          for complex documents.
          To check status, type: check status abc-123-def-456"
  │
  ... user waits a few minutes ...
  │
  User: "check status abc-123-def-456"
  │
  Agent: calls get_document_job_result(job_id="abc-123-def-456")  → COMPLETED
  Agent: "Your PowerPoint is ready! Download it here: [link]"
```

The user controls when to check, and the agent gives them the exact query to use.

## Testing

Test the agent alone first, then the full pipeline.

### Test the agent directly (without Gateway/Quick)

You can invoke the agent directly using the AgentCore CLI:

```bash
source .venv/bin/activate
agentcore invoke -a document_skills_agent '{
  "skill_type": "xlsx",
  "prompt": "Create a simple budget tracker with 3 months of expenses and a totals row",
  "filename": "test_budget.xlsx"
}'
```

The response contains `file_base64`, the generated file encoded as base64.
When you leave out `job_id`, `docs_bucket`, and `jobs_table`, the agent runs synchronously.

### Test the full pipeline (Lambda → Agent → Step Function)

Invoke the submit_job Lambda directly:

```bash
aws lambda invoke \
  --function-name document-skill-submit-job \
  --payload '{"skill_type":"xlsx","prompt":"Create a simple budget tracker","filename":"test.xlsx"}' \
  --cli-binary-format raw-in-base64-out \
  --region $AWS_REGION \
  /dev/stdout
```

This returns a `job_id`. Then poll for the result:

```bash
aws lambda invoke \
  --function-name document-skill-get-result \
  --payload '{"job_id":"<job-id-from-above>"}' \
  --cli-binary-format raw-in-base64-out \
  --region $AWS_REGION \
  /dev/stdout
```

Poll every 10 seconds until `status` is `COMPLETED` (includes a `download_url`)
or `FAILED`.

## Updating the Agent

After making changes to `agentcore_runtime/agent.py`:

```bash
source .venv/bin/activate
agentcore deploy
```

The agent redeploys in ~2 minutes. No CDK redeploy needed for agent-only changes.

## Updating the Infrastructure

After making changes to `cdk/document_skills_stack.py`:

```bash
cd cdk
cdk deploy --parameters AgentCoreRuntimeId=<your-runtime-id>
cd ..
```

CDK will show a diff of what changed and ask for confirmation.

## Cleanup

To tear down all resources:

```bash
# 1. Destroy the CDK stack (DynamoDB, S3, Lambdas, Step Function, Gateway, Cognito)
cd cdk
cdk destroy
cd ..

# 2. Destroy the AgentCore Runtime agent
agentcore destroy
```

CDK handles the Gateway and Cognito cleanup automatically, so there's no separate
gateway deletion step.

## Supported Document Types

Each skill type maps to one output format and its libraries:

| Skill | Output | Libraries Used |
|-------|--------|---------------|
| docx | Word document | python-docx, Pillow |
| pdf | PDF document | reportlab, Pillow |
| pptx | PowerPoint | python-pptx, matplotlib |
| xlsx | Excel spreadsheet | openpyxl, matplotlib, pandas |
| frontend-design | HTML/CSS/JS | Pure Python file write |

## Agent Reliability Features

The agent includes two Strands hooks to handle edge cases:

1. **MaxToolCallsHook** (20 calls max): Three-phase approach:
   - Calls 1–18: Normal execution
   - Call 19: Warning that cancels the call and tells the model to output base64 next
   - Call 20: Final allowed call (should be base64 output)
   - Call 21+: Hard-stop via `stop_event_loop`

2. **Base64CaptureHook**: Captures file output from tool results in real-time,
   before `SlidingWindowConversationManager` trims old messages between turns.

## Troubleshooting

**`agentcore deploy` fails with STS global endpoint error:**

```bash
export AWS_STS_REGIONAL_ENDPOINTS=regional
```

Or add `sts_regional_endpoints = regional` to your AWS config profile.

**CDK bootstrap fails with "bucket already exists":**
The CDK bootstrap bucket from a previous attempt may be orphaned. Delete the
`CDKToolkit` CloudFormation stack and the `cdk-*` S3 bucket manually, then
re-run `cdk bootstrap`.

**Agent times out or produces no file:**
Check CloudWatch logs at `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`.
The MaxToolCallsHook may have hit the limit. The agent logs show the call count.

**Step Function execution stuck in polling loop:**
The Step Function polls every 30s for up to 45 minutes. If the agent crashed
without updating DynamoDB, the job will stay in PROCESSING until the Step
Function times out. Check the agent's CloudWatch logs for errors.
