# Amazon Quick Knowledge Hub

The [official Amazon Quick documentation](https://docs.aws.amazon.com/quick/latest/userguide/){:target="_blank"} tells you what each feature does and how to configure it through the console. What it does not always give you is the complete working implementation. It will not hand you the exact OAuth redirect URIs for ServiceNow, the Terraform to bootstrap an account with IAM Identity Center, or the CDK stack that makes Cognito work as a desktop OIDC provider. This hub fills that gap with integration guides, infrastructure-as-code, and deployable reference architectures maintained by the Amazon Quick team.

## What this hub covers

The [integrations section](integration/actions/asana-action-setup-guide/README.md) has step-by-step OAuth and connector setup for each supported third-party service. Most guides follow the same pattern: register an OAuth app on the provider side, configure the redirect URI and scopes, then create the action connector in the Amazon Quick console. The MCP implementations are complete CDK stacks you can deploy directly.

The [infrastructure-as-code section](infrastructure-as-code/terraform/) covers deployable building blocks for running Quick. The [Terraform module](infrastructure-as-code/terraform/) bootstraps an Amazon Quick account with AWS IAM Identity Center from scratch, handling the account subscription, admin user, group membership, and IAM roles in a single `terraform apply`. The [custom domain redirect](infrastructure-as-code/quick-custom-domain-redirect/) is a CDK app that points a domain you own at your Quick sign-in URL using a CloudFront Function, an ACM certificate, and Route 53 alias records.

The [management section](manage-quick/observability/) covers identity configuration, security guardrails, customization options, and a CloudWatch-based observability MCP that exposes chat logs, feedback, agent hours, Quick Sight metrics, and CloudTrail audit data through natural language queries in Quick itself.

The [desktop section](amazon-quick-on-desktop/) is a CDK stack that deploys Amazon Cognito as an OIDC provider for the desktop application. It includes an API Gateway proxy that strips the `offline_access` scope (which Cognito does not support but Quick sends with every request), a user invitation flow, and MFA configuration. This is for [enterprise deployments](https://docs.aws.amazon.com/quick/latest/userguide/desktop-enterprise-setup.html){:target="_blank"} where you use local users or IAM Identity Center without a federated IdP.

The [use cases section](use-cases/actuarial-analysis-solution/) has complete, deployable solutions covering chat agent embedding, document generation, compliance automation, and operational dashboards.

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
