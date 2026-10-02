# Request Classification and Routing

Lookup data for classifying a request and deciding whether to call a single rule
connector or the order fulfillment agent.

## Classification signals

| Signal words | Category | Primary connector |
|--------------|----------|-------------------|
| order status, inventory, stock level, shipment tracking | DATA_LOOKUP | space data (Dataset Q&A) |
| quote, pricing, discount, margin | QUOTE | sc-quoting |
| invoice, payment, approval, AP aging | INVOICE | sc-invoice |
| supplier approval, compliance, budget, policy | GOVERNANCE | sc-governance |
| disruption, delay, risk, mitigation, impact | DISRUPTION | sc-disruption |
| attached PDF or document invoice | INVOICE_UPLOAD | sc-invoice (after extraction) |
| show me, chart, trend, dashboard, visualize, compare, top, breakdown | VISUALIZATION | space data plus charts |
| process all orders, full workflow, end to end, 3+ domains, combined reasoning | AGENT | sc-order-fulfillment |
| two domains, no agent orchestration needed | MULTI_STEP | call connectors sequentially |

## Agent vs single connector

Use the order fulfillment agent when the request involves three or more rule
connectors or needs reasoning about the combined result. Use a single connector
for one-domain asks.

| Scenario | Use agent? | Why |
|----------|-----------|-----|
| Process all pending orders end to end | Yes | Spans quote, governance, inventory |
| Full workflow: validate, quote, check compliance | Yes | Multi-step chain with dependencies |
| Disruption hit: assess, find alternatives, re-quote | Yes | Chains disruption, governance, quoting |
| Generate a quote for 1000 units | No | Single connector call |
| Is a supplier approved? | No | Single governance check |
| Show the payment queue | No | Single invoice call |
| Quote plus compliance check | Maybe | Two connectors, can run sequentially without the agent |

Rule of thumb: the agent is for three or more connectors or combined reasoning
(for example "can we fulfill this?" needs inventory, supplier, and quoting
logic). When sending to the agent, always include the pre-fetched space data in
the request, because the agent has no direct data connections.
