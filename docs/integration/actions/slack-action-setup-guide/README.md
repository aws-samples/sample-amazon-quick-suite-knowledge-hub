# Slack - Action Setup Guide

1) Go to Slack (<https://api.slack.com/apps>)

![Slack API Apps page](images/image_1.png)

2) Click on **Create New App**, choose **From scratch**

![Create New App](images/image_2.png)

3) Enter any app name and choose your Slack Workspace

![App name and workspace](images/image_3.png)

4) Once created, credential information will be displayed

![App credentials](images/image_4.png)

5) Click on **OAuth & Permissions**

![OAuth & Permissions](images/image_5.png)

6) (Optional.) Click on **Add an OAuth Scope** under Bot Token Scopes:

![OAuth Scopes](images/image_6.png)

7) Add Redirect URL:

   `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   **Note**: You MUST click on **Save URLs** button.

8) Go to Quick Suite and choose Integration

9) Select Slack and choose Next

![Quick Suite Integration](images/image_7.png)

![Select Slack](images/image_8.png)

10) Insert the following data:

    **Name**: Slack Actions

    **Base URL**: `https://slack.com/api`

    **Client ID**: (from Step 4)

    **Client Secret**: (from Step 4)

    **Token URL**: `https://slack.com/api/oauth.v2.access`

    **Authorization URL**: `https://slack.com/oauth/v2/authorize`

    **Redirect URL**: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![Slack configuration form](images/image_9.png)

11) Once Action is added, you are taken to this page. Click on **Sign-in**

![Action added successfully](images/image_10.png)

12) Pop up window will display, click on **Allow**

![Authorization popup](images/image_11.png)
