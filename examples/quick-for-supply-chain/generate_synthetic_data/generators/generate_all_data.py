#!/usr/bin/env python3
"""
Supply Chain Demo — Master Data Generator (fully synthetic, deterministic)
═══════════════════════════════════════════════════════════════════════════
Generates ALL demo data from a single source of truth defined in Python:

1. Structured per-table CSVs   (synthetic_data/structured/tables/<TABLE>.csv)
2. Salesforce import CSVs       (synthetic_data/salesforce/{Accounts,Contacts,Opportunities}.csv)
3. Unstructured documents       (synthetic_data/documents/ — via generate_documents.py)

Design principles
-----------------
* FULLY SYNTHETIC — no real-looking company names, emails, addresses or phones.
  Values are obviously fictional: 'Sample Manufacturing Co 01',
  'contact01@example.com' (example.com/.org are reserved for docs), addresses
  like '100 Example Ave, Springfield, ST 00001', phones '555-01NN'
  (the 555-01xx range is reserved for fiction).
* DETERMINISTIC — seeded with ``random.seed(42)``; identical output every run.
* DEPENDENCY-FREE — uses only the Python standard library (no faker, no new deps).
* SINGLE SOURCE OF TRUTH — the schema lives in ``schema.py`` and every table is
  synthesized here. No SQL is parsed or copied; the generator emits CSVs only.

Usage:
    python generate_synthetic_data/generators/generate_all_data.py
    python generate_synthetic_data/generators/generate_all_data.py --output ./synthetic_data/
    python generate_synthetic_data/generators/generate_all_data.py --structured-only
    python generate_synthetic_data/generators/generate_all_data.py --salesforce-only
    python generate_synthetic_data/generators/generate_all_data.py --documents-only

All outputs share the same IDs, names, and amounts — guaranteed consistent.
"""
import os
import csv
import argparse
import random
import subprocess
import sys
from pathlib import Path

_run = subprocess.run

# Support both "python generate_all_data.py" and package-style imports.
try:
    from . import schema as schema_mod
except ImportError:  # pragma: no cover - direct-run fallback
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import schema as schema_mod

SCHEMA = schema_mod.SCHEMA

# One RNG, seeded once, so the whole run is reproducible.
RNG = random.Random(42)

# ═══════════════════════════════════════════════════════════════
# Synthetic value vocabularies (obviously fictional, doc-reserved)
# ═══════════════════════════════════════════════════════════════

INDUSTRIES = [
    "Manufacturing", "Technology", "Healthcare", "Retail", "Aerospace",
    "Energy", "Transportation", "Food & Beverage", "Construction", "Utilities",
]
TIERS = ["Enterprise", "Premium", "Standard"]
CREDIT_TERMS = ["Net 15", "Net 30", "Net 45", "Net 60"]
CITIES = [
    "Springfield", "Fairview", "Riverton", "Centerville", "Lakeside",
    "Georgetown", "Franklin", "Greenville", "Bristol", "Clinton",
]
# Fictional two-letter "states" (not real US postal codes) — clearly synthetic.
STATES = ["ST", "SX", "SY", "SZ", "SW", "SV", "SU", "ST", "SX", "SY"]
REGIONS = ["NA", "EMEA", "APAC", "LATAM"]

PRODUCT_CATEGORIES = [
    "Motors", "Bearings", "Pumps", "Valves", "Electronics", "Gears",
    "Seals", "Thermal", "Composites", "Sensors", "Actuators", "Conveyors",
    "Welding",
]

CERTIFICATIONS_POOL = [
    "ISO 9001", "ISO 14001", "AS9100", "IATF 16949", "API", "IECQ", "NADCAP",
]

CARRIERS = ["Carrier A", "Carrier B", "Carrier C"]
SERVICE_LEVELS = ["ground", "express", "freight", "overnight"]
ORDER_STATUSES = ["New", "Processing", "Shipped", "Delivered", "Backordered", "Cancelled"]
SHIPPING_METHODS = ["standard", "express", "overnight"]
PRIORITIES = ["low", "normal", "high", "critical"]

# ── Counts (kept close to the original demo footprint) ──────────
N_WAREHOUSES = 3
N_PRODUCTS = 15
N_ACCOUNTS = 10
N_SUPPLIERS = 7
N_ORDERS = 21


def _phone(n):
    """Fictional phone in the reserved 555-01xx range."""
    return f"555-01{n % 100:02d}"


def _email(prefix, n, domain="example.com"):
    return f"{prefix}{n:02d}@{domain}"


def _money(v):
    return f"{v:.2f}"


# ═══════════════════════════════════════════════════════════════
# SINGLE SOURCE OF TRUTH — synthesize every table
# ═══════════════════════════════════════════════════════════════

def build_dataset():
    """Synthesize all tables as dict-of-lists keyed by table name.

    Returns a dict {TABLE: [row_dict, ...]} where each row_dict has exactly
    the columns declared in ``schema.SCHEMA[TABLE]``. Referential consistency
    is maintained (order items reference orders + SKUs, POs reference
    suppliers, etc.).
    """
    data = {}

    # ── WAREHOUSES ──────────────────────────────────────────────
    warehouses = []
    for i in range(1, N_WAREHOUSES + 1):
        wid = f"WH-{i:02d}"
        warehouses.append({
            "WAREHOUSE_ID": wid,
            "NAME": f"Sample Distribution Center {i:02d}",
            "CITY": CITIES[i % len(CITIES)],
            "STATE": STATES[i % len(STATES)],
            "ZIP": f"{i:05d}",
            "CAPACITY_PALLETS": 5000 + i * 1000,
            "UTILIZATION_PCT": _money(50 + (i * 7) % 40),
            "SHIPPING_DOCKS": 10 + i * 2,
            "CAPABILITIES": "cold_chain,hazmat,oversized",
            "OPERATING_HOURS": "06:00-22:00",
        })
    data["WAREHOUSES"] = warehouses
    wh_ids = [w["WAREHOUSE_ID"] for w in warehouses]

    # ── PRODUCTS ────────────────────────────────────────────────
    products = []
    for i in range(1, N_PRODUCTS + 1):
        sku = f"SKU-{100 + i}"
        cost = round(30 + (i * 7.5) % 120, 2)
        unit_price = round(cost * 2.3, 2)
        cat = PRODUCT_CATEGORIES[i % len(PRODUCT_CATEGORIES)]
        products.append({
            "SKU": sku,
            "NAME": f"Sample Component {i:02d}",
            "CATEGORY": cat,
            "UNIT_PRICE": _money(unit_price),
            "COST": _money(cost),
            "WEIGHT_LBS": _money(round(1 + (i * 1.7) % 30, 2)),
            "WAREHOUSE": wh_ids[i % len(wh_ids)],
            "STOCK_QTY": 50 + (i * 37) % 900,
            "REORDER_POINT": 20 + (i * 11) % 80,
            "LEAD_TIME_DAYS": 3 + (i * 3) % 18,
            "MIN_MARGIN_PCT": _money(25 + (i % 3) * 2),
        })
    data["PRODUCTS"] = products
    skus = [p["SKU"] for p in products]

    # ── ACCOUNTS ────────────────────────────────────────────────
    accounts = []
    for i in range(1, N_ACCOUNTS + 1):
        aid = f"ACC-{i:03d}"
        accounts.append({
            "ACCOUNT_ID": aid,
            "NAME": f"Sample Manufacturing Co {i:02d}",
            "INDUSTRY": INDUSTRIES[i % len(INDUSTRIES)],
            "ADDRESS": f"{i * 100} Example Ave",
            "CITY": CITIES[i % len(CITIES)],
            "STATE": STATES[i % len(STATES)],
            "ZIP": f"{i:05d}",
            "CONTACT_NAME": f"Contact {i:02d}",
            "CONTACT_EMAIL": _email("contact", i, "example.com"),
            "CONTACT_PHONE": _phone(i),
            "CREDIT_TERMS": CREDIT_TERMS[i % len(CREDIT_TERMS)],
            "CREDIT_LIMIT": _money(200000 + (i * 75000) % 800000),
            "TIER": TIERS[i % len(TIERS)],
        })
    data["ACCOUNTS"] = accounts
    acc_ids = [a["ACCOUNT_ID"] for a in accounts]

    # ── SUPPLIERS ───────────────────────────────────────────────
    suppliers = []
    for i in range(1, N_SUPPLIERS + 1):
        sid = f"SUP-{100 + i}"
        cats = ",".join(PRODUCT_CATEGORIES[j] for j in range(i % 4, i % 4 + 3))
        certs = ",".join(CERTIFICATIONS_POOL[: 1 + (i % 3)])
        suppliers.append({
            "SUPPLIER_ID": sid,
            "NAME": f"Sample Supplier Co {i:02d}",
            "LOCATION": f"{CITIES[i % len(CITIES)]}, {STATES[i % len(STATES)]}",
            "REGION": REGIONS[i % len(REGIONS)],
            "QUALITY_SCORE": 80 + (i * 3) % 18,
            "ON_TIME_PCT": _money(85 + (i * 2) % 14),
            "MIN_ORDER_QTY": 10 + (i * 15) % 190,
            "PAYMENT_TERMS": CREDIT_TERMS[i % len(CREDIT_TERMS)],
            "CAPACITY_UTIL_PCT": _money(45 + (i * 6) % 45),
            "CATEGORIES": cats,
            "CERTIFICATIONS": certs,
            "CONTACT_NAME": f"Supplier Contact {i:02d}",
            "CONTACT_EMAIL": _email("supplier", i, "example.org"),
            "RISK_TIER": ["low", "medium", "high"][i % 3],
            "APPROVAL_STATUS": "APPROVED" if i % 4 != 0 else "CONDITIONAL",
        })
    data["SUPPLIERS"] = suppliers
    sup_ids = [s["SUPPLIER_ID"] for s in suppliers]

    # ── ORDERS + ORDER_ITEMS ────────────────────────────────────
    orders = []
    order_items = []
    for i in range(1, N_ORDERS + 1):
        oid = f"ORD-2026-{10000 + i}"
        acc = acc_ids[i % len(acc_ids)]
        status = ORDER_STATUSES[i % len(ORDER_STATUSES)]
        order_date = f"2026-06-{(i % 28) + 1:02d}"
        # 1–2 line items per order, deterministic.
        n_items = 1 + (i % 2)
        total = 0.0
        for j in range(n_items):
            sku = skus[(i + j) % len(skus)]
            prod = products[(i + j) % len(products)]
            qty = 25 + ((i * 13 + j * 7) % 176)
            unit_price = float(prod["UNIT_PRICE"])
            line_total = round(qty * unit_price, 2)
            total += line_total
            order_items.append({
                "ORDER_ID": oid,
                "SKU": sku,
                "QUANTITY": qty,
                "UNIT_PRICE": _money(unit_price),
                "LINE_TOTAL": _money(line_total),
            })
        tracking = f"TRK-{i:06d}" if status in ("Shipped", "Delivered") else ""
        delivered = order_date.replace("06-", "06-") if status == "Delivered" else ""
        delivered_date = f"2026-06-{min((i % 28) + 3, 28):02d}" if status == "Delivered" else ""
        orders.append({
            "ORDER_ID": oid,
            "ACCOUNT_ID": acc,
            "STATUS": status,
            "ORDER_DATE": order_date,
            "TOTAL_AMOUNT": _money(round(total, 2)),
            "SHIPPING_METHOD": SHIPPING_METHODS[i % len(SHIPPING_METHODS)],
            "PRIORITY": PRIORITIES[i % len(PRIORITIES)],
            "TRACKING_NUMBER": tracking,
            "DELIVERED_DATE": delivered_date,
            "CANCEL_REASON": "Customer request" if status == "Cancelled" else "",
            "BACKORDER_REASON": "Supplier delay" if status == "Backordered" else "",
        })
    data["ORDERS"] = orders
    data["ORDER_ITEMS"] = order_items

    # ── INVENTORY (one row per product at its home warehouse) ────
    inventory = []
    for i, p in enumerate(products, start=1):
        on_hand = int(p["STOCK_QTY"])
        reserved = (i * 10) % 90
        inventory.append({
            "SKU": p["SKU"],
            "WAREHOUSE": p["WAREHOUSE"],
            "QUANTITY_ON_HAND": on_hand,
            "QUANTITY_RESERVED": reserved,
            "QUANTITY_AVAILABLE": max(on_hand - reserved, 0),
            "LAST_RESTOCKED": f"2026-05-{(i % 28) + 1:02d}",
            "LAST_SOLD": f"2026-06-{(i % 28) + 1:02d}",
        })
    data["INVENTORY"] = inventory

    # ── SUPPLIER_CONTRACTS ──────────────────────────────────────
    # First 4 suppliers each get a contract. We deliberately pick discounts /
    # penalty / terms so the document templates can encode the demo scenarios.
    contracts = []
    contract_specs = [
        # (discount, volume, penalty_late, warranty, terms)
        (12.0, 5000, 2.5, 12, "Net 45"),   # volume tiers + late penalty (doc #3)
        (8.0, 2000, 3.0, 24, "Net 30"),
        (15.0, 10000, 1.5, 6, "Net 30"),
        (10.0, 3000, 2.0, 6, "Net 15"),    # 2/10 early-payment discount (doc #4)
    ]
    for idx, (disc, vol, pen, war, terms) in enumerate(contract_specs, start=1):
        contracts.append({
            "CONTRACT_ID": f"CTR-2026-{idx:03d}",
            "SUPPLIER_ID": sup_ids[idx - 1],
            "EFFECTIVE_DATE": "2026-04-01",
            "EXPIRY_DATE": "2027-03-31",
            "DISCOUNT_PCT": _money(disc),
            "VOLUME_COMMITMENT": vol,
            "PENALTY_LATE_PCT": _money(pen),
            "WARRANTY_MONTHS": war,
            "PAYMENT_TERMS": terms,
            "STATUS": "ACTIVE",
        })
    data["SUPPLIER_CONTRACTS"] = contracts

    # ── PURCHASE_ORDERS + PO_LINE_ITEMS ─────────────────────────
    purchase_orders = []
    po_line_items = []
    N_POS = 5
    for i in range(1, N_POS + 1):
        po = f"PO-2026-{41000 + i}"
        sup = sup_ids[i % len(sup_ids)]
        prod = products[i % len(products)]
        qty = 100 + (i * 50)
        unit_cost = float(prod["COST"])
        subtotal = round(qty * unit_cost, 2)
        discount = round(subtotal * 0.10, 2)
        rush = round(subtotal * 0.08, 2) if i == 3 else 0.0
        total = round(subtotal - discount + rush, 2)
        status = ["confirmed", "in_transit", "delivered", "delivered", "confirmed"][i - 1]
        purchase_orders.append({
            "PO_NUMBER": po,
            "SUPPLIER_ID": sup,
            "ORDER_DATE": f"2026-05-{(i * 4) % 28 + 1:02d}",
            "URGENCY": "expedited" if i == 3 else "standard",
            "SUBTOTAL": _money(subtotal),
            "DISCOUNT_AMOUNT": _money(discount),
            "RUSH_SURCHARGE": _money(rush),
            "TOTAL_COST": _money(total),
            "STATUS": status,
            "LEAD_TIME_DAYS": 7 + i,
            "ESTIMATED_DELIVERY": f"2026-06-{(i * 3) % 28 + 1:02d}",
            "ACTUAL_DELIVERY": f"2026-06-{(i * 3) % 28 + 2:02d}" if status == "delivered" else "",
            "DELIVERED_ON_TIME": "TRUE" if status == "delivered" else "",
            "NOTES": f"Restock order {i:02d}",
        })
        po_line_items.append({
            "PO_NUMBER": po,
            "SKU": prod["SKU"],
            "QUANTITY": qty,
            "UNIT_COST": _money(unit_cost),
            "LINE_TOTAL": _money(round(qty * unit_cost, 2)),
        })
    data["PURCHASE_ORDERS"] = purchase_orders
    data["PO_LINE_ITEMS"] = po_line_items

    # ── SHIPMENTS + SHIPMENT_EVENTS (for shipped/delivered orders) ─
    shipments = []
    shipment_events = []
    shipped_orders = [o for o in orders if o["TRACKING_NUMBER"]]
    for i, o in enumerate(shipped_orders, start=1):
        acc = next(a for a in accounts if a["ACCOUNT_ID"] == o["ACCOUNT_ID"])
        trk = o["TRACKING_NUMBER"]
        status = "delivered" if o["STATUS"] == "Delivered" else "in_transit"
        shipments.append({
            "TRACKING_NUMBER": trk,
            "ORDER_ID": o["ORDER_ID"],
            "CARRIER": CARRIERS[i % len(CARRIERS)],
            "SERVICE_LEVEL": SERVICE_LEVELS[i % len(SERVICE_LEVELS)],
            "ORIGIN_WAREHOUSE": wh_ids[i % len(wh_ids)],
            "DEST_ADDRESS": acc["ADDRESS"],
            "DEST_CITY": acc["CITY"],
            "DEST_STATE": acc["STATE"],
            "DEST_ZIP": acc["ZIP"],
            "DEST_CONTACT": acc["CONTACT_NAME"],
            "WEIGHT_LBS": _money(round(20 + i * 12.5, 2)),
            "COST": _money(round(50 + i * 15.0, 2)),
            "STATUS": status,
            "SHIPPED_AT": f"2026-06-0{(i % 9) + 1} 08:00:00",
            "ESTIMATED_DELIVERY": f"2026-06-{(i * 2) % 28 + 5:02d}",
            "DELIVERED_AT": f"2026-06-{(i * 2) % 28 + 6:02d} 11:00:00" if status == "delivered" else "",
            "SIGNATURE": acc["CONTACT_NAME"] if status == "delivered" else "",
        })
        # A couple of tracking events per shipment.
        shipment_events.append({
            "TRACKING_NUMBER": trk,
            "EVENT_TIME": f"2026-06-0{(i % 9) + 1} 08:00:00",
            "LOCATION": f"{CITIES[i % len(CITIES)]}, {STATES[i % len(STATES)]}",
            "EVENT_TYPE": "pickup",
            "DESCRIPTION": "Package picked up",
        })
        shipment_events.append({
            "TRACKING_NUMBER": trk,
            "EVENT_TIME": f"2026-06-0{(i % 9) + 2} 14:00:00",
            "LOCATION": f"{CITIES[(i + 1) % len(CITIES)]}, {STATES[(i + 1) % len(STATES)]}",
            "EVENT_TYPE": "delivered" if status == "delivered" else "in_transit",
            "DESCRIPTION": "Delivered" if status == "delivered" else "In transit",
        })
    data["SHIPMENTS"] = shipments
    data["SHIPMENT_EVENTS"] = shipment_events

    # ── DISRUPTIONS ─────────────────────────────────────────────
    disruption_types = ["port_closure", "geopolitical", "supplier_disruption", "weather", "labor"]
    severities = ["low", "medium", "high"]
    disruptions = []
    for i in range(1, 6):
        disruptions.append({
            "ALERT_ID": f"ALERT-{i:03d}",
            "TYPE": disruption_types[i % len(disruption_types)],
            "SEVERITY": severities[i % len(severities)],
            "REGION": REGIONS[i % len(REGIONS)],
            "TITLE": f"Sample Disruption Event {i:02d}",
            "DESCRIPTION": f"Synthetic disruption scenario {i:02d} for demo purposes.",
            "AFFECTED_ROUTES": f"Route-{i:02d}",
            "AFFECTED_PRODUCTS": skus[i % len(skus)],
            "EST_DURATION_DAYS": (i * 7) % 60 + 3,
            "ISSUED_AT": f"2026-06-0{(i % 9) + 1} 08:00:00",
            "STATUS": "ACTIVE",
            "SOURCE": "SyntheticFeed",
        })
    data["DISRUPTIONS"] = disruptions

    # ── SALES_HISTORY (12 months for a subset of SKUs) ──────────
    sales_history = []
    months = [f"2025-{m:02d}" for m in range(7, 13)] + [f"2026-{m:02d}" for m in range(1, 7)]
    for p in products[:6]:
        base = 50 + (int(p["SKU"].split("-")[1]) % 5) * 40
        for mi, month in enumerate(months):
            units = base + (mi * 13) % 120
            revenue = round(units * float(p["UNIT_PRICE"]), 2)
            sales_history.append({
                "SKU": p["SKU"],
                "MONTH": month,
                "UNITS_SOLD": units,
                "REVENUE": _money(revenue),
                "RETURNS": (mi * 2) % 10,
                "AVG_DAYS_TO_FULFILL": 3 + (mi % 8),
                "WAREHOUSE": p["WAREHOUSE"],
            })
    data["SALES_HISTORY"] = sales_history

    # ── OPPORTUNITIES ───────────────────────────────────────────
    stages = ["Qualification", "Proposal", "Negotiation", "Closed Won"]
    opportunities = []
    for i in range(1, N_ACCOUNTS + 1):
        prods = ",".join(skus[(i + k) % len(skus)] for k in range(3))
        stage = stages[i % len(stages)]
        opportunities.append({
            "OPP_ID": f"OPP-{i:03d}",
            "ACCOUNT_ID": acc_ids[i - 1],
            "NAME": f"Sample Opportunity {i:02d}",
            "STAGE": stage,
            "AMOUNT": _money(75000 + (i * 45000) % 400000),
            "CLOSE_DATE": f"2026-0{(i % 6) + 6}-{(i % 28) + 1:02d}"[:10],
            "PROBABILITY_PCT": 100 if stage == "Closed Won" else 30 + (i * 10) % 60,
            "PRODUCTS": prods,
        })
    data["OPPORTUNITIES"] = opportunities

    # ── QUOTES + QUOTE_LINE_ITEMS ───────────────────────────────
    quotes = []
    quote_line_items = []
    quote_statuses = ["accepted", "pending", "expired", "pending", "accepted"]
    for i in range(1, 6):
        acc = accounts[i % len(accounts)]
        prod = products[i % len(products)]
        qty = 50 + i * 50
        list_price = float(prod["UNIT_PRICE"])
        disc = 10.0 + (i % 3) * 5
        unit_price = round(list_price * (1 - disc / 100), 2)
        subtotal = round(qty * unit_price, 2)
        tax = round(subtotal * 0.08, 2)
        total = round(subtotal + tax, 2)
        qid = f"QT-2026-{i:03d}"
        quotes.append({
            "QUOTE_ID": qid,
            "ACCOUNT_ID": acc["ACCOUNT_ID"],
            "CUSTOMER_TIER": acc["TIER"],
            "SUBTOTAL": _money(subtotal),
            "TAX": _money(tax),
            "TOTAL": _money(total),
            "MARGIN_PCT": _money(35 + (i * 3) % 15),
            "STATUS": quote_statuses[i - 1],
            "VALID_UNTIL": f"2026-06-{(i * 5) % 28 + 1:02d}",
            "NOTES": f"Synthetic quote {i:02d}",
        })
        quote_line_items.append({
            "QUOTE_ID": qid,
            "SKU": prod["SKU"],
            "QUANTITY": qty,
            "LIST_PRICE": _money(list_price),
            "DISCOUNT_PCT": _money(disc),
            "UNIT_PRICE": _money(unit_price),
            "LINE_TOTAL": _money(subtotal),
            "MARGIN_PCT": _money(40 + (i * 2) % 15),
        })
    data["QUOTES"] = quotes
    data["QUOTE_LINE_ITEMS"] = quote_line_items

    # ── INVOICES ────────────────────────────────────────────────
    # One invoice per PO. Invoice #1 exactly matches its PO (auto-approve);
    # invoice #3 carries a ~6% price variance (director review). These map to
    # document scenarios #1 and #2.
    invoices = []
    for i, po in enumerate(purchase_orders, start=1):
        # Invoice #3 = 6% variance vs PO total; others match.
        if i == 3:
            total_amount = round(float(po["TOTAL_COST"]) * 1.06, 2)
            action = "MANAGER_REVIEW"
            status = "pending_approval"
        else:
            total_amount = float(po["TOTAL_COST"])
            action = "AUTO_APPROVE"
            status = "scheduled"
        tax = round(total_amount * 0.08, 2)
        invoices.append({
            "INVOICE_ID": f"INV-2026-{5000 + i}",
            "INVOICE_NUMBER": f"SUP-INV-{40000 + i}",
            "SUPPLIER_ID": po["SUPPLIER_ID"],
            "PO_NUMBER": po["PO_NUMBER"],
            "INVOICE_DATE": f"2026-06-{(i % 28) + 1:02d}",
            "TOTAL_AMOUNT": _money(total_amount),
            "TAX_AMOUNT": _money(tax),
            "PAYMENT_TERMS": CREDIT_TERMS[i % len(CREDIT_TERMS)],
            "DUE_DATE": f"2026-07-{(i % 28) + 1:02d}",
            "STATUS": status,
            "APPROVAL_ACTION": action,
        })
    data["INVOICES"] = invoices

    # ── GL_ALLOCATION_MAP ───────────────────────────────────────
    gl_codes = ["5100-RAW", "5200-MFG", "5300-ELEC", "5400-EQUIP", "5500-ADV"]
    cost_centers = ["CC-PRODUCTION", "CC-ENGINEERING", "CC-LOGISTICS", "CC-AEROSPACE"]
    gl_map = []
    seen = set()
    for i, s in enumerate(suppliers, start=1):
        cats = s["CATEGORIES"].split(",")[:2]
        for cat in cats:
            key = (s["SUPPLIER_ID"], cat)
            if key in seen:
                continue
            seen.add(key)
            gl_map.append({
                "VENDOR_ID": s["SUPPLIER_ID"],
                "CATEGORY": cat,
                "GL_CODE": gl_codes[i % len(gl_codes)],
                "COST_CENTER": cost_centers[i % len(cost_centers)],
                "ASSET_THRESHOLD": _money(5000),
            })
    data["GL_ALLOCATION_MAP"] = gl_map

    return data


# ═══════════════════════════════════════════════════════════════
# OUTPUT: per-table CSVs (schema-ordered)
# ═══════════════════════════════════════════════════════════════

def generate_structured(output_dir, data):
    """Write one CSV per table under <output>/structured/tables/ using the
    column order defined in schema.SCHEMA. NO SQL is emitted."""
    tables_dir = os.path.join(output_dir, "structured", "tables")
    os.makedirs(tables_dir, exist_ok=True)

    written = []
    for table in schema_mod.TABLES:
        cols = schema_mod.columns(table)
        rows = data.get(table, [])
        csv_path = os.path.join(tables_dir, f"{table}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            for r in rows:
                writer.writerow([r.get(c, "") for c in cols])
        written.append((table, len(rows)))

    print(f"  ✅ Structured CSVs written to {tables_dir}/ ({len(written)} tables):")
    for table, n in written:
        print(f"     • {table}.csv ({n} rows)")


# ═══════════════════════════════════════════════════════════════
# OUTPUT: Salesforce CSVs (derived from the SAME synthetic data)
# ═══════════════════════════════════════════════════════════════

def generate_salesforce(output_dir, data):
    """Generate Salesforce Dataloader CSVs from the synthetic accounts/opps."""
    sf_dir = os.path.join(output_dir, "salesforce")
    os.makedirs(sf_dir, exist_ok=True)
    accounts = data["ACCOUNTS"]
    opportunities = data["OPPORTUNITIES"]

    with open(os.path.join(sf_dir, "Accounts.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "External_ID__c", "Name", "Industry", "BillingStreet", "BillingCity",
            "BillingState", "BillingPostalCode", "Phone", "Type", "Description",
        ])
        writer.writeheader()
        for a in accounts:
            writer.writerow({
                "External_ID__c": a["ACCOUNT_ID"],
                "Name": a["NAME"],
                "Industry": a["INDUSTRY"],
                "BillingStreet": a["ADDRESS"],
                "BillingCity": a["CITY"],
                "BillingState": a["STATE"],
                "BillingPostalCode": a["ZIP"],
                "Phone": a["CONTACT_PHONE"],
                "Type": "Customer - Direct",
                "Description": f"{a['TIER']} tier, {a['CREDIT_TERMS']}, credit limit ${a['CREDIT_LIMIT']}",
            })

    with open(os.path.join(sf_dir, "Contacts.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "Account_External_ID__c", "FirstName", "LastName", "Email", "Phone", "Title",
        ])
        writer.writeheader()
        for a in accounts:
            parts = a["CONTACT_NAME"].split(" ", 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ""
            writer.writerow({
                "Account_External_ID__c": a["ACCOUNT_ID"],
                "FirstName": first,
                "LastName": last,
                "Email": a["CONTACT_EMAIL"],
                "Phone": a["CONTACT_PHONE"],
                "Title": "Procurement Manager",
            })

    with open(os.path.join(sf_dir, "Opportunities.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "External_ID__c", "Account_External_ID__c", "Name", "StageName",
            "Amount", "CloseDate", "Probability", "Description",
        ])
        writer.writeheader()
        for o in opportunities:
            writer.writerow({
                "External_ID__c": o["OPP_ID"],
                "Account_External_ID__c": o["ACCOUNT_ID"],
                "Name": o["NAME"],
                "StageName": o["STAGE"],
                "Amount": o["AMOUNT"],
                "CloseDate": o["CLOSE_DATE"],
                "Probability": o["PROBABILITY_PCT"],
                "Description": f"Products: {o['PRODUCTS']}",
            })

    print(f"  ✅ Salesforce CSVs generated in {sf_dir}/")
    print(f"     • Accounts.csv ({len(accounts)} records)")
    print(f"     • Contacts.csv ({len(accounts)} records)")
    print(f"     • Opportunities.csv ({len(opportunities)} records)")


# ═══════════════════════════════════════════════════════════════
# OUTPUT: Documents (delegates to generate_documents.py)
# ═══════════════════════════════════════════════════════════════

def generate_documents(output_dir):
    """Generate template-driven documents from the synthetic CSVs.

    Delegates to generate_documents.py which reads the structured CSVs and
    fills document templates (no hardcoded company names/values).
    """
    doc_dir = os.path.join(output_dir, "documents")
    os.makedirs(doc_dir, exist_ok=True)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    doc_script = os.path.join(script_dir, "generate_documents.py")

    print(f"  Generating documents in: {doc_dir}")
    result = _run(
        [sys.executable, doc_script, "--data-dir", output_dir, "--output", doc_dir],
        capture_output=True, text=True, shell=False,
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"  ⚠️  Document generation reported an error:\n{result.stderr[:400]}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate all Supply Chain demo data (synthetic)")
    parser.add_argument("--output", default="./synthetic_data/", help="Output directory")
    parser.add_argument("--structured-only", action="store_true")
    # Back-compat aliases.
    parser.add_argument("--database-only", dest="structured_only", action="store_true")
    parser.add_argument("--snowflake-only", dest="structured_only", action="store_true")
    parser.add_argument("--salesforce-only", action="store_true")
    parser.add_argument("--documents-only", action="store_true")
    args = parser.parse_args()

    output = args.output
    os.makedirs(output, exist_ok=True)

    data = build_dataset()

    print("═" * 60)
    print("  Supply Chain Demo — Synthetic Data Generator")
    print("═" * 60)
    print(f"  Output: {output}")
    print(f"  Source of truth: {len(data['ACCOUNTS'])} accounts, "
          f"{len(data['OPPORTUNITIES'])} opportunities, "
          f"{len(schema_mod.TABLES)} tables")
    print("═" * 60)
    print()

    run_all = not (args.structured_only or args.salesforce_only or args.documents_only)

    if run_all or args.structured_only:
        print("━━━ Structured per-table CSVs ━━━")
        generate_structured(output, data)
        print()

    if run_all or args.salesforce_only:
        print("━━━ Salesforce Import CSVs ━━━")
        generate_salesforce(output, data)
        print()

    if run_all or args.documents_only:
        print("━━━ Unstructured Documents ━━━")
        # Documents need the structured CSVs to exist; ensure they do.
        if not os.path.exists(os.path.join(output, "structured", "tables", "SUPPLIERS.csv")):
            generate_structured(output, data)
        generate_documents(output)
        print()

    print("═" * 60)
    print("  ✅ DONE")
    print("═" * 60)
    print()
    print("  Next steps:")
    print("    Datasets/KB: ./scripts/create_datasets.sh")
    print("    Salesforce:  Import salesforce/*.csv via Dataloader (Accounts → Contacts → Opportunities)")
    print("    Documents:   Upload synthetic_data/documents/ into the Quick Space for RAG")
    print()


if __name__ == "__main__":
    main()
