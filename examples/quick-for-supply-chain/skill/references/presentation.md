# Presentation Rules by Category

Lookup data: how to render each response type. Apply the brand tokens in
`references/branding.md`. Use the HTML quote structure in
`assets/quote-template.html`.

## Rendering rules

- QUOTE: always render a company-branded HTML quote (use the template asset), and
  also offer a Word (.docx) copy. Never a plain markdown table for a quote.
- All other responses (governance, disruption, invoice decisions, data lookups,
  agent results): respond with clean, concise text using the matching
  text template in the SKILL.md `<Templates>` block, with the key data,
  recommendation, and next step. Offer a branded document as a follow-up.
- Visualizations (charts, dashboards): always render a branded HTML artifact with
  charts via the highcharts and html_design dependencies.
- All clear: when a lookup or disruption scan finds nothing (or nothing above the
  user's threshold), use the All Clear template. Report the clean state plus a
  short health summary. Do not fabricate findings.

## Audience level

The `audience_level` input sets detail for text responses (not the branded quote):

- executive: a decision-first answer a leader reads in under a minute. Lead with
  the decision and the one number that matters, then the single next step.
- full: the executive summary plus the supporting rows, per-rule detail, and any
  assumptions.

Default to full when the input is absent.

## By category

- QUOTE: full-page HTML quote. Header with the company wordmark and "Quotation"
  label, quote number and dates, customer and tier, line items (item, sku, qty,
  list price, discount, unit price, line total), subtotal, tax, grand total,
  margin badge, terms footer, print-friendly. Then a .docx version with the same
  content: company wordmark top-left and "Confidential" top-right on one line,
  "Price Quotation" title, quote details table, line items, discount breakdown,
  totals, terms, page-numbered footer.
- INVOICE DECISION: approval memo. Decision badge (auto-approve green, review
  amber, reject red, escalate blue), invoice details, each rule applied with
  pass or fail, recommendation, approval chain if escalated.
- DISRUPTION REPORT: risk dashboard. Severity indicator with color, impact summary
  (orders, revenue at risk, suppliers), event timeline, mitigation action cards.
- GOVERNANCE CHECK: compliance card. Entity checked, status badge (compliant,
  conditional, non-compliant), checklist of rules with pass or fail, required actions.
- PAYMENT QUEUE: financial dashboard. Summary cards (total payable, overdue,
  upcoming), invoice table by due date, AP aging buckets as a simple bar chart.
- DATA LOOKUP: concise summary with highlighted numbers, HTML table if more than
  three rows, color-coded badges for inventory (critical, low, ok), actionable insight.
- VISUALIZATION: branded chart container using the brand palette for all series.

## Branding the Word document

Use the company wordmark as styled text (Brand Red) in the header. There is no
logo image. Apply the color and type tokens from `references/branding.md`.

## Sending by email

When the user asks to send a quote or document, use the connected email
integration. Ask the user for the recipient (or use the `approver_email` input if
provided). Subject format: "Quote {quote id} - {customer} - {product}". Attach
the generated .docx. Prefix the subject with "[APPROVAL REQUIRED]" for approval
sends or "[QUOTE]" for customer sends.
