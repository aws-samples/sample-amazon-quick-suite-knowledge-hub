---
category: Capability
description: "Amazon Redshift MCP integration for database operations with Amazon Quick Suite"
---

# Redshift Data Query MCP - Amazon Redshift Database Integration

**Amazon Redshift Data Query MCP integration** with Amazon QuickSuite. This solution creates an MCP integration that enables read-only access to Amazon Redshift clusters through QuickSuite using MCP Actions with AWS LAB Redshift MCP Server.

##  Architecture

![Amazon Redshift Data Query MCP Architecture](./images/redshift-mcp-architecture.png)

**Components:**

- **AgentCore Gateway**: Amazon Bedrock AgentCore Gateway with Lambda target
- **Lambda Wrapper**: Wraps AWS LAB Redshift MCP Server for AgentCore compatibility
- **AWS LAB Redshift MCP Server**: Official AWS LAB MCP server implementation
- **QuickSuite Integration**: MCP Actions for conversational AI
- **Amazon Redshift**: Cluster discovery and SQL query execution

##  Purpose

This MCP integration enables:

- **Direct Redshift Access**: Query Amazon Redshift clusters through natural language
- **QuickSuite Integration**: Integration using MCP Actions
- **AgentCore Gateway**: Gateway with Lambda target and authentication
- **Read-Only Operations**: Secure database discovery and query execution

##  Project Structure

```
redshift-data-query-mcp/
 app.py                              # CDK deployment entry point
 cdk.json                            # CDK configuration
 pyproject.toml                      # Project dependencies
 uv.lock                             # Dependency lock file
 tools/                              # Lambda function code
    redshift_agentcore_lambda.py   # AgentCore MCP handler
    redshift_agentcore_tools.json  # MCP tool definitions
    requirements.txt               # Lambda dependencies
 cdk/                               # Infrastructure code
    redshift_agentcore_stack.py   # AgentCore Gateway stack
    README.md                      # CDK deployment guide
 README.md                          # This file
```

##  Quick Start

### Prerequisites

- **AWS CLI** configured with appropriate permissions
- **Node.js** (for AWS CDK)
- **Python 3.12+** with uv package manager
- **Docker** (required for Lambda dependency bundling)

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
uv sync
cdk deploy --require-approval never
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

Key outputs for QuickSuite Actions:

- `GatewayUrl` - AgentCore Gateway endpoint
- `ClientId` - OAuth2 client ID
- `ClientSecret` - OAuth2 client secret
##  Available Tools

### Redshift Tools (redshift_agentcore_lambda.py)

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `list_clusters` | Discover available Redshift clusters and serverless workgroups | None | cluster_mapping, configurations |
| `list_databases` | List databases in a Redshift cluster | cluster_identifier | database_list, metadata |
| `list_schemas` | List schemas in a database | cluster_identifier, database_name | schema_list, metadata |
| `list_tables` | List tables in a schema | cluster_identifier, database_name, schema_name | table_list, metadata |
| `list_columns` | List columns in a table | cluster_identifier, database_name, schema_name, table_name | column_list, data_types |
| `execute_query` | Execute SQL queries (read-only) | cluster_identifier, database_name, sql | query_results, rows |

### list_clusters

**Purpose**: Discover available Redshift clusters and serverless workgroups
**Input**: None
**Output**: Cluster mapping with identifiers, types, status, endpoints

### list_databases

**Purpose**: List databases in a Redshift cluster
**Input**: `cluster_identifier` (required): Target cluster identifier
**Output**: Database list with names, owners, types

### list_schemas

**Purpose**: List schemas in a database
**Input**:
- `cluster_identifier` (required): Target cluster identifier
- `schema_database_name` (required): Database name

**Output**: Schema list with names, owners, types

### list_tables

**Purpose**: List tables in a schema
**Input**:
- `cluster_identifier` (required): Target cluster identifier
- `table_database_name` (required): Database name
- `table_schema_name` (required): Schema name

**Output**: Table list with names, types, remarks

### list_columns

**Purpose**: List columns in a table
**Input**:
- `cluster_identifier` (required): Target cluster identifier
- `column_database_name` (required): Database name
- `column_schema_name` (required): Schema name
- `column_table_name` (required): Table name

**Output**: Column list with names, data types, constraints

### execute_query

**Purpose**: Execute SQL queries (read-only)
**Input**:
- `cluster_identifier` (required): Target cluster identifier
- `database_name` (required): Database name
- `sql` (required): SQL query string

**Output**: Query results with columns and rows
## QuickSuite Integration

Complete guide to integrate Amazon Redshift with Amazon QuickSuite using MCP Actions.

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

- **Name**: Amazon Redshift Data Query
- **Description**: Amazon Redshift database operations with read-only access
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
"Show me databases in cluster my-cluster"
"What tables are in the public schema of database dev?"
"Execute query: SELECT COUNT(*) FROM sales WHERE date > '2024-01-01'"
```

## Troubleshooting

**MCP Authentication Issues:**

- Verify OAuth2 credentials in QuickSuite MCP Actions
- Check Cognito token endpoint configuration
- Ensure client secret is correctly copied

**Redshift Access:**

- Verify Amazon Redshift cluster exists and is accessible
- Check IAM permissions for Redshift Data API
- Confirm cluster status is available

**AgentCore Gateway:**

- Monitor AgentCore Gateway throttling limits
- Check Lambda timeout and memory settings
- Review CORS configuration for QuickSuite

##  Documentation

- [CDK Deployment Guide](cdk/README.md) - Infrastructure details
- [MCP Tool Definitions](tools/redshift_agentcore_tools.json) - API specifications
- [AWS LAB Redshift MCP Server](https://awslabs.github.io/mcp/servers/redshift-mcp-server) - Official implementation

## License

This library is licensed under the MIT-0 License.
- `CognitoTokenUrl` - OAuth2 token endpoint

### Configure MCP Action in Amazon Quick Suite

**Step 1: Access Integrations**

1. Navigate to **Integrations** in Amazon Quick Suite
2. Select **Actions**
3. Select the **+** button for **Model Context Protocol**

**Step 2: Configure MCP Server**
Complete the MCP configuration:

- **Name**: Amazon Redshift Database Operations
- **Description**: Amazon Redshift cluster discovery and SQL query execution
- **MCP Server Endpoint**: Enter your `GatewayUrl` from AWS CDK deployment outputs
- Select **Next**

**Step 3: Configure Authentication**

1. For Authentication, select **Service Authentication**
2. Keep **Service-to-service OAuth** within the Authentication type field
3. Enter the authentication values from your AWS CDK deployment outputs:

   - **Client ID** → Enter your `ClientId` (ensure no leading/trailing spaces)
   - **Client Secret** → Enter your `ClientSecret` (ensure no leading/trailing spaces)
   - **Token URL** → Enter your `CognitoTokenUrl`

**Step 4: Complete Setup**

1. Select **Create and Continue**
2. Select **Next**
3. Select **Next**

### Usage in Amazon Quick Suite

Example queries:
```
"List all available Redshift clusters"
"Show me databases in cluster my-redshift-cluster"
"List tables in the public schema"
"Execute SELECT * FROM sales_data LIMIT 10"
"Run SELECT COUNT(*) FROM customer_orders WHERE status = 'completed'"
```

## Troubleshooting

**MCP Authentication Issues:**

- Verify OAuth2 credentials in Amazon Quick Suite MCP Actions
- Check Amazon Cognito token endpoint configuration
- Ensure client secret is correctly copied without extra spaces

**Amazon Redshift Access:**

- Verify Amazon Redshift clusters exist and are accessible
- Check AWS Identity and Access Management (IAM) permissions for Amazon Redshift services
- Confirm cluster status is available

**Amazon Bedrock Agent Runtime:**

- Monitor Amazon Bedrock Agent Runtime throttling limits
- Check AWS Lambda timeout and memory settings
- Review Cross-Origin Resource Sharing (CORS) configuration for Amazon Quick Suite

**Deployment Issues:**

- Ensure AWS credentials are configured: `aws configure`
- Check AWS CDK bootstrap: `cdk bootstrap`
- Verify Python 3.9+ and Node.js are installed
- For uv issues, use pip as fallback: `pip install -e .`

## Documentation

- [AWS CDK Deployment Guide](cdk/README.md) - Infrastructure deployment details
- [AWS LAB Redshift MCP Server](https://awslabs.github.io/mcp/servers/redshift-mcp-server) - Official MCP server documentation

## Security

See [CONTRIBUTING](../../HOW-TO-CONTRIBUTE.md) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
