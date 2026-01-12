# MS Sharepoint - Action Setup Guide

1. Sign in to the Microsoft Entra admin center as at least an Application Developer.

1. Browse to **Entra ID** => **App registrations** and select **New registration**

   Choose **Single tenant** and provide optional `Web` Redirect URI: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   Click **Register** button:

   ![App registration form](images/image_1.png)

1. Navigate to this app registration => **API permissions** => **Add a permission** => **Microsoft Graph API**:

   **Note**: Leave the pre-selected `User.Read` under **Microsoft Graph**.

   ![API permissions](images/image_2.png)

1. From **Certificates & secrets** => **Client secrets** => **+ New client secret**, copy client secret value

   (**Note**: only copy the secret **Value**, NOT the Secret ID)

   ![Client secret creation](images/image_3.png)

1. Also copy the **Client ID** and **Tenant ID** from there:

   ![Client ID and Tenant ID](images/image_4.png)

1. Go to **AWS** > **Quick Suite** page, and click on **Integration**:

   ![Quick Suite Integration](images/image_6.png)

1. Select **Microsoft SharePoint Online Integration** then **Next**

   ![Configuration form](images/image_5.png)

   **Connection Type**: Choose `Public network` or Your own VPC

   **Base URL**: `https://graph.microsoft.com/v1.0`

   **Client ID**: (copy the value from step 5)

   **Client secret**: (copy the value from step 4)

   **Token URL**: `https://login.microsoftonline.com/{your_tenant_id}/oauth2/v2.0/token`

   **Authorization URL** (Not needed for **Service authentication**):
   `https://login.microsoftonline.com/{your_tenant_id}/oauth2/v2.0/authorize`

   **Redirect URL** (Not needed for **Service authentication**):
   `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   **Note**: `{your_tenant_id}` is copied from step 5).

   ![MS Teams configuration](images/image_7.png)

   ![Configuration form](images/image_8.png)

1. Click on **Create and continue**

1. Navigate to **Integration** => **Actions** => **Microsoft SharePoint Online Integration** (or your MS Sharepoint integration name), and click on **Sign in**:

   ![Authorization consent](images/image_9.png)

1. Check **Consent on behalf of your organization** and click **Accept**:

   ![Authorization consent](images/image_10.png)

1. Enter your Microsoft Sharepoint email and password in the popup window

1. In Quick Suite, while creating chat agent, you can now link this action to the chat agent.
