# Supply Chain Operations Skill

## Installation

Copy `SKILL.md` to your Amazon Quick skills directory:

```bash
cp skill/SKILL.md ~/.quickwork/profiles/{your-profile}/skills/supply-chain-ops/SKILL.md
```

## Customization

Before installing, update these values in `SKILL.md`:

1. **Space ID** (line containing `space ID is`):
   Replace `<your-quick-suite-space-id>` with your Quick Space ID.

2. **Branding** (if not AnyCompany):
   Search for `#D22630` (AnyCompany Red) and replace with your customer's brand color.
   Replace the logo data URI with your customer's logo.

## What This Skill Does

Orchestrates 4 business MCP gateways + 1 Agent + Snowflake data:

| Component | Purpose |
|-----------|---------|
| sc-quoting-gateway | Pricing rules, tier/volume discounts |
| sc-governance-gateway | Supplier approval, compliance, budget authority |
| sc-invoice-gateway | Invoice approval rules, payment queue |
| sc-disruption-alert-gateway | Disruption alerts, impact, mitigation |
| sc-order-fulfillment-agent connector (direct AgentCore runtime URL) | Multi-step reasoning (chains all above) |
| Quick Space | Structured data (Snowflake) + documents |

## Triggers

The skill activates on: supply chain, order management, inventory, quote, invoice, shipment, supplier, disruption, procurement, fulfillment, payment queue, pricing, compliance, approval, RFQ, purchase order, PO, AP aging, vendor, contract, process all orders, end to end, full workflow
