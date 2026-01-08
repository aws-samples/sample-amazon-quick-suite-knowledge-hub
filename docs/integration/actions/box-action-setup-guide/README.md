# Box - Action Setup Guide

Quick users can connect to 3P agents using the custom MCP connector in Quick Suite. The steps below explain how a user can connect to Box's agents from Quick.

## Step 1: Access Integrations

1) From the Quick Suite Home screen, select **Integrations** from the Connections section on the left navigation panel
2) Select the **Actions** tab in the main panel
3) Select the plus (+) sign in the **Model Context Protocol** tile in the Set up a new integration section

![Quick Suite Integrations](images/image_1.png)

## Step 2: Create Integration

4) On the Create integration screen, enter the **Name** and **Description** for the Box integration
5) For MCP server endpoint, enter Box's remote MCP server: `https://mcp.box.com`
6) Click **Next**

![Create integration form](images/image_2.png)

## Step 3: Configure Authentication

7) Select the authentication method: **User authentication** for Box

8) Enter the following details:

   a. **Client ID**

   b. **Client secret**

   c. **Token URL**

   d. **Authorization URL**

9) Click **Create and continue**

This allows the underlying OAuth security framework to verify the identity of the client application and establish a secure, authenticated connection.

![Authentication configuration](images/image_3.png)

## Step 4: Grant Authorization

10) A pop-up window from Box MCP server will appear with a request to approve authorization for the Amazon Quick Suite MCP client
11) Click **Grant Access to Box**

![Box authorization popup](images/image_4.png)

## Step 5: Verify Integration

12) Verify that the MCP tools are retrieved and displayed on the screen
13) Optionally, share the integration with other users and groups

![MCP tools retrieved](images/image_5.png)

## Step 6: Use Box Integration

To invoke the Box agents from Quick Chat, enter a prompt such as:

"Review the customer feedback survey documents from January, February, and March and identify the top 5 most frequently mentioned pain points"
