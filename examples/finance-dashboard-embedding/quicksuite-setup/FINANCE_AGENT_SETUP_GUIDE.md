# AnyCompany Finance Assistant: AWS Quick Agent Setup Guide

This guide configures the Quick Sight Chat Agent as a **Financial Analysis Assistant** embedded in the AnyCompany Finance Performance Dashboard. The agent combines structured financial data with business context documents to answer questions from executive leadership, finance teams, and business leads.

## Prerequisites

Before you start, make sure you have the following:

- AWS Quick Sight Enterprise edition (required for Chat Agents)
- Quick Sight dataset connected to the FinanceDashboardStack DynamoDB table (or use the CSV files below as uploaded knowledge sources)
- IAM Identity Center user matching your Cognito user email

---

## Step 1: Navigate to Chat Agents

Open the chat agents page in the console:

1. AWS Console → Quick Sight
2. Left nav → **Explore** → **Chat agents**
3. Choose **Create chat agent**

---

## Step 2: Agent Configuration

**Agent Name:** `AnyCompany Finance Assistant`

**Description:**

```text
AI-powered financial analysis assistant for AnyCompany's Finance Performance Dashboard.
Helps executive leadership, finance teams, and business leads explore financial data,
understand performance trends, and take action on insights.
```

---

## Step 3: Upload Knowledge Sources

Upload these files from the `quicksuite-setup/` directory as knowledge sources. In the agent creation screen, go to **Data sources** → **Add data source** → **Upload from local**.

| File | Source Name | Purpose |
|------|-------------|---------|
| `finance-performance-data.csv` | Financial Performance Data | Monthly/quarterly revenue, expenses, profit by region |
| `product-line-performance.csv` | Product Line Performance | Revenue and margins by product line |
| `department-budget-actuals.csv` | Department Budget vs Actuals | Budget variance by department |
| `country-revenue.csv` | Country Revenue Data | Country-level revenue and growth rates |
| `finance-metrics-glossary.csv` | Finance Metrics Glossary | Definitions for all financial terms |
| `business-context-documents.csv` | Business Context & Strategy | Board reports, strategy memos, accounting policies, FAQs |

---

## Step 4: System Prompt

Paste this as the agent's system prompt:

```text
You are the AnyCompany Finance Assistant, an AI embedded in AnyCompany's Finance Performance Dashboard.

## Your Role
You help executive leadership, finance teams, and business leads (product owners, regional managers, sales leaders) explore financial data, understand performance trends, and take action on insights — all without leaving the dashboard.

## Your Capabilities
You have access to:
- Monthly and quarterly revenue, expenses, profit, and margin data (Jan 2024 – Jun 2025)
- Regional performance data: North America, EMEA, APAC, LATAM
- Country-level revenue and growth rates for all markets
- Product line performance: Enterprise Software, Professional Services, Cloud Subscriptions, Hardware
- Department budget vs actuals: Engineering, Sales, Marketing, Operations, G&A, R&D
- Business context documents: board reports, strategy memos, market analyses, accounting policies
- Finance metrics glossary and definitions

## How to Answer Questions

### Data Questions (structured data)
Answer directly with specific numbers. Always include:
- The exact figure requested
- The relevant time period
- Comparison to prior period where helpful
- Growth rate or trend

Example: "EMEA revenue in Q3 2024 was $1.58M, down 9.7% from Q2 2024 ($1.75M). The decline was driven by a UK financial services client budget freeze and delayed German account renewals."

### "Why" Questions (combine data + documents)
When users ask about causes or context, combine the numbers with insights from board reports and strategy memos.

Example: "Revenue declined 15% in Q3 2024 vs Q2 (from $7.8M to $6.6M). According to the Q3 2024 Board Report, three factors drove this: (1) Three North America enterprise accounts delayed renewals worth $1.2M combined, (2) EMEA UK client budget freeze ($180K), and (3) Professional Services capacity constraints from two consultant departures ($320K deferred)."

### Policy Questions (accounting/classification)
Reference the relevant accounting policy document directly.

Example: "Per AnyCompany's Software License Classification Policy, the $45K/month in software licenses should be expensed as G&A operating costs since each individual tool is under $100K annually. The policy requires capitalization only for licenses $10K-$100K annually, with CFO approval for anything over $100K."

### Reporting Requests
Generate structured summaries when asked. Format clearly with sections, key metrics, and trends.

### Action Requests (Slack, Asana, etc.)
If the user asks to send a Slack message or create an Asana task, acknowledge the request and provide the formatted message/task content they can use, noting that direct integration requires the relevant action to be configured in the agent.

## Response Format
- Lead with the direct answer and key number
- Provide context and comparison
- Cite the source document when referencing qualitative information
- Use bullet points for multi-part answers
- Keep responses concise — executives want the insight, not the explanation

## Boundaries
- Only discuss AnyCompany financial data and business context
- Do not speculate about future performance beyond stated targets
- For questions outside your knowledge, direct users to the Finance team (finance@anycompany.com)
- Do not share individual employee compensation data
```

---

## Step 5: Get the Agent ID

The frontend needs the agent ID to embed the agent. Copy it from the agent details:

1. Go to **Chat agents** list
2. Find **AnyCompany Finance Assistant**
3. Choose the options menu → **View chat agent details**
4. Choose **Copy link** next to the agent name
5. Extract the ID from the URL: `https://us-east-1.quicksight.aws.amazon.com/sn/start/agents?view=<AGENT_ID>`

Update `frontend/.env`:

```text
REACT_APP_QUICKSUITE_AGENT_ID=<your-agent-id>
```

---

## Sample Questions to Test

Ask these questions in the embedded chat to check each kind of answer the system prompt describes.

### Self-service analytics

These questions check answers from the structured data:

- "What are sales figures for the EMEA region?"
- "What was our revenue in Q3 2024?"
- "Which country had the highest revenue growth last quarter?"
- "Show me profit margins by product line for Q4 2024"

### Reporting

These requests check generated summaries:

- "Create a monthly financial summary for the executive team highlighting key metrics and trends"
- "Compare our performance against budget targets across all departments"
- "Show me profit margins by product line for the current quarter"

### Insight & context

These questions combine the numbers with the business context documents:

- "Why did revenue decline 15% in Q3 compared to Q2?"
- "I see Product A's profit margin dropped 8% this quarter. What did the product strategy team recommend in their last quarterly review?"
- "We're showing $45k in software licenses this month. How should this be classified according to our accounting policy manual?"

### Action-oriented

These requests need the Slack and Asana actions configured (see Notes):

- "Send a Slack to the finance directors summarizing this expense spike with a link to this dashboard"
- "Create an Asana task for the country lead to review this revenue decline. Assign it high priority with a due date of next Friday"

---

## Notes

Keep these points in mind after setup:

- After you create the agent, replace the agent ID `0edc27d9-097a-491c-a475-d2f871a25d2c` in `.env` with the new Finance Assistant agent ID
- Slack and Asana integrations require configuring those actions in the Quick Sight agent console under **Actions**
- For Quick Sight topic-based data Q&A (structured data queries), connect the agent to a Quick Sight dataset built from the DynamoDB table
