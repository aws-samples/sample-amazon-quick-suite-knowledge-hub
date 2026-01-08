# Actuarial Analysis Solution CDK Deployment

## 📦 What This Deploys

1. **S3 Buckets** - Claims data storage and Athena results
2. **Athena Integration** - SQL queries with Glue catalog
   - Glue Database: `claims_db_{suffix}`
   - Glue Crawler: Auto-discovers schema
   - Athena Workgroup: `actuarial-workgroup`
3. **Lambda Functions**
   - Actuarial analysis tools (7 specialized tools)
   - Data query tools (SQL interface)
4. **AgentCore Memory** - Session-based data persistence
5. **AgentCore Gateway** - Natural language interface with OAuth2 authentication
6. **IAM Roles** - Least-privilege permissions for all services

## 🏗️ Architecture Components

### AgentCore Memory

- **Purpose**: Stores intermediate results between tool calls
- **Benefits**: Eliminates redundant calculations, enables complex workflows
- **Storage**: Bedrock AgentCore memory service

### AgentCore Gateway

- **Authentication**: Cognito OAuth2 with client credentials
- **Protocol**: MCP (Model Context Protocol)
- **Tools Integration**: Both actuarial and data query tools
- **URL Format**: `https://{gateway-id}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp`

## 🚀 Deployment

### Prerequisites

```bash
# Install CDK
npm install -g aws-cdk

# Install Python dependencies
pip install -r requirements.txt
```

### Deploy Everything

```bash
cd actuarial-analysis-solution
cdk deploy --require-approval never
```

## 📊 What Gets Created

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

## 🔧 Key Outputs

After deployment:

```
GatewayUrl: https://{gateway-id}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
ClientId: {cognito-client-id}
ClientSecret: {cognito-client-secret}
TokenEndpoint: https://{domain}.auth.us-east-1.amazoncognito.com/oauth2/token
```

## 🧪 Testing

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

## 🗑️ Cleanup

```bash
cdk destroy --force
```
