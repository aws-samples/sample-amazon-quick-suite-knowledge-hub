---
category: Capability
description: "Amazon Bedrock Knowledge Base Retrieval MCP integration with QuickSuite"
---

# Bedrock KB Retrieval MCP - Amazon Bedrock Knowledge Base Integration

**Amazon Bedrock Knowledge Base Retrieval MCP integration** with Amazon QuickSuite. This solution creates an MCP integration that enables direct access to Amazon Bedrock Knowledge Bases through QuickSuite using MCP Actions.

## 🏗️ Architecture

![Bedrock KB Retrieval MCP Architecture](./images/bedrockkb-architecture.png)

**Components:**

- **AgentCore Gateway**: Amazon Bedrock AgentCore Gateway with Lambda target
- **Lambda Function**: Amazon Bedrock Knowledge Base retrieval handler
- **QuickSuite Integration**: MCP Actions for conversational AI
- **Amazon Bedrock Knowledge Bases**: Document retrieval and semantic search

## 🎯 Purpose

This MCP integration enables:

- **Direct KB Access**: Query Amazon Bedrock Knowledge Bases through natural language
- **QuickSuite Integration**: Integration using MCP Actions
- **AgentCore Gateway**: Gateway with Lambda target and authentication
- **Document Retrieval**: Semantic search with reranking and filtering

## 📁 Project Structure

```
bedrock-kb-retrieval-mcp/
├── app.py                          # CDK deployment entry point
├── cdk.json                        # CDK configuration
├── requirements.txt                # CDK dependencies
├── tools/                          # Lambda function code
│   ├── kb_agentcore_lambda.py     # AgentCore MCP handler
│   ├── kb_agentcore_tools.json    # MCP tool definitions
│   └── requirements.txt           # Lambda dependencies
├── cdk/                           # Infrastructure code
│   ├── kb_direct_stack.py         # AgentCore Gateway stack
│   └── README.md                  # CDK deployment guide
└── README.md                      # This file
```

## 🚀 Quick Start

### 1. Clone Repository (Sparse Checkout)

```bash
# Clone repository with sparse checkout
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub.git
cd sample-amazon-quick-suite-knowledge-hub

# Configure sparse checkout for this use case only
git sparse-checkout set docs/use-cases/bedrock-kb-retrieval-mcp
cd docs/use-cases/bedrock-kb-retrieval-mcp
```

### 2. Deploy AgentCore Gateway

```bash
npm install -g aws-cdk
pip install -r requirements.txt
cdk deploy --require-approval never
```

```

### 3. Get Outputs

**Option 1: AWS Console (Recommended)**

1. Go to **AWS CloudFormation** in the AWS Console
2. Find the **BedrockKBStack** stack
3. Click on the **Outputs** tab
4. Copy the required values for Quick Suite integration

**Option 2: CLI Commands**
```bash
aws cloudformation describe-stacks --stack-name BedrockKBStack --query 'Stacks[0].Outputs'
```

Key outputs for QuickSuite Actions:

- `GatewayUrl` - AgentCore Gateway endpoint
- `ClientId` - OAuth2 client ID
- `ClientSecret` - OAuth2 client secret
- `CognitoTokenUrl` - OAuth2 token endpoint

## 🔧 Available Tools

### Knowledge Base Tools (kb_agentcore_lambda.py)

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `ListKnowledgeBases` | Discover available Bedrock Knowledge Bases | None | knowledge_base_mapping, data_sources |
| `QueryKnowledgeBases` | Natural language retrieval from Knowledge Bases | query, knowledge_base_id, options | documents, content, scores |

## QuickSuite Integration

### ListKnowledgeBases

**Purpose**: Discover available Bedrock Knowledge Bases and data sources
**Input**: None
**Output**: Knowledge base mapping with IDs, names, descriptions, data sources

### QueryKnowledgeBases

**Purpose**: Natural language retrieval from Bedrock Knowledge Bases
**Input**:

- `query` (required): Natural language search query
- `knowledge_base_id` (required): Target KB ID
- `number_of_results` (optional): Result count (default: 10, max: 100)
- `reranking` (optional): Enable reranking (default: false)
- `data_source_ids` (optional): Filter by data sources

**Output**: Newline-separated JSON documents with content, location, score

## QuickSuite Integration

Complete guide to integrate KB Direct with Amazon QuickSuite using MCP Actions.

### Prerequisites

From your CDK deployment, you'll need:

- `GatewayUrl` - AgentCore Gateway endpoint
- `ClientId` - Cognito Client ID
- `ClientSecret` - Cognito Client Secret
- `CognitoTokenUrl` - OAuth2 token endpoint

### Configure MCP Action in QuickSuite

**Step 1: Access Integrations**

1. Navigate to **Integrations** in Amazon QuickSuite
2. Click on **Actions**
3. Click the **+** button for **Model Context Protocol**

**Step 2: Configure MCP Server**
Fill in the MCP configuration:

- **Name**: Bedrock Knowledge Base Retrieval
- **Description**: Amazon Bedrock Knowledge Base retrieval with natural language queries
- **MCP Server Endpoint**: Paste your `GatewayUrl` from CDK deployment outputs
- Click **Next**

**Step 3: Configure Authentication**

1. For Authentication, select **Service Authentication**
2. Keep **Service-to-service OAuth** within the Authentication type field
3. Fill in the authentication values from your CDK deployment outputs:

   - **Client ID** → Paste your `ClientId` (ensure no leading/trailing spaces)
   - **Client Secret** → Paste your `ClientSecret` (ensure no leading/trailing spaces)
   - **Token URL** → Paste your `CognitoTokenUrl`

**Step 4: Complete Setup**

1. Click **Create and Continue**
2. Select **Next**
3. Select **Next**

### Usage in QuickSuite

```
"List all available knowledge bases"
"Search for AWS Lambda best practices in my knowledge base"
"Find documents about data encryption with reranking enabled"
"Query kb-12345 about compliance requirements"
```

## Troubleshooting

**MCP Authentication Issues:**

- Verify OAuth2 credentials in QuickSuite MCP Actions
- Check Cognito token endpoint configuration
- Ensure client secret is correctly copied

**Knowledge Base Access:**

- Verify Amazon Bedrock KB exists and is accessible
- Check IAM permissions for Amazon Bedrock services
- Confirm KB indexing is complete

**AgentCore Gateway:**

- Monitor AgentCore Gateway throttling limits
- Check Lambda timeout and memory settings
- Review CORS configuration for QuickSuite

## 📚 Documentation

- [CDK Deployment Guide](cdk/README.md) - Infrastructure details
- [MCP Tool Definitions](tools/kb_agentcore_tools.json) - API specifications

## License

This library is licensed under the MIT-0 License.
