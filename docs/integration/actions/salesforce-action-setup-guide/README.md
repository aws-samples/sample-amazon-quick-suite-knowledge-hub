# Salesforce - Action Setup Guide

1) Log in to your Salesforce Online Developer Edition account at <https://login.salesforce.com>

   or Salesforce Online Sandbox Edition account at <https://test.salesforce.com>

2) From the Salesforce Online profile menu, copy your Salesforce Online URL, if you haven't already. This will be the root of the Base URL to use later.

![Salesforce profile menu](images/image_1.png)

3) Then, from the Salesforce Online profile menu, select the Setup icon and then select Setup.

![Setup navigation](images/image_2.png)

4) From the left navigation menu, go to **PLATFORM TOOLS** → **Apps** → **External Client Apps** → **Settings**, Enable **Allow creation of connected apps**. Then create **New Connected App**

![Platform tools navigation](images/image_3.png)

5) On the New Connected App page, do the following:

   In Basic information, enter the following required information:

   **Connect App Name** – A name for your connected app.

   **API Name** – A name for your API.

   **Contact Email** – Your contact email.

![Basic information form](images/image_4.png)

6) In API (Enable OAuth Settings), add callback url: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   Check the following checkboxes:

   1) Enable OAuth Settings
   2) Require Secret for Web Server Flow
   3) Require Secret for Refresh Token Flow
   4) Enable Authorization Code and Credentials Flow
   5) Enable Token Exchange Flow
   6) Require Secret for Token Exchange Flow
   7) Introspect All Tokens

   And select the following OAuth scopes:

   - `visualforce`
   - `custom_permissions`
   - `open_id`
   - `refresh_token, offline_access`
   - `wave_api`
   - `web`
   - `chatter_api`
   - `id, profile, email, address, phone`
   - `api`
   - `eclair_api`
   - `pardot_api`
   - `full`

![OAuth settings configuration](images/image_5.png)

7) Get Consumer Key (Client ID) and Consumer Secret (Client Secret)

   From the Manage Connected Apps page (if re-entering, click on the **View** button on the right

![Manage Connected Apps](images/image_6.png)

   ), choose **Manage Consumer Details**. You will be redirected to a Connected App Name summary page.

![Consumer details](images/image_7.png)

   **Note**:

   **Consumer Key** is Client ID

   **Consumer Secret** is Client Secret.

![Key and secret mapping](images/image_8.png)

   **Note**: Please wait for at least 10 minutes for Salesforce to be ready.

8) Go to Quick Suite and choose Integration, Select **Salesforce** and choose **Next**

   Insert the following data:

   **Base URL**: `https://<your_salesforce_instance>.my.salesforce.com/services/data/v60.0`

   **Client ID**: (from Step #7)

   **Client Secret**: (from Step #7)

   **Authentication URL** (for User authentication option only, NOT needed for Service authentication):

   - Developer Edition: `https://login.salesforce.com/services/oauth2/authorize`
   - Sandbox Edition: `https://test.salesforce.com/services/oauth2/authorize`

   **Token URL**:

   - Developer Edition: `https://login.salesforce.com/services/oauth2/token`
   - Sandbox Edition: `https://test.salesforce.com/services/oauth2/token`

   **Redirect URL**: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

   Your configuration should look like this:

![Salesforce configuration](images/image_9.png)

![Configuration form](images/image_10.png)

9) Once Action is added, you are taken to this page. Click on **Sign-in**

   Pop up window will display, click on **Allow**

![Authorization popup](images/image_11.png)

   Once added, click **Sign-in**
