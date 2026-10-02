# Space Data Queries by Category

Lookup data: what to retrieve from the connected space before calling a rule
connector. The space holds the real business data (datasets plus a knowledge
base of documents). Use Dataset Q&A for single-dataset questions. Use the
provisioned Topic (`sc-supply-chain-topic`) for cross-table questions, because it
encodes the join graph across the supply chain tables. Use document search for
invoices and contracts.

Datasets available in the space: Orders and Customers, Inventory and Products,
Suppliers and Contracts, Sales Trends, Shipments and Tracking, Disruptions.
Documents available: invoices and contracts.

| Request category | Query from the space | Then pass to connector |
|------------------|----------------------|------------------------|
| QUOTE | Suppliers and Contracts (tier, discount, contract terms); Inventory and Products (sku, list price, cost, min margin); Orders and Customers (history) | sc-quoting generate-quote |
| INVOICE | Document search for the invoice; Orders and Customers for the matching PO | sc-invoice apply-approval-rules |
| GOVERNANCE | Suppliers and Contracts (supplier record, contract terms) | sc-governance check-supplier-approval / compliance |
| DISRUPTION | Shipments and Tracking (affected shipments); Orders and Customers (open orders at risk); Inventory and Products (buffer stock); Disruptions dataset (the active disruption list) | sc-disruption assess-impact / classify / mitigate |
| DATA_LOOKUP | The relevant dataset directly | (none; answer from data) |
| MULTI_STEP / AGENT | Pre-fetch all relevant datasets, pass as context | sc-order-fulfillment |

When a query spans multiple of these tables (for example orders joined to
shipments and products), ask the question against the Topic rather than a single
dataset so the relationships resolve. If a lookup or a disruption scan returns no
match, or nothing above the user's threshold, report that plainly and do not
invent findings.

## Invoice upload path

When the user attaches a PDF or text invoice:

1. Read the attached file and extract invoice number, PO reference, supplier,
   line items, amounts, due date, and payment terms.
2. Query the space for the matching PO, the supplier contract, and the supplier record.
3. Run supplier approval, regulatory compliance, and invoice approval rules, then
   compare invoice terms against contract terms.
4. Synthesize one recommendation: APPROVE, REVIEW, or REJECT, with the reason from each check.

Worked example (from the sample documents): invoice SUP-INV-40003 totals
$15,582.00 against PO-2026-41003 approved at $14,700.00, a 6.0 percent variance
that exceeds the 5 percent tolerance, so invoice approval returns DIRECTOR_REVIEW
while the supplier passes approval and compliance. Invoice SUP-INV-40001 is the
clean auto-approve case.
