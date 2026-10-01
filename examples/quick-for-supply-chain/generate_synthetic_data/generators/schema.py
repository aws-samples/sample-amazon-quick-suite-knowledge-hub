#!/usr/bin/env python3
"""
Supply Chain Demo — Machine-readable schema (single source of truth)
═══════════════════════════════════════════════════════════════════

This module is THE canonical schema for the supply-chain demo. It encodes the
table -> ordered columns (+ SQL types) definitions as a single source of truth.
There are no ``.sql`` files: both the generated CSVs *and* the DB CREATE TABLE
DDL are derived from :data:`SCHEMA` here.

* The synthetic data generator imports :data:`SCHEMA` and emits one CSV per
  table using exactly these column orders.
* The DB loaders call :func:`create_table_ddl` to generate per-engine
  ``CREATE TABLE IF NOT EXISTS`` statements — no SQL files are read.

Types are standard SQL types (VARCHAR(n), DECIMAL(p,s), INT, DATE, TIMESTAMP,
BOOLEAN); :func:`create_table_ddl` maps them to each engine's dialect.

NOTE: Natural keys are supplied by the generator as data, so no surrogate
auto-increment ``ID`` columns or PRIMARY KEY constraints are emitted.
"""

from __future__ import annotations

import re

# Each entry: TABLE -> list of (COLUMN_NAME, SQL_TYPE) in CSV/emit order.
SCHEMA: dict[str, list[tuple[str, str]]] = {
    "PRODUCTS": [
        ("SKU", "VARCHAR(20)"),
        ("NAME", "VARCHAR(200)"),
        ("CATEGORY", "VARCHAR(50)"),
        ("UNIT_PRICE", "DECIMAL(10,2)"),
        ("COST", "DECIMAL(10,2)"),
        ("WEIGHT_LBS", "DECIMAL(8,2)"),
        ("WAREHOUSE", "VARCHAR(20)"),
        ("STOCK_QTY", "INT"),
        ("REORDER_POINT", "INT"),
        ("LEAD_TIME_DAYS", "INT"),
        ("MIN_MARGIN_PCT", "DECIMAL(5,2)"),
    ],
    "ACCOUNTS": [
        ("ACCOUNT_ID", "VARCHAR(20)"),
        ("NAME", "VARCHAR(200)"),
        ("INDUSTRY", "VARCHAR(50)"),
        ("ADDRESS", "VARCHAR(500)"),
        ("CITY", "VARCHAR(100)"),
        ("STATE", "VARCHAR(50)"),
        ("ZIP", "VARCHAR(20)"),
        ("CONTACT_NAME", "VARCHAR(100)"),
        ("CONTACT_EMAIL", "VARCHAR(200)"),
        ("CONTACT_PHONE", "VARCHAR(50)"),
        ("CREDIT_TERMS", "VARCHAR(20)"),
        ("CREDIT_LIMIT", "DECIMAL(12,2)"),
        ("TIER", "VARCHAR(20)"),
    ],
    "ORDERS": [
        ("ORDER_ID", "VARCHAR(30)"),
        ("ACCOUNT_ID", "VARCHAR(20)"),
        ("STATUS", "VARCHAR(30)"),
        ("ORDER_DATE", "DATE"),
        ("TOTAL_AMOUNT", "DECIMAL(12,2)"),
        ("SHIPPING_METHOD", "VARCHAR(30)"),
        ("PRIORITY", "VARCHAR(20)"),
        ("TRACKING_NUMBER", "VARCHAR(50)"),
        ("DELIVERED_DATE", "DATE"),
        ("CANCEL_REASON", "VARCHAR(200)"),
        ("BACKORDER_REASON", "VARCHAR(200)"),
    ],
    "ORDER_ITEMS": [
        ("ORDER_ID", "VARCHAR(30)"),
        ("SKU", "VARCHAR(20)"),
        ("QUANTITY", "INT"),
        ("UNIT_PRICE", "DECIMAL(10,2)"),
        ("LINE_TOTAL", "DECIMAL(12,2)"),
    ],
    "INVENTORY": [
        ("SKU", "VARCHAR(20)"),
        ("WAREHOUSE", "VARCHAR(20)"),
        ("QUANTITY_ON_HAND", "INT"),
        ("QUANTITY_RESERVED", "INT"),
        ("QUANTITY_AVAILABLE", "INT"),
        ("LAST_RESTOCKED", "DATE"),
        ("LAST_SOLD", "DATE"),
    ],
    "SUPPLIERS": [
        ("SUPPLIER_ID", "VARCHAR(20)"),
        ("NAME", "VARCHAR(200)"),
        ("LOCATION", "VARCHAR(200)"),
        ("REGION", "VARCHAR(10)"),
        ("QUALITY_SCORE", "INT"),
        ("ON_TIME_PCT", "DECIMAL(5,2)"),
        ("MIN_ORDER_QTY", "INT"),
        ("PAYMENT_TERMS", "VARCHAR(30)"),
        ("CAPACITY_UTIL_PCT", "DECIMAL(5,2)"),
        ("CATEGORIES", "VARCHAR(500)"),
        ("CERTIFICATIONS", "VARCHAR(500)"),
        ("CONTACT_NAME", "VARCHAR(100)"),
        ("CONTACT_EMAIL", "VARCHAR(200)"),
        ("RISK_TIER", "VARCHAR(20)"),
        ("APPROVAL_STATUS", "VARCHAR(20)"),
    ],
    "SUPPLIER_CONTRACTS": [
        ("CONTRACT_ID", "VARCHAR(30)"),
        ("SUPPLIER_ID", "VARCHAR(20)"),
        ("EFFECTIVE_DATE", "DATE"),
        ("EXPIRY_DATE", "DATE"),
        ("DISCOUNT_PCT", "DECIMAL(5,2)"),
        ("VOLUME_COMMITMENT", "INT"),
        ("PENALTY_LATE_PCT", "DECIMAL(5,2)"),
        ("WARRANTY_MONTHS", "INT"),
        ("PAYMENT_TERMS", "VARCHAR(30)"),
        ("STATUS", "VARCHAR(20)"),
    ],
    "PURCHASE_ORDERS": [
        ("PO_NUMBER", "VARCHAR(30)"),
        ("SUPPLIER_ID", "VARCHAR(20)"),
        ("ORDER_DATE", "DATE"),
        ("URGENCY", "VARCHAR(20)"),
        ("SUBTOTAL", "DECIMAL(12,2)"),
        ("DISCOUNT_AMOUNT", "DECIMAL(10,2)"),
        ("RUSH_SURCHARGE", "DECIMAL(10,2)"),
        ("TOTAL_COST", "DECIMAL(12,2)"),
        ("STATUS", "VARCHAR(30)"),
        ("LEAD_TIME_DAYS", "INT"),
        ("ESTIMATED_DELIVERY", "DATE"),
        ("ACTUAL_DELIVERY", "DATE"),
        ("DELIVERED_ON_TIME", "BOOLEAN"),
        ("NOTES", "VARCHAR(500)"),
    ],
    "PO_LINE_ITEMS": [
        ("PO_NUMBER", "VARCHAR(30)"),
        ("SKU", "VARCHAR(20)"),
        ("QUANTITY", "INT"),
        ("UNIT_COST", "DECIMAL(10,2)"),
        ("LINE_TOTAL", "DECIMAL(12,2)"),
    ],
    "SHIPMENTS": [
        ("TRACKING_NUMBER", "VARCHAR(50)"),
        ("ORDER_ID", "VARCHAR(30)"),
        ("CARRIER", "VARCHAR(50)"),
        ("SERVICE_LEVEL", "VARCHAR(30)"),
        ("ORIGIN_WAREHOUSE", "VARCHAR(20)"),
        ("DEST_ADDRESS", "VARCHAR(500)"),
        ("DEST_CITY", "VARCHAR(100)"),
        ("DEST_STATE", "VARCHAR(50)"),
        ("DEST_ZIP", "VARCHAR(20)"),
        ("DEST_CONTACT", "VARCHAR(100)"),
        ("WEIGHT_LBS", "DECIMAL(8,2)"),
        ("COST", "DECIMAL(10,2)"),
        ("STATUS", "VARCHAR(30)"),
        ("SHIPPED_AT", "TIMESTAMP"),
        ("ESTIMATED_DELIVERY", "DATE"),
        ("DELIVERED_AT", "TIMESTAMP"),
        ("SIGNATURE", "VARCHAR(100)"),
    ],
    "SHIPMENT_EVENTS": [
        ("TRACKING_NUMBER", "VARCHAR(50)"),
        ("EVENT_TIME", "TIMESTAMP"),
        ("LOCATION", "VARCHAR(200)"),
        ("EVENT_TYPE", "VARCHAR(50)"),
        ("DESCRIPTION", "VARCHAR(500)"),
    ],
    "DISRUPTIONS": [
        ("ALERT_ID", "VARCHAR(30)"),
        ("TYPE", "VARCHAR(30)"),
        ("SEVERITY", "VARCHAR(20)"),
        ("REGION", "VARCHAR(10)"),
        ("TITLE", "VARCHAR(300)"),
        ("DESCRIPTION", "VARCHAR(2000)"),
        ("AFFECTED_ROUTES", "VARCHAR(500)"),
        ("AFFECTED_PRODUCTS", "VARCHAR(500)"),
        ("EST_DURATION_DAYS", "INT"),
        ("ISSUED_AT", "TIMESTAMP"),
        ("STATUS", "VARCHAR(20)"),
        ("SOURCE", "VARCHAR(100)"),
    ],
    "SALES_HISTORY": [
        ("SKU", "VARCHAR(20)"),
        ("MONTH", "VARCHAR(7)"),
        ("UNITS_SOLD", "INT"),
        ("REVENUE", "DECIMAL(12,2)"),
        ("RETURNS", "INT"),
        ("AVG_DAYS_TO_FULFILL", "INT"),
        ("WAREHOUSE", "VARCHAR(20)"),
    ],
    "OPPORTUNITIES": [
        ("OPP_ID", "VARCHAR(20)"),
        ("ACCOUNT_ID", "VARCHAR(20)"),
        ("NAME", "VARCHAR(200)"),
        ("STAGE", "VARCHAR(50)"),
        ("AMOUNT", "DECIMAL(12,2)"),
        ("CLOSE_DATE", "DATE"),
        ("PROBABILITY_PCT", "INT"),
        ("PRODUCTS", "VARCHAR(500)"),
    ],
    "WAREHOUSES": [
        ("WAREHOUSE_ID", "VARCHAR(20)"),
        ("NAME", "VARCHAR(100)"),
        ("CITY", "VARCHAR(100)"),
        ("STATE", "VARCHAR(50)"),
        ("ZIP", "VARCHAR(20)"),
        ("CAPACITY_PALLETS", "INT"),
        ("UTILIZATION_PCT", "DECIMAL(5,2)"),
        ("SHIPPING_DOCKS", "INT"),
        ("CAPABILITIES", "VARCHAR(500)"),
        ("OPERATING_HOURS", "VARCHAR(50)"),
    ],
    "QUOTES": [
        ("QUOTE_ID", "VARCHAR(30)"),
        ("ACCOUNT_ID", "VARCHAR(20)"),
        ("CUSTOMER_TIER", "VARCHAR(20)"),
        ("SUBTOTAL", "DECIMAL(12,2)"),
        ("TAX", "DECIMAL(10,2)"),
        ("TOTAL", "DECIMAL(12,2)"),
        ("MARGIN_PCT", "DECIMAL(5,2)"),
        ("STATUS", "VARCHAR(20)"),
        ("VALID_UNTIL", "DATE"),
        ("NOTES", "VARCHAR(500)"),
    ],
    "QUOTE_LINE_ITEMS": [
        ("QUOTE_ID", "VARCHAR(30)"),
        ("SKU", "VARCHAR(20)"),
        ("QUANTITY", "INT"),
        ("LIST_PRICE", "DECIMAL(10,2)"),
        ("DISCOUNT_PCT", "DECIMAL(5,2)"),
        ("UNIT_PRICE", "DECIMAL(10,2)"),
        ("LINE_TOTAL", "DECIMAL(12,2)"),
        ("MARGIN_PCT", "DECIMAL(5,2)"),
    ],
    "INVOICES": [
        ("INVOICE_ID", "VARCHAR(30)"),
        ("INVOICE_NUMBER", "VARCHAR(50)"),
        ("SUPPLIER_ID", "VARCHAR(20)"),
        ("PO_NUMBER", "VARCHAR(30)"),
        ("INVOICE_DATE", "DATE"),
        ("TOTAL_AMOUNT", "DECIMAL(12,2)"),
        ("TAX_AMOUNT", "DECIMAL(10,2)"),
        ("PAYMENT_TERMS", "VARCHAR(30)"),
        ("DUE_DATE", "DATE"),
        ("STATUS", "VARCHAR(30)"),
        ("APPROVAL_ACTION", "VARCHAR(30)"),
    ],
    "GL_ALLOCATION_MAP": [
        ("VENDOR_ID", "VARCHAR(20)"),
        ("CATEGORY", "VARCHAR(50)"),
        ("GL_CODE", "VARCHAR(20)"),
        ("COST_CENTER", "VARCHAR(30)"),
        ("ASSET_THRESHOLD", "DECIMAL(10,2)"),
    ],
}

# Ordered list of tables the demo needs (emit order).
TABLES: list[str] = list(SCHEMA.keys())


def columns(table: str) -> list[str]:
    """Return the ordered column names for ``table``."""
    return [c for c, _ in SCHEMA[table]]


def types(table: str) -> dict[str, str]:
    """Return {column: sql_type} for ``table``."""
    return dict(SCHEMA[table])


# ── DDL generation (single source of truth → per-engine CREATE TABLE) ──
# Supported engines and their type-family mappings. Each engine maps the
# standard families used in SCHEMA to its native dialect. VARCHAR/DECIMAL keep
# their (n) / (p,s) parameters; scalar families map by name.
_ENGINE_ALIASES = {
    "ansi": "ansi",
    "sqlalchemy": "ansi",
    "sqlite": "ansi",
    "snowflake": "snowflake",
    "postgres": "postgres",
    "postgresql": "postgres",
}

_TYPE_MAPS: dict[str, dict[str, str]] = {
    # family -> engine-native rendering. VARCHAR/DECIMAL use "{args}" for params.
    "snowflake": {
        "VARCHAR": "VARCHAR({args})",
        "DECIMAL": "NUMBER({args})",
        "INT": "INTEGER",
        "DATE": "DATE",
        "TIMESTAMP": "TIMESTAMP_NTZ",
        "BOOLEAN": "BOOLEAN",
    },
    "postgres": {
        "VARCHAR": "VARCHAR({args})",
        "DECIMAL": "NUMERIC({args})",
        "INT": "INTEGER",
        "DATE": "DATE",
        "TIMESTAMP": "TIMESTAMP",
        "BOOLEAN": "BOOLEAN",
    },
    "ansi": {
        "VARCHAR": "VARCHAR({args})",
        "DECIMAL": "DECIMAL({args})",
        "INT": "INTEGER",
        "DATE": "DATE",
        "TIMESTAMP": "TIMESTAMP",
        "BOOLEAN": "BOOLEAN",
    },
}

# Split e.g. "VARCHAR(20)" -> ("VARCHAR", "20"); "INT" -> ("INT", None).
_TYPE_RE = re.compile(r"^\s*([A-Za-z_]+)\s*(?:\(([^)]*)\))?\s*$")


def _map_sql_type(sql_type: str, engine: str) -> str:
    """Map a standard SCHEMA SQL type to the given engine's dialect."""
    m = _TYPE_RE.match(sql_type)
    if not m:
        raise ValueError(f"Unrecognized SQL type: {sql_type!r}")
    family, args = m.group(1).upper(), m.group(2)
    type_map = _TYPE_MAPS[engine]
    if family not in type_map:
        raise ValueError(f"Unsupported SQL type family {family!r} in {sql_type!r}")
    rendered = type_map[family]
    if "{args}" in rendered:
        if args is None:
            raise ValueError(f"Type {family} requires parameters: {sql_type!r}")
        # Normalize whitespace inside parameter lists (e.g. "10, 2" -> "10,2").
        norm = ",".join(p.strip() for p in args.split(","))
        return rendered.format(args=norm)
    return rendered


def create_table_ddl(
    engine: str = "ansi",
    if_not_exists: bool = True,
    schema_prefix: str | None = None,
) -> list[str]:
    """Generate one ``CREATE TABLE`` statement per table from :data:`SCHEMA`.

    Args:
        engine: Target dialect — one of ``snowflake``, ``postgres``,
            ``ansi``/``sqlalchemy``/``sqlite``. Types are mapped per engine.
        if_not_exists: Emit ``CREATE TABLE IF NOT EXISTS`` when True.
        schema_prefix: Optional schema/namespace to qualify table names
            (e.g. ``"PUBLIC"`` → ``PUBLIC.PRODUCTS``). No database is
            hardcoded; when None, bare table names are used.

    Returns:
        A list of ``CREATE TABLE`` statements (no trailing semicolons), one per
        table, in :data:`TABLES` order.
    """
    key = _ENGINE_ALIASES.get(engine.lower())
    if key is None:
        raise ValueError(
            f"Unsupported engine {engine!r}. "
            f"Choose from: {', '.join(sorted(_ENGINE_ALIASES))}."
        )

    prefix = f"{schema_prefix}." if schema_prefix else ""
    exists_clause = "IF NOT EXISTS " if if_not_exists else ""

    statements: list[str] = []
    for table in TABLES:
        cols = SCHEMA[table]
        col_defs = ",\n".join(
            f"    {name} {_map_sql_type(sql_type, key)}" for name, sql_type in cols
        )
        stmt = f"CREATE TABLE {exists_clause}{prefix}{table} (\n{col_defs}\n)"
        statements.append(stmt)
    return statements


def _main(argv: list[str] | None = None) -> int:
    """Optional CLI: print CREATE TABLE DDL for an engine.

    Usage: ``python -m generate_synthetic_data.generators.schema [engine]``
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    engine = args[0] if args else "ansi"
    for stmt in create_table_ddl(engine):
        print(stmt + ";\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
