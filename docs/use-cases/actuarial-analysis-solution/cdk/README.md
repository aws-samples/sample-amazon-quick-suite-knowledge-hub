# Actuarial Analysis Solution CDK Deployment

##  What This Deploys

1. **S3 Buckets** - Claims data storage and Athena results (stack-prefixed naming)
2. **Athena Integration** - SQL queries with Glue catalog
   - Glue Database: `{stack-name}-claims-db-{unique_id}`
   - Glue Crawler: Auto-discovers schema
   - Athena Workgroup: `{stack-name}-actuarial-workgroup-{unique_id}`
3. **Lambda Functions** (PythonFunction construct)
   - Actuarial analysis tools (7 specialized tools)
   - Data query tools (SQL interface)
4. **AgentCore Memory** - Session-based data persistence (7-day retention)
5. **AgentCore Gateway** - Native MCP protocol with OAuth2 authentication
6. **IAM Roles** - Least-privilege permissions for all services

##  Architecture Components

### AgentCore Memory

- **Purpose**: Stores intermediate results between tool calls
- **Benefits**: Eliminates redundant calculations, enables complex workflows
- **Storage**: BedrockAgentCore memory service with 7-day event expiry
- **Naming**: `ActuarialAgentMemory_{unique_id}` (follows AWS naming pattern)

### AgentCore Gateway

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

##  Deployment

### Prerequisites

```bash
# Install CDK
npm install -g aws-cdk

# Install Python dependencies
uv sync
```

### Deploy Everything

```bash
cd actuarial-analysis-solution
cdk deploy --require-approval never
```

##  What Gets Created

### Data Layer

- S3 bucket for claims data with sample dataset
- Glue crawler for automatic schema discovery
- Athena workgroup for optimized queries

### Compute Layer

- **Actuarial Lambda**: 7 specialized analysis tools
- **Data Query Lambda**: Flexible SQL interface
- **Memory Creator Lambda**: AgentCore memory management
- **Gateway Creator Lambda**: Gateway deployment automation

### Integration Layer

- **AgentCore Memory**: Session-based data persistence
- **AgentCore Gateway**: Natural language interface
- **OAuth2 Authentication**: Secure API access

##  Key Outputs

After deployment:

```
GatewayUrl: https://{gateway-id}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
ClientId: {cognito-client-id}
ClientSecret: {cognito-client-secret}
TokenEndpoint: https://{domain}.auth.us-east-1.amazoncognito.com/oauth2/token
```

##  Testing

### 1. Athena Queries

```sql
SELECT * FROM claims_db_{suffix}.claims LIMIT 10;
SELECT line_of_business, COUNT(*) FROM claims_db_{suffix}.claims GROUP BY 1;
```

### 2. Gateway Authentication

```bash
# Get OAuth2 token
curl -X POST {TokenEndpoint} \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id={ClientId}&client_secret={ClientSecret}&scope={Scope}"
```

##  Cleanup

```bash
cdk destroy --force
```
