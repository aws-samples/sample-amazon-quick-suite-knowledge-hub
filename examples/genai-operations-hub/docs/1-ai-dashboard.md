# Task 1: AI Dashboard

Build AI-powered visualizations with the Generative BI capabilities in Quick Sight.

**Duration:** 15-20 minutes

---

## Part 1: Create Topics

Topics enable natural language queries on your datasets. You'll create one topic per dataset so you can ask questions about each one in natural language.

### What are Topics?

Topics in Quick Sight define the business context for your data. They enable non-technical users to ask questions like "What were the invocations yesterday?" without knowing SQL or data structures.

### Step 1: Create Topic for Daily Invocations

Start with the topic for the daily invocations dataset:

1. Navigate to the **AWS Quick Sight Console**
2. Choose **Topics** in the left navigation menu
3. Choose **Create Topic**
4. Configure the topic:
   - **Topic name**: `Daily Bedrock Invocations`
   - **Description**: `Daily invocation logs from Bedrock models`
   - Choose **Continue**
5. Add the dataset:
   - Choose **Datasets**
   - Select `daily-bedrock-invocations`
   - Choose **Add data**
6. Review the auto-generated field names and synonyms
7. Choose **Save**

**Tip:** Quick Sight automatically generates synonyms for your fields (for example, "date" might also be recognized as "day", "time", "when"). You can customize these later if needed.

### Step 2: Create Topic for Model Performance

Repeat the steps for the model performance dataset:

1. Choose **Create Topic** again
2. Configure:
   - **Topic name**: `Model Performance Metrics`
   - **Description**: `Aggregated performance metrics by model`
   - Choose **Continue**
3. Select dataset: `model-performance-metrics`
4. Choose **Add data** and **Save**

### Step 3: Create Topic for Stop Reasons

Then create the last topic, for the stop reasons dataset:

1. Choose **Create Topic** again
2. Configure:
   - **Topic name**: `Stop Reason Analysis`
   - **Description**: `Analysis of model stop reasons`
   - Choose **Continue**
3. Select dataset: `stop-reason-analysis`
4. Choose **Add data** and **Save**

**Validation:**

- ✓ The Daily Bedrock Invocations, Model Performance Metrics, and Stop Reason Analysis topics visible in the Topics list
- ✓ Each topic shows "Ready" status
- ✓ Field names and synonyms are auto-generated

---

## Part 2: Build Dashboard with Generative BI

Use Generative BI in Quick Sight to build visualizations from natural language, with no drag and drop needed.

### What is Generative BI?

Generative BI is the AI-powered feature of AWS Quick Sight that creates visualizations from natural language. Describe what you want to see, and it generates the charts and insights.

### Step 1: Create a New Analysis

Create an analysis on the daily invocations dataset:

1. Choose **Analyses** in the left navigation menu
2. Choose **New analysis**
3. Select dataset: `daily-bedrock-invocations`
4. Choose **Create analysis**

### Step 2: Generate Visualizations with Natural Language

Choose **Build visual with Q** and describe what you want. Try these prompts:

#### Visualization 1: Daily Trends

Plot daily invocation volume:

```text
Show me invocation count by date as a line chart
```

**Tip:** Be specific with chart types ("line chart", "bar chart", "pie chart") for better results.

#### Visualization 2: Model Comparison

Compare invocation volume across models:

```text
Compare total invocations by model ID as a bar chart
```

#### Visualization 3: Latency Analysis

Check which models respond fastest:

```text
Show average latency by model ID
```

#### Visualization 4: Token Usage

Track input and output tokens per day:

```text
Display total input tokens and output tokens by date
```

#### Visualization 5: Performance Over Time

See how latency changes over time for each model:

```text
Show average latency trend over time by model
```

**Pro Tip:** After each visualization is generated, choose **Add to sheet** to pin it to your dashboard.

### Step 3: Add Insights from Other Datasets (Optional)

Switch datasets to create more visualizations:

#### Using Model Performance Dataset

Add the model performance dataset to the analysis:

1. Choose **Add** → **Add dataset**
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

Then add the stop reason dataset:

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

1. Select the visual
2. Use the **Format visual** panel on the right to adjust:
   - **Colors**: Change color schemes
   - **Labels**: Add or modify axis labels
   - **Axis settings**: Adjust scales and ranges
   - **Titles**: Update chart titles
   - **Legends**: Position and format legends

### Step 5: Arrange Your Dashboard

Lay out the visuals so the dashboard reads well:

1. Drag and drop visuals to rearrange them
2. Resize visuals by dragging corners
3. Add text boxes for context:
   - Choose **Insert** → **Text box**
   - Add titles, descriptions, or insights
4. Add filters (optional):
   - Choose **Filter** in the left panel
   - Add filters for date ranges, model IDs, or other fields

---

## Part 3: Publish Dashboard

Publish the analysis as a dashboard, then share it.

### Step 1: Publish Your Dashboard

Publish from the analysis:

1. Choose **Publish dashboard** in the top right corner
2. Configure:
   - **Dashboard name**: `GenAI Operations Dashboard`
   - **Notes** (optional): `Real-time insights into Bedrock model performance and usage`
3. Choose **Publish dashboard**

### Step 2: Share with Users (Optional)

To grant access to specific users:

1. In the published dashboard, choose **Share** → **Share dashboard**
2. Enter email addresses or usernames
3. Set permissions:
   - **Viewer**: Can view the dashboard (read-only)
   - **Co-owner**: Can edit and reshare the dashboard
4. Choose **Share**

**Permissions Tip:** Grant Viewer access to stakeholders who need to monitor metrics, and Co-owner access to analysts who will maintain the dashboard.

### Step 3: Get Shareable Link (Optional)

To create a link anyone in your organization can access:

1. Choose **Share** → **Get shareable link**
2. Toggle **Enable link sharing** ON
3. Copy the generated URL
4. Share the link via email, Slack, or your team wiki

**Security Note:** Anyone with the link can view the dashboard. Use user-specific sharing for sensitive data.

---

## Validation Checklist

Before you continue, confirm each item:

- ✓ Each topic from Part 1 created and showing "Ready" status
- ✓ Analysis created with multiple visualizations
- ✓ Visualizations generated using natural language prompts
- ✓ Dashboard published with name "GenAI Operations Dashboard"
- ✓ Dashboard appears in the Dashboards list
- ✓ You can view the dashboard without edit mode

---

## What You've Accomplished

In this task, you completed the following:

- ✅ Created a topic per dataset for natural language queries
- ✅ Built AI-powered visualizations using Generative BI
- ✅ Published a comprehensive GenAI Operations Dashboard
- ✅ Enabled team collaboration on operational insights

---

## Next Steps

Continue to [Task 2: Create Space](2-create-space.md) to set up a collaborative workspace!
