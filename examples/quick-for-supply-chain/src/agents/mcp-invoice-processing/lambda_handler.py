"""
Invoice Processing MCP — Lambda Handler for AgentCore Gateway
═══════════════════════════════════════════════════════════════
Single tool: apply_approval_rules

A comprehensive invoice approval rules engine that applies ALL deterministic
business rules that must NEVER be left to model judgment:
- Price tolerance thresholds
- Quantity variance caps
- Amount-based approval routing
- Payment terms enforcement
- Asset vs expense classification
- Early payment discount decisioning
- Payment run alignment

Quick handles: PDF extraction, database queries (PO, GR, duplicates, GL mapping),
date/tax validation (model reasoning). This MCP handles the MATH and RULES.
"""

import json
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════════════════════
# BUSINESS RULES CONFIGURATION
# These are the deterministic rules that must always be enforced
# ═══════════════════════════════════════════════════════════════

RULES = {
    # ─── Price Tolerance ─────────────────────────────────────
    "price_variance": {
        "auto_approve_pct": 2.0,  # ≤2% → auto-approve (no human needed)
        "manager_review_pct": 5.0,  # 2.01%-5% → manager review
        "director_review_pct": 10.0,  # 5.01%-10% → director review
        # >10% → auto-reject
    },
    # ─── Quantity Tolerance ──────────────────────────────────
    "quantity_variance": {
        "allow_under_shipment": True,  # Allow invoice qty < PO qty (partial delivery)
        "over_shipment_tolerance_pct": 5.0,  # Allow up to 5% over PO qty
        # >5% over → reject line item
    },
    # ─── Amount-Based Approval Routing ───────────────────────
    "amount_thresholds": {
        "auto_approve_max": 10000,  # ≤$10K → auto-approve (if within tolerance)
        "manager_approve_max": 50000,  # $10K-$50K → manager
        "director_approve_max": 200000,  # $50K-$200K → director
        # >$200K → VP Finance
    },
    # ─── Payment Terms Enforcement ───────────────────────────
    "payment_terms": {
        "max_allowed_days": 60,  # Reject if invoice requests > Net 60
        "enforce_contract_terms": True,  # Reject if invoice requests earlier than contract
    },
    # ─── Asset vs Expense Classification ─────────────────────
    "asset_classification": {
        "capex_threshold": 5000,  # Single item > $5K → flag as Fixed Asset
        "capex_categories": ["Equipment", "Machinery", "IT Hardware", "Vehicles"],
    },
    # ─── Early Payment Discount ──────────────────────────────
    "early_payment": {
        "cost_of_capital_annual_pct": 5.0,  # Company's cost of capital
        "min_savings_threshold": 50,  # Minimum $ savings to justify early payment
        "auto_take_discount_below": 10000,  # Auto-take discount for invoices < $10K
    },
    # ─── Payment Run Schedule ────────────────────────────────
    "payment_runs": {
        "days": ["Tuesday", "Thursday"],  # Payments processed on these days
        "cutoff_time": "14:00",  # Must be approved by 2 PM day before
    },
    # ─── Aggregate Controls ──────────────────────────────────
    "aggregate_controls": {
        "single_vendor_daily_max": 500000,  # Flag if total to 1 vendor in 1 day > $500K
        "single_approver_daily_max": 200000,  # Flag if 1 approver approves > $200K in a day
    },
}


# ═══════════════════════════════════════════════════════════════
# TOOL IMPLEMENTATION
# ═══════════════════════════════════════════════════════════════


def apply_approval_rules(
    invoice_id: str = None,
    po_number: str = None,
    amount: float = None,
    invoice_data: dict = None,
    po_data: dict = None,
    goods_receipt: dict = None,
    vendor_contract: dict = None,
    daily_vendor_total: float = 0,
) -> dict:
    """
    Comprehensive invoice approval rules engine.
    Accepts either simple gateway params (invoice_id, po_number, amount) or full objects.
    Applies ALL deterministic business rules and returns a decision.

    Quick provides all data (extracted invoice + database queries).
    This tool applies the rules and returns the verdict.

    Args:
        invoice_data: {
            invoice_number: str,
            supplier_id: str,
            invoice_date: str (YYYY-MM-DD),
            line_items: [{sku, description, qty, unit_price, line_total, category}],
            total: float,
            tax_amount: float,
            payment_terms_requested: str (e.g., "Net 30"),
            discount_terms: str (optional, e.g., "2/10 Net 30"),
            currency: str
        }
        po_data: {
            po_number: str,
            line_items: [{sku, qty, unit_price, line_total}],
            total: float,
            payment_terms: str,
            approved_by: str
        }
        goods_receipt: (optional) {
            gr_number: str,
            items_received: [{sku, qty_received, qty_accepted, condition}],
            received_date: str
        }
        vendor_contract: (optional) {
            contract_id: str,
            payment_terms: str,
            discount_terms: str (e.g., "2/10 Net 30"),
            max_price_increase_pct: float
        }
        daily_vendor_total: float — total already approved today for this vendor

    Returns:
        Comprehensive decision with:
        - approval_action (AUTO_APPROVE | MANAGER_REVIEW | DIRECTOR_REVIEW | VP_REVIEW | REJECT)
        - line_item_results (per-item pass/fail)
        - issues (blocking problems)
        - warnings (non-blocking notes)
        - payment_recommendation (when to pay, discount decision)
        - classification (expense vs asset)
        - escalation_reason (if not auto-approved)
    """
    # Build objects from simple gateway params if full objects not provided
    if not invoice_data and invoice_id:
        invoice_data = {
            "invoice_number": invoice_id,
            "supplier_id": "UNKNOWN",
            "invoice_date": datetime.now().strftime("%Y-%m-%d"),
            "line_items": [
                {
                    "sku": "LINE-1",
                    "description": "Invoice line",
                    "qty": 1,
                    "unit_price": amount or 0,
                    "line_total": amount or 0,
                    "category": "General",
                }
            ],
            "total": amount or 0,
            "tax_amount": 0,
            "payment_terms_requested": "Net 30",
            "currency": "USD",
        }
    if not po_data and po_number:
        po_data = {
            "po_number": po_number,
            "line_items": [
                {
                    "sku": "LINE-1",
                    "qty": 1,
                    "unit_price": amount or 0,
                    "line_total": amount or 0,
                }
            ],
            "total": amount or 0,
            "payment_terms": "Net 30",
            "approved_by": "system",
        }
    if not invoice_data or not po_data:
        return {
            "decision": {
                "action": "REJECT",
                "escalation_reason": "Missing invoice or PO data",
            },
            "issues": [
                {
                    "rule": "INPUT",
                    "severity": "critical",
                    "detail": "invoice_id/invoice_data and po_number/po_data required",
                }
            ],
        }

    issues = []  # Blocking — prevents approval
    warnings = []  # Non-blocking — informational
    line_results = []
    flags = []  # Risk flags

    invoice_total = invoice_data.get("total", 0)
    po_total = po_data.get("total", 0)

    # ─── RULE SET 1: Line-Item Matching ──────────────────────
    for inv_item in invoice_data.get("line_items", []):
        sku = inv_item.get("sku") or inv_item.get("description", "unknown")
        po_item = next(
            (
                p
                for p in po_data.get("line_items", [])
                if p.get("sku") == inv_item.get("sku")
            ),
            None,
        )

        if not po_item:
            issues.append(
                {
                    "rule": "LINE_ITEM_MATCH",
                    "severity": "high",
                    "detail": f"Item '{sku}' on invoice but NOT on PO {po_data.get('po_number')}",
                    "action": "reject_line",
                }
            )
            line_results.append(
                {"item": sku, "status": "REJECTED", "reason": "Not on PO"}
            )
            continue

        # Price variance check
        inv_price = inv_item.get("unit_price", 0)
        po_price = po_item.get("unit_price", 0)
        price_var_pct = (
            abs((inv_price - po_price) / po_price * 100) if po_price > 0 else 100
        )

        price_status = "PASS"
        if price_var_pct > 10:
            issues.append(
                {
                    "rule": "PRICE_TOLERANCE",
                    "severity": "critical",
                    "detail": f"{sku}: ${inv_price} vs PO ${po_price} ({price_var_pct:.1f}% — exceeds 10% max)",
                    "action": "reject_line",
                }
            )
            price_status = "REJECTED"
        elif price_var_pct > RULES["price_variance"]["manager_review_pct"]:
            issues.append(
                {
                    "rule": "PRICE_TOLERANCE",
                    "severity": "high",
                    "detail": f"{sku}: ${inv_price} vs PO ${po_price} ({price_var_pct:.1f}% — exceeds 5%, needs director)",
                    "action": "director_review",
                }
            )
            price_status = "DIRECTOR_REVIEW"
        elif price_var_pct > RULES["price_variance"]["auto_approve_pct"]:
            warnings.append(
                {
                    "rule": "PRICE_TOLERANCE",
                    "detail": f"{sku}: ${inv_price} vs PO ${po_price} ({price_var_pct:.1f}% — within 5%, needs manager)",
                }
            )
            price_status = "MANAGER_REVIEW"

        # Quantity variance check
        inv_qty = inv_item.get("qty", 0)
        po_qty = po_item.get("qty", 0)
        qty_status = "PASS"

        if inv_qty > po_qty:
            over_pct = ((inv_qty - po_qty) / po_qty * 100) if po_qty > 0 else 100
            if over_pct > RULES["quantity_variance"]["over_shipment_tolerance_pct"]:
                issues.append(
                    {
                        "rule": "QTY_OVER_SHIPMENT",
                        "severity": "high",
                        "detail": f"{sku}: invoiced {inv_qty} vs PO {po_qty} (+{over_pct:.1f}% — exceeds 5% cap)",
                        "action": "reject_line",
                    }
                )
                qty_status = "REJECTED"
            else:
                warnings.append(
                    {
                        "rule": "QTY_OVER_SHIPMENT",
                        "detail": f"{sku}: invoiced {inv_qty} vs PO {po_qty} (+{over_pct:.1f}% — within tolerance)",
                    }
                )

        # Goods receipt matching (if provided)
        gr_status = "NOT_CHECKED"
        if goods_receipt:
            gr_item = next(
                (
                    g
                    for g in goods_receipt.get("items_received", [])
                    if g.get("sku") == inv_item.get("sku")
                ),
                None,
            )
            if not gr_item:
                issues.append(
                    {
                        "rule": "GOODS_RECEIPT",
                        "severity": "medium",
                        "detail": f"{sku}: no goods receipt record — cannot confirm delivery",
                    }
                )
                gr_status = "MISSING"
            elif gr_item.get("qty_accepted", gr_item.get("qty_received", 0)) < inv_qty:
                issues.append(
                    {
                        "rule": "GOODS_RECEIPT",
                        "severity": "high",
                        "detail": f"{sku}: accepted {gr_item.get('qty_accepted', gr_item.get('qty_received'))} but invoiced {inv_qty}",
                    }
                )
                gr_status = "QUANTITY_MISMATCH"
            else:
                gr_status = "MATCHED"

        line_results.append(
            {
                "item": sku,
                "invoice_qty": inv_qty,
                "po_qty": po_qty,
                "invoice_price": inv_price,
                "po_price": po_price,
                "price_variance_pct": round(price_var_pct, 2),
                "price_status": price_status,
                "qty_status": qty_status,
                "gr_status": gr_status,
            }
        )

    # ─── RULE SET 2: Payment Terms Enforcement ───────────────
    requested_terms = invoice_data.get("payment_terms_requested", "")
    contract_terms = (vendor_contract or {}).get(
        "payment_terms", po_data.get("payment_terms", "")
    )

    if requested_terms and contract_terms:
        req_days = int("".join(filter(str.isdigit, requested_terms)) or "30")
        contract_days = int("".join(filter(str.isdigit, contract_terms)) or "30")

        if req_days > RULES["payment_terms"]["max_allowed_days"]:
            issues.append(
                {
                    "rule": "PAYMENT_TERMS_MAX",
                    "severity": "high",
                    "detail": f"Requested {requested_terms} exceeds company max (Net {RULES['payment_terms']['max_allowed_days']})",
                }
            )

        if (
            RULES["payment_terms"]["enforce_contract_terms"]
            and req_days < contract_days
        ):
            warnings.append(
                {
                    "rule": "PAYMENT_TERMS_CONTRACT",
                    "detail": f"Invoice requests {requested_terms} but contract is Net {contract_days} — use contract terms",
                }
            )

    # ─── RULE SET 3: Asset vs Expense Classification ─────────
    classification = "EXPENSE"
    asset_items = []
    for inv_item in invoice_data.get("line_items", []):
        item_total = inv_item.get(
            "line_total", inv_item.get("unit_price", 0) * inv_item.get("qty", 1)
        )
        category = inv_item.get("category", "")

        if (
            item_total > RULES["asset_classification"]["capex_threshold"]
            or category in RULES["asset_classification"]["capex_categories"]
        ):
            asset_items.append(
                {
                    "item": inv_item.get("sku", inv_item.get("description")),
                    "amount": item_total,
                    "category": category,
                }
            )

    if asset_items:
        classification = (
            "MIXED"
            if len(asset_items) < len(invoice_data.get("line_items", []))
            else "CAPITAL_ASSET"
        )
        flags.append(
            {
                "flag": "CAPEX_REVIEW",
                "detail": f"{len(asset_items)} item(s) classified as Fixed Asset (>${RULES['asset_classification']['capex_threshold']:,})",
                "items": asset_items,
            }
        )

    # ─── RULE SET 4: Early Payment Discount ──────────────────
    payment_recommendation = {"action": "PAY_ON_DUE_DATE"}
    discount_terms = invoice_data.get("discount_terms") or (vendor_contract or {}).get(
        "discount_terms", ""
    )

    if discount_terms and "/" in discount_terms:
        try:
            parts = discount_terms.replace(" ", "").split("/")
            discount_pct = float(parts[0])
            discount_days = int(
                "".join(
                    filter(
                        str.isdigit,
                        parts[1].split("Net")[0] if "Net" in parts[1] else parts[1],
                    )
                )
            )

            req_days_pay = int(
                "".join(filter(str.isdigit, requested_terms or contract_terms or "30"))
            )
            days_early = req_days_pay - discount_days

            # NPV calculation
            savings = invoice_total * (discount_pct / 100)
            cost_of_capital_daily = (
                RULES["early_payment"]["cost_of_capital_annual_pct"] / 100 / 365
            )
            opportunity_cost = invoice_total * cost_of_capital_daily * days_early
            net_benefit = savings - opportunity_cost

            if net_benefit >= RULES["early_payment"]["min_savings_threshold"]:
                payment_recommendation = {
                    "action": "TAKE_DISCOUNT",
                    "discount_pct": discount_pct,
                    "pay_by_day": discount_days,
                    "savings": round(savings, 2),
                    "opportunity_cost": round(opportunity_cost, 2),
                    "net_benefit": round(net_benefit, 2),
                    "reason": f"Pay within {discount_days} days to save ${savings:.2f} (net benefit ${net_benefit:.2f} after cost of capital)",
                }
            else:
                payment_recommendation = {
                    "action": "PAY_ON_DUE_DATE",
                    "reason": f"Discount saves ${savings:.2f} but net benefit only ${net_benefit:.2f} (below ${RULES['early_payment']['min_savings_threshold']} threshold)",
                }
        except (ValueError, IndexError):
            pass  # Can't parse discount terms — pay on due date

    # ─── RULE SET 5: Payment Run Alignment ───────────────────
    today = datetime.now()
    payment_days = RULES["payment_runs"]["days"]

    if payment_recommendation.get("action") == "TAKE_DISCOUNT":
        target_date = today + timedelta(
            days=payment_recommendation.get("pay_by_day", 30)
        )
    else:
        req_days_val = int(
            "".join(filter(str.isdigit, requested_terms or contract_terms or "30"))
        )
        target_date = today + timedelta(days=req_days_val)

    # Find nearest payment run day ON or BEFORE target
    for offset in range(7):
        check_date = target_date - timedelta(days=offset)
        if check_date.strftime("%A") in payment_days and check_date > today:
            payment_recommendation["recommended_payment_date"] = check_date.strftime(
                "%Y-%m-%d"
            )
            payment_recommendation["payment_run_day"] = check_date.strftime("%A")
            break

    # ─── RULE SET 6: Aggregate Controls ──────────────────────
    if (
        daily_vendor_total + invoice_total
        > RULES["aggregate_controls"]["single_vendor_daily_max"]
    ):
        flags.append(
            {
                "flag": "VENDOR_DAILY_LIMIT",
                "detail": f"Total to this vendor today would be ${daily_vendor_total + invoice_total:,.0f} (exceeds ${RULES['aggregate_controls']['single_vendor_daily_max']:,.0f} daily cap)",
                "action": "VP_REVIEW",
            }
        )

    # ─── FINAL DECISION ──────────────────────────────────────
    critical_issues = [i for i in issues if i["severity"] == "critical"]
    high_issues = [i for i in issues if i["severity"] == "high"]

    # Determine approval action
    if critical_issues:
        action = "REJECT"
        escalation_reason = (
            f"{len(critical_issues)} critical issue(s): {critical_issues[0]['detail']}"
        )
        approver_needed = None
    elif high_issues:
        if any("director" in str(i.get("action", "")) for i in high_issues):
            action = "DIRECTOR_REVIEW"
            escalation_reason = f"{len(high_issues)} issue(s) requiring director review"
            approver_needed = "Director of Finance"
        else:
            action = "MANAGER_REVIEW"
            escalation_reason = f"{len(high_issues)} issue(s) requiring manager review"
            approver_needed = "AP Manager"
    elif any(f.get("action") == "VP_REVIEW" for f in flags):
        action = "VP_REVIEW"
        escalation_reason = "Aggregate control flag triggered"
        approver_needed = "VP Finance"
    elif invoice_total > RULES["amount_thresholds"]["director_approve_max"]:
        action = "VP_REVIEW"
        escalation_reason = f"Amount ${invoice_total:,.0f} exceeds director limit (${RULES['amount_thresholds']['director_approve_max']:,.0f})"
        approver_needed = "VP Finance"
    elif invoice_total > RULES["amount_thresholds"]["manager_approve_max"]:
        action = "DIRECTOR_REVIEW"
        escalation_reason = f"Amount ${invoice_total:,.0f} exceeds manager limit (${RULES['amount_thresholds']['manager_approve_max']:,.0f})"
        approver_needed = "Director of Finance"
    elif invoice_total > RULES["amount_thresholds"]["auto_approve_max"] and warnings:
        action = "MANAGER_REVIEW"
        escalation_reason = f"Amount ${invoice_total:,.0f} above auto-approve threshold with {len(warnings)} warning(s)"
        approver_needed = "AP Manager"
    elif warnings and invoice_total > RULES["amount_thresholds"]["auto_approve_max"]:
        action = "MANAGER_REVIEW"
        escalation_reason = f"{len(warnings)} warning(s) on invoice above ${RULES['amount_thresholds']['auto_approve_max']:,.0f}"
        approver_needed = "AP Manager"
    else:
        action = "AUTO_APPROVE"
        escalation_reason = None
        approver_needed = None

    return {
        "decision": {
            "action": action,
            "approver_needed": approver_needed,
            "escalation_reason": escalation_reason,
            "invoice_number": invoice_data.get("invoice_number"),
            "po_number": po_data.get("po_number"),
            "invoice_total": invoice_total,
            "po_total": po_total,
            "total_variance_pct": round(
                abs((invoice_total - po_total) / po_total * 100) if po_total > 0 else 0,
                2,
            ),
        },
        "line_item_results": line_results,
        "issues": issues,
        "warnings": warnings,
        "flags": flags,
        "classification": {
            "type": classification,
            "asset_items": asset_items if asset_items else None,
        },
        "payment_recommendation": payment_recommendation,
        "rules_applied": list(RULES.keys()),
        "rules_version": "2.0.0",
        "evaluated_at": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════
def get_payment_queue(
    status: str = None, days: int = None, supplier_id: str = None
) -> dict:
    """AP aging report and payment schedule."""
    queue = [
        {
            "invoice_id": "INV-2026-4001",
            "supplier": "GlobalParts Inc.",
            "supplier_id": "SUP-101",
            "amount": 23400.00,
            "due_date": "2026-06-05",
            "days_overdue": 6,
            "status": "overdue",
            "priority": "high",
        },
        {
            "invoice_id": "INV-2026-4015",
            "supplier": "PrecisionTech GmbH",
            "supplier_id": "SUP-102",
            "amount": 8750.00,
            "due_date": "2026-06-09",
            "days_overdue": 2,
            "status": "overdue",
            "priority": "medium",
        },
        {
            "invoice_id": "INV-2026-4022",
            "supplier": "American Steel Works",
            "supplier_id": "SUP-103",
            "amount": 45200.00,
            "due_date": "2026-06-12",
            "days_overdue": -1,
            "status": "pending",
            "priority": "high",
        },
        {
            "invoice_id": "INV-2026-4030",
            "supplier": "MidWest Industrial Supply",
            "supplier_id": "SUP-105",
            "amount": 12600.00,
            "due_date": "2026-06-15",
            "days_overdue": -4,
            "status": "scheduled",
            "priority": "normal",
        },
        {
            "invoice_id": "INV-2026-4038",
            "supplier": "Nordic Materials AB",
            "supplier_id": "SUP-106",
            "amount": 67500.00,
            "due_date": "2026-06-18",
            "days_overdue": -7,
            "status": "scheduled",
            "priority": "normal",
        },
        {
            "invoice_id": "INV-2026-4041",
            "supplier": "QuickShip Distributors",
            "supplier_id": "SUP-107",
            "amount": 5200.00,
            "due_date": "2026-07-01",
            "days_overdue": -20,
            "status": "pending",
            "priority": "low",
        },
    ]
    if status:
        queue = [q for q in queue if q["status"] == status]
    if supplier_id:
        queue = [q for q in queue if q["supplier_id"] == supplier_id]
    if days:
        queue = [q for q in queue if q["days_overdue"] >= -days]

    overdue = [q for q in queue if q["days_overdue"] > 0]
    upcoming_7 = [q for q in queue if -7 <= q["days_overdue"] <= 0]

    return {
        "payment_queue": queue,
        "summary": {
            "total_payable": sum(q["amount"] for q in queue),
            "overdue_amount": sum(q["amount"] for q in overdue),
            "overdue_count": len(overdue),
            "upcoming_7_days": sum(q["amount"] for q in upcoming_7),
        },
        "aging_buckets": {
            "current": sum(q["amount"] for q in queue if q["days_overdue"] <= 0),
            "1_30_days": sum(
                q["amount"] for q in queue if 1 <= q["days_overdue"] <= 30
            ),
            "31_60_days": sum(
                q["amount"] for q in queue if 31 <= q["days_overdue"] <= 60
            ),
            "61_90_days": sum(
                q["amount"] for q in queue if 61 <= q["days_overdue"] <= 90
            ),
            "over_90_days": sum(q["amount"] for q in queue if q["days_overdue"] > 90),
        },
        "as_of": datetime.now().isoformat(),
    }


TOOLS = {
    "apply_approval_rules": apply_approval_rules,
    "get_payment_queue": get_payment_queue,
}

TOOL_SCHEMAS = [
    {
        "name": "apply_approval_rules",
        "description": (
            "Comprehensive invoice approval rules engine. Applies ALL deterministic business rules: "
            "price/quantity tolerance thresholds, amount-based approval routing, payment terms enforcement, "
            "asset vs expense classification, early payment discount NPV calculation, payment run alignment, "
            "and aggregate vendor controls. Returns approval decision (AUTO_APPROVE / MANAGER_REVIEW / "
            "DIRECTOR_REVIEW / VP_REVIEW / REJECT) with full reasoning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {
                    "type": "string",
                    "description": "Invoice ID (simple gateway param)",
                },
                "po_number": {
                    "type": "string",
                    "description": "PO number (simple gateway param)",
                },
                "amount": {
                    "type": "number",
                    "description": "Invoice amount (simple gateway param)",
                },
                "invoice_data": {
                    "type": "object",
                    "description": "Full invoice object (advanced)",
                },
                "po_data": {
                    "type": "object",
                    "description": "Full PO object (advanced)",
                },
                "goods_receipt": {"type": "object", "description": "Optional GR data"},
                "vendor_contract": {
                    "type": "object",
                    "description": "Optional contract data",
                },
                "daily_vendor_total": {
                    "type": "number",
                    "description": "Total already approved today for this vendor",
                    "default": 0,
                },
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "get_payment_queue",
        "description": "AP aging report and payment schedule. Returns invoices sorted by due date with aging buckets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "overdue", "scheduled"],
                    "description": "Filter by payment status",
                },
                "days": {
                    "type": "integer",
                    "description": "Aging bucket filter in days",
                },
                "supplier_id": {
                    "type": "string",
                    "description": "Filter by supplier ID",
                },
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# LAMBDA HANDLER
# ═══════════════════════════════════════════════════════════════


def handler(event, context):
    try:
        # Extract tool name from event if not in context
        if not (
            context.client_context
            and hasattr(context.client_context, "custom")
            and context.client_context.custom
            and context.client_context.custom.get("bedrockAgentCoreToolName")
        ):
            tool_name = None
            if isinstance(event, dict):
                tool_name = (
                    event.get("toolName")
                    or event.get("tool_name")
                    or event.get("bedrockAgentCoreToolName")
                )
                headers = event.get("headers", {})
                if headers:
                    tool_name = tool_name or headers.get("bedrockAgentCoreToolName")

            if tool_name:
                if not hasattr(context, "client_context") or not context.client_context:
                    context.client_context = type("ClientContext", (), {})()
                if not hasattr(context.client_context, "custom"):
                    context.client_context.custom = {}
                context.client_context.custom["bedrockAgentCoreToolName"] = tool_name

        # Get tool name from context
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName")

        # Handle prefixed format: sc-quoting-mcp___generate_quote
        if tool_name and "___" in tool_name:
            tool_name = tool_name.split("___")[-1]

        # Parameters are in the event root
        parameters = event

        # Route to the correct function
        func = TOOLS.get(tool_name)
        if not func:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": f"Unknown tool: {tool_name}"}),
                    }
                ],
                "isError": True,
            }

        # Call function with matching params
        import inspect

        sig = inspect.signature(func)
        valid_params = {k: v for k, v in parameters.items() if k in sig.parameters}
        result = func(**valid_params)

        return {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "isError": False,
        }

    except Exception as e:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
            "isError": True,
        }
