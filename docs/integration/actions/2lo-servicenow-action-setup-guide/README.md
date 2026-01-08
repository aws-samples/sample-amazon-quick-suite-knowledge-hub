# 2LO - ServiceNow - Action Setup Guide

Reference: <https://www.servicenow.com/docs/bundle/xanadu-platform-security/page/administer/security/task/t_CreateEndpointforExternalClients.html>

1) For ServiceNow Developer Instance, visit <https://developer.servicenow.com/dev.do#!/home> and click on **Start Building** button. Switch to **New Inbound Integration Experience**:

![ServiceNow Developer Instance](images/image_1.png)

2) Click on **New integration** button:

![New integration button](images/image_2.png)

3) Choose **OAuth - Client credentials grant**:

![OAuth Client credentials grant](images/image_3.png)

4) Fill in the form and copy the **Client ID** and **Secret** values, as these will be used in next step.

   **Note**: Make sure you select **System Administrator** as OAuth application user:

![OAuth application form](images/image_4.png)

5) Navigate to **All** > **System OAuth** > **Application Registry** and click on your application registry.

![Application Registry](images/image_5.png)

6) Provide **Redirect URL**:

   `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![Redirect URL configuration](images/image_6.png)

7) Under **All**, search **sys_properties.list** and hit enter key:

![System properties search](images/image_7.png)

   Add a new property as below:

   **Name**: `glide.oauth.inbound.client.credential.grant_type.enabled`

   **Type**: `true | false`

   **Value**: `true`

![System property configuration](images/image_8.png)

8) Go to **AWS** > **Quick Suite** page, and click on **Integration**:

![Quick Suite Integration](images/image_9.png)

9) Select **ServiceNow** then **Next**, enter the following information:

    **Authentication type**: Service-to-service OAuth

    **Base URL**: `https://<YOUR_INSTANCE>.service-now.com`

    **Client ID**: copy the value from step 4

    **Client secret**: copy the value from step 4

    **Token URL**: `https://<YOUR_INSTANCE>.service-now.com/oauth_token.do`

![ServiceNow configuration form](images/image_10.png)

10) Navigate to **Integration** => **Actions** => **ServiceNow Integration** (or your ServiceNow integration name), and click on **Sign in**:

![ServiceNow Integration sign in](images/image_11.png)

11) Sign in to ServiceNow and click **Allow**:

![ServiceNow authorization](images/image_12.png)

12) **Using ServiceNow Integration in Quick Suite**

Now that your ServiceNow integration is configured and authorized, you can use it across different Quick Suite components:

**For Chat Agents:**

1. Navigate to **Chat** in Quick Suite
2. Create a new chat agent or edit an existing one
3. In the agent configuration, go to the **Actions** section
4. Click **Add Action** and select your **ServiceNow Integration**
5. Configure which ServiceNow operations the agent can perform (e.g., create tickets, search knowledge base, update records)

**For Flows:**

1. Go to **Flows** in Quick Suite
2. Create a new flow or edit an existing workflow
3. Add an **Action Step** to your flow
4. Select your **ServiceNow Integration** from the available actions
5. Configure the specific ServiceNow operation and parameters for that step

**For Automate Projects:**

1. Navigate to **Automate** in Quick Suite
2. Create or edit an automation project
3. Add a **ServiceNow Action** component to your automation workflow
4. Configure the integration to automatically perform ServiceNow operations based on triggers or schedules

**Example Use Cases:**

- **Chat Agent**: "Create a ServiceNow incident ticket for this issue"
- **Flow**: Automatically create tickets when certain conditions are met
- **Automate**: Schedule regular ServiceNow data synchronization

Your ServiceNow integration is now ready to streamline IT service management across all Quick Suite capabilities.
