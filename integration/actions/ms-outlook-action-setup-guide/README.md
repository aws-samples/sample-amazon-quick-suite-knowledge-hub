# MS Outlook - Action Setup Guide

1) Sign in to the Microsoft Entra admin center as at least an Application Developer.

2) Browse to **Entra ID** => **App registrations** and select **New registration**

   Choose **Single tenant** and provide optional Web platform with Redirect URI:

   `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   Click **Register** button:

![App registration form](images/image_1.png)

3) Navigate to this app registration => **API permissions** => **Add a permission** => **Microsoft Graph API**:

   **Note**: Leave the pre-selected User.Read there.

   From Application permissions, select:

   - `Mail.Read`
   - `Mail.Send`
   - `Calendars.ReadWrite`

   Then click on **Grant admin consent for <XXXX>**, you'll see a total of 4 permissions as below:

![API permissions](images/image_2.png)

4) From **Certificates & secrets** => **Client secrets** => **+ New client secret**, copy client secret value

   (**Note**: only copy the secret **Value**, NOT the Secret ID)

![Client secret creation](images/image_3.png)

5) Also copy the **Client ID** and **Tenant ID** from there:

![Client ID and Tenant ID](images/image_4.png)

6) If not yet provided Redirect URI in step 2), then go to **Authentication** => **Add a platform** => **Web**, provide redirect URL:

   `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![Authentication redirect URI](images/image_5.png)

7) Go to **AWS** > **Quick** page, and click on **Integration**:

![Quick Integration](images/image_6.png)

8) Select **Microsoft Outlook** then **Next**

   **Base URL**: `https://graph.microsoft.com/v1.0`

   **Client ID**: copy the value from step 5

   **Client Secret**: copy the value from step 4

   **Token URL**: `https://login.microsoftonline.com/{your_tenant_id}/oauth2/v2.0/token`

   **Authorization URL**: `https://login.microsoftonline.com/{your_tenant_id}/oauth2/v2.0/authorize`

   **Redirect URL**: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   **Note**: `{your_tenant_id}` is copied from step 5).

![MS Outlook configuration](images/image_7.png)

![Configuration form](images/image_8.png)

![Configuration complete](images/image_9.png)

9) Click on **Create and continue**

10) Navigate to **Integration** => **Actions** => **Microsoft Outlook Integration** (or your MS Outlook integration name), and click on **Sign in**:

![MS Outlook Integration sign in](images/image_10.png)

11) Check **Consent on behalf of your organization** and click **Accept**:

![Authorization consent](images/image_11.png)

12) In Quick, while creating chat agent, you can now link this action to the chat agent.
