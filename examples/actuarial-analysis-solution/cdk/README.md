# Actuarial Analysis Solution CDK Deployment

This CDK app deploys the data, compute, and integration resources behind the Actuarial Analysis Tools, ready to connect to AWS Quick.

## What This Deploys

The stack creates these resources:

1. **S3 Buckets**: Claims data storage and Athena results (stack-prefixed naming)
2. **Athena Integration**: SQL queries with Glue catalog
   - Glue Database: `{stack-name}-claims-db-{unique_id}`
   - Glue Crawler: Auto-discovers schema
   - Athena Workgroup: `{stack-name}-actuarial-workgroup-{unique_id}`
3. **Lambda Functions** (PythonFunction construct)
   - Actuarial analysis tools (7 specialized tools)
   - Data query tools (SQL interface)
4. **AgentCore Memory**: Session-based data persistence (7-day retention)
5. **AgentCore Gateway**: Native MCP protocol with OAuth2 authentication
6. **IAM Roles**: Least-privilege permissions for all services

## Architecture Components

The memory and the gateway are the parts that tie the tools together.

### AgentCore Memory

The memory keeps results from one tool call available to the next:

- **Purpose**: Stores intermediate results between tool calls
- **Benefits**: Eliminates redundant calculations, enables complex workflows
- **Storage**: BedrockAgentCore memory service with 7-day event expiry
- **Naming**: `ActuarialAgentMemory_{unique_id}` (follows AWS naming pattern)

### AgentCore Gateway

The gateway is the MCP endpoint that Quick calls:

- **Authentication**: Cognito OAuth2 with client credentials
- **Protocol**: MCP
- **Tools Integration**: Both actuarial and data query tools via GatewayTargets
- **URL Format**: `https://{gateway-id}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp`

### Resource Naming

All resources use consistent naming: `{stack-name}-resource-type-{unique_id}`

Examples:

- S3 Buckets: `quicksuite-actuarial-mcp-claims-a1b2c3d4`
- Lambda Functions: `quicksuite-actuarial-mcp-actuarial-tools-a1b2c3d4`
- Database: `quicksuite-actuarial-mcp-claims-db-a1b2c3d4`

## Deployment

Install the tools once, then deploy the stack.

### Prerequisites

Install the CDK CLI and the Python dependencies:

```bash
# Install CDK
npm install -g aws-cdk

# Install Python dependencies
uv sync
```

### Deploy Everything

Run the deployment from the solution folder:

```bash
cd actuarial-analysis-solution
cdk deploy --require-approval never
```

## What Gets Created

The resources fall into data, compute, and integration layers.

### Data Layer

The data layer stores and catalogs the claims:

- S3 bucket for claims data with sample dataset
- Glue crawler for automatic schema discovery
- Athena workgroup for optimized queries

### Compute Layer

The compute layer runs the tools and the setup automation:

- **Actuarial Lambda**: 7 specialized analysis tools
- **Data Query Lambda**: Flexible SQL interface
- **Memory Creator Lambda**: AgentCore memory management
- **Gateway Creator Lambda**: Gateway deployment automation

### Integration Layer

The integration layer connects the tools to Quick:

- **AgentCore Memory**: Session-based data persistence
- **AgentCore Gateway**: Natural language interface
- **OAuth2 Authentication**: Authenticated API access with client credentials

## Key Outputs

After deployment, the stack outputs these values:

```text
GatewayUrl: https://{gateway-id}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
ClientId: {cognito-client-id}
ClientSecret: {cognito-client-secret}
TokenEndpoint: https://{domain}.auth.us-east-1.amazoncognito.com/oauth2/token
```

## Testing

Check the data and the gateway after deployment.

### 1. Athena Queries

Run these queries in Athena to confirm the claims table loaded:

```sql
SELECT * FROM claims_db_{suffix}.claims LIMIT 10;
SELECT line_of_business, COUNT(*) FROM claims_db_{suffix}.claims GROUP BY 1;
```

### 2. Gateway Authentication

Request an OAuth2 token with the client credentials from the outputs:

```bash
# Get OAuth2 token
curl -X POST {TokenEndpoint} \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id={ClientId}&client_secret={ClientSecret}&scope={Scope}"
```

## Cleanup

To delete the stack and its resources, run:

```bash
cdk destroy --force
```
