# Jira Cloud - Action Setup Guide

1) Visit <https://developer.atlassian.com/console/myapps/> and login with your account and go to **My apps** page

2) Click **Create**, and select **OAuth 2.0 Integration**

![Create OAuth 2.0 Integration](images/image_1.png)

3) Insert the Name and click **Create**

4) Once created, click on **Permissions** to edit

![Permissions tab](images/image_2.png)

5.1) You will need to add scopes from Jira API:

Here're the required scope list (more than Q Business's Jira plugin scopes):

From **Jira API**, add:

**Classic scopes**:

- `read:jira-work`
- `write:jira-work`
- `read:jira-user`
- `manage:jira-configuration`
- `manage:jira-webhook`
- `manage:jira-project`

**Granular scopes**:

- `read:sprint:jira-software`
- `write:sprint:jira-software`
- `delete:sprint:jira-software`
- `read:board-scope:jira-software`
- `read:project:jira`
- `write:board-scope:jira-software`

5.2) Navigate to **Authorization** => **Configure** and add this Callback URL (e.g. for us-east-1 region):

`https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![Authorization configuration](images/image_3.png)

6) Go back to **Settings**, under Authentication details:

   Copy **Client ID** and **Secret** values, as these will be used in next step

![Settings with credentials](images/image_4.png)

7) Go to **AWS** > **Quick** page, and click on **Integration**:

![Quick Integration](images/image_5.png)

8) Select **Jira Cloud**, then **Next**

![Select Jira Cloud](images/image_6.png)

9) On Jira Cloud connection details page, insert the following as below:

![Jira Cloud connection details](images/image_7.png)

**Base URL** - ie `https://api.atlassian.com/ex/jira/<instance ID>` *instance ID is retrieved from below step

To retrieve Instance ID that will be used later as Domain URL, go to `https://<your namespace>.atlassian.net/_edge/tenant_info`

ie. `https://<your namespace>.atlassian.net/_edge/tenant_info`

cloudId returned is the Instance ID

![Instance ID retrieval](images/image_8.png)

**Client ID**: copy the value from step 6

**Client secret**: copy the value from step 6

**Token URL**: `https://auth.atlassian.com/oauth/token`

**Authorization URL**: `https://auth.atlassian.com/authorize`

**Redirect URL**: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![Configuration form](images/image_9.png)

10) Click on **Sign in** and pop-up window will display

![Sign in popup](images/image_10.png)

11) Click on **Accept**, and confirm the pop-up screen closing.

![Accept authorization](images/image_11.png)

12) Now the action summary page should show as '**Signed in**'

![Signed in status](images/image_12.png)

13) In Quick, while creating chat agent, you can now link this action to the chat agent.

![Link to chat agent](images/image_13.png)
