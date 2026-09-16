# Amazon Quick Knowledge Hub

The [official Amazon Quick documentation](https://docs.aws.amazon.com/quick/latest/userguide/){:target="_blank"} tells you what each feature does and how to configure it through the console. What it does not always give you is the complete working implementation. It will not hand you the exact OAuth redirect URIs for ServiceNow, the Terraform to bootstrap an account with IAM Identity Center, or the CDK stack that makes Cognito work as a desktop OIDC provider. This hub fills that gap with integration guides, infrastructure-as-code, and deployable reference architectures maintained by the Amazon Quick team.

## Amazon Quick at a glance

[Amazon Quick](https://aws.amazon.com/quick/){:target="_blank"} is an agentic AI workspace with six features that work together. [Amazon Quick Sight](https://docs.aws.amazon.com/quick/latest/userguide/supported-data-sources.html){:target="_blank"} is the business intelligence feature where you connect to data sources, prepare data, build interactive dashboards, and ask natural language questions against your data. [Amazon Quick Flows](https://docs.aws.amazon.com/quick/latest/userguide/using-amazon-quick-flows.html){:target="_blank"} automates repetitive tasks with AI-powered workflows that use your data and take actions in connected applications. [Amazon Quick Automate](https://docs.aws.amazon.com/quick/latest/userguide/using-amazon-quick-automations.html){:target="_blank"} builds end-to-end business process automations with AI agents that make contextual decisions, execute actions across your applications, and loop in humans when judgment is needed. [Amazon Quick Index](https://aws.amazon.com/quicksuite/index/){:target="_blank"} connects your organization's documents and data to Amazon Quick so AI responses are grounded in your information. [Amazon Quick Research](https://docs.aws.amazon.com/quick/latest/userguide/view-research-report.html){:target="_blank"} conducts in-depth AI-powered research across the web and your data, delivered as a cited report. [Apps in Amazon Quick](https://docs.aws.amazon.com/quick/latest/userguide/getting-started-apps.html){:target="_blank"} lets you build fully managed interactive web applications using AI that securely connect to your services, store data, and use your existing information.

These features are tied together by shared capabilities. You build [chat agents](https://docs.aws.amazon.com/quick/latest/userguide/working-with-agents.html){:target="_blank"} with specific instructions, knowledge sources, and tools attached, then share them with your team. You organize resources into [spaces](https://docs.aws.amazon.com/quick/latest/userguide/working-with-spaces.html){:target="_blank"} so agents only draw from relevant data. You connect third-party services through [action connectors](https://docs.aws.amazon.com/quick/latest/userguide/action-connectors.html){:target="_blank"} using OAuth, [MCP](https://spec.modelcontextprotocol.io/specification/){:target="_blank"}, or OpenAPI. You bring documents in through [knowledge bases](https://docs.aws.amazon.com/quick/latest/userguide/knowledge-base-integrations.html){:target="_blank"} with automatic sync and access controls. [Extensions](https://docs.aws.amazon.com/quick/latest/userguide/extension-access.html){:target="_blank"} make Quick available inside your [browser](https://docs.aws.amazon.com/quick/latest/userguide/browser-extension-user-guide.html){:target="_blank"}, Slack, Microsoft Teams, and Microsoft 365 applications. The [desktop application](https://docs.aws.amazon.com/quick/latest/userguide/amazon-quick-desktop.html){:target="_blank"} connects to local files, email, calendar, and connected services natively on macOS and Windows. Chat also handles [document and visual creation](https://docs.aws.amazon.com/quick/latest/userguide/document-and-visual-creation.html){:target="_blank"} from natural language.

## What this hub covers

The [integrations section](integration/actions/asana-action-setup-guide/README.md) has step-by-step OAuth and connector setup for each supported third-party service. Most guides follow the same pattern: register an OAuth app on the provider side, configure the redirect URI and scopes, then create the action connector in the Amazon Quick console. The MCP implementations are complete CDK stacks you can deploy directly.

The [infrastructure as code section](infrastructure as code/Terraform/README.md) covers deployable building blocks for running Quick. The [Terraform module](infrastructure as code/Terraform/README.md) bootstraps an Amazon Quick account with AWS IAM Identity Center from scratch, handling the account subscription, admin user, group membership, and IAM roles in a single `terraform apply`. The [custom domain redirect](infrastructure as code/quick-custom-domain-redirect/README.md) is a CDK app that points a domain you own at your Quick sign-in URL using a CloudFront Function, an ACM certificate, and Route 53 alias records.

The [management section](manage quick/Identity.md) covers identity configuration, security guardrails, customization options, and a CloudWatch-based observability MCP that exposes chat logs, feedback, agent hours, Quick Sight metrics, and CloudTrail audit data through natural language queries in Quick itself.

The [desktop section](amazon-quick-on-desktop/README.md) is a CDK stack that deploys Amazon Cognito as an OIDC provider for the desktop application. The desktop app points directly at the Cognito hosted UI OAuth endpoints, and the stack includes a user invitation flow and MFA configuration. This is for [enterprise deployments](https://docs.aws.amazon.com/quick/latest/userguide/desktop-enterprise-setup.html){:target="_blank"} where you use local users or IAM Identity Center without a federated IdP.

The [use cases section](use-cases/actuarial-analysis-solution/README.md) has complete, deployable solutions covering chat agent embedding, document generation, compliance automation, and operational dashboards.

## Workshops

| Workshop | What you build |
|----------|----------------|
| [Amazon Quick Flows](https://catalog.us-east-1.prod.workshops.aws/workshops/a8484e57-2e30-40ee-bd98-0122f0d05acc){:target="_blank"} | Workflow automation with AI decision-making, customer support triage, and agent-backed flows |
| [A Complete Guide to Amazon Quick](https://catalog.workshops.aws/amazon-quick-suite-workshop/en-US){:target="_blank"} | Data connections, dashboards, chat agents, spaces, and app building |
| [Security and Governance Controls](https://catalog.us-east-1.prod.workshops.aws/workshops/fc1e6164-b5f5-4158-a269-88e71b769af3/en-US){:target="_blank"} | Identity integration, access policies, data governance, monitoring, and compliance |

## Links

| Resource | Link |
|----------|------|
| Official documentation | [docs.aws.amazon.com/quick](https://docs.aws.amazon.com/quick/latest/userguide/){:target="_blank"} |
| Features | [aws.amazon.com/quick/features](https://aws.amazon.com/quick/features/){:target="_blank"} |
| FAQs | [aws.amazon.com/quick/faqs](https://aws.amazon.com/quick/faqs/){:target="_blank"} |
| Pricing | [aws.amazon.com/quicksuite/pricing](https://aws.amazon.com/quicksuite/pricing/){:target="_blank"} |
| Sign in | [quicksight.aws.amazon.com](https://quicksight.aws.amazon.com/){:target="_blank"} |
| Community | [community.amazonquicksight.com](https://community.amazonquicksight.com/){:target="_blank"} |
| YouTube | [youtube.com/@AmazonQuickSuite](https://www.youtube.com/@AmazonQuickSuite){:target="_blank"} |

## Contributing

See [How to Contribute](HOW-TO-CONTRIBUTE.md).
