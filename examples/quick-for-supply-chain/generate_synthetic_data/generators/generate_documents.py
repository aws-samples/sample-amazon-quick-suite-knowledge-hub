#!/usr/bin/env python3
"""
Generate Sample Transactional Documents (template-driven, synthetic)
═══════════════════════════════════════════════════════════════════
Reads the generated synthetic CSVs and fills HIGH-LEVEL TEMPLATES with
placeholders — no company names, prices, or IDs are hardcoded. Every value
comes from the synthetic data so documents stay consistent with the CSVs.

Demo scenarios encoded by the 4 documents:
    1. Invoice that EXACTLY matches its PO            → AUTO_APPROVE
    2. Invoice with a ~6% price variance vs its PO    → DIRECTOR/MANAGER REVIEW
    3. Supplier contract with volume tiers + late penalty
    4. Supplier contract with a 2/10 early-payment discount

Selection is deterministic (driven by the seeded CSVs):
    * Invoices come from the INVOICES + PURCHASE_ORDERS + PO_LINE_ITEMS tables.
      The auto-approve invoice is the one whose APPROVAL_ACTION == AUTO_APPROVE
      and whose amount matches its PO; the variance invoice is the one whose
      TOTAL_AMOUNT differs from its PO TOTAL_COST.
    * Contracts come from SUPPLIER_CONTRACTS (first two). The volume-tier
      contract is the one with the largest VOLUME_COMMITMENT; the early-payment
      contract is the one with the shortest payment terms (Net 15).

Usage:
    python generate_synthetic_data/generators/generate_documents.py \
        --data-dir ./synthetic_data/ --output ./synthetic_data/documents/

Also writes GENERATION_GUIDE.md — a tool-agnostic spec so a user can
regenerate the documents with any agentic tool.

Requirements (optional):
    pip install reportlab      # for PDF output; TXT is always produced
"""

import argparse
import csv
import os

# ═══════════════════════════════════════════════════════════════
# CSV loading helpers
# ═══════════════════════════════════════════════════════════════


def _tables_dir(data_dir):
    return os.path.join(data_dir, "structured", "tables")


def load_table(data_dir, table):
    """Load a table CSV as a list of dict rows. Returns [] if missing."""
    path = os.path.join(_tables_dir(data_dir), f"{table}.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def index_by(rows, key):
    return {r[key]: r for r in rows}


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════
# Templates (placeholders only — filled from synthetic CSVs)
# ═══════════════════════════════════════════════════════════════

INVOICE_TEMPLATE = """════════════════════════════════════════════════════════════
{supplier_name}
{supplier_location}
Contact: {supplier_email}
════════════════════════════════════════════════════════════

COMMERCIAL INVOICE

Invoice Number: {invoice_number}          Date: {invoice_date}
PO Reference:   {po_id}                    Terms: {payment_terms}
Bill To:        [Our Company]
Due Date:       {due_date}

────────────────────────────────────────────────────────────
SKU        Description                     Qty    Unit Price   Amount
────────────────────────────────────────────────────────────
{sku:<10} {description:<30} {qty:>5}  ${unit_price:>10}  ${line_amount:>12}
────────────────────────────────────────────────────────────
                                          Subtotal:   ${subtotal:>12}
                                          Tax:         ${tax:>12}
                                          TOTAL DUE:  ${total:>12}
════════════════════════════════════════════════════════════

{variance_note}
"""

CONTRACT_TEMPLATE = """════════════════════════════════════════════════════════════
MASTER SUPPLY AGREEMENT
Contract ID: {contract_id}
════════════════════════════════════════════════════════════

Effective: {effective_date} — {expiry_date}
Supplier:  {supplier_name} ({supplier_id}), {supplier_location}
Buyer:     [Our Company]

ARTICLE 1 — PRODUCTS
Categories: {categories}

ARTICLE 2 — PRICING
Base Discount: {discount}% off list price
{pricing_block}
Annual Volume Commitment: {volume:,} units minimum

ARTICLE 3 — DELIVERY & PENALTIES
Late Delivery Penalty: {penalty}% of PO value per day

ARTICLE 4 — QUALITY & WARRANTY
Certifications: {certifications}
Warranty: {warranty} months from delivery

ARTICLE 5 — PAYMENT
Terms: {payment_terms}
Currency: USD
{payment_block}

ARTICLE 6 — TERMINATION
Convenience: 60 days written notice
Cause: 30 days cure period
════════════════════════════════════════════════════════════
"""


# ═══════════════════════════════════════════════════════════════
# Entity selection from synthetic CSVs
# ═══════════════════════════════════════════════════════════════


def select_entities(data_dir):
    """Pick the specific rows the 4 documents encode, from the synthetic CSVs."""
    invoices = load_table(data_dir, "INVOICES")
    pos = index_by(load_table(data_dir, "PURCHASE_ORDERS"), "PO_NUMBER")
    po_lines = load_table(data_dir, "PO_LINE_ITEMS")
    products = index_by(load_table(data_dir, "PRODUCTS"), "SKU")
    suppliers = index_by(load_table(data_dir, "SUPPLIERS"), "SUPPLIER_ID")
    contracts = load_table(data_dir, "SUPPLIER_CONTRACTS")

    po_line_by_po = {}
    for line in po_lines:
        po_line_by_po.setdefault(line["PO_NUMBER"], line)

    # Invoice #1: matches PO exactly (AUTO_APPROVE).
    match_inv = next(
        (
            i
            for i in invoices
            if i["APPROVAL_ACTION"] == "AUTO_APPROVE"
            and i["PO_NUMBER"] in pos
            and abs(_f(i["TOTAL_AMOUNT"]) - _f(pos[i["PO_NUMBER"]]["TOTAL_COST"]))
            < 0.01
        ),
        None,
    )
    # Invoice #2: ~6% variance vs PO (review).
    var_inv = next(
        (
            i
            for i in invoices
            if i["PO_NUMBER"] in pos
            and abs(_f(i["TOTAL_AMOUNT"]) - _f(pos[i["PO_NUMBER"]]["TOTAL_COST"]))
            >= 0.01
        ),
        None,
    )

    # Contracts: volume-tier one = largest VOLUME_COMMITMENT; early-payment one
    # = shortest payment terms (Net 15) among the first two.
    contracts_sorted = contracts[:]
    volume_contract = (
        max(contracts_sorted, key=lambda c: int(c["VOLUME_COMMITMENT"]))
        if contracts
        else None
    )
    early_pay = next((c for c in contracts if c["PAYMENT_TERMS"] == "Net 15"), None)
    if early_pay is None and len(contracts) >= 2:
        early_pay = contracts[1]

    return {
        "match_invoice": match_inv,
        "variance_invoice": var_inv,
        "volume_contract": volume_contract,
        "early_pay_contract": early_pay,
        "pos": pos,
        "po_line_by_po": po_line_by_po,
        "products": products,
        "suppliers": suppliers,
    }


# ═══════════════════════════════════════════════════════════════
# Fill templates
# ═══════════════════════════════════════════════════════════════


def render_invoice(inv, ctx, *, variance):
    po = ctx["pos"].get(inv["PO_NUMBER"], {})
    line = ctx["po_line_by_po"].get(inv["PO_NUMBER"], {})
    sku = line.get("SKU", "")
    prod = ctx["products"].get(sku, {})
    supplier = ctx["suppliers"].get(inv["SUPPLIER_ID"], {})

    qty = line.get("QUANTITY", "")
    unit_price = _f(line.get("UNIT_COST", 0))
    total = _f(inv["TOTAL_AMOUNT"])
    tax = _f(inv["TAX_AMOUNT"])
    subtotal = round(total - tax, 2)

    if variance:
        po_total = _f(po.get("TOTAL_COST", 0))
        pct = round((total - po_total) / po_total * 100, 1) if po_total else 0.0
        note = (
            f"⚠️  VARIANCE NOTE: PO {inv['PO_NUMBER']} total was "
            f"${po_total:,.2f}. Invoice total ${total:,.2f} = {pct}% increase.\n"
            f"    Exceeds tolerance → requires DIRECTOR/MANAGER REVIEW."
        )
    else:
        note = f"This invoice matches PO {inv['PO_NUMBER']} exactly. No variance → AUTO_APPROVE."

    return INVOICE_TEMPLATE.format(
        supplier_name=supplier.get("NAME", inv["SUPPLIER_ID"]),
        supplier_location=supplier.get("LOCATION", ""),
        supplier_email=supplier.get("CONTACT_EMAIL", ""),
        invoice_number=inv["INVOICE_NUMBER"],
        invoice_date=inv["INVOICE_DATE"],
        po_id=inv["PO_NUMBER"],
        payment_terms=inv["PAYMENT_TERMS"],
        due_date=inv["DUE_DATE"],
        sku=sku,
        description=prod.get("NAME", ""),
        qty=qty,
        unit_price=f"{unit_price:,.2f}",
        line_amount=f"{_f(line.get('LINE_TOTAL', 0)):,.2f}",
        subtotal=f"{subtotal:,.2f}",
        tax=f"{tax:,.2f}",
        total=f"{total:,.2f}",
        variance_note=note,
    )


def render_contract(contract, ctx, *, early_payment):
    supplier = ctx["suppliers"].get(contract["SUPPLIER_ID"], {})
    discount = _f(contract["DISCOUNT_PCT"])

    if early_payment:
        pricing_block = (
            f"Volume Tiers:\n"
            f"  Standard order:  {discount:.0f}% discount\n"
            f"  Bulk order:      {discount + 4:.0f}% discount"
        )
        payment_block = (
            "*** EARLY PAYMENT DISCOUNT: 2/10 Net 15 ***\n"
            "  2% discount if paid within 10 days of invoice date.\n"
            "  (Annualized benefit ~36% — strongly recommended)"
        )
    else:
        pricing_block = (
            f"Volume Tiers:\n"
            f"  100+ units/order:   {discount:.0f}% discount\n"
            f"  500+ units/order:   {discount + 3:.0f}% discount\n"
            f"  1000+ units/order:  {discount + 6:.0f}% discount"
        )
        payment_block = "Late Payment Interest: 1.5% per month"

    return CONTRACT_TEMPLATE.format(
        contract_id=contract["CONTRACT_ID"],
        effective_date=contract["EFFECTIVE_DATE"],
        expiry_date=contract["EXPIRY_DATE"],
        supplier_name=supplier.get("NAME", contract["SUPPLIER_ID"]),
        supplier_id=contract["SUPPLIER_ID"],
        supplier_location=supplier.get("LOCATION", ""),
        categories=supplier.get("CATEGORIES", ""),
        discount=f"{discount:.0f}",
        pricing_block=pricing_block,
        volume=int(contract["VOLUME_COMMITMENT"]),
        penalty=f"{_f(contract['PENALTY_LATE_PCT']):.1f}",
        certifications=supplier.get("CERTIFICATIONS", ""),
        warranty=contract["WARRANTY_MONTHS"],
        payment_terms=contract["PAYMENT_TERMS"],
        payment_block=payment_block,
    )


# ═══════════════════════════════════════════════════════════════
# Output writers
# ═══════════════════════════════════════════════════════════════


def _write_txt(output_dir, name, text):
    path = os.path.join(output_dir, name + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _write_pdf(output_dir, name, text):
    """Write a simple text-in-PDF via reportlab, if available. Returns path or None."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        return None
    path = os.path.join(output_dir, name + ".pdf")
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    y = height - 40
    c.setFont("Courier", 8)
    for line in text.splitlines():
        if y < 40:
            c.showPage()
            c.setFont("Courier", 8)
            y = height - 40
        c.drawString(36, y, line[:110])
        y -= 11
    c.save()
    return path


def write_document(output_dir, name, text):
    txt_path = _write_txt(output_dir, name, text)
    print(f"  ✅ {txt_path}")
    pdf_path = _write_pdf(output_dir, name, text)
    if pdf_path:
        print(f"  ✅ {pdf_path}")


# ═══════════════════════════════════════════════════════════════
# GENERATION_GUIDE.md
# ═══════════════════════════════════════════════════════════════

GUIDE = """# Document Generation Guide (tool-agnostic)

These 4 demo documents are **template-driven** and must be filled entirely from
the synthetic structured CSVs under `synthetic_data/structured/tables/`. No
company names, prices, or IDs are hardcoded — every value is pulled from a CSV
row so the documents stay consistent with the datasets. Any agentic tool
(e.g. Kiro) can regenerate them by following this spec.

## Source tables
- `INVOICES.csv` — INVOICE_NUMBER, SUPPLIER_ID, PO_NUMBER, INVOICE_DATE,
  TOTAL_AMOUNT, TAX_AMOUNT, PAYMENT_TERMS, DUE_DATE, APPROVAL_ACTION
- `PURCHASE_ORDERS.csv` — PO_NUMBER, SUPPLIER_ID, TOTAL_COST
- `PO_LINE_ITEMS.csv` — PO_NUMBER, SKU, QUANTITY, UNIT_COST, LINE_TOTAL
- `PRODUCTS.csv` — SKU, NAME (used as line-item description)
- `SUPPLIERS.csv` — SUPPLIER_ID, NAME, LOCATION, CONTACT_EMAIL, CATEGORIES,
  CERTIFICATIONS
- `SUPPLIER_CONTRACTS.csv` — CONTRACT_ID, SUPPLIER_ID, EFFECTIVE_DATE,
  EXPIRY_DATE, DISCOUNT_PCT, VOLUME_COMMITMENT, PENALTY_LATE_PCT,
  WARRANTY_MONTHS, PAYMENT_TERMS

## The 4 documents

### 1. Invoice — exact PO match (AUTO_APPROVE)
- Select the INVOICES row where `APPROVAL_ACTION == AUTO_APPROVE` and
  `TOTAL_AMOUNT == PURCHASE_ORDERS.TOTAL_COST` for the same PO_NUMBER.
- Fields to pull: supplier from SUPPLIERS via SUPPLIER_ID; line item from
  PO_LINE_ITEMS via PO_NUMBER; product name from PRODUCTS via SKU.
- Scenario: totals match the PO exactly → the invoice-processing agent
  auto-approves it.

### 2. Invoice — ~6% price variance (DIRECTOR/MANAGER REVIEW)
- Select the INVOICES row whose `TOTAL_AMOUNT` differs from its PO
  `TOTAL_COST` (≈ 6% higher).
- Same field mapping as document #1, plus a variance note computed as
  `(TOTAL_AMOUNT - TOTAL_COST) / TOTAL_COST * 100`.
- Scenario: exceeds the price tolerance → routed to director/manager review.

### 3. Supplier contract — volume tiers + late penalty
- Select the SUPPLIER_CONTRACTS row with the largest `VOLUME_COMMITMENT`.
- Render volume-tier pricing derived from `DISCOUNT_PCT` and a late-delivery
  penalty from `PENALTY_LATE_PCT`.
- Scenario: the quoting/governance agents apply tiered discounts and penalty
  clauses.

### 4. Supplier contract — 2/10 early-payment discount
- Select the SUPPLIER_CONTRACTS row with `PAYMENT_TERMS == Net 15` (shortest
  terms).
- Render a `2/10 Net 15` early-payment discount block.
- Scenario: the invoice-processing agent recommends taking the early-payment
  discount.

## Output
- Write `.txt` for each document (always) and `.pdf` if `reportlab` is
  installed, into `synthetic_data/documents/`.
- Keep filenames stable and derived from the selected IDs, e.g.
  `Invoice_<INVOICE_NUMBER>.txt`, `Contract_<CONTRACT_ID>.txt`.
"""


def write_guide(output_dir):
    path = os.path.join(output_dir, "GENERATION_GUIDE.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(GUIDE)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Generate template-driven documents from synthetic CSVs"
    )
    parser.add_argument(
        "--data-dir",
        default="./synthetic_data/",
        help="Synthetic data root (contains structured/tables/)",
    )
    parser.add_argument(
        "--output",
        default="./synthetic_data/documents/",
        help="Output directory for documents",
    )
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print("Generating template-driven documents...")
    print(f"  Data dir: {args.data_dir}")
    print(f"  Output:   {args.output}")
    print()

    tables_dir = _tables_dir(args.data_dir)
    if not os.path.exists(os.path.join(tables_dir, "INVOICES.csv")):
        print(f"  ⚠️  Structured CSVs not found under {tables_dir}.")
        print("     Run generate_all_data.py first. Still writing GENERATION_GUIDE.md.")
        write_guide(args.output)
        return

    ctx = select_entities(args.data_dir)

    if ctx["match_invoice"]:
        inv = ctx["match_invoice"]
        write_document(
            args.output,
            f"Invoice_{inv['INVOICE_NUMBER']}",
            render_invoice(inv, ctx, variance=False),
        )
    if ctx["variance_invoice"]:
        inv = ctx["variance_invoice"]
        write_document(
            args.output,
            f"Invoice_{inv['INVOICE_NUMBER']}",
            render_invoice(inv, ctx, variance=True),
        )
    if ctx["volume_contract"]:
        c = ctx["volume_contract"]
        write_document(
            args.output,
            f"Contract_{c['CONTRACT_ID']}_volume",
            render_contract(c, ctx, early_payment=False),
        )
    if ctx["early_pay_contract"]:
        c = ctx["early_pay_contract"]
        write_document(
            args.output,
            f"Contract_{c['CONTRACT_ID']}_earlypay",
            render_contract(c, ctx, early_payment=True),
        )

    write_guide(args.output)

    print(f"\n✅ Documents generated in {args.output}")
    print("  Scenarios encoded:")
    print("   1. Matching invoice → AUTO_APPROVE")
    print("   2. ~6% variance invoice → DIRECTOR/MANAGER REVIEW")
    print("   3. Contract with volume tiers + late penalty")
    print("   4. Contract with 2/10 early-payment discount")


if __name__ == "__main__":
    main()
