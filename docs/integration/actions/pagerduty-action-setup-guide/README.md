# PagerDuty - Action Setup Guide

## Setup on PagerDuty Advance

1) Go to PagerDuty page (`https://<your sub-domain>.pagerduty.com/incidents`), and log in to PagerDuty account

2) Select **Integrations** > **App Registration**

![Integrations menu](images/image_1.png)

3) Enter Name and Description, and select **OAuth 2.0** for Functionality

![App registration form](images/image_2.png)

4) For Configure OAuth2.0, choose **Classic User OAuth** and select the fields like follows:

   **Redirect URL** - `https://<region of Quick>.quicksight.aws.amazon.com/sn/oauthcallback`

   **Permission Scope** - Read & Write

![OAuth configuration](images/image_3.png)

5) Copy down the **Client ID** and **Secret**

![Client credentials](images/image_4.png)

## Setup on Quick

6) Go to **Action** > **New Action**, and select **PagerDuty Advance**

![Quick Suite PagerDuty setup](images/image_5.png)

7) Confirm available actions and click **Next**

![Available actions](images/image_6.png)

8) Setup the values for each field accordingly:

   **Base URL**: `https://api.pagerduty.com`

   **Client ID**: (from step 5)

   **Client secret**: (from step 5)

   **Token URL**: `https://identity.pagerduty.com/oauth/token`

   **Authorization URL**: `https://identity.pagerduty.com/oauth/authorize`

   **Redirect URL**: `https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback`

![Configuration form](images/image_7.png)

9) After creation successfully, you are going to see **Sign in** button for the very first time only.

![Action created successfully](images/image_8.png)

10) You will get pop-up window requesting for consent

![Authorization popup](images/image_9.png)

11) After consent, you are signed in and ready to use the actions

![Signed in status](images/image_10.png)
