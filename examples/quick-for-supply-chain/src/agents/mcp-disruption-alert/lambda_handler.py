"""
Disruption Alert MCP — Lambda Handler for AgentCore Gateway
═══════════════════════════════════════════════════════════════
Impact assessment, severity classification, mitigation recommendations,
and escalation chain for supply chain disruptions.

Called DIRECTLY by Quick (not via Agent) for real-time disruption triage.
Quick passes disruption context + affected data from the database via Amazon Quick/web search.

Tools:
- assess_disruption_impact: Calculate revenue/orders/customers at risk
- classify_severity: Apply severity rules (Level 1-4)
- recommend_mitigation: Match disruption to response playbook actions
- get_escalation_chain: Who to notify based on severity and region
"""

import json
from datetime import datetime

# ─── Severity Classification Rules ──────────────────────────
SEVERITY_RULES = {
    "level_4_critical": {
        "label": "Level 4 — Critical",
        "criteria": "Revenue at risk > $500K OR >5 Enterprise customers affected OR complete supply line cut",
        "response_time": "1 hour",
        "color": "red",
    },
    "level_3_high": {
        "label": "Level 3 — High",
        "criteria": "Revenue at risk $100K-$500K OR 2-5 Enterprise customers OR SLA breach imminent",
        "response_time": "4 hours",
        "color": "orange",
    },
    "level_2_moderate": {
        "label": "Level 2 — Moderate",
        "criteria": "Revenue at risk $25K-$100K OR Premium customers affected OR lead time extension >7 days",
        "response_time": "24 hours",
        "color": "yellow",
    },
    "level_1_low": {
        "label": "Level 1 — Low",
        "criteria": "Revenue at risk <$25K OR only Standard tier affected OR buffer stock covers gap",
        "response_time": "48 hours",
        "color": "green",
    },
}

# ─── Escalation Matrix ───────────────────────────────────────
ESCALATION_MATRIX = {
    "level_4_critical": [
        {"role": "VP Operations", "channel": "phone + email", "within_minutes": 0},
        {"role": "COO", "channel": "phone", "within_minutes": 15},
        {
            "role": "Affected Account Managers",
            "channel": "email + Teams",
            "within_minutes": 30,
        },
        {
            "role": "Customer Success (Enterprise)",
            "channel": "email",
            "within_minutes": 60,
        },
    ],
    "level_3_high": [
        {
            "role": "Supply Chain Manager",
            "channel": "email + Teams",
            "within_minutes": 0,
        },
        {"role": "VP Operations", "channel": "email", "within_minutes": 60},
        {
            "role": "Affected Account Managers",
            "channel": "Teams",
            "within_minutes": 120,
        },
    ],
    "level_2_moderate": [
        {"role": "Supply Chain Manager", "channel": "email", "within_minutes": 0},
        {"role": "Logistics Coordinator", "channel": "Teams", "within_minutes": 60},
    ],
    "level_1_low": [
        {"role": "Logistics Coordinator", "channel": "Teams", "within_minutes": 0},
    ],
}

# ─── Mitigation Playbook Templates ──────────────────────────
MITIGATION_PLAYBOOKS = {
    "port_closure": {
        "immediate_actions": [
            "Identify all in-transit shipments routed through affected port",
            "Contact carriers for diversion options and cost estimates",
            "Assess inventory buffer — how many days of supply remain?",
        ],
        "short_term": [
            "Reroute via alternate port (typical alternatives by region provided)",
            "Expedite existing POs from unaffected suppliers",
            "Evaluate air freight for critical/high-priority orders",
        ],
        "long_term": [
            "Dual-source strategy for affected product categories",
            "Increase safety stock for items with single-port dependency",
            "Review carrier contracts for force majeure flexibility",
        ],
        "typical_duration_days": "3-14 (weather) / 30-90 (labor/geopolitical)",
    },
    "supplier_failure": {
        "immediate_actions": [
            "Determine affected SKUs and open POs with this supplier",
            "Check approved vendor list for qualified alternates",
            "Assess current inventory vs demand for affected items",
        ],
        "short_term": [
            "Emergency PO to approved alternate supplier (expedited)",
            "Contact distributor network for spot-buy availability",
            "Notify affected customers of potential delay with revised ETA",
        ],
        "long_term": [
            "Qualify additional suppliers for affected categories",
            "Implement dual-source policy for critical components",
            "Increase safety stock for single-sourced items",
        ],
        "typical_duration_days": "14-56",
    },
    "natural_disaster": {
        "immediate_actions": [
            "Confirm status of own facilities in affected region",
            "Identify all suppliers and shipments in affected geography",
            "Activate business continuity plan",
        ],
        "short_term": [
            "Reroute shipments away from affected area",
            "Source from alternate geography suppliers",
            "Prioritize fulfillment of critical/Enterprise orders",
        ],
        "long_term": [
            "Geographic diversification of supply base",
            "Review insurance coverage for supply chain interruption",
            "Update disaster recovery plans with lessons learned",
        ],
        "typical_duration_days": "7-90",
    },
    "labor_strike": {
        "immediate_actions": [
            "Assess which ports/facilities are affected",
            "Quantify shipments in queue and expected delays",
            "Evaluate pre-positioning inventory at alternate locations",
        ],
        "short_term": [
            "Divert inbound shipments to unaffected ports",
            "Front-load orders before strike deadline (if advance warning)",
            "Switch to air freight for high-priority items",
        ],
        "long_term": [
            "Develop multi-port strategy to reduce single-point dependency",
            "Build strategic buffer inventory before known negotiation windows",
            "Establish relationships with non-union carrier alternatives",
        ],
        "typical_duration_days": "7-60",
    },
    "cybersecurity": {
        "immediate_actions": [
            "Isolate affected systems — confirm own systems not compromised",
            "Switch to manual processes for critical operations",
            "Verify data integrity of recent orders and shipments",
        ],
        "short_term": [
            "Activate backup communication channels with suppliers",
            "Manual order processing until systems restored",
            "Communicate delays to affected customers",
        ],
        "long_term": [
            "Security audit of supply chain digital touchpoints",
            "Implement redundant communication channels",
            "Vendor cybersecurity assessment program",
        ],
        "typical_duration_days": "3-30",
    },
}


# ─── Tool Implementations ────────────────────────────────────


def assess_disruption_impact(
    disruption: dict = None,
    affected_orders: list = None,
    affected_shipments: list = None,
    inventory_data: list = None,
    customer_accounts: list = None,
    **kwargs,
) -> dict:
    """
    Calculate the business impact of a disruption.
    Quick passes context (from the database via Amazon Quick + Salesforce + web search).
    MCP applies impact scoring logic.

    Tolerant of PARTIAL input: if the caller only passes the disruption alert fields
    (e.g. severity/region/type/affected_routes) and omits orders/shipments/inventory/
    customers, this still returns a valid (partial) assessment instead of erroring —
    so the orchestrator does not loop retrying the same call. Flat disruption fields
    passed at the top level (alert_id, type, region, severity, …) are folded into the
    disruption record via **kwargs.
    """
    # Accept a nested 'disruption' dict OR flat fields passed at the top level.
    disruption = dict(disruption or {})
    for k in (
        "alert_id",
        "type",
        "region",
        "severity",
        "title",
        "affected_routes",
        "affected_products",
        "est_duration_days",
        "estimated_duration_days",
    ):
        if k in kwargs and k not in disruption:
            disruption[k] = kwargs[k]
    affected_orders = affected_orders or []
    affected_shipments = affected_shipments or []
    inventory_data = inventory_data or []
    customer_accounts = customer_accounts or []

    # Revenue at risk
    total_revenue_at_risk = sum(o.get("total_amount", 0) for o in affected_orders)

    # Customer impact
    affected_account_ids = {o.get("account_id") for o in affected_orders}
    affected_customers = [
        c for c in customer_accounts if c.get("account_id") in affected_account_ids
    ]
    enterprise_affected = [
        c for c in affected_customers if c.get("tier") == "Enterprise"
    ]
    premium_affected = [c for c in affected_customers if c.get("tier") == "Premium"]

    # Order breakdown
    critical_orders = [
        o for o in affected_orders if o.get("priority") in ["critical", "high"]
    ]

    # Shipment impact
    in_transit_affected = [
        s for s in affected_shipments if s.get("status") == "in_transit"
    ]
    delayed_shipments = len(in_transit_affected)

    # Inventory coverage
    days_of_supply = {}
    for inv in inventory_data:
        sku = inv.get("sku")
        qty = inv.get("quantity_on_hand", inv.get("current_quantity", 0))
        # Rough daily consumption estimate
        daily_usage = qty / 30 if qty > 0 else 1
        days_of_supply[sku] = round(qty / max(daily_usage, 1), 1)

    critical_items = [sku for sku, days in days_of_supply.items() if days < 7]

    return {
        "impact_assessment": {
            "revenue_at_risk": total_revenue_at_risk,
            "orders_affected": len(affected_orders),
            "critical_high_priority_orders": len(critical_orders),
            "shipments_delayed": delayed_shipments,
            "customers_affected": {
                "total": len(affected_customers),
                "enterprise": len(enterprise_affected),
                "premium": len(premium_affected),
                "enterprise_names": [c.get("name") for c in enterprise_affected],
            },
            "inventory_risk": {
                "items_below_7_days_supply": critical_items,
                "days_of_supply_by_sku": days_of_supply,
            },
            "disruption_type": disruption.get("type", "unknown"),
            "estimated_duration_days": disruption.get(
                "estimated_duration_days", disruption.get("est_duration_days", 0)
            ),
            "assessed_at": datetime.now().isoformat(),
        }
    }


def classify_severity(impact_assessment: dict) -> dict:
    """
    Apply severity classification rules (Level 1-4) based on impact data.
    """
    impact = impact_assessment.get("impact_assessment", impact_assessment)

    revenue = impact.get("revenue_at_risk", 0)
    enterprise_count = impact.get("customers_affected", {}).get("enterprise", 0)
    critical_orders = impact.get("critical_high_priority_orders", 0)
    items_at_risk = len(
        impact.get("inventory_risk", {}).get("items_below_7_days_supply", [])
    )

    # Classification logic
    if revenue > 500000 or enterprise_count > 5 or items_at_risk > 5:
        severity = "level_4_critical"
    elif revenue > 100000 or enterprise_count >= 2 or critical_orders > 3:
        severity = "level_3_high"
    elif revenue > 25000 or enterprise_count >= 1 or items_at_risk > 2:
        severity = "level_2_moderate"
    else:
        severity = "level_1_low"

    rule = SEVERITY_RULES[severity]

    return {
        "severity": {
            "level": severity,
            "label": rule["label"],
            "response_time_required": rule["response_time"],
            "color": rule["color"],
            "criteria_matched": rule["criteria"],
            "scoring_factors": {
                "revenue_at_risk": revenue,
                "enterprise_customers": enterprise_count,
                "critical_orders": critical_orders,
                "items_below_safety_stock": items_at_risk,
            },
        }
    }


def recommend_mitigation(
    disruption_id: str = None,
    disruption_type: str = None,
    impact_assessment: dict = None,
    available_alternatives: list = None,
) -> dict:
    """
    Match disruption type to response playbook and recommend specific actions.
    Accepts either disruption_id (gateway schema) or disruption_type directly.
    """
    # If only disruption_id provided, require disruption_type from the orchestrator
    if disruption_id and not disruption_type:
        disruption_type = "natural_disaster"  # Default — orchestrator should pass disruption_type from the database via Amazon Quick
    if not disruption_type:
        disruption_type = "natural_disaster"

    playbook = MITIGATION_PLAYBOOKS.get(
        disruption_type, MITIGATION_PLAYBOOKS.get("natural_disaster")
    )

    # Customize recommendations based on available alternatives
    specific_recommendations = []
    if available_alternatives:
        for alt in available_alternatives[:3]:
            specific_recommendations.append(
                f"Switch to {alt.get('name', 'alternate supplier')} "
                f"(lead time: {alt.get('lead_time_days', '?')} days, "
                f"quality: {alt.get('quality_score', '?')}/100)"
            )

    return {
        "mitigation_plan": {
            "disruption_type": disruption_type,
            "playbook": playbook,
            "specific_alternatives": specific_recommendations,
            "estimated_recovery_days": playbook.get("typical_duration_days"),
            "recommended_priority": "immediate_actions",
            "generated_at": datetime.now().isoformat(),
        }
    }


def get_escalation_chain(severity_level: str, region: str = "NA") -> dict:
    """
    Return the notification/escalation chain for the given severity level.
    """
    chain = ESCALATION_MATRIX.get(severity_level, ESCALATION_MATRIX["level_1_low"])

    return {
        "escalation": {
            "severity": severity_level,
            "region": region,
            "chain": chain,
            "total_stakeholders": len(chain),
            "first_notification": chain[0] if chain else None,
            "full_notification_within_minutes": chain[-1]["within_minutes"]
            if chain
            else 0,
        }
    }


# ─── Tool Registry ───────────────────────────────────────────


# Gateway-facing tools (names match gateway target schema)
def get_active_disruptions(
    disruptions: list = None, severity: str = None, region: str = None
) -> dict:
    """List current active supply chain disruptions with severity and impact.

    Args:
        disruptions: Active disruption data from the database via Amazon Quick (list of disruption objects).
                     Each must have: disruption_id, type, title, region, severity, status.
        severity: Optional filter by severity level
        region: Optional filter by region
    """
    if not disruptions:
        return {
            "error": "disruptions data required — orchestrator must query the database and pass active disruptions"
        }
    if severity:
        disruptions = [d for d in disruptions if d["severity"] == severity]
    if region:
        disruptions = [d for d in disruptions if d["region"] == region]
    return {
        "disruptions": disruptions,
        "total": len(disruptions),
        "as_of": datetime.now().isoformat(),
    }


def assess_impact(
    disruption_id: str,
    affected_orders: list = None,
    affected_shipments: list = None,
    inventory_data: list = None,
) -> dict:
    """Assess impact of a disruption on orders and suppliers.

    Args:
        disruption_id: The disruption to assess
        affected_orders: Orders impacted (from the database via Amazon Quick query filtered by disruption)
        affected_shipments: Shipments impacted
        inventory_data: Current inventory levels for affected SKUs
    """
    if not affected_orders:
        return {
            "error": "affected_orders data required — orchestrator must query the database for orders impacted by this disruption"
        }

    revenue_at_risk = sum(o.get("total_amount", 0) for o in affected_orders)
    enterprise_customers = len(
        [o for o in affected_orders if o.get("customer_tier") == "Enterprise"]
    )

    return {
        "impact_assessment": {
            "disruption_id": disruption_id,
            "revenue_at_risk": revenue_at_risk,
            "affected_orders": len(affected_orders),
            "affected_shipments": len(affected_shipments or []),
            "enterprise_customers": enterprise_customers,
            "days_of_supply_remaining": min(
                (
                    i.get("quantity_available", 0)
                    for i in (inventory_data or [{"quantity_available": 14}])
                ),
                default=14,
            ),
        },
        "assessed_at": datetime.now().isoformat(),
    }


TOOLS = {
    "get_active_disruptions": get_active_disruptions,
    "assess_impact": assess_impact,
    "assess_disruption_impact": assess_disruption_impact,
    "classify_severity": classify_severity,
    "recommend_mitigation": recommend_mitigation,
    "get_escalation_chain": get_escalation_chain,
}

TOOL_SCHEMAS = [
    {
        "name": "assess_disruption_impact",
        "description": "Calculate business impact of a disruption — revenue at risk, affected orders/customers/shipments, inventory coverage. Quick passes all context from the database via Amazon Quick + Salesforce.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "disruption": {
                    "type": "object",
                    "description": "Disruption details: {type, region, title, estimated_duration_days}",
                },
                "affected_orders": {
                    "type": "array",
                    "description": "Orders potentially impacted (from the database via Amazon Quick)",
                },
                "affected_shipments": {
                    "type": "array",
                    "description": "Shipments in transit through affected area",
                },
                "inventory_data": {
                    "type": "array",
                    "description": "Current stock levels for affected SKUs",
                },
                "customer_accounts": {
                    "type": "array",
                    "description": "Customer account details (tier, name) from Salesforce",
                },
            },
            "required": ["disruption"],
        },
    },
    {
        "name": "classify_severity",
        "description": "Apply severity rules (Level 1-4) based on impact assessment. Determines response time required and escalation level.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "impact_assessment": {
                    "type": "object",
                    "description": "Output from assess_disruption_impact",
                },
            },
            "required": ["impact_assessment"],
        },
    },
    {
        "name": "recommend_mitigation",
        "description": "Match disruption type to response playbook — returns immediate/short-term/long-term actions plus specific alternate supplier recommendations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "disruption_type": {
                    "type": "string",
                    "enum": [
                        "port_closure",
                        "supplier_failure",
                        "natural_disaster",
                        "labor_strike",
                        "cybersecurity",
                    ],
                },
                "impact_assessment": {"type": "object"},
                "available_alternatives": {
                    "type": "array",
                    "description": "Alternate suppliers/routes (from the database via Amazon Quick)",
                },
            },
            "required": ["disruption_type", "impact_assessment"],
        },
    },
    {
        "name": "get_escalation_chain",
        "description": "Who to notify and by when, based on severity level. Returns roles, channels, and timing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity_level": {
                    "type": "string",
                    "enum": [
                        "level_4_critical",
                        "level_3_high",
                        "level_2_moderate",
                        "level_1_low",
                    ],
                },
                "region": {"type": "string", "default": "NA"},
            },
            "required": ["severity_level"],
        },
    },
]


# ─── Lambda Handler ──────────────────────────────────────────


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

        # Call function with matching params.
        import inspect

        sig = inspect.signature(func)
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if accepts_kwargs:
            # Function takes **kwargs → pass ALL event fields through (extras folded).
            valid_params = dict(parameters)
        else:
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
