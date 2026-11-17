# Task 2: Create Space

Set up a Quick Sight Space to organize your dashboard and enable AI-powered queries.

**Duration:** 8-12 minutes

---

## What is a Quick Sight Space?

A Space is a collaborative workspace that brings together dashboards, topics, and AI capabilities. Spaces enable:

- Centralized access to related analytics content
- Natural language queries across multiple datasets
- Team collaboration with granular permissions
- Integration with Amazon Q for conversational AI

---

## Part 1: Create and Configure Space

In this part, you create the Space and add the dashboard and topics from Task 1.

### Step 1: Create a New Space

Create the Space from the console:

1. Navigate to the **AWS Quick Sight Console**
2. Choose **Spaces** in the left navigation menu
3. Choose **Create space**
4. Configure your Space:
   - **Space name**: `GenAI Operations Hub`
   - **Description**: `Operational insights for Bedrock model invocations`

**Tip:** Space names should be descriptive and follow your organization's naming conventions. This makes them easier to find and understand their purpose.

### Step 2: Add Dashboard to Space

Add the dashboard you published in Task 1:

1. Choose the **Dashboards** button
2. Select **Dashboard** from the content type options
3. Choose your dashboard: **GenAI Operations Dashboard**
4. Choose **Add**

**Important:** Only published dashboards can be added to Spaces. If you don't see your dashboard, verify it was published in Task 1.

### Step 3: Add Topics to Space

Topics enable natural language queries within your Space. Add each topic you created in Task 1:

1. In the Space, choose **Topics**
2. Multi-select each of the following topics one at a time:
   - `Daily Bedrock Invocations`
   - `Model Performance Metrics`
   - `Stop Reason Analysis`
3. Choose **Add** after selecting each topic

**Why Topics Matter:** Topics define the business context for your data. When integrated with Amazon Q, they enable non-technical users to ask questions without knowing SQL.

### Step 4: Capture Your Space URL

You'll need your Space URL in the next task, where you create a custom AI agent.

1. While viewing your Space, copy the URL from your browser's address bar
2. The format will be:

   ```text
   https://<region>.quicksight.aws.amazon.com/sn/spaces/<space-id>
   ```

3. Save this URL in a notes file or text editor

**Important:** Keep this Space URL handy! You'll need the `<space-id>` portion when you configure your chat agent in Task 3.

---

## Part 2: Configure Permissions

In this part, you decide who else can use the Space.

### Step 1: Share Space with Team Members (Optional)

Enable team collaboration by sharing the Space:

1. Choose the **Share space** button
2. Add team members by entering their email addresses or usernames
3. Assign appropriate permission levels:
   - **Viewer**: Can view all content in the Space (read-only)
   - **Contributor**: Can add/edit content but not manage Space settings
   - **Owner**: Full control including Space settings and deletion
4. Choose **Share** to send invitations

**Permission Guidelines:**

- Grant **Viewer** access to stakeholders who need to monitor metrics
- Grant **Contributor** access to analysts who will refine dashboards and topics
- Grant **Owner** access sparingly to team leads responsible for Space governance

### Step 2: Test Space Access (Optional)

If you shared the Space, verify access:

1. Ask a team member to navigate to the Space URL
2. Confirm they can view the dashboard and topics
3. Verify their permissions match what you assigned

---

## Validation Checklist

Before you continue, confirm each item:

- ✓ Space "GenAI Operations Hub" is created
- ✓ Dashboard "GenAI Operations Dashboard" is visible in the Space
- ✓ Each topic from Task 1 is accessible from the Space
- ✓ Space URL saved for next task
- ✓ Visibility settings match your security requirements
- ✓ Team members added with appropriate permissions (if applicable)

---

## What You've Accomplished

In this task, you completed the following:

- ✅ Created a Quick Sight Space to organize GenAI operations content
- ✅ Added your dashboard and topics to the Space
- ✅ Configured appropriate visibility and permissions
- ✅ Enabled team collaboration on operational insights
- ✅ Prepared the foundation for AI-powered natural language queries

---

## Next Steps

Continue to [Task 3: Custom Agent](3-custom-agent.md) to build a chat agent connected to your Space!
