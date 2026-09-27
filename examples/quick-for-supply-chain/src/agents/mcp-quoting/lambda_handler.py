"""
Quoting MCP — Lambda Handler for AgentCore Gateway
═══════════════════════════════════════════════════════════════
Applies pricing RULES (tier discounts, volume discounts, margin floors)
to product data provided by the caller (from the database via Amazon Quick).

Tools:
- generate_quote: Build a quote for a customer request
- get_pricing_rules: Retrieve pricing policies, discount tiers, margin rules
- validate_quote: Check a draft quote against business rules

Architecture Principle:
  DATA (products, history) → passed as parameters from orchestrator
  RULES (pricing tiers, volume discounts) → defined here
"""

import json
import random
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════════════════════
# BUSINESS RULES (owned by this MCP)
# ═══════════════════════════════════════════════════════════════

PRICING_TIERS = {
    "Enterprise": {
        "base_discount_pct": 15,
        "volume_multiplier": 1.2,
        "payment_terms": "Net 60",
        "expedite_available": True,
    },
    "Premium": {
        "base_discount_pct": 10,
        "volume_multiplier": 1.1,
        "payment_terms": "Net 45",
        "expedite_available": True,
    },
    "Standard": {
        "base_discount_pct": 5,
        "volume_multiplier": 1.0,
        "payment_terms": "Net 30",
        "expedite_available": False,
    },
}

VOLUME_DISCOUNTS = [
    {"min_qty": 1, "max_qty": 49, "discount_pct": 0},
    {"min_qty": 50, "max_qty": 199, "discount_pct": 5},
    {"min_qty": 200, "max_qty": 499, "discount_pct": 10},
    {"min_qty": 500, "max_qty": 999, "discount_pct": 15},
    {"min_qty": 1000, "max_qty": 99999, "discount_pct": 20},
]

POLICIES = {
    "max_combined_discount_pct": 35,
    "quote_validity_days": [15, 30, 45, 60],
    "approval_required_above": 50000,
    "auto_approve_below": 10000,
}


# ═══════════════════════════════════════════════════════════════
# TOOL FUNCTIONS
# ═══════════════════════════════════════════════════════════════


def generate_quote(
    account_id: str,
    customer_tier: str,
    items: list,
    products: list = None,
    valid_days: int = 30,
    notes: str = "",
) -> dict:
    """Generate a price quote. Products data is passed from the orchestrator (the database via Quick).

    Args:
        account_id: Customer account ID
        customer_tier: One of Enterprise, Premium, Standard
        items: List of {sku, quantity} to quote
        products: Product catalog data from the database via Amazon Quick (list of {sku, name, list_price, cost, min_margin_pct})
        valid_days: Quote validity in days (default 30)
        notes: Optional notes
    """
    if not products:
        return {
            "error": "products data required — orchestrator must query the database and pass product catalog"
        }

    tier = PRICING_TIERS.get(customer_tier, PRICING_TIERS["Standard"])
    line_items = []
    subtotal = 0
    total_cost = 0

    for item in items:
        product = next((p for p in products if p["sku"] == item["sku"]), None)
        if not product:
            line_items.append(
                {"sku": item["sku"], "error": "Product not found in provided catalog"}
            )
            continue

        qty = item["quantity"]
        list_price = product["list_price"]

        # Apply tier discount (RULE)
        tier_discount = tier["base_discount_pct"]

        # Apply volume discount (RULE)
        vol_discount = 0
        for vd in VOLUME_DISCOUNTS:
            if vd["min_qty"] <= qty <= vd["max_qty"]:
                vol_discount = vd["discount_pct"]
                break

        # Combined discount capped per policy
        total_discount_pct = min(
            tier_discount + vol_discount, POLICIES["max_combined_discount_pct"]
        )
        unit_price = list_price * (1 - total_discount_pct / 100)
        line_total = unit_price * qty
        line_cost = product["cost"] * qty
        margin_pct = ((unit_price - product["cost"]) / unit_price) * 100

        line_items.append(
            {
                "sku": item["sku"],
                "product_name": product["name"],
                "quantity": qty,
                "list_price": list_price,
                "tier_discount_pct": tier_discount,
                "volume_discount_pct": vol_discount,
                "total_discount_pct": total_discount_pct,
                "unit_price": round(unit_price, 2),
                "line_total": round(line_total, 2),
                "margin_pct": round(margin_pct, 1),
                "margin_ok": margin_pct >= product.get("min_margin_pct", 25),
            }
        )
        subtotal += line_total
        total_cost += line_cost

    overall_margin = ((subtotal - total_cost) / subtotal * 100) if subtotal > 0 else 0

    return {
        "quote": {
            "quote_id": f"QT-{datetime.now().strftime('%Y')}-{random.randint(100, 999)}",  # noqa: S311 - demo quote id, not security-sensitive
            "account_id": account_id,
            "customer_tier": customer_tier,
            "line_items": line_items,
            "subtotal": round(subtotal, 2),
            "tax_estimate": round(subtotal * 0.08, 2),
            "total": round(subtotal * 1.08, 2),
            "overall_margin_pct": round(overall_margin, 1),
            "payment_terms": tier["payment_terms"],
            "valid_until": (datetime.now() + timedelta(days=valid_days)).strftime(
                "%Y-%m-%d"
            ),
            "created_at": datetime.now().isoformat(),
            "notes": notes,
            "status": "draft",
        }
    }


def get_pricing_rules(category: str = None) -> dict:
    """Retrieve all pricing policies, tier discounts, volume tiers, and margin rules.
    This is RULES-ONLY — no data dependency."""
    data = {
        "customer_tiers": PRICING_TIERS,
        "volume_discounts": VOLUME_DISCOUNTS,
        "policies": POLICIES,
    }
    if category and category in data:
        return {category: data[category]}
    return data


def validate_quote(quote: dict) -> dict:
    """Validate a draft quote against business rules (margins, approval thresholds).
    Quote object is passed from the orchestrator."""
    if not quote:
        return {
            "valid": False,
            "issues": ["No quote provided"],
            "recommendation": "Provide quote object",
        }

    issues = []
    for item in quote.get("line_items", []):
        if not item.get("margin_ok", True):
            issues.append(f"{item['sku']}: margin {item['margin_pct']}% below minimum")

    total = quote.get("total", 0)
    needs_approval = total > POLICIES["approval_required_above"]
    auto_approved = total < POLICIES["auto_approve_below"]

    return {
        "valid": len(issues) == 0,
        "quote_id": quote.get("quote_id"),
        "issues": issues,
        "needs_manager_approval": needs_approval,
        "auto_approved": auto_approved,
        "recommendation": "Approved"
        if auto_approved and not issues
        else "Needs review"
        if issues
        else "Submit for approval",
    }


# ═══════════════════════════════════════════════════════════════
# TOOL REGISTRY & HANDLER
# ═══════════════════════════════════════════════════════════════

TOOLS = {
    "generate_quote": generate_quote,
    "get_pricing_rules": get_pricing_rules,
    "validate_quote": validate_quote,
}

TOOL_SCHEMAS = [
    {
        "name": "generate_quote",
        "description": "Generate a price quote for a customer. Applies tier discounts, volume discounts, and calculates margins. Requires products data from the database via Amazon Quick.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "customer_tier": {
                    "type": "string",
                    "enum": ["Enterprise", "Premium", "Standard"],
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                            "quantity": {"type": "integer"},
                        },
                    },
                },
                "products": {
                    "type": "array",
                    "description": "Product catalog from the database via Amazon Quick: [{sku, name, list_price, cost, min_margin_pct}]",
                    "items": {"type": "object"},
                },
                "valid_days": {"type": "integer", "default": 30},
                "notes": {"type": "string"},
            },
            "required": ["account_id", "customer_tier", "items", "products"],
        },
    },
    {
        "name": "get_pricing_rules",
        "description": "Retrieve pricing policies: tier discounts, volume tiers, approval thresholds. Pure rules — no data dependency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["customer_tiers", "volume_discounts", "policies"],
                }
            },
        },
    },
    {
        "name": "validate_quote",
        "description": "Validate a draft quote against business rules (margins, approval thresholds).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "quote": {
                    "type": "object",
                    "description": "The quote object to validate",
                }
            },
            "required": ["quote"],
        },
    },
]


def handler(event, context):
    try:
        # Extract tool name from AgentCore Gateway context
        tool_name = None
        if (
            hasattr(context, "client_context")
            and context.client_context
            and hasattr(context.client_context, "custom")
            and context.client_context.custom
        ):
            tool_name = context.client_context.custom.get("bedrockAgentCoreToolName")

        # Fallback: extract from event
        if not tool_name and isinstance(event, dict):
            tool_name = (
                event.get("toolName")
                or event.get("tool_name")
                or event.get("bedrockAgentCoreToolName")
            )
            headers = event.get("headers", {})
            if headers and not tool_name:
                tool_name = headers.get("bedrockAgentCoreToolName")

        # Handle prefixed format: sc-quoting-mcp___generate_quote
        if tool_name and "___" in tool_name:
            tool_name = tool_name.split("___")[-1]

        # Route to the correct function
        func = TOOLS.get(tool_name)
        if not func:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error": f"Unknown tool: {tool_name}",
                                "available": list(TOOLS.keys()),
                            }
                        ),
                    }
                ],
                "isError": True,
            }

        # Call function with matching params
        import inspect

        sig = inspect.signature(func)
        parameters = event if isinstance(event, dict) else {}
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
