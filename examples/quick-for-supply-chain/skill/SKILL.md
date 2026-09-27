---
name: supply-chain-ops
display_name: Supply Chain Operations
description: "MUST be loaded for ANY supply chain request — including quotes, invoices, orders, inventory, suppliers, disruptions, pricing, compliance, shipments, procurement, fulfillment, payment queues. Do NOT call sc-quoting-gateway, sc-invoice-gateway, sc-governance-gateway, or sc-disruption-alert-gateway directly — ALWAYS load this skill first. This skill orchestrates Space data queries (Snowflake) BEFORE calling MCPs, and renders results as professional company-branded HTML documents instead of plain tables."
icon: "📦"
trigger: supply chain, order management, inventory, stockout, quote, invoice, shipment, supplier, disruption, procurement, fulfillment, payment queue, pricing, compliance, approval, RFQ, purchase order, PO, AP aging, vendor, contract, process all orders, end to end, full workflow, order fulfillment
inputs:
  - name: query
    description: "The user's supply chain question or request — could be a data lookup, a business rule check, a multi-step workflow, or an analytical question"
    type: string
    required: true
tools: [run_python, run_javascript, file_write, file_read, open_in_session_tab, search_spaces, list_space_documents, list_space_resources, email_send, email_search, prepare_file_for_sending_to_connector]
---

## Overview

This skill handles ALL supply chain operations queries. It MUST be loaded instead of calling individual MCP connectors directly. The workflow is:

1. **Query the Space** (connected to Snowflake) to get real business data
2. **Call MCPs** with that data for business rule evaluation
3. **Render a professional company-branded HTML document** — NEVER output plain markdown tables

## Required Skills to Load

Before executing, load the relevant MCP connector skills based on query classification:
- **Quoting**: `load_skill("quick_suite__sc_quoting_gateway")` — tools: generate_quote, get_quote_history, get_pricing_rules, validate_quote
- **Governance**: `load_skill("quick_suite__sc_governance_gateway")` — tools: check_supplier_approval, log_audit_event, get_policy_rules, validate_budget_authority, get_audit_trail, check_regulatory_compliance
- **Invoice**: `load_skill("quick_suite__sc_invoice_gateway")` — tools: apply_approval_rules, get_payment_queue
- **Disruption**: `load_skill("quick_suite__sc_disruption_alert_gateway")` — tools: assess_disruption_impact, classify_severity, recommend_mitigation, get_escalation_chain
- **Agent (Multi-Step)**: `load_skill("quick_suite__sc_order_fulfillment_agent")` — the sc-order-fulfillment-agent connector (direct AgentCore runtime URL); send it a natural-language multi-step instruction
- **Data/Documents**: `load_skill("quick_suite__spaces")` — tools: query_topic, get_dashboard_sheet, search_relevant_content, list_space_documents


## Architecture

### Data Sources (Quick Space: "SupplyChainManagement")
- **Orders & Customers** — order status, amounts, customer details
- **Inventory & Products** — stock levels, reorder points, product catalog
- **Suppliers & Contracts** — supplier info, contract terms, discount tiers
- **Sales Trends** — historical sales by product/period
- **Shipments & Tracking** — shipment status, carrier, delivery events
- **Documents** — 2 invoices (`SUP-INV-40001` auto-approve, `SUP-INV-40003` ~6% variance → review), 2 contracts (`CTR-2026-003` volume, `CTR-2026-004` early-pay)

### Business Rule MCPs (AgentCore Connectors)
- **sc-quoting-gateway** — generate_quote, get_quote_history, get_pricing_rules, validate_quote
- **sc-governance-gateway** — check_supplier_approval, log_audit_event, get_policy_rules, validate_budget_authority, get_audit_trail, check_regulatory_compliance
- **sc-invoice-gateway** — apply_approval_rules, get_payment_queue
- **sc-disruption-alert-gateway** — assess_disruption_impact, classify_severity, recommend_mitigation, get_escalation_chain
- **sc-order-fulfillment-agent connector (direct AgentCore runtime URL)** — invoke the sc_order_fulfillment_agent runtime for multi-step reasoning

## Workflow

### Step 1: Classify the Query
- **Mode**: `agentic`
- **Input**: `{{query}}`
- **Output**: Classification into one or more categories: DATA_LOOKUP, QUOTE, INVOICE, GOVERNANCE, DISRUPTION, MULTI_STEP
- **Validate**: At least one category identified
- **On failure**: Default to DATA_LOOKUP and attempt a space query

Classification guide:
| Signal words | Category | Primary tools |
|---|---|---|
| order status, inventory, stock level, shipment tracking | DATA_LOOKUP | query_topic / get_dashboard_sheet |
| quote, pricing, discount, margin | QUOTE | sc-quoting-gateway tools |
| invoice, payment, approval, AP aging | INVOICE | sc-invoice-gateway tools |
| supplier approval, compliance, budget, audit, policy | GOVERNANCE | sc-governance-gateway tools |
| disruption, delay, risk, mitigation, impact | DISRUPTION | sc-disruption-alert-gateway tools |
| "process all orders", "full workflow", "end to end", "check everything", multi-step reasoning across quoting + governance + invoice + disruption | AGENT | sc-order-fulfillment-agent connector (direct AgentCore runtime URL) |
| user attached/uploaded a PDF invoice or document | INVOICE_UPLOAD | Read attached file → extract data → apply_approval_rules |
| show me, chart, trend, dashboard, visualize, compare, top, breakdown, sales trends, month over month | VISUALIZATION | query_topic + Highcharts |
| combination of 2 categories but NOT needing agent orchestration (e.g., "quote + compliance check") | MULTI_STEP | Call individual MCPs sequentially |

### Step 2: Load Required Skills
- **Mode**: `deterministic`
- **Tool**: `load_skill`
- **Input**: Skill names based on Step 1 classification
- **Output**: Tools become available for use
- **Validate**: Skills loaded successfully
- **On failure**: Retry once; if still failing, inform user the connector may not be configured

Always load `quick_suite__spaces` for data access. Then load the relevant MCP skill(s) based on classification.

### Step 3: Retrieve Structured Data (ALWAYS — this is mandatory for ALL queries)
- **Mode**: `agentic`
- **Tool**: `query_topic` or `get_dashboard_sheet`
- **Input**: Natural language question mapped to the appropriate dataset
- **Output**: Tabular data or document content from the space
- **Validate**: Response contains relevant rows/text
- **On failure**: Try `search_relevant_content` with broader terms, or list documents with `list_space_documents`

The space ID is `<quick-space-id>` (configure via the `QUICK_SPACE_ID` environment variable).

**CRITICAL: The Quick Space contains the real business data (connected to Snowflake). The MCPs are ONLY rule engines — they do NOT have their own data. You MUST query the Space first to get actual data, then pass it to MCPs for rule evaluation.**

**What to query by category:**
- **QUOTE**: Query "Suppliers & Contracts" for customer tier/discount info, "Inventory & Products" for product details (SKU, price, stock), and "Orders & Customers" for customer history
- **INVOICE**: Query the space for invoice documents (`search_relevant_content`), plus "Orders & Customers" for matching PO data
- **GOVERNANCE**: Query "Suppliers & Contracts" for supplier details, contract terms
- **DISRUPTION**: Query "Shipments & Tracking" for affected shipments, "Orders & Customers" for open orders at risk, "Inventory & Products" for buffer stock levels
- **DATA_LOOKUP**: Query the relevant dataset directly

Use `query_topic` for natural language questions against datasets. Use `search_relevant_content` for document content (invoices, contracts).

**The data from this step is what you pass as parameters to the MCP tools in Step 4.** For example, for a quote: get the customer's tier from the space, get the product's list price from the space, then pass those values to `generate_quote`.

**If user UPLOADED/ATTACHED a file (invoice PDF):**
When the user attaches a PDF invoice directly in chat:
1. Read the attached file using `file_read_pdf` to extract text
2. Parse key fields: invoice number, PO reference, supplier name, line items, amounts, due date, payment terms
3. Query the Space for: matching PO, supplier contract, supplier details
4. Run FULL checks (not just invoice approval):
   a. **Governance**: `check_supplier_approval(supplier_name=...)` — is supplier still approved?
   b. **Governance**: `check_regulatory_compliance(supplier_name=...)` — do they meet standards?
   c. **Invoice**: `apply_approval_rules(...)` — does the invoice pass 3-way match, variance tolerance, amount thresholds?
   d. **Contract check**: Compare invoice terms vs contract terms (payment terms, pricing, discounts)
5. Synthesize all results into a single recommendation: APPROVE / REVIEW / REJECT with reasoning from each check

**Example extraction:**
- From document (`Invoice_SUP-INV-40003.txt`): Invoice# SUP-INV-40003, PO-2026-41003, Supplier: Sample Supplier Co 04 (SUP-104), Invoice total: $15,582.00
- From Space: PO-2026-41003 approved total was $14,700.00
- Variance: ($15,582.00 - $14,700.00) / $14,700.00 = 6.0%
- Run: `check_supplier_approval(supplier_name="Sample Supplier Co 04")` → ✅ Approved
- Run: `check_regulatory_compliance(supplier_name="Sample Supplier Co 04")` → ✅ Compliant
- Run: `apply_approval_rules(invoice_id="SUP-INV-40003", po_reference="PO-2026-41003", variance_pct=6.0, amount=15582.00)` → 🔍 DIRECTOR_REVIEW (6% variance exceeds 5% threshold)

This is the most impressive demo — user drops a PDF, Quick reads it, cross-references Snowflake data, and makes an approval recommendation in seconds.

### Step 4: Apply Business Rules (if QUOTE, INVOICE, GOVERNANCE, or DISRUPTION)
- **Mode**: `agentic`
- **Tool**: The appropriate MCP connector tool(s)
- **Input**: Parameters extracted from the query + any data from Step 3
- **Output**: Business rule evaluation result (approval decision, quote, compliance check, etc.)
- **Validate**: MCP returns a structured response with a clear outcome
- **On failure**: Check parameter formatting. MCP tools expect specific field names — inspect the tool schema.

**Quoting examples:**
- "Generate a quote for Sample Manufacturing Co 03, 1000 units of Sample Component 03 (SKU-103), align with contract CTR-2026-003" →
  1. Query "Orders & Customers"/"Suppliers & Contracts" for the account tier (ACC-003 = Enterprise) and contract CTR-2026-003 terms.
  2. Query "Inventory & Products" for SKU-103 list price + min margin.
  3. `generate_quote(customer="Sample Manufacturing Co 03", product="SKU-103", quantity=1000, list_price=..., customer_tier="Enterprise", contract_id="CTR-2026-003")`.
  4. **Render the result as a professional company-branded HTML quote document** (line items, tier/contract discount, subtotal/tax/total, validity) — and offer a **DOCX** copy via `file_write`. NEVER return a plain markdown table.
  5. If the account/product/contract names don't resolve, ask the user to pick from the real values (don't invent data).
- "What are our pricing rules?" → `get_pricing_rules()`

**Invoice examples:**
- "Should we approve invoice SUP-INV-40003?" → First search space for the invoice document + matching PO (PO-2026-41003) → then `apply_approval_rules(invoice_id="SUP-INV-40003")`
- "Show the payment queue" → First query "Orders & Customers" for open orders context → then `get_payment_queue()`

**Governance examples:**
- "Is Sample Supplier Co 04 an approved supplier?" → `check_supplier_approval(supplier_name="Sample Supplier Co 04")`
- "Does a Regional Manager have budget authority for $50K?" → `validate_budget_authority(role="Regional Manager", amount=50000)`

**Disruption examples:**
- "Any active disruptions?" → Query the "Disruptions" dataset in the Space (the disruption list lives in the data layer, not an MCP tool).
- "What's the impact of the port delay?" → First query "Shipments & Tracking" + "Orders & Customers" (+ inventory/accounts) for affected items → then `assess_disruption_impact(disruption={...}, affected_orders=[...], affected_shipments=[...], inventory_data=[...], customer_accounts=[...])` → `classify_severity(impact_assessment=...)` → `recommend_mitigation(disruption_type="...", impact_assessment=...)` → `get_escalation_chain(severity_level="...")`.

### Step 4b: Call the Agent (if AGENT classification)
- **Mode**: `agentic`
- **Tool**: the sc-order-fulfillment-agent connector (direct AgentCore runtime URL)
- **Input**: A natural language prompt describing the multi-step workflow + context data from Step 3
- **Output**: Agent's reasoning result with recommendations across multiple domains
- **Validate**: Agent returns a structured response (not an error)
- **On failure**: If agent times out (>5 min) or errors, fall back to calling individual MCPs sequentially

**WHEN TO CALL THE AGENT vs INDIVIDUAL MCPs:**

| Scenario | Use Agent? | Why |
|---|---|---|
| "Process all pending orders end-to-end" | ✅ YES | Requires reasoning across quote + governance + inventory |
| "Full workflow for new order: validate, quote, check compliance" | ✅ YES | Multi-step chain with dependencies |
| "Disruption hit — assess impact, find alternatives, re-quote" | ✅ YES | Chains disruption → governance → quoting |
| "Generate a quote for 1000 units" | ❌ NO | Single MCP call |
| "Is GlobalParts an approved supplier?" | ❌ NO | Single governance check |
| "Show me the payment queue" | ❌ NO | Single invoice call |
| "Quote + validate compliance for new supplier" | ⚠️ MAYBE | 2 MCPs — can call sequentially without agent |

**Rule of thumb:** Use the agent when the request involves **3+ MCPs** or requires **reasoning about the combined result** (e.g., "can we fulfill this?" needs inventory + supplier + quoting logic).

**How to call:**
```
# Send the prompt to the sc-order-fulfillment-agent connector (direct AgentCore runtime URL)
sc-order-fulfillment-agent(prompt="<natural language instruction with context>")
```

**Example prompts to pass to the sc-order-fulfillment-agent connector:**
- `sc-order-fulfillment-agent(prompt="Process order from Midwest Manufacturing: 1000 Stainless Steel Valves. Check inventory availability, verify supplier compliance, generate quote with contract-aligned pricing, and flag any disruption risks. Customer tier: Enterprise, Contract: CTR-2026-005.")`
- `sc-order-fulfillment-agent(prompt="We have a disruption on GlobalParts shipments. Assess impact on open orders PO-2026-41001 and PO-2026-41003, check if MidWest Industrial is an approved alternative, and generate contingency quotes for affected SKUs.")`
- `sc-order-fulfillment-agent(prompt="Run end-of-day order processing: check all pending invoices for approval, identify orders at risk from active disruptions, and generate a summary with recommended actions.")`

**IMPORTANT:** Always include relevant context from Step 3 (Space data) in the prompt you send to the agent. The agent doesn't query Snowflake directly — you pre-fetch the data and pass it as part of the instruction. Example:
```
# First query Space for context
orders = query_topic("show all pending orders for Midwest Manufacturing")
inventory = query_topic("inventory levels for SKU-104")

# Then send context to the sc-order-fulfillment-agent connector (direct AgentCore runtime URL)
sc-order-fulfillment-agent(prompt=f"Process this order: {orders}. Current inventory: {inventory}. Generate quote, check compliance, flag risks.")
```

### Step 5: Synthesize and Present
- **Mode**: `agentic`
- **Input**: Results from Steps 3 and 4
- **Output**: Professional styled document (HTML artifact) for quotes/invoices/reports, or concise summary for simple lookups
- **Validate**: Response directly addresses the original query with specific numbers/recommendations
- **On failure**: If data is incomplete, state what's known and what's missing

**RENDERING RULES:**
- **QUOTE** → ALWAYS render as company-branded HTML artifact (use the template below). Never a plain table for quotes.
- **All other responses** (governance, disruption, invoice decisions, data lookups, agent results) → Respond with clean, concise text/markdown. Include key data, recommendations, and next steps.
- **After any non-quote response**, offer a follow-up: *"Would you like me to generate a formal company-branded document for this?"* — if the user says yes, THEN render as HTML artifact.
- **Visualizations** (charts/dashboards) → Always render as HTML artifact with Highcharts (these are inherently visual).

#### Presentation Rules by Category

**QUOTE → Generate a professional quote document as an HTML artifact:**
- Load `html_design` skill for theme tokens and design system
- Render as a full-page styled HTML document with:
  - Company header with logo placeholder and "QUOTE" label
  - Quote number, date, valid-until date prominently displayed
  - Customer details section (name, address if available)
  - Line items table with columns: Item, SKU, Qty, List Price, Discount, Unit Price, Line Total
  - Subtotal, tax, and grand total in a summary box
  - Terms & conditions footer (payment terms, validity, margin indicator)
  - Professional color scheme (dark header, clean white body, accent for totals)
  - Print-friendly layout
- Use `<artifact type="html">` to render inline, then also save as HTML file and open in session tab
- The quote should look like something you'd email to a customer — not a markdown table

**QUOTE → ALSO generate a Word document (.docx) artifact:**
After rendering the HTML artifact inline, ALSO generate a professional Word document using `canvas_docx` skill (run_javascript with docx-js). The DOCX version should:
- Load `canvas_docx` skill and read `creation.md` for API reference
- Include the company logo at top-left (from user's attached file or workspace). Use a borderless 2-column table to place logo on left and "CONFIDENTIAL" text on right, on the same line.
- Use the company brand color palette: Primary Red `#D22630`, Brand Blue `#0071B3`, Dark `#1D1D1D`
- Include these sections:
  1. Header row: company logo (left) + "CONFIDENTIAL" label (right) — same line using borderless table
  2. Title: "PRICE QUOTATION" centered
  3. Subtitle: Quote number
  4. Quote Details table (Customer, Account ID, Tier, Date, Valid Until, Payment Terms, Status)
  5. Line Items table with headers: SKU, Product, Qty, List Price, Discount, Unit Price, Line Total
  6. Discount Breakdown section
  7. Summary/Totals table (Subtotal, Tax, Total, Margin)
  8. Terms & Conditions
  9. Footer with page numbers and quote reference
- Use `open_in_session_tab` to display the generated DOCX
- Save to `artifacts/quote_{quote_id}.docx`

**AnyCompany Logo for DOCX:**
- Check `workspace/attached_files/` for `hon-aero-anycompany-logo-web.png`
- If not found, check `/Users/vkachha/Documents/AnyCompany/` for any logo PNG
- Use `ImageRun` with the logo buffer, transformation width ~200px

**QUOTE → Send via Outlook:**
After generating the quote (HTML + DOCX), offer to send it for approval or to the customer. When sending:
- Load the `outlook_builtin` skill for email tools
- **Recipient**: Always send to `<approver-email>` (configure the approver/recipient address for your environment)
- **Subject**: "Quote {quote_id} — {customer_name} — {product_name}"
- **Body**: Brief summary of quote (total, qty, product, valid until) with the DOCX attached
- **Attach**: The generated DOCX quote file using `prepare_file_for_sending_to_connector`
- For "send for approval": Subject prefix with "[APPROVAL REQUIRED]"
- For "send to customer": Subject prefix with "[QUOTE]"

**INVOICE DECISION → Generate a styled approval memo:**
- Render as an HTML artifact with:
  - Header: "Invoice Review — [Invoice ID]"
  - Decision badge: ✅ AUTO-APPROVE (green), 🔍 REVIEW (amber), ❌ REJECT (red), ⬆️ ESCALATE (blue)
  - Invoice details panel (supplier, amount, PO reference, date)
  - Rules applied section — list each rule with pass/fail indicator
  - Recommendation with next steps
  - Approval chain if escalation needed

**DISRUPTION REPORT → Generate a risk dashboard:**
- Render as an HTML artifact with:
  - Severity indicator (Critical/High/Medium/Low with color coding)
  - Impact summary: affected orders count, revenue at risk, suppliers impacted
  - Timeline of events
  - Mitigation options as action cards
  - If multiple disruptions, show a summary table with drill-down capability

**GOVERNANCE CHECK → Generate a compliance card:**
- Render as an HTML artifact with:
  - Entity being checked (supplier name, budget request, etc.)
  - Status badge: ✅ COMPLIANT, ⚠️ CONDITIONAL, ❌ NON-COMPLIANT
  - Checklist of rules evaluated with pass/fail
  - Policy references
  - Required actions if non-compliant

**PAYMENT QUEUE → Generate a financial dashboard:**
- Render as an HTML artifact with:
  - Summary cards: Total payable, overdue amount, upcoming payments
  - Table of invoices sorted by due date with status indicators
  - AP aging buckets (Current, 30, 60, 90+ days) — use a simple bar chart via Highcharts if html_design + highcharts skills are loaded

**DATA LOOKUPS (orders, inventory, shipments, sales) → Concise but styled:**
- For simple queries: A brief summary with key numbers highlighted, followed by a clean HTML table if more than 3 rows
- For inventory alerts: Use color-coded badges (🔴 Critical, 🟡 Low, 🟢 OK)
- For shipment tracking: Timeline visualization showing current status
- Always include actionable insights ("3 items below reorder point — consider restocking")

**DATA VISUALIZATIONS (charts, dashboards, trends) → Generate company-branded Highcharts HTML artifact:**
- Load `highcharts` and `html_design` skills for chart rendering
- Use the AnyCompany color palette for all chart series (see colors below)
- Wrap charts in a branded container with AnyCompany header/footer
- See the "Visualization Template" section below for the exact HTML structure

#### HTML Document Template Guidelines

All HTML documents MUST follow AnyCompany Manufacturing branding:

**Brand Identity:**
- **Logo (embedded data URI — use this directly in `<img>` src):**
```
data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTUzIiBoZWlnaHQ9IjI4IiB2aWV3Qm94PSIwIDAgMTUzIDI4IiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgo8cGF0aCBkPSJNMTIuMTk1OCAwLjAzNzY1ODdIMTkuMDc0MVYyMS44ODUySDEyLjE5NThWMTEuOTAzMUg2Ljg2NTY3VjIxLjk0OEgwLjAzODA4NTlWMC4wMzc2NTg3SDYuOTAzNzRWOS4xNTMzNkgxMi4yMzM4TDEyLjE5NTggMC4wMzc2NTg3Wk0zOC4xODYzIDkuNTY3N0MzOC43OTQ2IDEwLjc0MDMgMzkuMTUzNCAxMi4wMjM4IDM5LjI0MDggMTMuMzM5QzM5LjMyODEgMTQuNjU0MiAzOS4xNDE5IDE1Ljk3MzEgMzguNjkzOSAxNy4yMTQzQzM4LjAzMzQgMTkuMDg4IDM2LjY3MDQgMjAuNjM4OSAzNC44ODY3IDIxLjU0NjJDMzMuMzY5NCAyMi4yMjQyIDMxLjcxNTcgMjIuNTUwNSAzMC4wNTE1IDIyLjUwMDRDMjguMDMwMyAyMi41NzQ0IDI2LjAzODEgMjIuMDA2OSAyNC4zNjYxIDIwLjg4MDdDMjIuODY1NCAxOS44MDMxIDIxLjc3ODMgMTguMjUzOSAyMS4yODIzIDE2LjQ4NjFDMjAuOTA1NSAxNS4xOTk4IDIwLjgxODIgMTMuODQ3NSAyMS4wMjY1IDEyLjUyNDJDMjEuMjM0OCAxMS4yMDA5IDIxLjczMzYgOS45Mzg5IDIyLjQ4NzkgOC44MjY5QzIzLjE0OTQgNy45NDQ3NiAyMy45ODM1IDcuMjAzMzUgMjQuOTQwNiA2LjY0NjgxQzI1Ljg5NzggNi4wOTAyOCAyNi45NTgzIDUuNzMgMjguMDU5MSA1LjU4NzQ0QzI4LjY0ODMgNS40NzIwMSAyOS4yNDc5IDUuNDE3MzEgMjkuODQ4NSA1LjQyNDIxQzMxLjIwMDcgNS4zNjcwNCAzMi41NTE4IDUuNTU4NjIgMzMuODMzNCA1Ljk4OTIzQzM1LjY4ODYgNi41NzUzMSAzNy4yNDY0IDcuODQyNDQgMzguMTg2MyA5LjUzMDA0VjkuNTY3N1pNMzIuMzg2NiAxMS43NjVDMzIuNDAwOCAxMC44NzkgMzIuMjY3OSA5Ljk5Njc1IDMxLjk5MzIgOS4xNTMzNkMzMS42NjMzIDguMzM3MjEgMzEuMDc5NSA3LjQyMDYyIDMwLjA4OTYgNy40MjA2MkgyOS43NDdDMjkuMjE0IDcuNTIxMDcgMjguNzE5IDguMDczNTMgMjguMjQ5NSA5LjA2NTQ2QzI3Ljg2OTEgMTAuMzk5NCAyNy43MzE2IDExLjc4OTcgMjcuODQzNCAxMy4xNzEzQzI3Ljg0MzQgMTMuNzExMiAyNy44NDM0IDE0LjMwMTMgMjcuODQzNCAxNC45MjkxQzI3Ljg0MzQgMTcuODA0NSAyOC4wNDY0IDE4LjY5NiAyOC42ODA5IDE5LjU4NzRDMjguODE2MyAxOS44MTc5IDI4Ljk5OTQgMjAuMDE3NiAyOS4yMTggMjAuMTczNEMyOS40MzY3IDIwLjMyOTEgMjkuNjg2MSAyMC40Mzc1IDI5Ljk1IDIwLjQ5MTVDMzAuMTU1NSAyMC41MTI4IDMwLjM2MzMgMjAuNDkxNCAzMC41NTk5IDIwLjQyODdDMzAuNzU2NiAyMC4zNjYgMzAuOTM3OSAyMC4yNjMzIDMxLjA5MjIgMjAuMTI3M0MzMS44NzE4IDE5LjI1NDMgMzIuMzIwNSAxOC4xNDAxIDMyLjM2MTIgMTYuOTc1OEMzMi40MTIgMTYuNDQ4NCAzMi40MTIgMTMuODQ5MyAzMi4zODY2IDExLjcyNzNWMTEuNzY1Wk01NS40NDU2IDYuMDE0MzRDNTQuNTE0NyA1LjY0NzM5IDUzLjUxNjQgNS40Nzc4OSA1Mi41MTUyIDUuNTE2ODJDNTEuNTEzOSA1LjU1NTc1IDUwLjUzMjEgNS44MDIyNSA0OS42MzMzIDYuMjQwMzVDNDguNzg0NCA2LjcwNTI1IDQ4LjA0NDggNy4zNDI4OCA0Ny40NjMxIDguMTExMlY1LjkxMzg5SDQxLjExNzhWMjEuODg1Mkg0Ny40NjMxVjEwLjQwOUM0Ny44MzE5IDkuNzI3OTggNDguNDAzNSA5LjE3NTY3IDQ5LjEwMDIgOC44MjY5QzQ5LjQ1NDcgOC42MzA3NiA0OS44NjE2IDguNTQ3MjUgNTAuMjY1NyA4LjU4NzY4QzUwLjY2OTggOC42MjgxMSA1MS4wNTE2IDguNzkwNTIgNTEuMzU5MiA5LjA1MjkxQzUxLjczMiA5LjUzMzMxIDUxLjkxNjggMTAuMTMwOCA1MS44Nzk1IDEwLjczNTRWMjEuODg1Mkg1OC4xNjE0VjkuNzMwOTNDNTguMTE0IDguOTE1MDYgNTcuODMzNyA4LjEyOTIyIDU3LjM1MyA3LjQ2NDY4QzU2Ljg3MjMgNi44MDAxNCA1Ni4yMTA5IDYuMjg0MTYgNTUuNDQ1NiA1Ljk3NjY3VjYuMDE0MzRaTTc3LjIyMjggOS43MDU4MkM3Ny45ODEgMTEuMzMxOSA3OC4zMjk0IDEzLjExNTYgNzguMjM4IDE0LjkwNEg2Ni44MTY0VjE4LjE0MzVDNjYuODc3NyAxOC40OTczIDY3LjAwMjMgMTguODM3NSA2Ny4xODQ1IDE5LjE0OEM2Ny40NTEgMTkuNDg3IDY4LjM2NDcgMjAuODE3OSA3MS40NzM5IDIwLjMyODJDNzQuOTI1OCAxOS43ODgzIDc0LjgzNjkgMTcuMzM5OSA3NC45MDA0IDE3LjMzOTlINzcuNjI4OUM3Ny41ODE2IDE4LjM0NTkgNzcuMTY2NyAxOS4zMDA4IDc2LjQ2MTMgMjAuMDI2OUM3NC45NjkzIDIxLjM0ODEgNzMuMDk2NSAyMi4xNzM2IDcxLjEwNTkgMjIuMzg3NEM2OS43MTYgMjIuNjM0NiA2OC4yODk5IDIyLjYwMDggNjYuOTEzNyAyMi4yODc5QzY1LjUzNzQgMjEuOTc1IDY0LjIzOTQgMjEuMzg5NSA2My4wOTgxIDIwLjU2NjhDNjEuODkwMSAxOS41NzU3IDYwLjk4NDkgMTguMjcxNCA2MC40ODM4IDE2LjhDNTkuNzg4MiAxNC40NjgyIDU5Ljk4NyAxMS45NjQzIDYxLjA0MjIgOS43Njg2QzYxLjg5MjQgOC4zMjM1MSA2My4xNDE2IDcuMTQ4MzYgNjQuNjQzMiA2LjM4MTA3QzY2LjE0NDcgNS42MTM3OCA2Ny44MzY0IDUuMjg2MiA2OS41MTk1IDUuNDM2NzZDNzMuNTI5OCA1LjU4NzQzIDc1LjgxNDEgNy4wMTg4MyA3Ny4yMjI4IDkuNjY4MTVWOS43MDU4MlpNNzEuNjEzNSAxMS4zMzgxQzcxLjYxNzUgMTAuNjM2MyA3MS41MzY2IDkuOTM2NTUgNzEuMzcyNCA5LjI1MzhDNzEuMjI3NCA4LjcxODQ4IDcwLjkzNzkgOC4yMzIzMiA3MC41MzQ4IDcuODQ3NTJDNzAuMjI5NyA3LjU1NTExIDY5LjgzNjEgNy4zNjk3NSA2OS40MTQzIDcuMzE5ODVDNjguOTkyNiA3LjI2OTk2IDY4LjU2NTkgNy4zNTgyOSA2OC4xOTk3IDcuNTcxMjlDNjcuNTY3MyA4LjEwNDI2IDY3LjE0ODYgOC44NDM2NSA2Ny4wMTk1IDkuNjU1NkM2Ni45MDQyIDEwLjYzMDYgNjYuODU3NiAxMS42MTIzIDY2Ljg3OTkgMTIuNTkzN0g3MS42MTM1QzcxLjYwMDggMTIuNDQzIDcxLjYwMDggMTIuMDc4OSA3MS42MTM1IDExLjMzODFaTTEzNS4xNDMgOS42OTMyN0MxMzUuODk5IDExLjMxOTMgMTM2LjIzOSAxMy4xMDQ2IDEzNi4xMzMgMTQuODkxNUgxMjQuNzg3VjE4LjEzMDlDMTI0Ljg0OSAxOC40ODY1IDEyNC45NzggMTguODI3MyAxMjUuMTY4IDE5LjEzNTRDMTI1LjQyMiAxOS40NzQ0IDEyNi4zNDggMjAuODA1NCAxMjkuNDU4IDIwLjMxNTdDMTMyLjkwOSAxOS43NzU4IDEzMi44MDggMTcuMzI3MyAxMzIuODg0IDE3LjMyNzNIMTM1LjYxM0MxMzUuNTU5IDE4LjMzMTggMTM1LjE0NSAxOS4yODQ1IDEzNC40NDUgMjAuMDE0M0MxMzIuOTQ2IDIxLjM1NDIgMTMxLjA1OSAyMi4xOTMyIDEyOS4wNTIgMjIuNDEyNUMxMjYuODA1IDIyLjYzODYgMTIzLjYyIDIyLjcwMTMgMTIxLjA1NiAyMC41OTE5QzExOS44NDUgMTkuNjAxMSAxMTguOTM2IDE4LjI5NzEgMTE4LjQyOSAxNi44MjUxQzExNy43MzQgMTQuNDkzMyAxMTcuOTMzIDExLjk4OTQgMTE4Ljk4OCA5Ljc5MzcxQzExOS44MzggOC4zNDg2MyAxMjEuMDg3IDcuMTczNDcgMTIyLjU4OSA2LjQwNjE4QzEyNC4wOSA1LjYzODg5IDEyNS43ODIgNS4zMTEzMSAxMjcuNDY1IDUuNDYxODdDMTMxLjQ1IDUuNTg3NDMgMTMzLjczNCA3LjAxODgzIDEzNS4xNDMgOS42NjgxNVY5LjY5MzI3Wk0xMjkuNDgzIDExLjMzODFDMTI5LjQ5MiAxMC42MzUzIDEyOS40MDYgOS45MzQ0NyAxMjkuMjI5IDkuMjUzOEMxMjkuMDgzIDguNzE2MDcgMTI4Ljc4OSA4LjIyOTE2IDEyOC4zNzkgNy44NDc1MkMxMjguMDY2IDcuNTc3NyAxMjcuNjcyIDcuNDE1ODggMTI3LjI1OCA3LjM4NjQ0QzEyNi44NDQgNy4zNTcgMTI2LjQzMSA3LjQ2MTUzIDEyNi4wODIgNy42ODQzQzEyNS40NDkgOC4yMTcyNyAxMjUuMDMxIDguOTU2NjUgMTI0LjkwMiA5Ljc2ODZDMTI0Ljc4MSAxMC43Mzg5IDEyNC43MzQgMTEuNzE2OSAxMjQuNzYyIDEyLjY5NDJIMTI5LjQ4M0MxMjkuNDgzIDEyLjQ0MyAxMjkuNDgzIDEyLjA3ODkgMTI5LjQ4MyAxMS4zMzgxWk0xMTUuNTIzIDUuOTEzODlMMTEzLjA3NCAxNC44NjY0TDExMC4zNDUgNS45MTM4OUgxMDQuNzQ5TDEwMi4yMTEgMTQuODY2NEw5OS40MDYxIDUuOTEzODlIOTAuNzUxTDg3LjYwMzggMTMuNTIyOUw4NC42MzQxIDUuOTEzODlINzcuNjc5Nkw4NC4xMTM4IDIyLjIzNjhDODQuMDkzOCAyMi40NDU3IDg0LjA1MTMgMjIuNjUyIDgzLjk4NjkgMjIuODUyQzgzLjcwOTYgMjMuNzU0NiA4My4yNDI1IDI0LjU4OSA4Mi42MTYzIDI1LjMwMDRDODIuNDE5MiAyNS41MzE2IDgyLjE2NDQgMjUuNzA3NyA4MS44NzcyIDI1LjgxMTJDODEuNTkgMjUuOTE0OCA4MS4yODA0IDI1Ljk0MjEgODAuOTc5MiAyNS44OTA2QzgxLjIwNTMgMjUuNjc2NyA4MS4zODY1IDI1LjQyMDYgODEuNTEyMiAyNS4xMzcyQzgxLjY4NjEgMjQuNDY4MSA4MS41OTUzIDIzLjc1ODUgODEuMjU4NCAyMy4xNTM0QzgwLjk1NTMgMjIuNjk3IDgwLjUxMDEgMjIuMzUwOCA3OS45OTAzIDIyLjE2NzNDNzkuNDcwNCAyMS45ODM3IDc4LjkwNDMgMjEuOTcyOSA3OC4zNzc2IDIyLjEzNjNDNzcuNzEyNSAyMi4yODEyIDc3LjEzMDkgMjIuNjc3MiA3Ni43NTY0IDIzLjI0Qzc2LjM4MTkgMjMuODAyOSA3Ni4yNDQyIDI0LjQ4OCA3Ni4zNzI1IDI1LjE0OThDNzYuNTI0OCAyNi40MDU0IDc3LjM4NzggMjcuNTczMSA3OS41MzI1IDI3Ljk0OThDODAuMzY4OSAyOC4xMTU3IDgxLjIzNDEgMjguMDcwMyA4Mi4wNDgxIDI3LjgxOEM4Mi44NjIyIDI3LjU2NTYgODMuNTk4OCAyNy4xMTQzIDg0LjE5IDI2LjUwNThDODUuMTM4MSAyNS4zOTUgODUuODY1OCAyNC4xMTcgODYuMzM0NyAyMi43MzlMOTMuMDQ4IDYuNDE2MTNMOTcuODE5OCAyMS44ODUySDEwMy4yMTNMMTA1Ljc1MSAxMi42MTg4TDEwOC42NyAyMS44ODUySDExMy45MTJMMTE4LjM1MyA1LjkxMzg5SDExNS41MjNaTTEzOC4wMzYgMjEuODg1MkgxNDQuMTUzVjAuMDM3NjU4N0gxMzguMDM2VjIxLjg4NTJaTTE0Ni4wNTcgMC4wMzc2NTg3VjIxLjg4NTJIMTUyLjE3NFYwLjAzNzY1ODdIMTQ2LjA1N1oiIGZpbGw9IiNEMjI2MzAiLz4KPC9zdmc+Cg==
```
- **Company Name**: AnyCompany Manufacturing
- Always place the logo in the bottom-right of the document (matching their corporate doc style) OR top-right for quotes/invoices
- Include "Company Confidential" footer text where appropriate
- **Actual Brand Red from their logo**: `#D22630`

**Color Palette:**
- **Brand Red** (primary accent, subtitles, highlights, page markers): `#D32F2F`
- **Secondary Red** (darker, for hover): `#B71C1C`
- **Brand Blue** (CTAs, links, interactive elements): `#0071B3`
- **Secondary Blue**: `#007BC2`
- **Dark** (header backgrounds, primary text): `#1D1D1D`
- **Body Text**: `#303030`
- **Subtle Text**: `#535353`
- **Light Background** (alternating rows, sections): `#F7F7F7`
- **Border/Divider**: `#E0E0E0`
- **Header Bar**: Use a subtle red gradient or solid `#D32F2F` top border/accent stripe
- **Success Green**: `#468254` (approved, compliant)
- **Warning Amber**: `#B8860B` (review needed)
- **Alert Red**: `#C62828` (rejected, critical)

**Typography:**
- **Font Stack**: `"Inter", Helvetica, Arial, sans-serif`
- **Document title**: 24px, bold, `#1D1D1D`
- **Section headers**: 14px, uppercase, letter-spacing 1px, `#0071B3`
- **Body text**: 14px, `#303030`
- **Numbers/IDs**: Use monospace (`"SF Mono", "Fira Code", Consolas, monospace`)
- **Amounts**: Right-aligned, bold for totals

**Layout:**
- Max-width: 800px, centered with `margin: 0 auto`
- Header: White background with a red top accent stripe (4px `#D32F2F`), document title left, company logo right
- Content: Clean white with `padding: 40px`
- Footer: Light gray `#F7F7F7` background, "Company Confidential © 2025 AnyCompany Inc." + generation date
- Page accent: Small red corner triangle or red sidebar element (matching their corporate deck style)

**Component Styles:**
- **Status badges**: Rounded pill (`border-radius: 20px`), white text on colored bg (use red for alerts, green for success, blue for info)
- **Tables**: Header row `#1D1D1D` bg with white text, alternating `#F7F7F7` rows, no outer border, subtle row borders
- **CTA buttons**: `#D32F2F` background, white text, `border-radius: 4px`, uppercase, letter-spacing (secondary buttons use `#0071B3`)
- **Cards/Panels**: White bg, `1px solid #E0E0E0`, `border-radius: 8px`, subtle shadow
- **Totals/Summary box**: Right-aligned, light red tint `rgba(211, 47, 47, 0.05)` background with `#D32F2F` left border
- **Section dividers**: Thin red line (`1px solid #D32F2F`) or red dot separator

**Print Styles:**
- Include `@media print` rules: hide non-essential UI, ensure page breaks, keep tables intact
- Add "Company Confidential © 2025 AnyCompany Inc. All rights reserved." in footer
- Include "Generated by Supply Chain Operations • [date]" as secondary footer line

**Design Reference:**
This branding matches the company's internal corporate document style (see eRFQ Process deck): red accent color, company wordmark bottom-right, clean white backgrounds, bold black headers, red highlights for key terms. Our generated documents should look like they came from the same design system as their HASP/SCC portal materials.

#### QUOTE HTML Template (USE THIS EXACTLY — fill in values from MCP response)

```html
<artifact type="html">
<!DOCTYPE html>
<html>
<head>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #303030; background: #fff; }
.doc { max-width: 800px; margin: 0 auto; border: 1px solid #E0E0E0; }
.header { padding: 30px 40px; border-bottom: 1px solid #E0E0E0; display: flex; justify-content: space-between; align-items: center; border-top: 4px solid #D32F2F; }
.header-left h1 { font-size: 28px; font-weight: 700; color: #1D1D1D; letter-spacing: -0.5px; }
.header-left .doc-type { font-size: 11px; text-transform: uppercase; letter-spacing: 2px; color: #D32F2F; font-weight: 600; margin-bottom: 4px; }
.header-right img { height: 32px; }
.meta { padding: 24px 40px; background: #F7F7F7; display: grid; grid-template-columns: 1fr 1fr; gap: 20px; border-bottom: 1px solid #E0E0E0; }
.meta-group label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #535353; display: block; margin-bottom: 2px; }
.meta-group span { font-size: 14px; font-weight: 600; color: #1D1D1D; }
.meta-group .mono { font-family: "SF Mono", "Fira Code", Consolas, monospace; }
.content { padding: 30px 40px; }
.section-title { font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #D32F2F; font-weight: 600; margin-bottom: 12px; border-bottom: 1px solid #E0E0E0; padding-bottom: 8px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
thead th { background: #1D1D1D; color: #fff; padding: 10px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; text-align: left; }
thead th.right { text-align: right; }
tbody td { padding: 12px; border-bottom: 1px solid #E0E0E0; font-size: 13px; }
tbody tr:nth-child(even) { background: #F7F7F7; }
tbody td.right { text-align: right; }
tbody td.bold { font-weight: 700; }
.totals { display: flex; justify-content: flex-end; margin-bottom: 30px; }
.totals-box { background: rgba(211, 47, 47, 0.04); border-left: 3px solid #D32F2F; padding: 16px 24px; min-width: 280px; }
.totals-row { display: flex; justify-content: space-between; padding: 6px 0; font-size: 13px; }
.totals-row.grand { font-size: 18px; font-weight: 700; color: #1D1D1D; border-top: 2px solid #D32F2F; margin-top: 8px; padding-top: 12px; }
.margin-badge { display: inline-block; background: #468254; color: #fff; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; margin-left: 8px; }
.terms { padding: 20px 40px; background: #F7F7F7; border-top: 1px solid #E0E0E0; }
.terms h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #535353; margin-bottom: 8px; }
.terms p { font-size: 12px; color: #535353; line-height: 1.6; }
.footer { padding: 16px 40px; border-top: 1px solid #E0E0E0; display: flex; justify-content: space-between; align-items: center; }
.footer-text { font-size: 10px; color: #535353; }
.footer img { height: 20px; opacity: 0.6; }
.status-badge { display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; }
.status-draft { background: #FFF3E0; color: #E65100; }
.status-approved { background: #E8F5E9; color: #2E7D32; }
@media print { .doc { border: none; } }
</style>
</head>
<body>
<div class="doc">
  <div class="header">
    <div class="header-left">
      <div class="doc-type">Quotation</div>
      <h1>QT-2026-XXX</h1>
    </div>
    <div class="header-right">
      <img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTUzIiBoZWlnaHQ9IjI4IiB2aWV3Qm94PSIwIDAgMTUzIDI4IiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgo8cGF0aCBkPSJNMTIuMTk1OCAwLjAzNzY1ODdIMTkuMDc0MVYyMS44ODUySDEyLjE5NThWMTEuOTAzMUg2Ljg2NTY3VjIxLjk0OEgwLjAzODA4NTlWMC4wMzc2NTg3SDYuOTAzNzRWOS4xNTMzNkgxMi4yMzM4TDEyLjE5NTggMC4wMzc2NTg3Wk0zOC4xODYzIDkuNTY3N0MzOC43OTQ2IDEwLjc0MDMgMzkuMTUzNCAxMi4wMjM4IDM5LjI0MDggMTMuMzM5QzM5LjMyODEgMTQuNjU0MiAzOS4xNDE5IDE1Ljk3MzEgMzguNjkzOSAxNy4yMTQzQzM4LjAzMzQgMTkuMDg4IDM2LjY3MDQgMjAuNjM4OSAzNC44ODY3IDIxLjU0NjJDMzMuMzY5NCAyMi4yMjQyIDMxLjcxNTcgMjIuNTUwNSAzMC4wNTE1IDIyLjUwMDRDMjguMDMwMyAyMi41NzQ0IDI2LjAzODEgMjIuMDA2OSAyNC4zNjYxIDIwLjg4MDdDMjIuODY1NCAxOS44MDMxIDIxLjc3ODMgMTguMjUzOSAyMS4yODIzIDE2LjQ4NjFDMjAuOTA1NSAxNS4xOTk4IDIwLjgxODIgMTMuODQ3NSAyMS4wMjY1IDEyLjUyNDJDMjEuMjM0OCAxMS4yMDA5IDIxLjczMzYgOS45Mzg5IDIyLjQ4NzkgOC44MjY5QzIzLjE0OTQgNy45NDQ3NiAyMy45ODM1IDcuMjAzMzUgMjQuOTQwNiA2LjY0NjgxQzI1Ljg5NzggNi4wOTAyOCAyNi45NTgzIDUuNzMgMjguMDU5MSA1LjU4NzQ0QzI4LjY0ODMgNS40NzIwMSAyOS4yNDc5IDUuNDE3MzEgMjkuODQ4NSA1LjQyNDIxQzMxLjIwMDcgNS4zNjcwNCAzMi41NTE4IDUuNTU4NjIgMzMuODMzNCA1Ljk4OTIzQzM1LjY4ODYgNi41NzUzMSAzNy4yNDY0IDcuODQyNDQgMzguMTg2MyA5LjUzMDA0VjkuNTY3N1pNMzIuMzg2NiAxMS43NjVDMzIuNDAwOCAxMC44NzkgMzIuMjY3OSA5Ljk5Njc1IDMxLjk5MzIgOS4xNTMzNkMzMS42NjMzIDguMzM3MjEgMzEuMDc5NSA3LjQyMDYyIDMwLjA4OTYgNy40MjA2MkgyOS43NDdDMjkuMjE0IDcuNTIxMDcgMjguNzE5IDguMDczNTMgMjguMjQ5NSA5LjA2NTQ2QzI3Ljg2OTEgMTAuMzk5NCAyNy43MzE2IDExLjc4OTcgMjcuODQzNCAxMy4xNzEzQzI3Ljg0MzQgMTMuNzExMiAyNy44NDM0IDE0LjMwMTMgMjcuODQzNCAxNC45MjkxQzI3Ljg0MzQgMTcuODA0NSAyOC4wNDY0IDE4LjY5NiAyOC42ODA5IDE5LjU4NzRDMjguODE2MyAxOS44MTc5IDI4Ljk5OTQgMjAuMDE3NiAyOS4yMTggMjAuMTczNEMyOS40MzY3IDIwLjMyOTEgMjkuNjg2MSAyMC40Mzc1IDI5Ljk1IDIwLjQ5MTVDMzAuMTU1NSAyMC41MTI4IDMwLjM2MzMgMjAuNDkxNCAzMC41NTk5IDIwLjQyODdDMzAuNzU2NiAyMC4zNjYgMzAuOTM3OSAyMC4yNjMzIDMxLjA5MjIgMjAuMTI3M0MzMS44NzE4IDE5LjI1NDMgMzIuMzIwNSAxOC4xNDAxIDMyLjM2MTIgMTYuOTc1OEMzMi40MTIgMTYuNDQ4NCAzMi40MTIgMTMuODQ5MyAzMi4zODY2IDExLjcyNzNWMTEuNzY1Wk01NS40NDU2IDYuMDE0MzRDNTQuNTE0NyA1LjY0NzM5IDUzLjUxNjQgNS40Nzc4OSA1Mi41MTUyIDUuNTE2ODJDNTEuNTEzOSA1LjU1NTc1IDUwLjUzMjEgNS44MDIyNSA0OS42MzMzIDYuMjQwMzVDNDguNzg0NCA2LjcwNTI1IDQ4LjA0NDggNy4zNDI4OCA0Ny40NjMxIDguMTExMlY1LjkxMzg5SDQxLjExNzhWMjEuODg1Mkg0Ny40NjMxVjEwLjQwOUM0Ny44MzE5IDkuNzI3OTggNDguNDAzNSA5LjE3NTY3IDQ5LjEwMDIgOC44MjY5QzQ5LjQ1NDcgOC42MzA3NiA0OS44NjE2IDguNTQ3MjUgNTAuMjY1NyA4LjU4NzY4QzUwLjY2OTggOC42MjgxMSA1MS4wNTE2IDguNzkwNTIgNTEuMzU5MiA5LjA1MjkxQzUxLjczMiA5LjUzMzMxIDUxLjkxNjggMTAuMTMwOCA1MS44Nzk1IDEwLjczNTRWMjEuODg1Mkg1OC4xNjE0VjkuNzMwOTNDNTguMTE0IDguOTE1MDYgNTcuODMzNyA4LjEyOTIyIDU3LjM1MyA3LjQ2NDY4QzU2Ljg3MjMgNi44MDAxNCA1Ni4yMTA5IDYuMjg0MTYgNTUuNDQ1NiA1Ljk3NjY3VjYuMDE0MzRaTTc3LjIyMjggOS43MDU4MkM3Ny45ODEgMTEuMzMxOSA3OC4zMjk0IDEzLjExNTYgNzguMjM4IDE0LjkwNEg2Ni44MTY0VjE4LjE0MzVDNjYuODc3NyAxOC40OTczIDY3LjAwMjMgMTguODM3NSA2Ny4xODQ1IDE5LjE0OEM2Ny40NTEgMTkuNDg3IDY4LjM2NDcgMjAuODE3OSA3MS40NzM5IDIwLjMyODJDNzQuOTI1OCAxOS43ODgzIDc0LjgzNjkgMTcuMzM5OSA3NC45MDA0IDE3LjMzOTlINzcuNjI4OUM3Ny41ODE2IDE4LjM0NTkgNzcuMTY2NyAxOS4zMDA4IDc2LjQ2MTMgMjAuMDI2OUM3NC45NjkzIDIxLjM0ODEgNzMuMDk2NSAyMi4xNzM2IDcxLjEwNTkgMjIuMzg3NEM2OS43MTYgMjIuNjM0NiA2OC4yODk5IDIyLjYwMDggNjYuOTEzNyAyMi4yODc5QzY1LjUzNzQgMjEuOTc1IDY0LjIzOTQgMjEuMzg5NSA2My4wOTgxIDIwLjU2NjhDNjEuODkwMSAxOS41NzU3IDYwLjk4NDkgMTguMjcxNCA2MC40ODM4IDE2LjhDNTkuNzg4MiAxNC40NjgyIDU5Ljk4NyAxMS45NjQzIDYxLjA0MjIgOS43Njg2QzYxLjg5MjQgOC4zMjM1MSA2My4xNDE2IDcuMTQ4MzYgNjQuNjQzMiA2LjM4MTA3QzY2LjE0NDcgNS42MTM3OCA2Ny44MzY0IDUuMjg2MiA2OS41MTk1IDUuNDM2NzZDNzMuNTI5OCA1LjU4NzQzIDc1LjgxNDEgNy4wMTg4MyA3Ny4yMjI4IDkuNjY4MTVWOS43MDU4MlpNNzEuNjEzNSAxMS4zMzgxQzcxLjYxNzUgMTAuNjM2MyA3MS41MzY2IDkuOTM2NTUgNzEuMzcyNCA5LjI1MzhDNzEuMjI3NCA4LjcxODQ4IDcwLjkzNzkgOC4yMzIzMiA3MC41MzQ4IDcuODQ3NTJDNzAuMjI5NyA3LjU1NTExIDY5LjgzNjEgNy4zNjk3NSA2OS40MTQzIDcuMzE5ODVDNjguOTkyNiA3LjI2OTk2IDY4LjU2NTkgNy4zNTgyOSA2OC4xOTk3IDcuNTcxMjlDNjcuNTY3MyA4LjEwNDI2IDY3LjE0ODYgOC44NDM2NSA2Ny4wMTk1IDkuNjU1NkM2Ni45MDQyIDEwLjYzMDYgNjYuODU3NiAxMS42MTIzIDY2Ljg3OTkgMTIuNTkzN0g3MS42MTM1QzcxLjYwMDggMTIuNDQzIDcxLjYwMDggMTIuMDc4OSA3MS42MTM1IDExLjMzODFaTTEzNS4xNDMgOS42OTMyN0MxMzUuODk5IDExLjMxOTMgMTM2LjIzOSAxMy4xMDQ2IDEzNi4xMzMgMTQuODkxNUgxMjQuNzg3VjE4LjEzMDlDMTI0Ljg0OSAxOC40ODY1IDEyNC45NzggMTguODI3MyAxMjUuMTY4IDE5LjEzNTRDMTI1LjQyMiAxOS40NzQ0IDEyNi4zNDggMjAuODA1NCAxMjkuNDU4IDIwLjMxNTdDMTMyLjkwOSAxOS43NzU4IDEzMi44MDggMTcuMzI3MyAxMzIuODg0IDE3LjMyNzNIMTM1LjYxM0MxMzUuNTU5IDE4LjMzMTggMTM1LjE0NSAxOS4yODQ1IDEzNC40NDUgMjAuMDE0M0MxMzIuOTQ2IDIxLjM1NDIgMTMxLjA1OSAyMi4xOTMyIDEyOS4wNTIgMjIuNDEyNUMxMjYuODA1IDIyLjYzODYgMTIzLjYyIDIyLjcwMTMgMTIxLjA1NiAyMC41OTE5QzExOS44NDUgMTkuNjAxMSAxMTguOTM2IDE4LjI5NzEgMTE4LjQyOSAxNi44MjUxQzExNy43MzQgMTQuNDkzMyAxMTcuOTMzIDExLjk4OTQgMTE4Ljk4OCA5Ljc5MzcxQzExOS44MzggOC4zNDg2MyAxMjEuMDg3IDcuMTczNDcgMTIyLjU4OSA2LjQwNjE4QzEyNC4wOSA1LjYzODg5IDEyNS43ODIgNS4zMTEzMSAxMjcuNDY1IDUuNDYxODdDMTMxLjQ1IDUuNTg3NDMgMTMzLjczNCA3LjAxODgzIDEzNS4xNDMgOS42NjgxNVY5LjY5MzI3Wk0xMjkuNDgzIDExLjMzODFDMTI5LjQ5MiAxMC42MzUzIDEyOS40MDYgOS45MzQ0NyAxMjkuMjI5IDkuMjUzOEMxMjkuMDgzIDguNzE2MDcgMTI4Ljc4OSA4LjIyOTE2IDEyOC4zNzkgNy44NDc1MkMxMjguMDY2IDcuNTc3NyAxMjcuNjcyIDcuNDE1ODggMTI3LjI1OCA3LjM4NjQ0QzEyNi44NDQgNy4zNTcgMTI2LjQzMSA3LjQ2MTUzIDEyNi4wODIgNy42ODQzQzEyNS40NDkgOC4yMTcyNyAxMjUuMDMxIDguOTU2NjUgMTI0LjkwMiA5Ljc2ODZDMTI0Ljc4MSAxMC43Mzg5IDEyNC43MzQgMTEuNzE2OSAxMjQuNzYyIDEyLjY5NDJIMTI5LjQ4M0MxMjkuNDgzIDEyLjQ0MyAxMjkuNDgzIDEyLjA3ODkgMTI5LjQ4MyAxMS4zMzgxWk0xMTUuNTIzIDUuOTEzODlMMTEzLjA3NCAxNC44NjY0TDExMC4zNDUgNS45MTM4OUgxMDQuNzQ5TDEwMi4yMTEgMTQuODY2NEw5OS40MDYxIDUuOTEzODlIOTAuNzUxTDg3LjYwMzggMTMuNTIyOUw4NC42MzQxIDUuOTEzODlINzcuNjc5Nkw4NC4xMTM4IDIyLjIzNjhDODQuMDkzOCAyMi40NDU3IDg0LjA1MTMgMjIuNjUyIDgzLjk4NjkgMjIuODUyQzgzLjcwOTYgMjMuNzU0NiA4My4yNDI1IDI0LjU4OSA4Mi42MTYzIDI1LjMwMDRDODIuNDE5MiAyNS41MzE2IDgyLjE2NDQgMjUuNzA3NyA4MS44NzcyIDI1LjgxMTJDODEuNTkgMjUuOTE0OCA4MS4yODA0IDI1Ljk0MjEgODAuOTc5MiAyNS44OTA2QzgxLjIwNTMgMjUuNjc2NyA4MS4zODY1IDI1LjQyMDYgODEuNTEyMiAyNS4xMzcyQzgxLjY4NjEgMjQuNDY4MSA4MS41OTUzIDIzLjc1ODUgODEuMjU4NCAyMy4xNTM0QzgwLjk1NTMgMjIuNjk3IDgwLjUxMDEgMjIuMzUwOCA3OS45OTAzIDIyLjE2NzNDNzkuNDcwNCAyMS45ODM3IDc4LjkwNDMgMjEuOTcyOSA3OC4zNzc2IDIyLjEzNjNDNzcuNzEyNSAyMi4yODEyIDc3LjEzMDkgMjIuNjc3MiA3Ni43NTY0IDIzLjI0Qzc2LjM4MTkgMjMuODAyOSA3Ni4yNDQyIDI0LjQ4OCA3Ni4zNzI1IDI1LjE0OThDNzYuNTI0OCAyNi40MDU0IDc3LjM4NzggMjcuNTczMSA3OS41MzI1IDI3Ljk0OThDODAuMzY4OSAyOC4xMTU3IDgxLjIzNDEgMjguMDcwMyA4Mi4wNDgxIDI3LjgxOEM4Mi44NjIyIDI3LjU2NTYgODMuNTk4OCAyNy4xMTQzIDg0LjE5IDI2LjUwNThDODUuMTM4MSAyNS4zOTUgODUuODY1OCAyNC4xMTcgODYuMzM0NyAyMi43MzlMOTMuMDQ4IDYuNDE2MTNMOTcuODE5OCAyMS44ODUySDEwMy4yMTNMMTA1Ljc1MSAxMi42MTg4TDEwOC42NyAyMS44ODUySDExMy45MTJMMTE4LjM1MyA1LjkxMzg5SDExNS41MjNaTTEzOC4wMzYgMjEuODg1MkgxNDQuMTUzVjAuMDM3NjU4N0gxMzguMDM2VjIxLjg4NTJaTTE0Ni4wNTcgMC4wMzc2NTg3VjIxLjg4NTJIMTUyLjE3NFYwLjAzNzY1ODdIMTQ2LjA1N1oiIGZpbGw9IiNEMjI2MzAiLz4KPC9zdmc+Cg==" alt="AnyCompany">
    </div>
  </div>
  <div class="meta">
    <div class="meta-group"><label>Customer</label><span>CUSTOMER NAME</span></div>
    <div class="meta-group"><label>Account ID</label><span class="mono">ACCT-ID</span></div>
    <div class="meta-group"><label>Date</label><span>June 9, 2026</span></div>
    <div class="meta-group"><label>Valid Until</label><span>July 9, 2026</span></div>
    <div class="meta-group"><label>Tier</label><span>Enterprise</span></div>
    <div class="meta-group"><label>Status</label><span class="status-badge status-draft">Draft</span></div>
  </div>
  <div class="content">
    <div class="section-title">Line Items</div>
    <table>
      <thead>
        <tr><th>Product</th><th>SKU</th><th class="right">Qty</th><th class="right">List Price</th><th class="right">Discount</th><th class="right">Unit Price</th><th class="right">Total</th></tr>
      </thead>
      <tbody>
        <tr>
          <td>PRODUCT NAME</td><td class="mono">SKU-XXX</td><td class="right">1,000</td><td class="right">$140.00</td><td class="right">25%</td><td class="right bold">$105.00</td><td class="right bold">$105,000.00</td>
        </tr>
      </tbody>
    </table>
    <div class="totals">
      <div class="totals-box">
        <div class="totals-row"><span>Subtotal</span><span>$105,000.00</span></div>
        <div class="totals-row"><span>Tax Estimate</span><span>$8,400.00</span></div>
        <div class="totals-row grand"><span>Total</span><span>$113,400.00</span></div>
        <div class="totals-row"><span>Margin</span><span>47.6% <span class="margin-badge">✓ Healthy</span></span></div>
      </div>
    </div>
  </div>
  <div class="terms">
    <h3>Terms & Conditions</h3>
    <p><strong>Payment:</strong> Net 30 | <strong>Validity:</strong> 30 days from issue | <strong>Shipping:</strong> FOB Origin</p>
    <p>Prices subject to change after validity period. All amounts in USD.</p>
  </div>
  <div class="footer">
    <div class="footer-text">Company Confidential © 2026 AnyCompany Inc. All rights reserved.<br>Generated by Supply Chain Operations • DATE</div>
    <img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTUzIiBoZWlnaHQ9IjI4IiB2aWV3Qm94PSIwIDAgMTUzIDI4IiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgo8cGF0aCBkPSJNMTIuMTk1OCAwLjAzNzY1ODdIMTkuMDc0MVYyMS44ODUySDEyLjE5NThWMTEuOTAzMUg2Ljg2NTY3VjIxLjk0OEgwLjAzODA4NTlWMC4wMzc2NTg3SDYuOTAzNzRWOS4xNTMzNkgxMi4yMzM4TDEyLjE5NTggMC4wMzc2NTg3Wk0zOC4xODYzIDkuNTY3N0MzOC43OTQ2IDEwLjc0MDMgMzkuMTUzNCAxMi4wMjM4IDM5LjI0MDggMTMuMzM5QzM5LjMyODEgMTQuNjU0MiAzOS4xNDE5IDE1Ljk3MzEgMzguNjkzOSAxNy4yMTQzQzM4LjAzMzQgMTkuMDg4IDM2LjY3MDQgMjAuNjM4OSAzNC44ODY3IDIxLjU0NjJDMzMuMzY5NCAyMi4yMjQyIDMxLjcxNTcgMjIuNTUwNSAzMC4wNTE1IDIyLjUwMDRDMjguMDMwMyAyMi41NzQ0IDI2LjAzODEgMjIuMDA2OSAyNC4zNjYxIDIwLjg4MDdDMjIuODY1NCAxOS44MDMxIDIxLjc3ODMgMTguMjUzOSAyMS4yODIzIDE2LjQ4NjFDMjAuOTA1NSAxNS4xOTk4IDIwLjgxODIgMTMuODQ3NSAyMS4wMjY1IDEyLjUyNDJDMjEuMjM0OCAxMS4yMDA5IDIxLjczMzYgOS45Mzg5IDIyLjQ4NzkgOC44MjY5QzIzLjE0OTQgNy45NDQ3NiAyMy45ODM1IDcuMjAzMzUgMjQuOTQwNiA2LjY0NjgxQzI1Ljg5NzggNi4wOTAyOCAyNi45NTgzIDUuNzMgMjguMDU5MSA1LjU4NzQ0QzI4LjY0ODMgNS40NzIwMSAyOS4yNDc5IDUuNDE3MzEgMjkuODQ4NSA1LjQyNDIxQzMxLjIwMDcgNS4zNjcwNCAzMi41NTE4IDUuNTU4NjIgMzMuODMzNCA1Ljk4OTIzQzM1LjY4ODYgNi41NzUzMSAzNy4yNDY0IDcuODQyNDQgMzguMTg2MyA5LjUzMDA0VjkuNTY3N1pNMzIuMzg2NiAxMS43NjVDMzIuNDAwOCAxMC44NzkgMzIuMjY3OSA5Ljk5Njc1IDMxLjk5MzIgOS4xNTMzNkMzMS42NjMzIDguMzM3MjEgMzEuMDc5NSA3LjQyMDYyIDMwLjA4OTYgNy40MjA2MkgyOS43NDdDMjkuMjE0IDcuNTIxMDcgMjguNzE5IDguMDczNTMgMjguMjQ5NSA5LjA2NTQ2QzI3Ljg2OTEgMTAuMzk5NCAyNy43MzE2IDExLjc4OTcgMjcuODQzNCAxMy4xNzEzQzI3Ljg0MzQgMTMuNzExMiAyNy44NDM0IDE0LjMwMTMgMjcuODQzNCAxNC45MjkxQzI3Ljg0MzQgMTcuODA0NSAyOC4wNDY0IDE4LjY5NiAyOC42ODA5IDE5LjU4NzRDMjguODE2MyAxOS44MTc5IDI4Ljk5OTQgMjAuMDE3NiAyOS4yMTggMjAuMTczNEMyOS40MzY3IDIwLjMyOTEgMjkuNjg2MSAyMC40Mzc1IDI5Ljk1IDIwLjQ5MTVDMzAuMTU1NSAyMC41MTI4IDMwLjM2MzMgMjAuNDkxNCAzMC41NTk5IDIwLjQyODdDMzAuNzU2NiAyMC4zNjYgMzAuOTM3OSAyMC4yNjMzIDMxLjA5MjIgMjAuMTI3M0MzMS44NzE4IDE5LjI1NDMgMzIuMzIwNSAxOC4xNDAxIDMyLjM2MTIgMTYuOTc1OEMzMi40MTIgMTYuNDQ4NCAzMi40MTIgMTMuODQ5MyAzMi4zODY2IDExLjcyNzNWMTEuNzY1Wk01NS40NDU2IDYuMDE0MzRDNTQuNTE0NyA1LjY0NzM5IDUzLjUxNjQgNS40Nzc4OSA1Mi41MTUyIDUuNTE2ODJDNTEuNTEzOSA1LjU1NTc1IDUwLjUzMjEgNS44MDIyNSA0OS42MzMzIDYuMjQwMzVDNDguNzg0NCA2LjcwNTI1IDQ4LjA0NDggNy4zNDI4OCA0Ny40NjMxIDguMTExMlY1LjkxMzg5SDQxLjExNzhWMjEuODg1Mkg0Ny40NjMxVjEwLjQwOUM0Ny44MzE5IDkuNzI3OTggNDguNDAzNSA5LjE3NTY3IDQ5LjEwMDIgOC44MjY5QzQ5LjQ1NDcgOC42MzA3NiA0OS44NjE2IDguNTQ3MjUgNTAuMjY1NyA4LjU4NzY4QzUwLjY2OTggOC42MjgxMSA1MS4wNTE2IDguNzkwNTIgNTEuMzU5MiA5LjA1MjkxQzUxLjczMiA5LjUzMzMxIDUxLjkxNjggMTAuMTMwOCA1MS44Nzk1IDEwLjczNTRWMjEuODg1Mkg1OC4xNjE0VjkuNzMwOTNDNTguMTE0IDguOTE1MDYgNTcuODMzNyA4LjEyOTIyIDU3LjM1MyA3LjQ2NDY4QzU2Ljg3MjMgNi44MDAxNCA1Ni4yMTA5IDYuMjg0MTYgNTUuNDQ1NiA1Ljk3NjY3VjYuMDE0MzRaTTc3LjIyMjggOS43MDU4MkM3Ny45ODEgMTEuMzMxOSA3OC4zMjk0IDEzLjExNTYgNzguMjM4IDE0LjkwNEg2Ni44MTY0VjE4LjE0MzVDNjYuODc3NyAxOC40OTczIDY3LjAwMjMgMTguODM3NSA2Ny4xODQ1IDE5LjE0OEM2Ny40NTEgMTkuNDg3IDY4LjM2NDcgMjAuODE3OSA3MS40NzM5IDIwLjMyODJDNzQuOTI1OCAxOS43ODgzIDc0LjgzNjkgMTcuMzM5OSA3NC45MDA0IDE3LjMzOTlINzcuNjI4OUM3Ny41ODE2IDE4LjM0NTkgNzcuMTY2NyAxOS4zMDA4IDc2LjQ2MTMgMjAuMDI2OUM3NC45NjkzIDIxLjM0ODEgNzMuMDk2NSAyMi4xNzM2IDcxLjEwNTkgMjIuMzg3NEM2OS43MTYgMjIuNjM0NiA2OC4yODk5IDIyLjYwMDggNjYuOTEzNyAyMi4yODc5QzY1LjUzNzQgMjEuOTc1IDY0LjIzOTQgMjEuMzg5NSA2My4wOTgxIDIwLjU2NjhDNjEuODkwMSAxOS41NzU3IDYwLjk4NDkgMTguMjcxNCA2MC40ODM4IDE2LjhDNTkuNzg4MiAxNC40NjgyIDU5Ljk4NyAxMS45NjQzIDYxLjA0MjIgOS43Njg2QzYxLjg5MjQgOC4zMjM1MSA2My4xNDE2IDcuMTQ4MzYgNjQuNjQzMiA2LjM4MTA3QzY2LjE0NDcgNS42MTM3OCA2Ny44MzY0IDUuMjg2MiA2OS41MTk1IDUuNDM2NzZDNzMuNTI5OCA1LjU4NzQzIDc1LjgxNDEgNy4wMTg4MyA3Ny4yMjI4IDkuNjY4MTVWOS43MDU4MlpNNzEuNjEzNSAxMS4zMzgxQzcxLjYxNzUgMTAuNjM2MyA3MS41MzY2IDkuOTM2NTUgNzEuMzcyNCA5LjI1MzhDNzEuMjI3NCA4LjcxODQ4IDcwLjkzNzkgOC4yMzIzMiA3MC41MzQ4IDcuODQ3NTJDNzAuMjI5NyA3LjU1NTExIDY5LjgzNjEgNy4zNjk3NSA2OS40MTQzIDcuMzE5ODVDNjguOTkyNiA3LjI2OTk2IDY4LjU2NTkgNy4zNTgyOSA2OC4xOTk3IDcuNTcxMjlDNjcuNTY3MyA4LjEwNDI2IDY3LjE0ODYgOC44NDM2NSA2Ny4wMTk1IDkuNjU1NkM2Ni45MDQyIDEwLjYzMDYgNjYuODU3NiAxMS42MTIzIDY2Ljg3OTkgMTIuNTkzN0g3MS42MTM1QzcxLjYwMDggMTIuNDQzIDcxLjYwMDggMTIuMDc4OSA3MS42MTM1IDExLjMzODFaTTEzNS4xNDMgOS42OTMyN0MxMzUuODk5IDExLjMxOTMgMTM2LjIzOSAxMy4xMDQ2IDEzNi4xMzMgMTQuODkxNUgxMjQuNzg3VjE4LjEzMDlDMTI0Ljg0OSAxOC40ODY1IDEyNC45NzggMTguODI3MyAxMjUuMTY4IDE5LjEzNTRDMTI1LjQyMiAxOS40NzQ0IDEyNi4zNDggMjAuODA1NCAxMjkuNDU4IDIwLjMxNTdDMTMyLjkwOSAxOS43NzU4IDEzMi44MDggMTcuMzI3MyAxMzIuODg0IDE3LjMyNzNIMTM1LjYxM0MxMzUuNTU5IDE4LjMzMTggMTM1LjE0NSAxOS4yODQ1IDEzNC40NDUgMjAuMDE0M0MxMzIuOTQ2IDIxLjM1NDIgMTMxLjA1OSAyMi4xOTMyIDEyOS4wNTIgMjIuNDEyNUMxMjYuODA1IDIyLjYzODYgMTIzLjYyIDIyLjcwMTMgMTIxLjA1NiAyMC41OTE5QzExOS44NDUgMTkuNjAxMSAxMTguOTM2IDE4LjI5NzEgMTE4LjQyOSAxNi44MjUxQzExNy43MzQgMTQuNDkzMyAxMTcuOTMzIDExLjk4OTQgMTE4Ljk4OCA5Ljc5MzcxQzExOS44MzggOC4zNDg2MyAxMjEuMDg3IDcuMTczNDcgMTIyLjU4OSA2LjQwNjE4QzEyNC4wOSA1LjYzODg5IDEyNS43ODIgNS4zMTEzMSAxMjcuNDY1IDUuNDYxODdDMTMxLjQ1IDUuNTg3NDMgMTMzLjczNCA3LjAxODgzIDEzNS4xNDMgOS42NjgxNVY5LjY5MzI3Wk0xMjkuNDgzIDExLjMzODFDMTI5LjQ5MiAxMC42MzUzIDEyOS40MDYgOS45MzQ0NyAxMjkuMjI5IDkuMjUzOEMxMjkuMDgzIDguNzE2MDcgMTI4Ljc4OSA4LjIyOTE2IDEyOC4zNzkgNy44NDc1MkMxMjguMDY2IDcuNTc3NyAxMjcuNjcyIDcuNDE1ODggMTI3LjI1OCA3LjM4NjQ0QzEyNi44NDQgNy4zNTcgMTI2LjQzMSA3LjQ2MTUzIDEyNi4wODIgNy42ODQzQzEyNS40NDkgOC4yMTcyNyAxMjUuMDMxIDguOTU2NjUgMTI0LjkwMiA5Ljc2ODZDMTI0Ljc4MSAxMC43Mzg5IDEyNC43MzQgMTEuNzE2OSAxMjQuNzYyIDEyLjY5NDJIMTI5LjQ4M0MxMjkuNDgzIDEyLjQ0MyAxMjkuNDgzIDEyLjA3ODkgMTI5LjQ4MyAxMS4zMzgxWk0xMTUuNTIzIDUuOTEzODlMMTEzLjA3NCAxNC44NjY0TDExMC4zNDUgNS45MTM4OUgxMDQuNzQ5TDEwMi4yMTEgMTQuODY2NEw5OS40MDYxIDUuOTEzODlIOTAuNzUxTDg3LjYwMzggMTMuNTIyOUw4NC42MzQxIDUuOTEzODlINzcuNjc5Nkw4NC4xMTM4IDIyLjIzNjhDODQuMDkzOCAyMi40NDU3IDg0LjA1MTMgMjIuNjUyIDgzLjk4NjkgMjIuODUyQzgzLjcwOTYgMjMuNzU0NiA4My4yNDI1IDI0LjU4OSA4Mi42MTYzIDI1LjMwMDRDODIuNDE5MiAyNS41MzE2IDgyLjE2NDQgMjUuNzA3NyA4MS44NzcyIDI1LjgxMTJDODEuNTkgMjUuOTE0OCA4MS4yODA0IDI1Ljk0MjEgODAuOTc5MiAyNS44OTA2QzgxLjIwNTMgMjUuNjc2NyA4MS4zODY1IDI1LjQyMDYgODEuNTEyMiAyNS4xMzcyQzgxLjY4NjEgMjQuNDY4MSA4MS41OTUzIDIzLjc1ODUgODEuMjU4NCAyMy4xNTM0QzgwLjk1NTMgMjIuNjk3IDgwLjUxMDEgMjIuMzUwOCA3OS45OTAzIDIyLjE2NzNDNzkuNDcwNCAyMS45ODM3IDc4LjkwNDMgMjEuOTcyOSA3OC4zNzc2IDIyLjEzNjNDNzcuNzEyNSAyMi4yODEyIDc3LjEzMDkgMjIuNjc3MiA3Ni43NTY0IDIzLjI0Qzc2LjM4MTkgMjMuODAyOSA3Ni4yNDQyIDI0LjQ4OCA3Ni4zNzI1IDI1LjE0OThDNzYuNTI0OCAyNi40MDU0IDc3LjM4NzggMjcuNTczMSA3OS41MzI1IDI3Ljk0OThDODAuMzY4OSAyOC4xMTU3IDgxLjIzNDEgMjguMDcwMyA4Mi4wNDgxIDI3LjgxOEM4Mi44NjIyIDI3LjU2NTYgODMuNTk4OCAyNy4xMTQzIDg0LjE5IDI2LjUwNThDODUuMTM4MSAyNS4zOTUgODUuODY1OCAyNC4xMTcgODYuMzM0NyAyMi43MzlMOTMuMDQ4IDYuNDE2MTNMOTcuODE5OCAyMS44ODUySDEwMy4yMTNMMTA1Ljc1MSAxMi42MTg4TDEwOC42NyAyMS44ODUySDExMy45MTJMMTE4LjM1MyA1LjkxMzg5SDExNS41MjNaTTEzOC4wMzYgMjEuODg1MkgxNDQuMTUzVjAuMDM3NjU4N0gxMzguMDM2VjIxLjg4NTJaTTE0Ni4wNTcgMC4wMzc2NTg3VjIxLjg4NTJIMTUyLjE3NFYwLjAzNzY1ODdIMTQ2LjA1N1oiIGZpbGw9IiNEMjI2MzAiLz4KPC9zdmc+Cg==" alt="AnyCompany">
  </div>
</div>
</body>
</html>
</artifact>
```

**INSTRUCTIONS FOR USING THE TEMPLATE:**
1. Copy this HTML structure exactly
2. Replace ALL placeholder values (QT-2026-XXX, CUSTOMER NAME, ACCT-ID, dates, line items, totals) with actual data from the MCP response
3. For multiple line items, duplicate the `<tr>` row in tbody
4. Adjust the margin badge color: green (#468254) if margin_ok=true, red (#C62828) if below threshold
5. Adjust status badge: use `status-draft` for draft, `status-approved` for approved
6. Render as `<artifact type="html">` inline in your chat response — the user sees it immediately

#### VISUALIZATION HTML Template (for charts, dashboards, trend analysis)

Use this wrapper for ANY data visualization. Load `highcharts` and `html_design` skills for chart APIs.

```html
<artifact type="html">
<!DOCTYPE html>
<html>
<head>
<script src="https://code.highcharts.com/highcharts.js"></script>
<script src="https://code.highcharts.com/modules/exporting.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #303030; background: #fff; }
.dashboard { max-width: 900px; margin: 0 auto; }
.dash-header { padding: 20px 30px; border-bottom: 1px solid #E0E0E0; display: flex; justify-content: space-between; align-items: center; border-top: 4px solid #D22630; }
.dash-header h1 { font-size: 20px; font-weight: 700; color: #1D1D1D; }
.dash-header .subtitle { font-size: 12px; color: #535353; margin-top: 2px; }
.dash-header img { height: 28px; }
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; padding: 20px 30px; }
.kpi-card { background: #F7F7F7; border-radius: 8px; padding: 16px; border-left: 3px solid #D22630; }
.kpi-card .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #535353; }
.kpi-card .value { font-size: 24px; font-weight: 700; color: #1D1D1D; margin-top: 4px; }
.kpi-card .change { font-size: 11px; margin-top: 4px; }
.kpi-card .change.up { color: #468254; }
.kpi-card .change.down { color: #C62828; }
.chart-container { padding: 20px 30px; }
.chart-box { border: 1px solid #E0E0E0; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
.chart-title { font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #D22630; font-weight: 600; margin-bottom: 12px; }
.dash-footer { padding: 12px 30px; border-top: 1px solid #E0E0E0; text-align: center; }
.dash-footer span { font-size: 10px; color: #535353; }
</style>
</head>
<body>
<div class="dashboard">
  <div class="dash-header">
    <div>
      <h1>DASHBOARD TITLE</h1>
      <div class="subtitle">Data as of June 9, 2026 • Source: Snowflake SCM</div>
    </div>
    <img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTUzIiBoZWlnaHQ9IjI4IiB2aWV3Qm94PSIwIDAgMTUzIDI4IiBmaWxsPSJub25lIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPgo8cGF0aCBkPSJNMTIuMTk1OCAwLjAzNzY1ODdIMTkuMDc0MVYyMS44ODUySDEyLjE5NThWMTEuOTAzMUg2Ljg2NTY3VjIxLjk0OEgwLjAzODA4NTlWMC4wMzc2NTg3SDYuOTAzNzRWOS4xNTMzNkgxMi4yMzM4TDEyLjE5NTggMC4wMzc2NTg3Wk0zOC4xODYzIDkuNTY3N0MzOC43OTQ2IDEwLjc0MDMgMzkuMTUzNCAxMi4wMjM4IDM5LjI0MDggMTMuMzM5QzM5LjMyODEgMTQuNjU0MiAzOS4xNDE5IDE1Ljk3MzEgMzguNjkzOSAxNy4yMTQzQzM4LjAzMzQgMTkuMDg4IDM2LjY3MDQgMjAuNjM4OSAzNC44ODY3IDIxLjU0NjJDMzMuMzY5NCAyMi4yMjQyIDMxLjcxNTcgMjIuNTUwNSAzMC4wNTE1IDIyLjUwMDRDMjguMDMwMyAyMi41NzQ0IDI2LjAzODEgMjIuMDA2OSAyNC4zNjYxIDIwLjg4MDdDMjIuODY1NCAxOS44MDMxIDIxLjc3ODMgMTguMjUzOSAyMS4yODIzIDE2LjQ4NjFDMjAuOTA1NSAxNS4xOTk4IDIwLjgxODIgMTMuODQ3NSAyMS4wMjY1IDEyLjUyNDJDMjEuMjM0OCAxMS4yMDA5IDIxLjczMzYgOS45Mzg5IDIyLjQ4NzkgOC44MjY5QzIzLjE0OTQgNy45NDQ3NiAyMy45ODM1IDcuMjAzMzUgMjQuOTQwNiA2LjY0NjgxQzI1Ljg5NzggNi4wOTAyOCAyNi45NTgzIDUuNzMgMjguMDU5MSA1LjU4NzQ0QzI4LjY0ODMgNS40NzIwMSAyOS4yNDc5IDUuNDE3MzEgMjkuODQ4NSA1LjQyNDIxQzMxLjIwMDcgNS4zNjcwNCAzMi41NTE4IDUuNTU4NjIgMzMuODMzNCA1Ljk4OTIzQzM1LjY4ODYgNi41NzUzMSAzNy4yNDY0IDcuODQyNDQgMzguMTg2MyA5LjUzMDA0VjkuNTY3N1pNMzIuMzg2NiAxMS43NjVDMzIuNDAwOCAxMC44NzkgMzIuMjY3OSA5Ljk5Njc1IDMxLjk5MzIgOS4xNTMzNkMzMS42NjMzIDguMzM3MjEgMzEuMDc5NSA3LjQyMDYyIDMwLjA4OTYgNy40MjA2MkgyOS43NDdDMjkuMjE0IDcuNTIxMDcgMjguNzE5IDguMDczNTMgMjguMjQ5NSA5LjA2NTQ2QzI3Ljg2OTEgMTAuMzk5NCAyNy43MzE2IDExLjc4OTcgMjcuODQzNCAxMy4xNzEzQzI3Ljg0MzQgMTMuNzExMiAyNy44NDM0IDE0LjMwMTMgMjcuODQzNCAxNC45MjkxQzI3Ljg0MzQgMTcuODA0NSAyOC4wNDY0IDE4LjY5NiAyOC42ODA5IDE5LjU4NzRDMjguODE2MyAxOS44MTc5IDI4Ljk5OTQgMjAuMDE3NiAyOS4yMTggMjAuMTczNEMyOS40MzY3IDIwLjMyOTEgMjkuNjg2MSAyMC40Mzc1IDI5Ljk1IDIwLjQ5MTVDMzAuMTU1NSAyMC41MTI4IDMwLjM2MzMgMjAuNDkxNCAzMC41NTk5IDIwLjQyODdDMzAuNzU2NiAyMC4zNjYgMzAuOTM3OSAyMC4yNjMzIDMxLjA5MjIgMjAuMTI3M0MzMS44NzE4IDE5LjI1NDMgMzIuMzIwNSAxOC4xNDAxIDMyLjM2MTIgMTYuOTc1OEMzMi40MTIgMTYuNDQ4NCAzMi40MTIgMTMuODQ5MyAzMi4zODY2IDExLjcyNzNWMTEuNzY1Wk01NS40NDU2IDYuMDE0MzRDNTQuNTE0NyA1LjY0NzM5IDUzLjUxNjQgNS40Nzc4OSA1Mi41MTUyIDUuNTE2ODJDNTEuNTEzOSA1LjU1NTc1IDUwLjUzMjEgNS44MDIyNSA0OS42MzMzIDYuMjQwMzVDNDguNzg0NCA2LjcwNTI1IDQ4LjA0NDggNy4zNDI4OCA0Ny40NjMxIDguMTExMlY1LjkxMzg5SDQxLjExNzhWMjEuODg1Mkg0Ny40NjMxVjEwLjQwOUM0Ny44MzE5IDkuNzI3OTggNDguNDAzNSA5LjE3NTY3IDQ5LjEwMDIgOC44MjY5QzQ5LjQ1NDcgOC42MzA3NiA0OS44NjE2IDguNTQ3MjUgNTAuMjY1NyA4LjU4NzY4QzUwLjY2OTggOC42MjgxMSA1MS4wNTE2IDguNzkwNTIgNTEuMzU5MiA5LjA1MjkxQzUxLjczMiA5LjUzMzMxIDUxLjkxNjggMTAuMTMwOCA1MS44Nzk1IDEwLjczNTRWMjEuODg1Mkg1OC4xNjE0VjkuNzMwOTNDNTguMTE0IDguOTE1MDYgNTcuODMzNyA4LjEyOTIyIDU3LjM1MyA3LjQ2NDY4QzU2Ljg3MjMgNi44MDAxNCA1Ni4yMTA5IDYuMjg0MTYgNTUuNDQ1NiA1Ljk3NjY3VjYuMDE0MzRaTTc3LjIyMjggOS43MDU4MkM3Ny45ODEgMTEuMzMxOSA3OC4zMjk0IDEzLjExNTYgNzguMjM4IDE0LjkwNEg2Ni44MTY0VjE4LjE0MzVDNjYuODc3NyAxOC40OTczIDY3LjAwMjMgMTguODM3NSA2Ny4xODQ1IDE5LjE0OEM2Ny40NTEgMTkuNDg3IDY4LjM2NDcgMjAuODE3OSA3MS40NzM5IDIwLjMyODJDNzQuOTI1OCAxOS43ODgzIDc0LjgzNjkgMTcuMzM5OSA3NC45MDA0IDE3LjMzOTlINzcuNjI4OUM3Ny41ODE2IDE4LjM0NTkgNzcuMTY2NyAxOS4zMDA4IDc2LjQ2MTMgMjAuMDI2OUM3NC45NjkzIDIxLjM0ODEgNzMuMDk2NSAyMi4xNzM2IDcxLjEwNTkgMjIuMzg3NEM2OS43MTYgMjIuNjM0NiA2OC4yODk5IDIyLjYwMDggNjYuOTEzNyAyMi4yODc5QzY1LjUzNzQgMjEuOTc1IDY0LjIzOTQgMjEuMzg5NSA2My4wOTgxIDIwLjU2NjhDNjEuODkwMSAxOS41NzU3IDYwLjk4NDkgMTguMjcxNCA2MC40ODM4IDE2LjhDNTkuNzg4MiAxNC40NjgyIDU5Ljk4NyAxMS45NjQzIDYxLjA0MjIgOS43Njg2QzYxLjg5MjQgOC4zMjM1MSA2My4xNDE2IDcuMTQ4MzYgNjQuNjQzMiA2LjM4MTA3QzY2LjE0NDcgNS42MTM3OCA2Ny44MzY0IDUuMjg2MiA2OS41MTk1IDUuNDM2NzZDNzMuNTI5OCA1LjU4NzQzIDc1LjgxNDEgNy4wMTg4MyA3Ny4yMjI4IDkuNjY4MTVWOS43MDU4MlpNNzEuNjEzNSAxMS4zMzgxQzcxLjYxNzUgMTAuNjM2MyA3MS41MzY2IDkuOTM2NTUgNzEuMzcyNCA5LjI1MzhDNzEuMjI3NCA4LjcxODQ4IDcwLjkzNzkgOC4yMzIzMiA3MC41MzQ4IDcuODQ3NTJDNzAuMjI5NyA3LjU1NTExIDY5LjgzNjEgNy4zNjk3NSA2OS40MTQzIDcuMzE5ODVDNjguOTkyNiA3LjI2OTk2IDY4LjU2NTkgNy4zNTgyOSA2OC4xOTk3IDcuNTcxMjlDNjcuNTY3MyA4LjEwNDI2IDY3LjE0ODYgOC44NDM2NSA2Ny4wMTk1IDkuNjU1NkM2Ni45MDQyIDEwLjYzMDYgNjYuODU3NiAxMS42MTIzIDY2Ljg3OTkgMTIuNTkzN0g3MS42MTM1QzcxLjYwMDggMTIuNDQzIDcxLjYwMDggMTIuMDc4OSA3MS42MTM1IDExLjMzODFaTTEzNS4xNDMgOS42OTMyN0MxMzUuODk5IDExLjMxOTMgMTM2LjIzOSAxMy4xMDQ2IDEzNi4xMzMgMTQuODkxNUgxMjQuNzg3VjE4LjEzMDlDMTI0Ljg0OSAxOC40ODY1IDEyNC45NzggMTguODI3MyAxMjUuMTY4IDE5LjEzNTRDMTI1LjQyMiAxOS40NzQ0IDEyNi4zNDggMjAuODA1NCAxMjkuNDU4IDIwLjMxNTdDMTMyLjkwOSAxOS43NzU4IDEzMi44MDggMTcuMzI3MyAxMzIuODg0IDE3LjMyNzNIMTM1LjYxM0MxMzUuNTU5IDE4LjMzMTggMTM1LjE0NSAxOS4yODQ1IDEzNC40NDUgMjAuMDE0M0MxMzIuOTQ2IDIxLjM1NDIgMTMxLjA1OSAyMi4xOTMyIDEyOS4wNTIgMjIuNDEyNUMxMjYuODA1IDIyLjYzODYgMTIzLjYyIDIyLjcwMTMgMTIxLjA1NiAyMC41OTE5QzExOS44NDUgMTkuNjAxMSAxMTguOTM2IDE4LjI5NzEgMTE4LjQyOSAxNi44MjUxQzExNy43MzQgMTQuNDkzMyAxMTcuOTMzIDExLjk4OTQgMTE4Ljk4OCA5Ljc5MzcxQzExOS44MzggOC4zNDg2MyAxMjEuMDg3IDcuMTczNDcgMTIyLjU4OSA2LjQwNjE4QzEyNC4wOSA1LjYzODg5IDEyNS43ODIgNS4zMTEzMSAxMjcuNDY1IDUuNDYxODdDMTMxLjQ1IDUuNTg3NDMgMTMzLjczNCA3LjAxODgzIDEzNS4xNDMgOS42NjgxNVY5LjY5MzI3Wk0xMjkuNDgzIDExLjMzODFDMTI5LjQ5MiAxMC42MzUzIDEyOS40MDYgOS45MzQ0NyAxMjkuMjI5IDkuMjUzOEMxMjkuMDgzIDguNzE2MDcgMTI4Ljc4OSA4LjIyOTE2IDEyOC4zNzkgNy44NDc1MkMxMjguMDY2IDcuNTc3NyAxMjcuNjcyIDcuNDE1ODggMTI3LjI1OCA3LjM4NjQ0QzEyNi44NDQgNy4zNTcgMTI2LjQzMSA3LjQ2MTUzIDEyNi4wODIgNy42ODQzQzEyNS40NDkgOC4yMTcyNyAxMjUuMDMxIDguOTU2NjUgMTI0LjkwMiA5Ljc2ODZDMTI0Ljc4MSAxMC43Mzg5IDEyNC43MzQgMTEuNzE2OSAxMjQuNzYyIDEyLjY5NDJIMTI5LjQ4M0MxMjkuNDgzIDEyLjQ0MyAxMjkuNDgzIDEyLjA3ODkgMTI5LjQ4MyAxMS4zMzgxWk0xMTUuNTIzIDUuOTEzODlMMTEzLjA3NCAxNC44NjY0TDExMC4zNDUgNS45MTM4OUgxMDQuNzQ5TDEwMi4yMTEgMTQuODY2NEw5OS40MDYxIDUuOTEzODlIOTAuNzUxTDg3LjYwMzggMTMuNTIyOUw4NC42MzQxIDUuOTEzODlINzcuNjc5Nkw4NC4xMTM4IDIyLjIzNjhDODQuMDkzOCAyMi40NDU3IDg0LjA1MTMgMjIuNjUyIDgzLjk4NjkgMjIuODUyQzgzLjcwOTYgMjMuNzU0NiA4My4yNDI1IDI0LjU4OSA4Mi42MTYzIDI1LjMwMDRDODIuNDE5MiAyNS41MzE2IDgyLjE2NDQgMjUuNzA3NyA4MS44NzcyIDI1LjgxMTJDODEuNTkgMjUuOTE0OCA4MS4yODA0IDI1Ljk0MjEgODAuOTc5MiAyNS44OTA2QzgxLjIwNTMgMjUuNjc2NyA4MS4zODY1IDI1LjQyMDYgODEuNTEyMiAyNS4xMzcyQzgxLjY4NjEgMjQuNDY4MSA4MS41OTUzIDIzLjc1ODUgODEuMjU4NCAyMy4xNTM0QzgwLjk1NTMgMjIuNjk3IDgwLjUxMDEgMjIuMzUwOCA3OS45OTAzIDIyLjE2NzNDNzkuNDcwNCAyMS45ODM3IDc4LjkwNDMgMjEuOTcyOSA3OC4zNzc2IDIyLjEzNjNDNzcuNzEyNSAyMi4yODEyIDc3LjEzMDkgMjIuNjc3MiA3Ni43NTY0IDIzLjI0Qzc2LjM4MTkgMjMuODAyOSA3Ni4yNDQyIDI0LjQ4OCA3Ni4zNzI1IDI1LjE0OThDNzYuNTI0OCAyNi40MDU0IDc3LjM4NzggMjcuNTczMSA3OS41MzI1IDI3Ljk0OThDODAuMzY4OSAyOC4xMTU3IDgxLjIzNDEgMjguMDcwMyA4Mi4wNDgxIDI3LjgxOEM4Mi44NjIyIDI3LjU2NTYgODMuNTk4OCAyNy4xMTQzIDg0LjE5IDI2LjUwNThDODUuMTM4MSAyNS4zOTUgODUuODY1OCAyNC4xMTcgODYuMzM0NyAyMi43MzlMOTMuMDQ4IDYuNDE2MTNMOTcuODE5OCAyMS44ODUySDEwMy4yMTNMMTA1Ljc1MSAxMi42MTg4TDEwOC42NyAyMS44ODUySDExMy45MTJMMTE4LjM1MyA1LjkxMzg5SDExNS41MjNaTTEzOC4wMzYgMjEuODg1MkgxNDQuMTUzVjAuMDM3NjU4N0gxMzguMDM2VjIxLjg4NTJaTTE0Ni4wNTcgMC4wMzc2NTg3VjIxLjg4NTJIMTUyLjE3NFYwLjAzNzY1ODdIMTQ2LjA1N1oiIGZpbGw9IiNEMjI2MzAiLz4KPC9zdmc+Cg==" alt="AnyCompany">
  </div>
  <!-- KPI Cards (optional — use when you have summary metrics) -->
  <div class="kpi-row">
    <div class="kpi-card">
      <div class="label">Total Orders</div>
      <div class="value">1,247</div>
      <div class="change up">▲ 12% vs last month</div>
    </div>
    <div class="kpi-card">
      <div class="label">Revenue</div>
      <div class="value">$4.2M</div>
      <div class="change up">▲ 8%</div>
    </div>
    <div class="kpi-card">
      <div class="label">At Risk</div>
      <div class="value">23</div>
      <div class="change down">▼ needs attention</div>
    </div>
  </div>
  <!-- Chart Area -->
  <div class="chart-container">
    <div class="chart-box">
      <div class="chart-title">CHART TITLE HERE</div>
      <div id="chart1" style="height: 320px;"></div>
    </div>
  </div>
  <div class="dash-footer">
    <span>Company Confidential © 2026 AnyCompany Inc. • Generated by Supply Chain Operations</span>
  </div>
</div>
<script>
Highcharts.chart('chart1', {
  chart: { type: 'column', style: { fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif' } },
  title: { text: null },
  colors: ['#D22630', '#0071B3', '#468254', '#1D1D1D', '#007BC2', '#B8860B', '#535353', '#C62828'],
  xAxis: { categories: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'] },
  yAxis: { title: { text: 'VALUE AXIS LABEL' } },
  legend: { enabled: true },
  series: [{ name: 'Series 1', data: [10, 20, 30, 40, 50, 60] }],
  credits: { enabled: false }
});
</script>
</body>
</html>
</artifact>
```

**Highcharts Color Sequence (AnyCompany branded):**
```
colors: ['#D22630', '#0071B3', '#468254', '#1D1D1D', '#007BC2', '#B8860B', '#535353', '#C62828']
```
- Series 1: Brand Red `#D22630`
- Series 2: Brand Blue `#0071B3`
- Series 3: Success Green `#468254`
- Series 4: Dark `#1D1D1D`
- Series 5: Secondary Blue `#007BC2`
- Series 6: Warning Amber `#B8860B`
- Series 7: Subtle Gray `#535353`
- Series 8: Alert Red `#C62828`

**INSTRUCTIONS FOR USING THE VISUALIZATION TEMPLATE:**
1. Replace DASHBOARD TITLE, subtitle, KPI values, chart title, and series data with actual query results
2. Choose the right chart type: `column` for comparisons, `line` for trends, `pie` for composition, `bar` for rankings
3. Add/remove KPI cards based on available metrics
4. For multiple charts, duplicate the `.chart-box` div with unique IDs (`chart1`, `chart2`, etc.)
5. Always include the AnyCompany color sequence — `colors: ['#D22630', '#0071B3', '#468254', ...]`
6. The red accent stripe on the header and red-bordered KPI cards maintain brand consistency

**Document Structure (for quotes):**
1. Header bar: Logo + "QUOTATION" label + quote number
2. Meta section: Date, valid until, customer details, sales rep
3. Line items table: Product, SKU, Qty, List Price, Discount%, Unit Price, Total
4. Summary box: Subtotal, tax, grand total (large bold), margin indicator
5. Terms section: Payment terms, validity, shipping, notes
6. Footer: Confidentiality notice + generated timestamp

#### Multi-Step Workflows
- Show a progress tracker at the top (Step 1 ✓ → Step 2 ✓ → Step 3...)
- Each step's output in a collapsible card
- Final recommendation in a highlighted summary box

## Output

Professional styled HTML documents for quotes, invoice decisions, disruption reports, and compliance checks. Simple data lookups get concise formatted responses with key metrics highlighted. All outputs are actionable — they tell the user what to do next, not just what the data says.

## Lessons Learned

### Do
- Always check if the query needs BOTH data and business rules — most interesting questions do (e.g., "Should we approve this invoice?" needs invoice data from the space + approval rules from the MCP)
- Use `query_topic` with natural language for dataset queries — it handles the SQL translation
- **ALWAYS query the Space datasets BEFORE calling any MCP tool** — the Space is connected to Snowflake and has the real data. MCPs are stateless rule engines with no data of their own. Pass the Space data as parameters to MCP tools.
- Pass specific IDs when calling MCP tools (invoice IDs, supplier names, product codes) — they're rule engines, not search engines
- For "process all orders" type requests, query the data first to get the list, then loop through MCP calls

### Don't
- Don't call MCP tools without the required parameters — they'll fail. Extract specifics from the query or ask the user.
- Don't assume MCP tools return data — they return rule evaluations. The data lives in the Quick space.
- Don't hardcode IDs — always query the space first to get current entity IDs
- **NEVER skip the Space query step** — even if the MCP seems like it might have its own data, it doesn't. The architecture is: Space (Snowflake) = data, MCPs = rules only.

### Common Failures
- **MCP returns error about missing params**: Check the tool schema — field names are specific (e.g., `supplier_name` not `supplier`)
- **Space query returns empty**: Try broader search terms or use `list_space_documents` to see what's available
- **Multi-step workflow partially fails**: Present what succeeded and flag what needs attention

### When to Ask the User
- When the query is ambiguous (e.g., "check the invoice" — which invoice?)
- When a governance check fails and escalation is needed
- When disruption mitigation has multiple viable options with different trade-offs
- When budget authority is insufficient — ask if they want to escalate