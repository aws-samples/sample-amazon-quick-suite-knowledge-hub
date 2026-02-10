# Task 1: AI Dashboard

Build AI-powered visualizations using QuickSight's Generative BI capabilities.

**Duration:** 15-20 minutes

---

## Part 1: Create Topics

Topics enable natural language queries on your datasets. You'll create one topic per dataset to unlock AI-powered insights.

### What are Topics?

Topics in QuickSight define the business context for your data. They enable non-technical users to ask questions like "What were the invocations yesterday?" without knowing SQL or data structures.

### Step 1: Create Topic for Daily Invocations

1. Navigate to the **Amazon QuickSight Console**
2. Click **Topics** in the left navigation menu
3. Click **Create Topic**
4. Configure the topic:
   - **Topic name**: `Daily Bedrock Invocations`
   - **Description**: `Daily invocation logs from Bedrock models`
   - Click **Continue**
5. Add the dataset:
   - Choose **Datasets**
   - Select `daily-bedrock-invocations`
   - Click **Add data**
6. Review the auto-generated field names and synonyms
7. Click **Save**

**Tip:** QuickSight automatically generates synonyms for your fields (e.g., "date" might also be recognized as "day", "time", "when"). You can customize these later if needed.

### Step 2: Create Topic for Model Performance

1. Click **Create Topic** again
2. Configure:
   - **Topic name**: `Model Performance Metrics`
   - **Description**: `Aggregated performance metrics by model`
   - Click **Continue**
3. Select dataset: `model-performance-metrics`
4. Click **Add data** and **Save**

### Step 3: Create Topic for Stop Reasons

1. Click **Create Topic** again
2. Configure:
   - **Topic name**: `Stop Reason Analysis`
   - **Description**: `Analysis of model stop reasons`
   - Click **Continue**
3. Select dataset: `stop-reason-analysis`
4. Click **Add data** and **Save**

**Validation:**
- ✓ Three topics visible in the Topics list
- ✓ Each topic shows "Ready" status
- ✓ Field names and synonyms are auto-generated

---

## Part 2: Build Dashboard with Generative BI

Use QuickSight's Generative BI to build visualizations using natural language - no drag and drop needed!

### What is Generative BI?

Generative BI is Amazon QuickSight's AI-powered feature that lets you create visualizations using natural language. Simply describe what you want to see, and it automatically generates the appropriate charts and insights.

### Step 1: Create a New Analysis

1. Click **Analyses** in the left navigation menu
2. Click **New analysis**
3. Select dataset: `daily-bedrock-invocations`
4. Click **Create analysis**

### Step 2: Generate Visualizations with Natural Language

Click **Build visual with Q** and describe what you want. Try these prompts:

#### Visualization 1: Daily Trends

```text
Show me invocation count by date as a line chart
```

**Tip:** Be specific with chart types ("line chart", "bar chart", "pie chart") for better results.

#### Visualization 2: Model Comparison

```text
Compare total invocations by model ID as a bar chart
```

#### Visualization 3: Latency Analysis

```text
Show average latency by model ID
```

#### Visualization 4: Token Usage

```text
Display total input tokens and output tokens by date
```

#### Visualization 5: Performance Over Time

```text
Show average latency trend over time by model
```

**Pro Tip:** After each visualization is generated, click **Add to sheet** to pin it to your dashboard.

### Step 3: Add Insights from Other Datasets (Optional)

Switch datasets to create more visualizations:

#### Using Model Performance Dataset

1. Click **Add** → **Add dataset**
2. Select `model-performance-metrics`
3. Try these prompts:

```text
Show top 3 models by total requests as a bar chart
```

```text
Compare average latency across all models
```

```text
Show the relationship between total requests and average latency as a scatter plot
```

#### Using Stop Reason Analysis Dataset

1. Add dataset: `stop-reason-analysis`
2. Try these prompts:

```text
Show stop reason distribution as a pie chart
```

```text
Which models have the most end_turn occurrences?
```

```text
Compare stop reason counts across different models as a stacked bar chart
```

### Step 4: Customize Visuals (Optional)

For each generated visual, you can refine the appearance:

1. Click the visual to select it
2. Use the **Format visual** panel on the right to adjust:
   - **Colors**: Change color schemes
   - **Labels**: Add or modify axis labels
   - **Axis settings**: Adjust scales and ranges
   - **Titles**: Update chart titles
   - **Legends**: Position and format legends

### Step 5: Arrange Your Dashboard

1. Drag and drop visuals to rearrange them
2. Resize visuals by dragging corners
3. Add text boxes for context:
   - Click **Insert** → **Text box**
   - Add titles, descriptions, or insights
4. Add filters (optional):
   - Click **Filter** in the left panel
   - Add filters for date ranges, model IDs, etc.

---

## Part 3: Publish Dashboard

### Step 1: Publish Your Dashboard

1. Click **Publish dashboard** in the top right corner
2. Configure:
   - **Dashboard name**: `GenAI Operations Dashboard`
   - **Notes** (optional): `Real-time insights into Bedrock model performance and usage`
3. Click **Publish dashboard**

### Step 2: Share with Users (Optional)

To grant access to specific users:

1. In the published dashboard, click **Share** → **Share dashboard**
2. Enter email addresses or usernames
3. Set permissions:
   - **Viewer**: Can view the dashboard (read-only)
   - **Co-owner**: Can edit and reshare the dashboard
4. Click **Share**

**Permissions Tip:** Grant Viewer access to stakeholders who need to monitor metrics, and Co-owner access to analysts who will maintain the dashboard.

### Step 3: Get Shareable Link (Optional)

To create a link anyone in your organization can access:

1. Click **Share** → **Get shareable link**
2. Toggle **Enable link sharing** ON
3. Copy the generated URL
4. Share the link via email, Slack, or your team wiki

**Security Note:** Anyone with the link can view the dashboard. Use user-specific sharing for sensitive data.

---

## Validation Checklist

- ✓ Three topics created and showing "Ready" status
- ✓ Analysis created with multiple visualizations
- ✓ Visualizations generated using natural language prompts
- ✓ Dashboard published with name "GenAI Operations Dashboard"
- ✓ Dashboard appears in the Dashboards list
- ✓ You can view the dashboard without edit mode

---

## What You've Accomplished

✅ Created three topics for natural language queries  
✅ Built AI-powered visualizations using Generative BI  
✅ Published a comprehensive GenAI Operations Dashboard  
✅ Enabled team collaboration on operational insights

---

## Next Steps

Continue to [Task 2: Create Space](2-create-space.md) to set up a collaborative workspace!
