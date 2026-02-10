# Task 4: Flow Automation

Automate daily reports using QuickSight Flows to send insights via email or Slack.

**Duration:** 30-40 minutes

---

## What are QuickSight Flows?

Flows enable you to automate analytics workflows:
- Schedule recurring reports
- Query data from Spaces and Topics
- Use AI agents to generate insights
- Send results via email or Slack
- No code required - built with natural language

---

## Part 1: Set Up Email Integration (Microsoft Outlook)

### Prerequisites

You'll need Azure AD admin privileges to complete this setup. If you don't have these permissions, contact your IT administrator before proceeding.

### Step 1: Register Azure Application

1. Sign in to the **Azure Portal** (portal.azure.com)
2. Navigate to **Azure Active Directory** → **App registrations**
3. Click **New registration**
4. Configure:
   - **Name**: `QuickSight Flows Integration`
   - **Supported account types**: **Accounts in this organizational directory only (Single tenant)**
   - **Redirect URI**: Leave blank for now
5. Click **Register**

### Step 2: Save Application Credentials

After registration, you'll see the app overview page. Copy and save these values:

- **Application (client) ID** - Example: `12345678-1234-1234-1234-123456789abc`
- **Directory (tenant) ID** - Example: `87654321-4321-4321-4321-cba987654321`

**Important:** Keep these IDs handy - you'll need them when creating the QuickSight integration.

### Step 3: Configure API Permissions

1. In your Azure app, click **API permissions** in the left menu
2. Click **Add a permission**
3. Select **Microsoft Graph**
4. Choose **Delegated permissions**
5. Search for and add these permissions:
   - `Mail.ReadWrite`
   - `Mail.Send`
   - `User.Read`
6. Click **Add permissions**
7. Click **Grant admin consent for [Your Organization]**
8. Confirm by clicking **Yes**

You should see green checkmarks next to each permission indicating admin consent was granted.

### Step 4: Generate Client Secret

1. Click **Certificates & secrets** in the left menu
2. Under **Client secrets**, click **New client secret**
3. Add a description: `QuickSight Integration`
4. Set expiration: **12 months** (recommended)
5. Click **Add**
6. **Immediately copy the Value** (not the Secret ID)

**Critical:** The client secret value is shown only once. Save it immediately in a secure location. If you lose it, you'll need to generate a new one.

### Step 5: Construct Token URL

You'll need to construct the token URL for QuickSight. The format is:

```
https://login.microsoftonline.com/{tenant-id}/oauth2/v2.0/token
```

Replace `{tenant-id}` with your Directory (tenant) ID from Step 2.

**Example:**
```
https://login.microsoftonline.com/87654321-4321-4321-4321-cba987654321/oauth2/v2.0/token
```

Save this URL - you'll need it in the next part.

### Step 6: Create QuickSight Integration

1. Go to the **Amazon QuickSight Console**
2. In the left navigation menu, expand **CONNECT APPS AND DATA**
3. Click **Integrations**
4. Click the **Actions** tab at the top
5. Click **Create integration** (or the **+** button)
6. Search for and select **Microsoft Outlook**
7. Click to open the integration setup

### Step 7: Configure Integration Settings

Fill in the integration form:

**Basic Information:**
- **Name**: `Microsoft Outlook Integration`
- **Description (Optional)**: `Send automated reports via email`

**Connection type:**
- Select **Public network**

**Authentication method:**
- Select **Service authentication** (for automated workflows)

**Authentication settings:**
- **Base URL**: `https://graph.microsoft.com/v1.0`
- **Client ID**: Paste your Application (client) ID from Azure
- **Client secret**: Paste your client secret value from Azure
- **Token URL**: Paste the token URL you constructed in Step 5

**Why Service Authentication?** This allows Flows to run automatically without requiring a user to be logged in. User authentication is better for interactive operations like Chat or Q Apps.

### Step 8: Create and Test

1. Click **Create and continue**
2. QuickSight will validate your credentials
3. If successful, you'll see a confirmation message

### Step 9: Share Integration (Optional)

If other users need to use this integration in their Flows:

1. On the **Share integration** screen, add team members by email or username
2. Click **Share**
3. Click **Done**

---

## Part 2: Create Automated Flow

### Step 1: Navigate to Flows

1. Go to the **QuickSight Console**
2. Click **Flows** in the left navigation menu
3. Click **Create flow**

### Step 2: Generate Flow with Natural Language

QuickSight will prompt you to describe your workflow in plain English.

**Enter this prompt:**

```text
Create a daily report flow that:
1. Runs every weekday at 8 AM
2. Queries the GenAI Operations Hub space for yesterday's Bedrock metrics
3. Asks the GenAI Ops Assistant agent to analyze the data
4. Sends a summary email with key metrics and insights
```

Click **Generate**

**AI-Powered Automation:** QuickSight will automatically create a multi-step workflow based on your description!

### Step 3: Review Generated Flow

QuickSight creates a flow with these steps:

**Flow Steps:**
1. **Enter Recipient Email Address** - Input step for email recipient
2. **Query Yesterday's Bedrock Metrics** - Queries the Space
3. **Analyze Bedrock Data** - Uses your chat agent for analysis
4. **Generate Report Summary** - Creates formatted output
5. **Send Daily Report Email** - Sends via Outlook integration

**Expected Errors:**
You'll see error messages at the top:
- "Action connector required" - Need to configure email integration
- "Schedule creation failed" - Need to manually create schedule

These are expected and will be resolved in the following steps.

### Step 4: Configure Email Input

Click on the **Enter Recipient Email Address** step:

1. **Title**: Keep as "Enter Recipient Email Address"
2. **Placeholder**: `Enter the email address to receive the daily report`
3. **Default Value (optional)**: Enter your team's email (e.g., `genai-ops-team@example.com`)
4. Check **Allow override of default value** if you want flexibility
5. Click **Save**

### Step 5: Customize Agent Analysis Prompt

Click on the **Analyze Bedrock Data** step and update the **Prompt instruction**:

```text
Analyze Query Yesterday's Bedrock Metrics Bedrock operations data and provide:

1. Key Metrics Summary:
- Total invocations
- Average latency
- Token usage (input/output)

2. Notable Trends:
- Day-over-day changes
- Model performance variations

3. Recommendations:
- Optimization opportunities
- Potential issues to investigate

Keep the summary concise (under 200 words) and actionable.
```

Click **Save**

**Why customize the prompt?** A well-structured prompt ensures consistent, actionable reports every day.

### Step 6: Configure Email Action

Click on the **Send Daily Report Email** step.

You'll see two errors that need to be fixed:
- **Action connector required**
- **Action required**

**Fix 1: Select Email Connector**

1. **Connector**: Select **Microsoft Outlook Integration** (created in Part 1)
2. **Type**: Select **Send an email (V2)**

**Fix 2: Configure Email Content**

In the **Prompt** field, update it to:

```text
Send an email to Enter Recipient Email Address with the subject 'Daily GenAI Operations Report - [Current Date]'. Include Generate Report Summary as the email body with professional formatting. Attach any relevant charts or metrics from Query Yesterday's Bedrock Metrics if available.
```

**Or use this simpler format:**
- **To**: Reference the input → `Enter Recipient Email Address`
- **Subject**: `Daily GenAI Operations Report - [Current Date]`
- **Body**: Reference the agent output → `Generate Report Summary`

Click **Save**

The error messages should now disappear once the connector and action type are configured.

### Step 7: Create Schedule

Now that the flow is configured, create the schedule:

1. Click **Create Schedule** (from the error banner or schedule icon)
2. Configure schedule settings:

**Set schedule:**
- **Schedule name**: `Daily reports`
- **Description**: `Weekday morning GenAI operations summary`
- **Repeats**: Select **Custom**
  - **Repeat**: Weekly
  - **On**: Check M, T, W, T, F (Monday through Friday)
  - **Start date**: Tomorrow at 8:00 AM
  - **Time zone**: Select your timezone (e.g., America/Denver)

3. Click **Next**

**Inputs:**
- If prompted for the email address input, enter the default recipient
- Click **Next**

**Review and Create:**
- Review the schedule summary
- Click **Create**

### Step 8: View Active Schedule

After creation, you'll see the schedule summary:

**Schedule details:**
- **Name**: Daily reports
- **Frequency**: At 08:00 AM, only on Monday, Tuesday, Wednesday, Thursday, and Friday
- **Status**: Active (toggle on/off)
- **Schedule runs**: Click to view execution history

### Step 9: Test the Flow (Optional)

Before waiting for the scheduled run, test manually:

1. Click **Run mode** in the top toolbar
2. Enter a test email address
3. Click **Start**
4. Monitor the execution in real-time
5. Check your email for the report

**Testing Tips:**
- Use your own email for testing
- Verify all steps complete successfully
- Check that the agent's analysis is relevant
- Confirm email formatting looks professional

### Step 10: Monitor Execution History

Track your flow's performance over time:

1. Open your flow
2. Click on the schedule name
3. Click **Schedule runs**
4. Review:
   - ✅ Completed runs
   - ⏳ In progress
   - ❌ Failed runs
5. Click any run to see detailed execution logs

---

## Validation Checklist

- ✓ Azure app registered with correct name
- ✓ Application (client) ID and Directory (tenant) ID saved
- ✓ API permissions configured: Mail.ReadWrite, Mail.Send, User.Read
- ✓ Admin consent granted (green checkmarks visible)
- ✓ Client secret generated and saved
- ✓ Token URL constructed correctly
- ✓ QuickSight integration created with Service authentication
- ✓ Integration validation successful
- ✓ Flow generated with all required steps
- ✓ Email input configured with default value
- ✓ Agent prompt customized for actionable insights
- ✓ Email connector selected (Microsoft Outlook Integration)
- ✓ Email action configured with proper template
- ✓ Schedule created for weekday mornings at 8 AM
- ✓ Schedule is active
- ✓ Test run completed successfully (optional)

---

## Troubleshooting

### Integration Validation Failed

**Error:** "Unable to authenticate with provided credentials"

**Solutions:**
1. Verify the Client ID and Client secret are copied correctly (no extra spaces)
2. Ensure the Token URL uses your correct tenant ID
3. Confirm admin consent was granted for all API permissions
4. Check that the client secret hasn't expired

### Admin Consent Issues

**Error:** "Need admin approval"

**Solution:** You must have Azure AD admin privileges to grant consent. Contact your IT administrator to:
1. Grant admin consent for the API permissions
2. Or assign you the "Cloud Application Administrator" role

### Token URL Format

**Common mistake:** Using the wrong token URL format

**Correct format:**
```
https://login.microsoftonline.com/{tenant-id}/oauth2/v2.0/token
```

**Incorrect formats:**
- ❌ `https://login.microsoftonline.com/common/oauth2/v2.0/token`
- ❌ `https://login.microsoft.com/{tenant-id}/oauth2/v2.0/token`
- ❌ Missing `/v2.0/token` at the end

### "Action connector required" Error

**Solution:** 
1. Click on the **Send Daily Report Email** step
2. Select your **Microsoft Outlook Integration** from the Connector dropdown
3. Select **Send an email (V2)** as the Type
4. Click **Save**

### "Schedule creation failed" Error

**Solution:**
1. Ensure all flow steps are configured and saved
2. Fix any remaining errors in the flow
3. Click **Create Schedule** again
4. If it still fails, try using the schedule icon in the toolbar instead

### Email Not Received

**Possible causes:**
1. **Wrong email address**: Verify the recipient email in the input step
2. **Integration not authenticated**: Re-authenticate the Outlook integration
3. **Permissions issue**: Ensure the integration has Mail.Send permission in Azure
4. **Spam folder**: Check recipient's spam/junk folder

### Agent Response is Generic

**Solution:**
1. Edit the **Analyze Bedrock Data** step
2. Make the prompt more specific with exact metrics needed
3. Reference the data source explicitly: "Query Yesterday's Bedrock Metrics"
4. Test the agent separately in Chat to verify it has access to the Space

---

## What You've Accomplished

✅ Set up Azure app registration for email integration  
✅ Configured Microsoft Graph API permissions  
✅ Created QuickSight Outlook integration with Service authentication  
✅ Generated automated workflow using natural language  
✅ Configured email recipient input  
✅ Customized AI agent analysis prompt  
✅ Connected email integration for delivery  
✅ Scheduled daily weekday execution at 8 AM  
✅ Enabled automated GenAI operations reporting

---

## Sample Daily Report

Here's what your team will receive each morning:

**Subject:** Daily GenAI Operations Report - January 15, 2025

**Body:**

```
Daily GenAI Operations Report

Key Metrics Summary:
• Total invocations: 1,247 (↑ 8% from previous day)
• Average latency: 342ms (↓ 5% improvement)
• Token usage: 1.2M input, 450K output

Notable Trends:
• Claude 3.5 Sonnet usage increased 15% - now 68% of all requests
• Latency improved across all models, particularly Haiku (↓ 12%)
• Peak usage shifted 1 hour earlier (now 10 AM - 2 PM)

Recommendations:
• Consider scaling Sonnet capacity for continued growth
• Investigate max_tokens stop reasons (6% of requests) - may indicate prompt optimization opportunities
• Monitor cost impact of increased Sonnet usage vs. Haiku

View full dashboard: https://quicksight.aws.amazon.com/...

---
Automated by QuickSight Flows
```

---

## Next Steps

**Congratulations!** You've completed all tasks in the GenAI Operations Hub workshop.

### Enhance Your Flow

Consider these improvements:
1. **Add conditional logic**: Only send email if there are anomalies
2. **Include visualizations**: Attach charts from the dashboard
3. **Multiple recipients**: Add distribution list support
4. **Slack integration**: Send to Slack channel in addition to email
5. **Weekly summaries**: Create a separate flow for weekly rollups

### Additional Resources

- [QuickSight Flows Documentation](https://docs.aws.amazon.com/quicksight/latest/user/flows.html)
- [Microsoft Graph API Reference](https://docs.microsoft.com/en-us/graph/api/overview)
- [Amazon Bedrock Logging](https://docs.aws.amazon.com/bedrock/latest/userguide/model-invocation-logging.html)

