<!-- markdownlint-disable MD029 -->
# Smartsheet - Action Setup Guide

1) Go to <https://app.smartsheet.com/b/home> and from left bottom corner, choose **Developer Tools**

![Smartsheet home page](images/image_1.png)

2) Choose **create new app**

![Developer Tools](images/image_2.png)

3) Insert required information

   App redirect URL is `https://<region>.quicksight.aws.amazon.com/sn/oauthcallback`

![Create new app form](images/image_3.png)

4) Once app is created, you will receive client id and secret. Copy this for next steps

![Client ID and Secret](images/image_4.png)

5) Go to **Quick** > **Integrations** > **New Action** > choose **Smartsheet**. Insert the client id and secret from previous step

![Quick Smartsheet setup](images/image_5.png)

6) Once action is created successfully, choose **sign-in**. You will get pop-up window

![Sign in button](images/image_6.png)

7) Choose **Allow** to complete the sign-in

![Authorization popup](images/image_7.png)

8) If the action shows as signed in at the left bottom corner, you are ready to use action.

![Signed in status](images/image_8.png)
