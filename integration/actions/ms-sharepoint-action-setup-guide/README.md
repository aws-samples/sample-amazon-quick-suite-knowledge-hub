# MS Sharepoint - Action Setup Guide

1) Sign in to the Microsoft Entra admin center as at least an Application Developer.

2) Browse to **Entra ID** => **App registrations** and select **New registration**

   - Choose **Single tenant** and provide optional `Web` Redirect URI: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   - Click **Register** button:

   ![App registration form](images/image_1.png)

3) Navigate to this app registration => **API permissions** => **Add a permission** => **Microsoft Graph API**:

   **Note**: Leave the pre-selected `User.Read` under **Microsoft Graph**.

   ![API permissions](images/image_2.png)

4) From **Certificates & secrets** => **Client secrets** => **+ New client secret**, copy client secret value

   (**Note**: only copy the secret **Value**, NOT the Secret ID)

   ![Client secret creation](images/image_3.png)

5) Copy the **Client ID** and **Tenant ID** from there:

   ![Client ID and Tenant ID](images/image_4.png)

6) Go to **AWS** > **Quick** page, and click on **Integration**:

   ![Quick Integration](images/image_6.png)

7) Select **Microsoft SharePoint Online Integration** then **Next**

   ![Configuration form](images/image_5.png)

   <br>
   **Connection Type**: Choose `Public network` or Your own VPC

   **Base URL**: `https://graph.microsoft.com/v1.0`

   **Client ID**: (copy the value from step 5)

   **Client secret**: (copy the value from step 4)

   **Token URL**: `https://login.microsoftonline.com/{your_tenant_id}/oauth2/v2.0/token`

   **Authorization URL** (Not needed for **Service authentication**):
   `https://login.microsoftonline.com/{your_tenant_id}/oauth2/v2.0/authorize`

   **Redirect URL** (Not needed for **Service authentication**): <br>
   `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   **Note**: `{your_tenant_id}` is copied from step 5).

   ![MS Teams configuration](images/image_7.png)

   ![Configuration form](images/image_8.png)

8) Click on **Create and continue**

9) Navigate to **Integration** => **Actions** => **Microsoft SharePoint Online Integration** (or your MS Sharepoint integration name), and click on **Sign in**:

   ![Authorization consent](images/image_9.png)

   - Enter your Microsoft Sharepoint email and password in the popup window;
   - Check **Consent on behalf of your organization** and click **Accept**:

   ![Authorization consent](images/image_10.png)

10) In Quick, while creating chat agent, you can now link this action to the chat agent
