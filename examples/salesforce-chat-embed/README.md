---
category: Capability
description: "Embed an AWS Quick Chat Agent into Salesforce as a Lightning Web Component"
---

# Embed Quick Chat Agent in Salesforce

Embed an AWS Quick Chat Agent into Salesforce as a Lightning Web Component (LWC).

## Architecture

The component shows the chat agent in an iframe, and the user signs in to Quick inside it:

```text
Salesforce User (logged in)
  → Lightning page loads LWC
  → LWC renders iframe with Quick Chat Agent embed URL
  → User authenticates via Quick (password or SSO)
  → Chat Agent is interactive inside Salesforce
```

## Prerequisites

You need the following before you start:

- AWS account with AWS Quick Enterprise Edition
- A Quick Chat Agent created and configured
- A Quick account with users provisioned (password-based or single sign-on)
- Salesforce org with Lightning Experience enabled
- Salesforce CLI (`sf`) installed (`brew install sf`)

## Setup

Complete these steps in order. Steps 1 and 2 happen in Quick, and the rest in Salesforce.

### 1. Get Your Chat Agent Embed URL

Copy the agent's embed code from the Quick console:

1. Go to the [Quick console](https://quicksight.aws.amazon.com/)
2. Navigate to **Explore** → **Chat agents**
3. Choose the options menu (⋮) next to your Chat Agent → **Embed**
4. Copy the embed code. It looks like this:

```html
<iframe
    width="450"
    height="800"
    allow="clipboard-read https://us-east-1.quicksight.aws.amazon.com; clipboard-write https://us-east-1.quicksight.aws.amazon.com"
    src="https://<region>.quicksight.aws.amazon.com/sn/account/<account-alias>/embed/share/accounts/<account-id>/chatagents/<agent-id>?directory_alias=<account-alias>">
</iframe>
```

Note the `src` URL. You need it in step 3.

### 2. Add Your Salesforce Domain to Quick

Allow Quick content to load in your Salesforce domain:

1. Go to Quick console → **Manage Quick** → **Domains and Embedding**
2. Add your Salesforce Lightning domain:

   ```text
   https://<your-org>.lightning.force.com
   ```

### 3. Configure the LWC

Edit `salesforce/force-app/main/default/lwc/quickChat/quickChat.html` and replace the iframe `src` and `allow` URLs with your values from step 1:

```html
<template>
    <lightning-card title="Quick Chat Agent">
        <div class="slds-p-around_medium">
            <iframe
                width="450"
                height="800"
                frameborder="0"
                allow="clipboard-read https://<region>.quicksight.aws.amazon.com; clipboard-write https://<region>.quicksight.aws.amazon.com"
                src="https://<region>.quicksight.aws.amazon.com/sn/account/<account-alias>/embed/share/accounts/<account-id>/chatagents/<agent-id>?directory_alias=<account-alias>"
            ></iframe>
        </div>
    </lightning-card>
</template>
```

### 4. Deploy to Salesforce

Sign in to your org with the Salesforce CLI and deploy the component:

```bash
cd salesforce

# Authenticate to your Salesforce org
sf org login web --set-default

# Deploy the LWC
sf project deploy start --source-dir force-app
```

### 5. Configure Salesforce Security

In Salesforce Setup, add the following:

**CSP Trusted Sites** (Setup → CSP Trusted Sites → New Trusted Site):

- Trusted Site Name: `QuickChatAgent`
- Trusted Site URL: `https://<region>.quicksight.aws.amazon.com`
- Check **all** context boxes (Connect, Font, Img, Media, Object, Script, Style, Frame)

### 6. Add the Component to a Page

Place the component on a Lightning page and make the page available:

1. Go to Setup → **Lightning App Builder**
2. Edit or create a Lightning page
3. Drag `QuickChat` from the Custom components panel onto the page
4. Save and Activate the page
5. Assign it to a Lightning app via **App Manager** → Navigation Items

## Project Structure

The Salesforce project holds the LWC and its Apex controller:

```text
salesforce-chat-embed/
└── salesforce/
    ├── sfdx-project.json
    └── force-app/main/default/
        ├── lwc/QuickChat/
        │   ├── QuickChat.html      # iframe with Chat Agent embed URL
        │   ├── QuickChat.js         # LWC controller (minimal)
        │   └── QuickChat.js-meta.xml
        └── classes/
            ├── QuickChatController.cls
            └── QuickChatController.cls-meta.xml
```

## User Access

Users accessing the Chat Agent in Salesforce must have:

1. A **Quick** account with a provisioned user (password-based or SSO)
2. Access to the Chat Agent's underlying topics/data in Quick

## Production Considerations

Before you roll this out, consider the following:

- The static embed URL requires users to authenticate with Quick separately
- For SSO-based embedding with trusted identity propagation, use the `generate_embed_url_for_registered_user_with_identity` API when Chat Agent support is added. See the [Quick Chat Agent Embedding Demo](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/examples/quick-chat-agent-embedding-demo/) for the API-based approach
- Consider storing the Chat Agent URL in a Salesforce Custom Metadata Type to make updates easier

## References

For more detail, see:

- [Quick Chat Agent Embedding Demo](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/examples/quick-chat-agent-embedding-demo/)
- [Embed Quick Chat Agents in Enterprise Applications](https://aws.amazon.com/blogs/machine-learning/embed-amazon-quick-suite-chat-agents-in-enterprise-applications/)
- [GenerateEmbedUrlForRegisteredUser API](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_GenerateEmbedUrlForRegisteredUser.html)
