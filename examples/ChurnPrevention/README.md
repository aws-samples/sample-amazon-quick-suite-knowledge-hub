# Preventing Churn Before It Happens

A contact center use case powered by Amazon Quick — Flows, Automate, and MCP Actions.

## The Problem

Unhappy customers leave without warning. Contact center teams spend days manually analyzing call data, while sentiment stays buried in unstructured transcripts. By the time someone flags an at-risk customer, it's too late — they've already churned. The result: lost revenue, generic retention offers that don't land, and no way to personalize at scale.

## The Solution

This use case turns reactive retention into proactive intervention. Using Amazon Quick, the entire pipeline — from detecting frustrated customers to delivering personalized bonus offers — runs in minutes, not days.

```
Identify → Analyze → Automate → Act
```

1. **Identify** — A Quick AI Chat Agent queries contact center data to surface customers with low satisfaction (CSAT ≤ 2)
2. **Analyze** — Sentiment analysis on call transcripts reveals frustration levels and root causes
3. **Automate** — Quick Flows converts the analysis into a reusable, repeatable workflow
4. **Act** — Quick Automate scores and ranks customers using the MCP scoring server, then generates personalized retention letters and uploads them to S3

### Workflow Steps (Quick Automate)

| Step | What It Does | Type |
|------|-------------|------|
| 1 | Extract negative sentiment document and contact center dataset from S3 | AI Agent |
| 2 | Identify unhappy customers and filter the dataset | AI Agent |
| 3 | Aggregate KPIs — CSAT, FCR, AHT — for those customers | AI Agent |
| 4 | Calculate lifetime value and pick top 2 customers | Deterministic (MCP Action) |
| 5 | Generate personalized bonus letters | Deterministic |
| 6 | Upload letters to S3 | Deterministic |

## What's in This Folder

**`mcp_customer_score.py`** — An AWS Lambda function that implements the Model Context Protocol (MCP). It's the scoring engine used in Step 4 of the workflow. Quick Automate calls it via an MCP Action connector backed by an API Gateway endpoint.

### What the Scoring Server Does

It exposes three tools over MCP:

- **`calculate_customer_scores`** — Takes contact center metrics (call volume, CSAT, first-call resolution, hold time, transfer count, complaint ratio) and produces a weighted loyalty score (0–100) with tier assignment (Platinum / Gold / Silver / Bronze)
- **`get_top_customers`** — Returns the top N customers by score for bonus allocation
- **`apply_scoring_rules`** — Applies custom business rules to filter and qualify customers

### Scoring Weights

| Factor | Weight | What It Measures |
|--------|--------|------------------|
| Satisfaction | 30% | Average CSAT score |
| Engagement | 25% | Call frequency |
| Resolution | 20% | First-call resolution + overall resolution rate |
| Efficiency | 15% | Low transfers and hold time |
| Issue Severity | 10% | Complaint-to-call ratio |

## How to Deploy

1. Deploy `mcp_customer_score.py` as an AWS Lambda function (Python 3.12)
2. Create an API Gateway REST endpoint pointing to the Lambda
3. In Amazon Quick, register the API Gateway URL as an MCP Action connector
4. Build the Quick Automate workflow using the six steps above — the scoring step calls the MCP Action

## Business Benefits

- **Minutes, not days** — From detection to personalized retention offer
- **Proactive** — Early warning catches at-risk customers before they churn
- **Personalized** — Offers are based on actual call center metrics, not one-size-fits-all
- **No coding for the workflow** — Business users build and modify everything in Quick Automate; the only code is this Lambda function

## Prerequisites

- AWS account with Amazon Quick enabled
- S3 bucket with contact center data (structured CSV) and negative sentiment documents
- IAM role with Lambda execution and S3 read/write permissions

## AWS Services

Amazon Quick (Chat Agent, Flows, Automate) · Amazon S3 · AWS Lambda · API Gateway · MCP Actions
