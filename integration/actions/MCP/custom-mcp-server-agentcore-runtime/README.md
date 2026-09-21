---
category: Capability
description: "Build Your Custom MCP Server Using AgentCore Runtime and Connect with Quick"
---

# Build Your Custom MCP Server Using AgentCore Runtime and Connect with Quick

Customers build AI agents and automations in Amazon Quick to analyze data, search enterprise knowledge, and run workflows across their business. Amazon Quick is a unified agentic AI workspace that empowers teams to analyze data, build intelligent agents, discover enterprise knowledge, and automate workflows, all in one place. Quick supports Model Context Protocol (MCP) integrations for action execution, data access, and AI agent integration. MCP provides a standard way for AI applications and agents to discover and invoke tools exposed by external services.

You can expose your application's capabilities as MCP tools by hosting your own MCP server, whether you're an independent software vendor (ISV), an enterprise integrating internal systems, or a developer building custom solutions. By configuring an MCP integration in Amazon Quick, Quick acts as an MCP client and connects to your MCP server endpoint to access the tools you expose. After that connection is in place, Quick AI agents and automations can invoke your tools to retrieve data and run actions in your product, using the customer's authentication, authorization, and governance controls.

This approach gives you a repeatable integration contract: you define tools once, publish a stable endpoint, and support the same model across customers. Your customers get a way to use your product inside Quick workflows, without building custom connectors for every use case.

This workshop demonstrates how to build and deploy a custom MCP Server to Amazon Bedrock AgentCore Runtime and connect it to Amazon Quick. While this example uses HR APIs, you can adapt this pattern for any APIs—proprietary ISV providers, enterprise applications, or custom services.

## Architecture

![Architecture Diagram](images/architecture.png)

## What You'll Build

An MCP server with 5 sample HR tools deployed to AgentCore Runtime, authenticated via Cognito, and accessible from Amazon Quick.

An MCP server with 5 sample HR tools deployed to AgentCore Runtime, authenticated via Cognito, and accessible from Amazon Quick.

### HR Tools

| Tool | Description |
|------|-------------|
| `get_employee_info` | Retrieve complete employee details |
| `check_leave_balance` | Query remaining leave days |
| `create_leave_request` | Submit employee leave requests |
| `update_employee_record` | Modify employee information |
| `create_support_ticket` | Create IT/HR support tickets |

---

## Workshop Files

Upload these 4 files to SageMaker JupyterLab:

| File | Purpose |
|------|---------|
| `HR_MCP_Workshop.ipynb` | Workshop notebook — run this |
| `hr_mcp_server.py` | MCP server with 5 HR tools |
| `utils.py` | Cognito setup (domain, scope, client secret, test user) |
| `requirements.txt` | Runtime dependencies (fastmcp, mcp, uvicorn) |

---

## Prerequisites (Infrastructure Setup)

Complete these steps **before** the workshop.

### 1. AWS Account

- An AWS account with access to `us-east-1` region
- Billing enabled (estimated workshop cost: < $5)

### 2. Create SageMaker IAM Execution Role

1. Go to **AWS Console → IAM → Roles → Create Role**
2. Trusted entity: **AWS Service → SageMaker**
3. Attach policy: **`AdministratorAccess`**
   - This is for workshop simplicity. In production, use least-privilege policies.
   - The notebook creates IAM roles, Cognito resources, SSM parameters, Secrets Manager secrets, and deploys to AgentCore — admin access avoids permission errors.
4. Role name: `SageMaker-MCP-Workshop-Role`
5. Click **Create role**

### 3. Create SageMaker Domain

1. Go to **AWS Console → Amazon SageMaker → Admin configurations → Domains**
2. Click **Create domain**
3. Choose **Quick setup**
4. Settings:
   - Domain name: `mcp-workshop-domain`
   - Execution role: Select `SageMaker-MCP-Workshop-Role` (created above)
5. Click **Submit**
6. Wait 5-10 minutes for status to become **InService**

### 4. Create SageMaker User Profile

If Quick setup didn't create one automatically:

1. In SageMaker → Domains → Select your domain
2. Click **Add user**
3. User name: `workshop-user`
4. Execution role: `SageMaker-MCP-Workshop-Role`
5. Click **Submit**

### 5. Launch JupyterLab Space

1. In SageMaker Studio, click **JupyterLab** from the left sidebar
2. Click **Create JupyterLab Space**
   - Name: `mcp-workshop`
   - Instance type: `ml.t3.medium` (2 vCPU, 4GB RAM — sufficient)
3. Click **Run space**
4. Wait 2-3 minutes, then click **Open JupyterLab**

### 6. Upload Workshop Files

1. In JupyterLab file browser, upload the 4 files listed above
2. Verify all files are in the same directory (home directory is fine)

---

## Running the Workshop

1. Open `HR_MCP_Workshop.ipynb`
2. Select kernel: **Python 3 (ipykernel)**
3. Run each cell with **Shift+Enter** — one at a time
4. **Do NOT use "Run All Cells"** — Step 4 (deploy) takes 3-5 minutes

### Workshop Steps (9 cells)

| Step | What | Time |
|------|------|------|
| 1 | Install dependencies (pip + zip utility) | ~2 min |
| 2 | Set up Cognito (pool, domain, scope, client, user) | ~10 sec |
| 3 | Create AgentCore execution IAM role | ~5 sec |
| 4 | Configure + deploy to AgentCore Runtime | ~3-5 min |
| 5 | Get agent ARN and MCP endpoint URL | ~2 sec |
| 6 | Store credentials in SSM + Secrets Manager | ~5 sec |
| 7 | Test — list all 5 tools | ~10 sec |
| 8 | Test — invoke all 5 tools with sample data | ~30 sec |
| 9 | Print connection details for Amazon Quick | instant |

---

## Connecting to Amazon Quick

After Step 9, you'll get 4 values to paste into the Amazon Quick MCP Client interface:

| Field | Example |
|-------|---------|
| MCP Server URL | `https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/...` |
| Client ID | `abc123def456` |
| Client Secret | `xyz789...` |
| Token URL | `https://hr-mcp-1234.auth.us-east-1.amazoncognito.com/oauth2/token` |

### Sample Prompts

- "What is the leave balance for EMP001?"
- "Create a vacation request for EMP001 from March 1-5"
- "Show me employee info for EMP002"
- "Create an IT support ticket for EMP001 about VPN issues"
- "Update the email for EMP001 to <alice.new@company.com>"

---

## Key Concepts

### Direct Code Deploy

AgentCore supports deploying MCP servers by zipping Python code + dependencies and uploading directly — no Docker, no ECR, no Dockerfile. The `bedrock-agentcore-starter-toolkit` CLI handles packaging and deployment.

**Important:** `requirements.txt` must only contain runtime dependencies. Do NOT include `boto3` or `bedrock-agentcore-starter-toolkit` — they bloat the package past the 750MB unzipped limit.

### Cognito Authentication

Amazon Quick uses OAuth `client_credentials` flow:

1. Quick sends `client_id` + `client_secret` to the Cognito token URL
2. Cognito returns a JWT with scope `hr-mcp/access`
3. Quick sends the JWT to the AgentCore MCP endpoint
4. AgentCore validates the JWT against the Cognito OIDC discovery URL

**Required Cognito resources:**

- **Domain** — without it, the `/oauth2/token` endpoint doesn't exist
- **Resource Server + Scope** — `client_credentials` grant requires at least one scope
- **App Client with Secret** — Quick requires `client_id` + `client_secret`

### AgentCore Execution Role

The IAM role that AgentCore assumes to run your code. Trust policy must:

- Allow `bedrock-agentcore.amazonaws.com` as principal
- Include `aws:SourceAccount` and `aws:SourceArn` conditions

---

## Cleanup

Run the cleanup cell at the bottom of the notebook, or manually:

1. Delete AgentCore agent: `agentcore destroy --force`
2. Delete IAM role: `AgentCore-HR-MCP-ExecutionRole`
3. Delete Cognito User Pool (includes domain and resource server)
4. Delete SSM parameter: `/hr_mcp_server/runtime/agent_arn`
5. Delete Secrets Manager secret: `hr_mcp_server/cognito/credentials`
6. Stop/delete SageMaker JupyterLab space
7. Optionally delete SageMaker domain

---

## Estimated Cost

| Resource | Cost |
|----------|------|
| SageMaker JupyterLab (ml.t3.medium) | ~$0.05/hour |
| Cognito | Free tier (50,000 MAUs) |
| AgentCore Runtime | Pay per request (~$0.001/request) |
| SSM + Secrets Manager | Negligible |
| **Total for workshop** | **< $5** |
