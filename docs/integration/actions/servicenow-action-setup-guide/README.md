# ServiceNow - Action Setup Guide

Reference: <https://www.servicenow.com/docs/bundle/xanadu-platform-security/page/administer/security/task/t_CreateEndpointforExternalClients.html>

1) For ServiceNow Developer Instance, visit <https://developer.servicenow.com/dev.do#!/home> and click on **Start Building** button

2) Navigate to **All** > **System OAuth** > **Application Registry** and then click **New**.

![Application Registry](images/image_1.png)

3) Choose **Create an OAuth API endpoint for external clients**

![OAuth endpoint selection](images/image_2.png)

4) Fill in the form:

   **Note**:

   **Client type**: choose either Integration as a User or Integration as a Service based on your requirements

   **Provide Redirect URL**: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   Then click **Submit** button on the bottom:

![OAuth endpoint form](images/image_3.png)

5) Re-entering it and click the Lock icon to unlock Client Secret

   Copy the **Client ID** and **Secret** values, as these will be used in next step

6) Go to **AWS** > **Quick Suite** page, and click on **Integration**:

![Quick Suite Integration](images/image_4.png)

7) Select **ServiceNow** then **Next**

   **Base URL**: `https://<YOUR_INSTANCE>.service-now.com`

   **Client ID**: copy the value from step 5

   **Client secret**: copy the value from step 5

   **Token URL**: `https://<YOUR_INSTANCE>.service-now.com/oauth_token.do`

   **Authorization URL**: `https://<YOUR_INSTANCE>.service-now.com/oauth_auth.do`

   **Redirect URL**: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![ServiceNow configuration](images/image_5.png)

8) Click on **Create and continue**

![Configuration complete](images/image_6.png)

9) Navigate to **Integration** => **Actions** => **ServiceNow Integration** (or your ServiceNow integration name), and click on **Sign in**:

![ServiceNow Integration sign in](images/image_7.png)

![Sign in page](images/image_8.png)

10) Sign in to ServiceNow and click **Allow**:

![ServiceNow authorization](images/image_9.png)

![Authorization confirmation](images/image_10.png)

11) In Quick Suite, while creating chat agent, you can now link this action to the chat agent.
