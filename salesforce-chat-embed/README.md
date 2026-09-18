---
category: Capability
description: "Embed an Amazon Quick Chat Agent into Salesforce as a Lightning Web Component"
---

# Embed Quick Chat Agent in Salesforce

Embed an Amazon Quick Chat Agent into Salesforce as a Lightning Web Component (LWC).

## Architecture

```
Salesforce User (logged in)
  → Lightning page loads LWC
  → LWC renders iframe with Quick Chat Agent embed URL
  → User authenticates via Quick (password or SSO)
  → Chat Agent is interactive inside Salesforce
```

## Prerequisites

- AWS account with Amazon Quick Enterprise Edition
- A Quick Chat Agent created and configured
- A Quick account with users provisioned (password-based or single sign-on)
- Salesforce org with Lightning Experience enabled
- Salesforce CLI (`sf`) installed (`brew install sf`)

## Setup

### 1. Get Your Chat Agent Embed URL

1. Go to the [Quick console](https://quicksight.aws.amazon.com/)
2. Navigate to **Explore** → **Chat agents**
3. Click the three dots next to your Chat Agent → **Embed**
4. Copy the embed code — it will look like:

```html
<iframe
    width="450"
    height="800"
    allow="clipboard-read https://us-east-1.quicksight.aws.amazon.com; clipboard-write https://us-east-1.quicksight.aws.amazon.com"
    src="https://<region>.quicksight.aws.amazon.com/sn/account/<account-alias>/embed/share/accounts/<account-id>/chatagents/<agent-id>?directory_alias=<account-alias>">
</iframe>
```

Note the `src` URL — you'll need it in step 3.

### 2. Add Your Salesforce Domain to Quick

1. Go to Quick console → **Manage Quick** → **Domains and Embedding**
2. Add your Salesforce Lightning domain:
   ```
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

1. Go to Setup → **Lightning App Builder**
2. Edit or create a Lightning page
3. Drag `QuickChat` from the Custom components panel onto the page
4. Save and Activate the page
5. Assign it to a Lightning app via **App Manager** → Navigation Items

## Project Structure

```
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

- The static embed URL requires users to authenticate with Quick separately
- For SSO-based embedding with trusted identity propagation, use the `generate_embed_url_for_registered_user_with_identity` API when Chat Agent support is added — see the [Quick Chat Agent Embedding Demo](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/use-cases/quick-chat-agent-embedding-demo/) for the API-based approach
- Consider storing the Chat Agent URL in a Salesforce Custom Metadata Type for easier updates

## References

- [Quick Chat Agent Embedding Demo](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/use-cases/Quick-chat-agent-embedding-demo/)
- [Embed Quick Chat Agents in Enterprise Applications](https://aws.amazon.com/blogs/machine-learning/embed-amazon-quick-suite-chat-agents-in-enterprise-applications/)
- [GenerateEmbedUrlForRegisteredUser API](https://docs.aws.amazon.com/quick/latest/APIReference/API_GenerateEmbedUrlForRegisteredUser.html)
