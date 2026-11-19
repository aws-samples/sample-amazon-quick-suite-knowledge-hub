---
category: Capability
description: "Embed an AWS Quick chat agent in Salesforce for registered users, with a per-user embed URL and the Quick embedding SDK"
---

# Embed AWS Quick Chat Agent in Salesforce using APIs

Embed an AWS Quick chat agent in Salesforce Lightning pages as a Lightning Web Component (LWC). Each Salesforce user gets a chat session tied to their own Quick user, without signing in again inside the frame.

This sample complements [Embed Quick Chat Agent in Salesforce](https://aws-samples.github.io/sample-amazon-quick-suite-knowledge-hub/examples/salesforce-chat-embed/), which shows a one-click embed URL in an iframe and has the user sign in to Quick. Here, a small AWS backend mints a single-use embed URL for the signed-in Salesforce user, and the Quick embedding SDK renders the chat.

## Architecture

```text
Salesforce user opens a Lightning page
  └─ quickchatAgentwAPI (LWC)
       1. loadScript(QuickChatEmbeddingSDK)                  static resource
       2. getEmbedUrl()  ──────────────────────────────────► Apex controller
       3.   POST callout:Quick_Chat_Embed_API/embed-url       Named Credential
              x-api-key: {!$Credential.Quick_Chat_Embed_API.ApiKey}
              body: { "userName": "<signed-in user's email>" }
       4.     ──► Amazon API Gateway (REST, API key required, usage plan)
       5.         ──► AWS Lambda
                        quicksight:DescribeUser                       → user ARN
                        quicksight:GenerateEmbedUrlForRegisteredUser  (QuickChat)
       6.     ◄── { "embedUrl": "https://..." }               single-use
       7. createEmbeddingContext() → embedQuickChat(url, container, options)
```

Design choices:

- **AWS credentials stay in AWS.** Only the Lambda function can mint embed URLs. Salesforce holds an API key.
- **The Salesforce email is the identity.** Apex sends `UserInfo.getUserEmail()`, and the Lambda function looks up the Quick user with that exact user name.
- **No automatic provisioning.** If no Quick user matches, the component shows "You don't have access…".
- **Apex sets the API key header itself**, using a credential merge field. Salesforce fills in the value when the request is sent, so the key never appears in code or debug logs.
- **A fresh URL every load.** Embed URLs are single-use, so the Apex method isn't cacheable.

## What's included

| Path                                                             | Purpose                                                                                                                                                                               |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `aws/template.yaml`                                              | CloudFormation: Lambda function, IAM role, API Gateway REST API with an API key and usage plan, KMS-encrypted log groups, access logging, and X-Ray tracing                           |
| `aws/lambda/index.py`                                            | Validates `userName`, calls `DescribeUser` and `GenerateEmbedUrlForRegisteredUser`, and returns `{embedUrl}`                                                                          |
| `scripts/deploy.sh`                                              | Deploys the stack, writes the API URL into the Named Credential, downloads the SDK, deploys the Salesforce metadata, and runs a smoke test                                            |
| `force-app/main/default/lwc/quickchatAgentwAPI`                  | The LWC, with settings for title, height, agent ID, locale, and file attachments                                                                                                      |
| `force-app/main/default/classes`                                 | `QuickchatAgentwAPIController` (Apex callout) and its tests (`HttpCalloutMock`)                                                                                                       |
| `force-app/main/default/namedCredentials`, `externalCredentials` | `Quick_Chat_Embed_API`: the endpoint URL and the encrypted `ApiKey` parameter                                                                                                         |
| `force-app/main/default/permissionsets`                          | `Quickchat_Agent_wAPI_User`: Apex class access and credential principal access                                                                                                        |
| `force-app/main/default/cspTrustedSites`                         | Allow frames from `*.quicksight.aws.amazon.com` and `*.quick.aws.amazon.com`                                                                                                          |
| `force-app/main/default/staticresources`                         | `QuickChatEmbeddingSDK`. The deploy script downloads the [embedding SDK](https://github.com/awslabs/amazon-quicksight-embedding-sdk) from npm, so it isn't stored in this repository. |

## Prerequisites

- An AWS account with AWS Quick Enterprise Edition and at least one chat agent

- A Salesforce org with Lightning Experience and **Lightning Web Security** turned on (Setup → Session Settings)

- AWS CLI v2, configured for the account that hosts Quick

- Salesforce CLI (`sf`), logged in: `sf org login web --alias <alias>`

- `jq`, `python3`, and `npm`

- Quick users whose user name is each Salesforce user's email, with access to the chat agent. Use a Quick role that includes chat agents in your account. For example:

  ```bash
  aws quicksight register-user --aws-account-id <account-id> --namespace default \
    --identity-type QUICKSIGHT --user-role READER_PRO \
    --email user@example.com --user-name user@example.com --region us-east-1
  ```

## Deploy

```bash
./scripts/deploy.sh --sf-org <alias> [--aws-profile <profile>] [--region us-east-1]
```

Run it from a terminal, because the API key step uses a hidden-input prompt. The script:

1. **Deploys the AWS stack.** It packages the Lambda function into a private S3 bucket with Block Public Access on, then deploys the `quick-chat-embed` stack. Allowed embed domains come from your org's My Domain (`https://<domain>.lightning.force.com` and `https://<domain>.my.salesforce.com`). Override them with `--allowed-domains`.
1. **Deploys Salesforce.** It writes the stack's `ApiBaseUrl` output into the Named Credential, downloads the SDK, deploys this sample's metadata, runs the Apex tests, and assigns the permission set to you.
1. **Stores the API key (manual step).** API Gateway generates the key, and it never appears in stack outputs or script output. Open the `ApiKeyConsoleUrl` link the script prints, choose **Show**, and paste the value at the hidden prompt. Copy the key **value**, not the key ID.
1. **Runs a smoke test.** It mints one embed URL through Salesforce and reports the status code, never the URL.

To enter the API key yourself instead, go to Setup → Named Credentials → External Credentials → **Quick Chat Embed API** → Principals → **ApiKeyPrincipal**, and add the authentication parameter `ApiKey`.

To set the key again after rotating it, run:

```bash
./scripts/deploy.sh --sf-org <alias> --skip-aws --skip-salesforce
```

Run `./scripts/deploy.sh --help` for all options.

## Add the component to a page

1. Open a page in Lightning App Builder.
1. Drag **Quick Chat Agent w API** onto it. Optionally set **Agent ID** to lock the chat to one agent, plus **Height**, **Locale**, or **Allow file attachments**.
1. Save, activate, and refresh the page.

Assign the permission set to every user of the component:

```bash
sf org assign permset --name Quickchat_Agent_wAPI_User --target-org <alias> --on-behalf-of <username>
```

## Troubleshooting

| Message or symptom                                   | Cause and fix                                                                                                                             |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| "You don't have access to the Quick chat agent yet…" | No Quick user name matches the Salesforce email. Register one.                                                                            |
| "The Quick chat service rejected the request…"       | API Gateway returned 403. The key is missing or wrong, the key ID was pasted instead of the value, or the parameter isn't named `ApiKey`. |
| `We couldn't access the credential(s)`               | The permission set isn't assigned to the user.                                                                                            |
| Chat frame refuses to connect                        | The Salesforce domain isn't in `--allowed-domains` or in Quick's **Domains and Embedding** settings.                                      |
| Error inside the chat                                | The Quick user has no access to the agent, or their role doesn't include chat agents.                                                     |

Two things that look wrong but aren't:

- **`Authorization=Not set` in Salesforce debug logs.** `NAMED_CREDENTIAL_REQUEST` always shows it, because the key goes in `x-api-key` and Salesforce doesn't log custom headers.
- **An `x-api-key` custom header on the External Credential.** It looks like it should work, but in testing API Gateway still returned 403. Keep the header in Apex.

Where to look in CloudWatch:

| What                    | Where                                                                                                                         |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Lambda logs             | `/aws/lambda/quick-chat-embed-embed-url`                                                                                      |
| API Gateway access logs | `/aws/apigateway/quick-chat-embed-access`. A 403 with `"apiKeyId":"-"` means no key was sent.                                 |
| Traces                  | X-Ray console. Accounts using CloudWatch Transaction Search show them under Application Signals → Transaction Search instead. |

## Security considerations

- The API requires an API key, and a usage plan throttles it (10 requests per second, burst 20, by default). An API key identifies a caller but isn't strong authentication. For production, consider `AuthorizationType: AWS_IAM` on the method, with Salesforce signing requests through an AWS Signature Version 4 Named Credential.
- The Lambda function trusts the `userName` it receives. Anyone holding the API key can mint embed URLs for any Quick user, so keep the key only in the Salesforce External Credential and rotate it if it leaks.
- The Lambda role can only describe users and generate embed URLs in one Quick namespace. Concurrency is capped at 10 by default.
- Embed URLs are single-use bearer links. They aren't logged or cached. Access logs record the API key ID and caller IP, never the key value.
- Redeploying the External Credential wipes its stored key, so the script deploys it only when it doesn't exist yet.
- Access logging needs the account-level API Gateway CloudWatch Logs role (API Gateway console → Settings). If the stack fails on `ApiStage` with a CloudWatch Logs role error, set it once per Region.

## Clean up

```bash
aws cloudformation delete-stack --stack-name quick-chat-embed
```

The KMS key is scheduled for deletion after a waiting period. The script's artifacts bucket (`<stack>-artifacts-<account>-<region>`) isn't part of the stack, so empty and delete it separately. In Salesforce, remove the component from your pages, then delete this sample's metadata.
