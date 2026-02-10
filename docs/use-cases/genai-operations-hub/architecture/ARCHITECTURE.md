# Architecture Diagram

## High-Level Architecture

```
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
│                    Amazon QuickSight                              │
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

1. **Log Generation**: Bedrock generates invocation logs
2. **Storage**: Logs stored in S3 bucket
3. **Cataloging**: Glue Data Catalog defines schema
4. **Querying**: Athena queries logs using SQL
5. **Visualization**: QuickSight connects to Athena
6. **AI Dashboard**: Q builds visuals from natural language
7. **Organization**: Dashboard added to Space
8. **Intelligence**: Custom agent uses Space as knowledge
9. **Automation**: Flows schedule reports
10. **Distribution**: Reports sent to email/Slack

## Components

### Infrastructure Layer (CDK)
- **S3 Buckets**: Log storage and Athena results
- **Glue Database**: Metadata catalog
- **Glue Table**: Schema definition for logs
- **Athena Workgroup**: Query execution environment
- **IAM Roles**: QuickSight permissions

### Analytics Layer (QuickSight)
- **Data Source**: Athena connection
- **Datasets**: Structured views of data
- **Q**: AI-powered query interface
- **Dashboard**: Visual analytics

### Intelligence Layer (QuickSight)
- **Space**: Collaborative workspace
- **Custom Agent**: AI assistant
- **Flows**: Automation engine

### Integration Layer
- **Email**: SES for notifications
- **Slack**: Webhook integration
- **API**: QuickSight APIs for automation

## Security Architecture

```
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

- **S3**: Unlimited storage, auto-scaling
- **Athena**: Serverless, parallel query execution
- **QuickSight**: Auto-scales with users
- **SPICE**: In-memory cache for fast queries

## Cost Optimization

1. **S3 Lifecycle**: Move old logs to Glacier
2. **Athena**: Use partitioning and compression
3. **SPICE**: Cache frequently accessed data
4. **QuickSight**: Use reader sessions for viewers

## Monitoring

- **CloudWatch**: Athena query metrics
- **S3 Metrics**: Storage and request metrics
- **QuickSight**: Dashboard usage analytics
- **Flow Execution**: Success/failure logs
