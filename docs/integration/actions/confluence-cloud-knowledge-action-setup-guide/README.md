# Confluence Cloud - Knowledge + Action Setup Guide

1) Visit <https://developer.atlassian.com/console/myapps/> and login with your account and go to **My apps** page

2) Click **Create**, and select **OAuth 2.0 Integration**

![Create OAuth 2.0 Integration](images/image_1.png)

3) Insert the Name and click **Create**

4) Once created, click on **Permissions** to edit

![Permissions tab](images/image_2.png)

5.1) You will need to add scopes from Confluence API:

From **Confluence API**, add:

**Classic scopes**:

- search:confluence

**Granular scopes**:

- read:page:confluence
- write:page:confluence
- read:space:confluence

5.2) Navigate to **Authorization** => **Configure** and add this Callback URL (e.g. for us-east-1 region):

`https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![Authorization configuration](images/image_3.png)

6) Go back to **Settings** and copy the **Client ID** and **Secret** values, as these will be used in next step

![Settings with credentials](images/image_4.png)

7) Go to **AWS** > **Quick Suite** page, and click on **Integration**:

![Quick Suite Integration](images/image_5.png)

8) Select **Atlassian Confluence**, then **Next**

9) Check both integration type if needed:

   **Perform actions in Atlassian Confluence**: This allows users to take actions to their Confluence cloud (read-and-write)

   **Bring data from Atlassian Confluence**: This creates a Confluence knowledge base (read-only)

![Integration type selection](images/image_6.png)

**Base URL**: `https://api.atlassian.com/ex/confluence/<instance ID>`

(*instance ID is retrieved from below step)

To retrieve Instance ID that will be used later as Domain URL, go to `https://<your namespace>.atlassian.net/_edge/tenant_info`

ie. `https://<your namespace>.atlassian.net/_edge/tenant_info`

cloudId returned is the Instance ID

**Note**:

- To configure Service Authentication, first generate API key without scopes at Confluence
- To configure User Authentication, follow this:

![Authentication configuration](images/image_7.png)

**Client ID**: (copy the value from step 6)

**Client secret**: (copy the value from step 6)

**Token URL**: `https://auth.atlassian.com/oauth/token`

**Authorization URL**: `https://auth.atlassian.com/authorize`

**Redirect URL**: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![Configuration form](images/image_8.png)

![Configuration complete](images/image_9.png)

10) Click on **Sign in** and pop-up window will display

![Sign in popup](images/image_10.png)

11) Click on **Accept**, and confirm the pop-up screen closing.

12) Now the action summary page should show as '**Signed in**'

13) In Quick Suite, while creating chat agent, you can now link this action to the chat agent.
