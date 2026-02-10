# Task 3: Custom Agent

Build a custom AI chat agent connected to your QuickSight Space for conversational analytics.

**Duration:** 15-20 minutes

---

## What is a Custom Agent?

A custom agent is an AI-powered chat interface that can:
- Answer natural language questions about your data
- Reference dashboards and topics in your Space
- Provide insights and recommendations
- Maintain conversation context across multiple queries

---

## Part 1: Create and Configure Agent

### Step 1: Navigate to Chat Agents

1. Go to the **Amazon QuickSight Console**
2. Click **Chat agents** in the left navigation menu
3. This opens the chat agents interface

### Step 2: Create Your Custom Agent

QuickSight offers two ways to create an agent. Choose the method that works best for you:

#### Option A: Using Natural Language Prompt (Recommended)

This is the fastest way to create an agent:

1. Click **Create chat agent**
2. In the text input box, enter:

```text
Create an AI assistant for analyzing Amazon Bedrock model invocation logs. 
The agent should help users understand model performance, usage trends, 
latency metrics, and token consumption patterns.
```

3. Click **Generate**
4. Review the auto-generated configuration
5. Click **Update preview** to test
6. When satisfied, click **Launch chat agent**

**Success!** The AI will automatically configure the agent's persona, communication style, and suggested prompts based on your description!

#### Option B: Manual Configuration (Advanced)

For more control over the agent's behavior:

1. Click **Create chat agent**
2. Click **Skip** to open builder mode
3. Configure basic details:
   - **Agent name**: `GenAI Ops Assistant`
   - **Description**: `AI assistant for Bedrock operations insights`
   - **Icon**: Choose an appropriate icon (optional)

Continue to the next steps for detailed configuration.

### Step 3: Configure Agent Persona (Manual Configuration Only)

The agent persona defines how your agent identifies itself and behaves.

In the **AGENT PERSONA** section:

1. **Agent identity:**

```text
You are the GenAI Operations Assistant, an AI expert specialized in 
Amazon Bedrock model invocations and performance analysis.
```

2. **Persona instructions:**

```text
Your primary tasks are:
- Analyze Bedrock model invocation logs
- Explain performance metrics (latency, tokens)
- Identify usage trends and patterns
- Provide optimization recommendations
- Answer questions about stop reasons and model behavior

Always reference specific data from dashboards and provide actionable insights.
```

**What are persona instructions?** These guide the agent's behavior and focus. Well-crafted instructions help the agent provide more relevant, actionable responses.

### Step 4: Configure Communication Style (Manual Configuration Only)

Define how the agent communicates with users.

In the **Communication style** section:

1. **Pick a response style preset**: Choose **Technical**
2. Customize the preset:
   - **Tone**: `Professional and data-driven`
   - **Response format**: `Use bullet points for metrics. Include specific numbers and percentages.`
   - **Length**: `Keep answers concise but comprehensive, typically 100-200 words.`

**Best Practice:** Match the communication style to your audience. Technical teams prefer detailed metrics, while executives prefer high-level summaries.

### Step 5: Link Knowledge Source (Space)

Connect your agent to the Space you created:

1. In the **Knowledge sources** section, select **Link specific existing spaces**
2. Click **Link**
3. Select: `GenAI Operations Hub`
4. Click **Link**

**Critical:** The agent needs access to your Space to answer questions about your data. Without this link, it won't have context about your Bedrock operations.

### Step 6: Add Suggested Prompts (Optional)

Help users get started with pre-written questions:

1. In the **Suggested prompts** section, click **Add prompt**
2. Add these example prompts:

```text
What are the key metrics in our GenAI operations?
```

```text
Which Bedrock model has the best latency performance?
```

```text
How have invocations trended over time?
```

```text
Are there any models with high max_tokens stop reasons?
```

3. Click **Save** after adding each prompt

---

## Part 2: Test and Launch Agent

### Step 1: Test with Sample Questions

Use the preview panel to test your agent with these sample questions:

#### Test Question 1: Overview
```text
What are the key metrics in our GenAI operations?
```

**Expected:** The agent should reference specific metrics from your dashboard (invocation counts, latency, token usage).

#### Test Question 2: Performance
```text
Which Bedrock model has the best latency performance?
```

**Expected:** The agent should compare models and provide specific latency numbers.

#### Test Question 3: Trends
```text
How have invocations trended over time?
```

**Expected:** The agent should describe trends with specific dates and numbers.

#### Test Question 4: Optimization
```text
Are there any models with high max_tokens stop reasons?
```

**Expected:** The agent should identify models with token limit issues and suggest optimization.

**Testing Tips:**
- Ask follow-up questions to test conversation continuity
- Try vague questions to see how the agent handles ambiguity
- Test edge cases (e.g., "What about yesterday's data?")

### Step 2: Launch Your Agent

Once testing is complete:

1. Click **Launch chat agent**
2. Your agent is now live and ready to use!

**Congratulations!** Your custom agent is now available to analyze Bedrock operations.

### Step 3: Share Agent (Optional)

To share your agent with team members:

1. From the **Chat agents** page, find your agent
2. Click the menu icon (three dots) → **Share**
3. Add team members by email or username
4. Set permissions:
   - **Owner**: Can edit, share, and delete the agent
   - **Viewer**: Can view and use the agent
5. Click **Share**

---

## Sample Conversations

Here are example conversations you can have with your agent:

### Conversation 1: Daily Report

**You:** "Give me a summary of today's Bedrock activity"

**Agent:** "Based on the GenAI Operations Dashboard:
- Total invocations: 1,247
- Most active model: Claude 3.5 Sonnet (68% of requests)
- Average latency: 342ms
- Total tokens processed: 1.2M input, 450K output
- Primary stop reason: end_turn (94%)"

### Conversation 2: Performance Analysis

**You:** "Which model should we use for low-latency applications?"

**Agent:** "Based on the Model Performance Metrics:
- Claude 3.5 Haiku has the lowest average latency at 187ms
- Claude 3.5 Sonnet averages 342ms
- For latency-sensitive applications, I recommend Claude 3.5 Haiku
- Note: Haiku also has 23% lower token costs"

### Conversation 3: Troubleshooting

**You:** "Are there any concerning patterns in our stop reasons?"

**Agent:** "Analyzing the Stop Reason Analysis:
- 6% of requests hit max_tokens limit
- This suggests prompts may need optimization
- Recommendation: Review prompts for Claude 3 Opus (highest max_tokens rate at 12%)
- Consider increasing max_tokens parameter or refining prompt length"

---

## Validation Checklist

- ✓ Agent created with appropriate name and description
- ✓ Agent persona configured (if using manual configuration)
- ✓ Communication style set to match audience
- ✓ Space "GenAI Operations Hub" linked as knowledge source
- ✓ Suggested prompts added (optional)
- ✓ Agent tested with sample questions
- ✓ Agent responses reference specific data from dashboards
- ✓ Agent launched and available for use

---

## What You've Accomplished

✅ Created a custom AI chat agent  
✅ Configured agent persona and communication style  
✅ Connected agent to your QuickSight Space  
✅ Tested agent with operational questions  
✅ Launched agent for team use  
✅ Enabled conversational analytics for Bedrock operations

---

## Next Steps

Continue to [Task 4: Flow Automation](4-flow-automation.md) to schedule automated reports!
