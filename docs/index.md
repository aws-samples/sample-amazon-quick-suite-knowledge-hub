---
hide:
  - navigation
  - toc
---

# Amazon Quick Knowledge Hub

<div class="qh-hero" markdown>
<div class="qh-hero__inner" markdown>

<span class="qh-hero__eyebrow">Amazon Quick · Knowledge Hub</span>

<h1 class="qh-hero__title">Everything you need to build on Amazon Quick</h1>

<p class="qh-hero__lead">
Integration guides, deployable infrastructure, MCP servers, and end-to-end
reference solutions. Curated and maintained by the Amazon Quick team to take you
from the console to a working implementation.
</p>

<div class="qh-hero__actions" markdown>
[Explore on GitHub](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub){ .md-button .md-button--primary }
[Official documentation](https://docs.aws.amazon.com/quick/latest/userguide/){ .md-button target="_blank" }
</div>

<div class="qh-hero__stats">
<!-- QH:STATS -->
</div>

</div>
</div>

## Browse the hub

<div class="qh-cats">
<!-- QH:CATEGORIES -->
</div>

## Featured solutions

<div class="qh-grid">
<!-- QH:FEATURED -->
</div>

## What this hub covers

The [integrations section](integration/actions/asana-action-setup-guide/) has step-by-step OAuth and connector setup for each supported third-party service. Most guides follow the same pattern: register an OAuth app on the provider side, configure the redirect URI and scopes, then create the action connector in the Amazon Quick console. The MCP implementations are complete CDK stacks you can deploy directly.

The [infrastructure section](infrastructure/quick-terraform/) covers deployable building blocks for running Quick. The [Terraform module](infrastructure/quick-terraform/) bootstraps an Amazon Quick account with AWS IAM Identity Center from scratch, handling the account subscription, admin user, group membership, and IAM roles in a single `terraform apply`. The [custom domain redirect](infrastructure/quick-custom-domain-redirect/) is a CDK app that points a domain you own at your Quick sign-in URL using a CloudFront Function, an ACM certificate, and Route 53 alias records.

The [infrastructure section](infrastructure/observability-agent/) also covers day-2 operations: a CloudWatch-based observability MCP that exposes chat logs, feedback, agent hours, Quick Sight metrics, and CloudTrail audit data through natural language queries in Quick itself, plus a [row-level security dataset-shaping example](infrastructure/rls-dataset-shaping/) for securing Quick Sight data.

The [desktop section](amazon-quick-on-desktop/) is a CDK stack that deploys Amazon Cognito as an OIDC provider for the desktop application. It includes an API Gateway proxy that strips the `offline_access` scope (which Cognito does not support but Quick sends with every request), a user invitation flow, and MFA configuration. This is for [enterprise deployments](https://docs.aws.amazon.com/quick/latest/userguide/desktop-enterprise-setup.html){:target="\_blank"} where you use local users or IAM Identity Center without a federated IdP.

The [use cases section](examples/actuarial-analysis-solution/) has complete, deployable solutions covering chat agent embedding, document generation, compliance automation, and operational dashboards.

## Workshops

| Workshop                                                                                                                                                 | What you build                                                                               |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| [Amazon Quick Flows](https://catalog.us-east-1.prod.workshops.aws/workshops/a8484e57-2e30-40ee-bd98-0122f0d05acc){:target="\_blank"}                     | Workflow automation with AI decision-making, customer support triage, and agent-backed flows |
| [A Complete Guide to Amazon Quick](https://catalog.workshops.aws/amazon-quick-suite-workshop/en-US){:target="\_blank"}                                   | Data connections, dashboards, chat agents, spaces, and app building                          |
| [Security and Governance Controls](https://catalog.us-east-1.prod.workshops.aws/workshops/fc1e6164-b5f5-4158-a269-88e71b769af3/en-US){:target="\_blank"} | Identity integration, access policies, data governance, monitoring, and compliance           |

## Links

<div class="qh-links">
<a class="qh-link" href="https://docs.aws.amazon.com/quick/latest/userguide/" target="_blank" rel="noopener">
  <span class="qh-link__name">Documentation</span>
  <span class="qh-link__host">docs.aws.amazon.com/quick</span>
</a>
<a class="qh-link" href="https://aws.amazon.com/quick/features/" target="_blank" rel="noopener">
  <span class="qh-link__name">Features</span>
  <span class="qh-link__host">aws.amazon.com/quick/features</span>
</a>
<a class="qh-link" href="https://aws.amazon.com/quick/faqs/" target="_blank" rel="noopener">
  <span class="qh-link__name">FAQs</span>
  <span class="qh-link__host">aws.amazon.com/quick/faqs</span>
</a>
<a class="qh-link" href="https://aws.amazon.com/quicksuite/pricing/" target="_blank" rel="noopener">
  <span class="qh-link__name">Pricing</span>
  <span class="qh-link__host">aws.amazon.com/quicksuite/pricing</span>
</a>
<a class="qh-link" href="https://quicksight.aws.amazon.com/" target="_blank" rel="noopener">
  <span class="qh-link__name">Sign in</span>
  <span class="qh-link__host">quicksight.aws.amazon.com</span>
</a>
<a class="qh-link" href="https://community.amazonquicksight.com/" target="_blank" rel="noopener">
  <span class="qh-link__name">Community</span>
  <span class="qh-link__host">community.amazonquicksight.com</span>
</a>
<a class="qh-link" href="https://www.youtube.com/@AmazonQuickSuite" target="_blank" rel="noopener">
  <span class="qh-link__name">YouTube</span>
  <span class="qh-link__host">youtube.com/@AmazonQuickSuite</span>
</a>
</div>

## Contributing

See [How to Contribute](HOW-TO-CONTRIBUTE.md).
