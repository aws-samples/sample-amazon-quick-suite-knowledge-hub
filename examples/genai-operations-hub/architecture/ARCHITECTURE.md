# Architecture Diagram

The GenAI Operations Hub moves Amazon Bedrock invocation logs through Amazon S3, AWS Glue, and Amazon Athena into AWS Quick. In Quick, a dashboard, a space, a custom agent, and flows report on them.

## High-Level Architecture

The numbers in the diagram match the steps in the Data Flow section.

```text
┌─────────────────────────────────────────────────────────────────────┐
│                         GenAI Operations Hub                         │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────┐
│  Amazon Bedrock  │
│  Invocation Logs │
└────────┬─────────┘
         │
         │ (1) Logs generated
         ▼
┌──────────────────┐
│   Amazon S3      │
│  Logs Bucket     │◄──── (2) Upload logs (manual or automated)
└────────┬─────────┘
         │
         │ (3) Query data
         ▼
┌──────────────────┐
│  AWS Glue        │
│  Data Catalog    │
│  - Database      │
│  - Table Schema  │
└────────┬─────────┘
         │
         │ (4) Metadata
         ▼
┌──────────────────┐
│  Amazon Athena   │
│  - Workgroup     │
│  - SQL Queries   │
│  - Views         │
└────────┬─────────┘
         │
         │ (5) Connect data source
         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    AWS Quick Sight                              │
│                                                                   │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────┐│
│  │   Data Source   │  │    Datasets      │  │   Q (AI)        ││
│  │   (Athena)      │─▶│  - Daily Trends  │─▶│  Natural Lang.  ││
│  │                 │  │  - Performance   │  │  Queries        ││
│  └─────────────────┘  │  - Stop Reasons  │  └────────┬────────┘│
│                       └──────────────────┘           │         │
│                                                       │         │
│                              (6) AI builds visuals   │         │
│                                     ▼                │         │
│                       ┌──────────────────┐           │         │
│                       │   Dashboard      │           │         │
│                       │  - Line charts   │           │         │
│                       │  - Bar charts    │           │         │
│                       │  - KPIs          │           │         │
│                       └────────┬─────────┘           │         │
│                                │                     │         │
│                                │ (7) Add to Space    │         │
│                                ▼                     │         │
│                       ┌──────────────────┐           │         │
│                       │     Space        │           │         │
│                       │  - Dashboard     │◄──────────┘         │
│                       │  - Datasets      │                     │
│                       │  - Context       │                     │
│                       └────────┬─────────┘                     │
│                                │                               │
│                                │ (8) Knowledge source          │
│                                ▼                               │
│                       ┌──────────────────┐                     │
│                       │  Custom Agent    │                     │
│                       │  - Q Assistant   │                     │
│                       │  - Chat Interface│                     │
│                       └────────┬─────────┘                     │
│                                │                               │
│                                │ (9) Automated queries         │
│                                ▼                               │
│                       ┌──────────────────┐                     │
│                       │     Flows        │                     │
│                       │  - Schedule      │                     │
│                       │  - Query data    │                     │
│                       │  - Ask agent     │                     │
│                       └────────┬─────────┘                     │
└────────────────────────────────┼───────────────────────────────┘
                                 │
                                 │ (10) Send reports
                                 ▼
                    ┌────────────────────────┐
                    │  Email / Slack         │
                    │  Daily Reports         │
                    └────────────────────────┘
```

## Data Flow

Each numbered step in the diagram does the following:

1. **Log Generation**: Bedrock generates invocation logs
2. **Storage**: Logs stored in S3 bucket
3. **Cataloging**: Glue Data Catalog defines schema
4. **Querying**: Athena queries logs using SQL
5. **Visualization**: Quick Sight connects to Athena
6. **AI Dashboard**: Generative BI builds visuals from natural language
7. **Organization**: Dashboard added to Space
8. **Intelligence**: Custom agent uses Space as knowledge
9. **Automation**: Flows schedule reports
10. **Distribution**: Reports sent to email/Slack

## Components

The sample has four layers, from the AWS resources that the CDK stack deploys to the integrations that deliver reports.

### Infrastructure Layer (CDK)

The CDK stack deploys these resources:

- **S3 Buckets**: Log storage and Athena results
- **Glue Database**: Metadata catalog
- **Glue Table**: Schema definition for logs
- **Athena Workgroup**: Query execution environment
- **IAM Roles**: Quick Sight permissions

### Analytics Layer (Quick Sight)

Quick Sight turns the query results into visuals:

- **Data Source**: Athena connection
- **Datasets**: Structured views of data
- **Generative BI**: AI-powered query interface
- **Dashboard**: Visual analytics

### Intelligence Layer (AWS Quick)

Quick adds context, chat, and automation on top of the dashboard:

- **Space**: Collaborative workspace
- **Custom Agent**: AI assistant
- **Flows**: Automation engine

### Integration Layer

Reports and automation reach people and systems through these integrations:

- **Email**: SES for notifications
- **Slack**: Webhook integration
- **API**: Quick Sight APIs for automation

## Security Architecture

The diagram shows the security controls at each layer.

```text
┌─────────────────────────────────────────────────────────────┐
│                      Security Layers                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   IAM Roles  │  │  S3 Bucket   │  │  QuickSight  │     │
│  │              │  │  Policies    │  │  Permissions │     │
│  │  - QS Access │  │  - Encryption│  │  - Row-level │     │
│  │  - Athena    │  │  - Versioning│  │  - Column    │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                              │
│  ┌──────────────────────────────────────────────────┐      │
│  │           Data Encryption                         │      │
│  │  - At Rest: S3 SSE-S3 or SSE-KMS                │      │
│  │  - In Transit: TLS 1.2+                          │      │
│  └──────────────────────────────────────────────────┘      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Scalability

Each service scales on its own:

- **S3**: Storage that grows with the logs, with no capacity to provision
- **Athena**: Serverless, parallel query execution
- **Quick Sight**: Auto-scales with users
- **SPICE**: In-memory cache for fast queries

## Cost Optimization

These settings keep storage and query costs down:

1. **S3 Lifecycle**: Move old logs to Glacier
2. **Athena**: Use partitioning and compression
3. **SPICE**: Cache frequently accessed data
4. **Quick Sight**: Use reader sessions for viewers

## Monitoring

Use these sources to watch the pipeline:

- **CloudWatch**: Athena query metrics
- **S3 Metrics**: Storage and request metrics
- **Quick Sight**: Dashboard usage analytics
- **Flow Execution**: Success/failure logs
