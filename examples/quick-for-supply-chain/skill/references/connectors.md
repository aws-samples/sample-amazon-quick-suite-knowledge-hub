# Connector Capability Map

Lookup data describing the five supply chain connectors this skill orchestrates
and the actions each exposes. These are custom action connectors configured in
Amazon Quick (four business rule MCP servers plus one order fulfillment agent).
Introspect each connector at runtime to confirm the exact tool names and
required parameters before calling. Names below are the business capability, not
a hardcoded function signature.

The connectors hold business RULES only. They hold no data. Query the connected
space (datasets and knowledge base) for the facts, then pass those facts to the
connector as parameters.

## sc-quoting (quoting rules)

- Generate a quote: takes account id, customer tier, line items, and the product
  catalog data (sku, name, list price, cost, minimum margin). Returns line
  pricing, tier and volume discounts, margins, totals, validity.
- Get pricing rules: returns tier discounts, volume tiers, and policy thresholds. No data needed.
- Validate a quote: checks a draft quote against margin floors and approval thresholds.

## sc-governance (compliance and authority rules)

- Check supplier approval: takes the supplier record (approval status, risk tier,
  categories, next audit date) plus optional category and PO value. Returns an
  approval verdict with issues.
- Validate budget authority: takes a role and amount. Returns whether the role is
  authorized and the next approver.
- Check regulatory compliance: takes shipment type, destination country, and
  product categories. Returns applicable regulations and sanctions blocks.
- Get policy rules: returns the governance policy catalog. Filter by category.

Note: there is no audit-logging tool on this connector. Do not reference one.

## sc-invoice (approval rules)

- Apply approval rules: takes invoice data and PO data (plus optional goods
  receipt and vendor contract). Returns a decision (auto-approve, manager review,
  director review, VP review, reject) with per-line results and reasoning.
- Get payment queue: returns the AP aging report and payment schedule.

## sc-disruption (impact and mitigation rules)

- Assess disruption impact: takes the disruption plus affected orders, shipments,
  inventory, and customer accounts. Returns revenue at risk and impact counts.
- Classify severity: takes an impact assessment. Returns a level (1 to 4) and
  required response time.
- Recommend mitigation: takes a disruption type and impact assessment. Returns a
  playbook of immediate, short-term, and long-term actions.
- Get escalation chain: takes a severity level. Returns who to notify and by when.

## sc-order-fulfillment (multi-step reasoning agent)

A single agent connector that reasons across the four rule connectors above.
Send it a natural-language instruction plus any space data as context. It has no
direct data connections, so pre-fetch the data and include it in the request.
Use it only for requests that span three or more domains or need combined
reasoning. Single-domain asks go directly to the matching rule connector.
