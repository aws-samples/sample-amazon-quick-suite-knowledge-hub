---
category: Capability
description: "Amazon Redshift MCP integration for database operations with QuickSuite"
---

# Redshift Data Query MCP - Amazon Redshift Database Operations

**Amazon Redshift MCP integration** with Amazon QuickSuite. This solution uses the **AWS LAB Redshift MCP Server** with Lambda wrapper that enables read-only access to Amazon Redshift clusters through QuickSuite using MCP Actions.

## 🏗️ Architecture

![Redshift Data Query MCP Architecture](./images/redshift-mcp-architecture.png)

**Components:**

- **AgentCore Gateway**: Amazon Bedrock AgentCore Gateway with Lambda target
- **Lambda Wrapper**: Wraps AWS LAB Redshift MCP Server for AgentCore compatibility
- **AWS LAB Redshift MCP Server**: Official AWS LAB MCP server implementation for Redshift operations
- **QuickSuite Integration**: MCP Actions for conversational AI
- **Amazon Redshift**: Cluster discovery and SQL query execution

## 🎯 Purpose

This MCP integration enables:

- **AWS LAB Redshift MCP Server**: Uses official AWS LAB MCP server implementation
- **Lambda Wrapper**: AgentCore Gateway compatibility layer
- **Read-Only Access**: Secure Redshift cluster and database operations
- **QuickSuite Integration**: Integration using MCP Actions

## 📁 Project Structure

```
redshift-data-query-mcp/
├── app.py                              # CDK deployment entry point
├── cdk.json                            # CDK configuration
├── requirements.txt                    # CDK dependencies
├── tools/                              # Lambda function code
│   ├── redshift_agentcore_lambda.py   # Lambda wrapper for AWS LAB Redshift MCP Server
│   └── requirements.txt               # Lambda dependencies
├── cdk/                               # Infrastructure code
│   ├── redshift_agentcore_stack.py   # AgentCore Gateway stack
│   └── README.md                      # CDK deployment guide
└── README.md                          # This file
```

## 🚀 Quick Start

### 1. Clone Repository (Sparse Checkout)

```bash
# Clone repository with sparse checkout
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub.git
cd sample-amazon-quick-suite-knowledge-hub

# Configure sparse checkout for this use case only
git sparse-checkout set docs/use-cases/redshift-data-query-mcp
cd docs/use-cases/redshift-data-query-mcp
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
2. Find the **RedshiftAgentCoreStack** stack
3. Click on the **Outputs** tab
4. Copy the required values for Quick Suite integration

**Option 2: CLI Commands**
```bash
aws cloudformation describe-stacks --stack-name RedshiftAgentCoreStack --query 'Stacks[0].Outputs'
```

Key outputs for QuickSuite integration:

- `GatewayUrl` - AgentCore Gateway endpoint
- `ClientId` - OAuth2 client ID
- `ClientSecret` - OAuth2 client secret
- `CognitoTokenUrl` - OAuth2 token endpoint

## 🔧 Available Tools

### Redshift Data Tools (redshift_agentcore_lambda.py)

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `list_clusters` | Discover available Redshift clusters and serverless workgroups | None | clusters, connection_details |
| `list_databases` | List databases in a Redshift cluster | cluster_identifier | databases, metadata |
| `list_schemas` | List schemas in a database | cluster_identifier, database_name | schemas, types |
| `list_tables` | List tables in a schema | cluster_identifier, database_name, schema_name | tables, types |
| `list_columns` | List columns in a table | cluster_identifier, database_name, schema_name, table_name | columns, data_types |
| `execute_query` | Execute SQL queries (read-only) | cluster_identifier, database_name, sql | query_results, rows |

### 4. Configure QuickSuite Integration

1. **Amazon QuickSuite** → **Integrations** → **Actions** → **Model Context Protocol**
2. **AgentCore Gateway Endpoint**: Use `GatewayUrl`
3. **Authentication**: Service-to-service OAuth with Cognito credentials
4. **Complete Setup** and test MCP integration

## 🔧 MCP Tools

The AWS LAB Redshift MCP Server provides these Redshift operations:

### Available Tools

- **list_clusters**: Discover available Amazon Redshift clusters and serverless workgroups
- **list_databases**: List databases in a Redshift cluster
- **list_schemas**: List schemas in a database
- **list_tables**: List tables in a schema
- **list_columns**: List columns in a table
- **execute_query**: Execute read-only SQL queries via Redshift Data API

**Note**: All tools are provided by the [AWS LAB Redshift MCP Server](https://awslabs.github.io/mcp/servers/redshift-mcp-server) implementation with built-in read-only security controls.

## QuickSuite Integration

Complete guide to integrate Redshift AgentCore Gateway with Amazon QuickSuite using MCP Actions.

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

- **Name**: Redshift Database Operations
- **Description**: Amazon Redshift cluster discovery and SQL query execution
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
"List all available Redshift clusters"
"Show me databases in cluster my-redshift-cluster"
"List tables in the public schema"
"Execute SELECT * FROM sales_data LIMIT 10"
"Run SELECT COUNT(*) FROM customer_orders WHERE status = 'completed'"
```

## Troubleshooting

**MCP Authentication Issues:**

- Verify OAuth2 credentials in QuickSuite MCP Actions
- Check Cognito token endpoint configuration
- Ensure client secret is correctly copied

**Redshift Access:**

- Verify Amazon Redshift clusters exist and are accessible
- Check IAM permissions for Redshift services
- Confirm cluster status is available

**AgentCore Gateway:**

- Monitor AgentCore Gateway throttling limits
- Check Lambda timeout and memory settings
- Review CORS configuration for QuickSuite

## 📚 Documentation

- [CDK Deployment Guide](cdk/README.md) - Infrastructure details
- [AWS LAB Redshift MCP Server](https://awslabs.github.io/mcp/servers/redshift-mcp-server) - Official MCP server documentation

## License

This library is licensed under the MIT-0 License.
