# Supply Chain Operations Skill

An Amazon Quick agent skill that orchestrates supply chain requests: it queries
the connected space for data, applies business rules through the supply chain
connectors, and renders branded documents. The skill follows the Amazon Quick
Official Catalog skill standard (frontmatter plus `## Overview` and `## Workflow`
with XML blocks, plus reference and asset files).

## Directory layout

```
skill/
  SKILL.md                     The skill definition (frontmatter + workflow)
  references/                  Lookup data the agent reads during execution
    connectors.md              Capability map of the five supply chain connectors
    data-queries.md            What to query from the space by request category
    routing.md                 Classification signals and agent-vs-connector routing
    presentation.md            Rendering rules by category and email guidance
    branding.md                Color palette, typography, layout, badge tokens
  assets/
    quote-template.html        Branded HTML quote structure to fill per quote
```

## Install

Copy the skill directory into the Amazon Quick desktop skills folder (create the
folder if it does not exist):

```bash
cp -R skill ~/.quickwork/profiles/{your-profile}/skills/supply-chain-ops
```

## Configure

The skill takes runtime inputs rather than hardcoded values:

- `space_id`: the Amazon Quick space holding the supply chain datasets and
  knowledge base (for example `supply-chain-management`). If omitted, the skill
  asks which space to use.
- `approver_email`: the default recipient when sending a quote for approval. If
  omitted, the skill asks for a recipient before sending.

## Rebrand (optional)

The default brand is a generic sample ("AnyCompany Manufacturing"), shown as a
styled text wordmark (there is no logo image). To rebrand, edit
`references/branding.md`: change the color tokens and the company name. The
quote template in `assets/quote-template.html` reads those tokens.

## Dependencies

The skill lists these in `depends-on` and expects them configured in Amazon Quick
as custom action connectors (the four business rule MCP servers plus the agent),
or available as skills:

- `sc-quoting`, `sc-governance`, `sc-invoice`, `sc-disruption`: business rule connectors
- `sc-order-fulfillment`: the multi-step reasoning agent connector
- `highcharts`, `html_design`: for branded charts and document styling
- `outlook`: for sending quotes by email

See the project README for how these connectors are deployed and configured.

## What this skill does

1. Classifies the request (quote, invoice, governance, disruption, data lookup,
   visualization, or a multi-step workflow).
2. Queries the connected space with Dataset Q&A for datasets and document search
   for invoices and contracts.
3. Passes that data to the matching rule connector, or to the order fulfillment
   agent for multi-domain requests.
4. Renders the result: a branded HTML and Word quote for quotes, branded charts
   for visualizations, concise text with the decision and the rule applied for
   everything else.
5. Confirms before sending anything by email.
