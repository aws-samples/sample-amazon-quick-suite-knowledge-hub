# Quick Reference Guide

## Repository Overview

**GenAI Operations Hub** - AI-powered operational dashboard for Amazon Bedrock using QuickSight Generative BI, Spaces, and Flows.

## File Structure

```
genai-operations-hub/
├── README.md                    # Main overview
├── DEPLOYMENT.md                # Deployment instructions
├── CONTRIBUTING.md              # Contribution guidelines
├── LICENSE                      # MIT-0 License
├── .gitignore                   # Git ignore rules
├── architecture/
│   └── ARCHITECTURE.md          # Architecture diagrams and details
├── infrastructure/              # CDK Infrastructure as Code
│   ├── app.py                   # CDK app entry point
│   ├── cdk.json                 # CDK configuration
│   ├── requirements.txt         # Python dependencies
│   └── stacks/
│       └── genai_ops_stack.py   # Main CDK stack
├── sample-data/
│   └── bedrock-logs/            # Sample Bedrock invocation logs
└── docs/                        # Step-by-step task guides
    ├── task-1-data-prep.md
    ├── task-2-quicksight-setup.md
    ├── task-3-ai-dashboard.md
    ├── task-4-create-space.md
    ├── task-5-custom-agent.md
    └── task-6-flow-automation.md
```

## Quick Start Commands

### Deploy Infrastructure
```bash
cd infrastructure
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap
cdk deploy
```

### Upload Sample Data
```bash
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name GenAIOperationsStack \
  --query 'Stacks[0].Outputs[?OutputKey==`LogsBucketName`].OutputValue' \
  --output text)
aws s3 cp sample-data/bedrock-logs/ s3://${BUCKET}/ --recursive
```

### Clean Up
```bash
aws s3 rm s3://${BUCKET}/ --recursive
cdk destroy
```

## Tasks

| Task | Description | Time | Difficulty |
|------|-------------|------|------------|
| 0 | Setup (CDK Deployment) | 15 min | Easy |
| 1 | AI Dashboard with Generative BI | 30 min | Medium |
| 2 | Create Space | 10 min | Easy |
| 3 | Custom Agent | 20 min | Medium |
| 4 | Flow Automation | 30 min | Medium |

**Total Time**: ~1.5 hours

## Key AWS Services

- **Amazon S3**: Log storage
- **AWS Glue**: Data catalog
- **Amazon Athena**: SQL queries
- **Amazon QuickSight**: Visualization and AI
  - **Generative BI**: Natural language visualization builder
  - **Spaces**: Collaborative workspaces
  - **Custom Agents**: AI assistants
  - **Flows**: Automation

## Infrastructure Resources

### Created by CDK
- S3 bucket: `genai-ops-bedrock-logs-{account}`
- S3 bucket: `genai-ops-athena-results-{account}`
- Glue database: `genai_ops_db`
- Glue table: `bedrock_invocation_logs`
- Athena workgroup: `genai-ops-workgroup`
- IAM role: QuickSight access

### Created Manually (Console)
- QuickSight data source
- QuickSight datasets (3)
- QuickSight dashboard
- QuickSight Space
- QuickSight custom agent
- QuickSight Flow

## Sample Queries

### Basic Analytics
```sql
-- Total invocations
SELECT COUNT(*) FROM bedrock_invocation_logs;

-- By model
SELECT modelId, COUNT(*) as count
FROM bedrock_invocation_logs
GROUP BY modelId;

-- Average latency
SELECT AVG(output.outputBodyJson.metrics.latencyMs) as avg_latency
FROM bedrock_invocation_logs;
```

### Advanced Analytics
```sql
-- Daily trends
SELECT 
    DATE(from_iso8601_timestamp(timestamp)) as date,
    COUNT(*) as invocations,
    AVG(output.outputBodyJson.metrics.latencyMs) as avg_latency
FROM bedrock_invocation_logs
GROUP BY DATE(from_iso8601_timestamp(timestamp))
ORDER BY date;

-- Token usage by model
SELECT 
    modelId,
    SUM(output.outputBodyJson.usage.inputTokens) as input_tokens,
    SUM(output.outputBodyJson.usage.outputTokens) as output_tokens
FROM bedrock_invocation_logs
GROUP BY modelId;
```

## QuickSight Generative BI Sample Prompts

- "Show me invocation count by date as a line chart"
- "Compare total invocations by model ID as a bar chart"
- "What is the average latency by model?"
- "Display total input tokens and output tokens by date"
- "Show me models with highest average latency"

## Custom Agent Sample Prompts

- "What are the key metrics in our GenAI operations?"
- "Which Bedrock model has the best latency performance?"
- "How have invocations trended over time?"
- "Are there any models with high max_tokens stop reasons?"
- "Compare Claude Sonnet and Claude Opus performance"

## Cost Estimates

| Service | Monthly Cost |
|---------|--------------|
| S3 Storage | $2-5 |
| Athena Queries | $1-3 |
| Glue Catalog | $0.10 |
| QuickSight Enterprise | $18/user |
| **Total** | **~$20-30** |

*Excludes QuickSight user fees*

## Troubleshooting

### CDK Deploy Fails
```bash
# Check permissions
aws sts get-caller-identity

# View errors
cdk deploy --verbose
```

### Athena Query Fails
- Verify Glue table schema
- Check S3 path in table location
- Ensure workgroup is configured

### QuickSight Connection Issues
- Grant S3 permissions to QuickSight
- Verify Athena workgroup access
- Check IAM role policies

## Support

- **Documentation**: See `docs/` folder
- **Architecture**: See `architecture/ARCHITECTURE.md`
- **Deployment**: See `DEPLOYMENT.md`
- **Contributing**: See `CONTRIBUTING.md`

## Links

- [Amazon Quick Suite Knowledge Hub](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/)
- [QuickSight Documentation](https://docs.aws.amazon.com/quicksight/)
- [Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [CDK Documentation](https://docs.aws.amazon.com/cdk/)

## License

MIT-0 - See LICENSE file
