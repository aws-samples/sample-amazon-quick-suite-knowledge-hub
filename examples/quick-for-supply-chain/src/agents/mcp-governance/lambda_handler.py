"""
Governance MCP — Lambda Handler for AgentCore Gateway
═══════════════════════════════════════════════════════════
Pure business rules: budget authority, regulatory enforcement, policy catalog.

Tools:
- check_supplier_approval: Validate supplier against approval rules
- validate_budget_authority: Check spending authority for a role/amount
- check_regulatory_compliance: Verify regulatory requirements for a shipment
- get_policy_rules: Return governance policies (rules catalog)
"""
import json
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
# BUSINESS RULES & MOCK SUPPLIER DATA
# ═══════════════════════════════════════════════════════════════

BUDGET_AUTHORITY = {
    "Procurement Specialist": {"single_po_limit": 10000, "monthly_limit": 50000, "requires_approval_above": 5000},
    "Procurement Lead": {"single_po_limit": 50000, "monthly_limit": 200000, "requires_approval_above": 25000},
    "Supply Chain Manager": {"single_po_limit": 100000, "monthly_limit": 500000, "requires_approval_above": 75000},
    "VP Operations": {"single_po_limit": 500000, "monthly_limit": 2000000, "requires_approval_above": 250000},
    "COO": {"single_po_limit": 5000000, "monthly_limit": 10000000, "requires_approval_above": 1000000},
}

REGULATORY_REQUIREMENTS = {
    "export_control": {"applicable_to": ["international_shipment"], "required_docs": ["EAR classification", "Destination country check", "End-user verification"], "authority": "BIS / Commerce Dept"},
    "hazmat_shipping": {"applicable_to": ["hazardous_materials"], "required_docs": ["SDS (Safety Data Sheet)", "DOT packaging certification", "Carrier hazmat endorsement"], "authority": "DOT / IATA"},
    "product_safety": {"applicable_to": ["consumer_products"], "required_docs": ["UL certification", "CE marking (if EU)", "CPSC compliance"], "authority": "UL / CPSC / EU Commission"},
    "conflict_minerals": {"applicable_to": ["electronics", "metals"], "required_docs": ["CMRT", "Smelter list", "Due diligence report"], "authority": "SEC / Dodd-Frank"},
}

POLICIES = [
    {"id": "GOV-001", "name": "Sole Source Justification", "rule": "Any PO > $25,000 to a single supplier without competitive bid requires written justification and VP approval", "category": "procurement"},
    {"id": "GOV-002", "name": "Supplier Diversity", "rule": "Minimum 15% of annual spend must go to diverse/minority-owned suppliers", "category": "procurement"},
    {"id": "GOV-003", "name": "Three-Quote Rule", "rule": "Any non-contracted purchase > $10,000 requires minimum 3 competitive quotes", "category": "procurement"},
    {"id": "GOV-004", "name": "Segregation of Duties", "rule": "PO creator cannot be the PO approver. Requester cannot approve their own purchase.", "category": "compliance"},
    {"id": "GOV-005", "name": "Contract Expiry Alert", "rule": "Contracts within 60 days of expiry must trigger renewal workflow", "category": "compliance"},
    {"id": "GOV-006", "name": "Country Sanctions Check", "rule": "All international POs must pass OFAC/SDN screening before submission", "category": "regulatory"},
    {"id": "GOV-007", "name": "Environmental Compliance", "rule": "Packaging must meet EPA sustainability targets. RoHS compliance for all electronics.", "category": "regulatory"},
    {"id": "GOV-008", "name": "Maximum Payment Terms", "rule": "Payment terms > Net 60 require Treasury approval", "category": "finance"},
]

SUPPLIER_APPROVAL_RULES = {
    "max_po_value_conditional": 50000,
    "audit_overdue_block": True,
    "blocked_status_enforcement": True,
    "category_match_required": True,
}

# ═══════════════════════════════════════════════════════════════
# TOOLS
# ═══════════════════════════════════════════════════════════════

def check_supplier_approval(supplier_id: str = None, supplier_name: str = None, supplier_data: dict = None, category: str = None, po_value: float = None) -> dict:
    """
    Validate supplier against governance rules. Supplier data must be provided
    by the orchestrator (queried from the database via Amazon Quick SUPPLY_CHAIN.SCM.SUPPLIERS table).
    
    Args:
        supplier_id: Supplier ID for reference
        supplier_name: Supplier name for reference
        supplier_data: Full supplier record from the database via Amazon Quick (must include: approval_status, risk_tier, categories, next_audit_date)
        category: Product category for category-specific rules
        po_value: PO value for threshold checks
    """
    if not supplier_data:
        return {"error": "supplier_data required — orchestrator must query the database SUPPLIERS table and pass the supplier record"}

    issues = []
    status = supplier_data.get("approval_status", "").upper()
    sid = supplier_data.get("supplier_id", "unknown")
    name = supplier_data.get("name", sid)

    if status == "NOT_FOUND":
        return {"approved": False, "supplier": name, "reason": "Not on approved vendor list", "issues": [{"rule": "NOT_IN_AVL", "severity": "critical", "detail": f"Supplier '{name}' not found in Approved Vendor List"}], "action_required": "Submit supplier onboarding request"}

    # Rule: Blocked suppliers cannot be used
    if status == "BLOCKED":
        return {
            "approved": False,
            "supplier": name,
            "supplier_id": sid,
            "approval_status": status,
            "reason": "BLOCKED — compliance violation or sanctions concern",
            "issues": [{"rule": "BLOCKED_STATUS", "severity": "critical", "detail": "Supplier is blocked. Cannot proceed."}],
            "action_required": "Select alternative supplier",
        }

    # Rule: Category must be approved
    if category:
        approved_categories = supplier_data.get("categories", "").split(",")
        approved_categories = [c.strip() for c in approved_categories]
        if category not in approved_categories:
            issues.append({"rule": "CATEGORY_MISMATCH", "severity": "high", "detail": f"Not approved for category: {category}. Approved: {approved_categories}"})

    # Rule: Conditional supplier restrictions
    if status == "CONDITIONAL":
        if po_value and po_value > SUPPLIER_APPROVAL_RULES["max_po_value_conditional"]:
            issues.append({"rule": "CONDITIONAL_PO_LIMIT", "severity": "high", "detail": f"PO ${po_value:,.0f} exceeds ${SUPPLIER_APPROVAL_RULES['max_po_value_conditional']:,.0f} limit for conditional supplier"})
        issues.append({"rule": "CONDITIONAL_STATUS", "severity": "medium", "detail": "Conditional supplier — requires VP approval"})

    # Rule: Audit must be current
    next_audit = supplier_data.get("next_audit_date")
    if next_audit:
        try:
            audit_date = datetime.strptime(next_audit, "%Y-%m-%d")
            if audit_date < datetime.now() and SUPPLIER_APPROVAL_RULES["audit_overdue_block"]:
                issues.append({"rule": "AUDIT_OVERDUE", "severity": "high", "detail": f"Audit overdue (was due {next_audit}) — schedule immediately"})
        except ValueError:
            pass

    # Rule: Risk tier assessment
    risk_tier = supplier_data.get("risk_tier", "medium")
    if risk_tier == "high":
        issues.append({"rule": "HIGH_RISK_TIER", "severity": "medium", "detail": "High-risk tier — additional oversight required"})

    approved = status == "APPROVED" and not any(i["severity"] == "high" for i in issues)

    return {
        "approved": approved,
        "conditional": status == "CONDITIONAL",
        "supplier": name,
        "supplier_id": sid,
        "approval_status": status,
        "risk_tier": risk_tier,
        "issues": issues,
        "action_required": "VP approval needed" if issues else None,
    }


def validate_budget_authority(role: str = None, user_role: str = None, amount: float = 0, po_type: str = "standard") -> dict:
    """Check if a user role has spending authority for an amount."""
    user_role = role or user_role or "Procurement Specialist"
    authority = BUDGET_AUTHORITY.get(user_role)
    if not authority:
        return {"authorized": False, "reason": f"Role '{user_role}' not found in authority matrix", "available_roles": list(BUDGET_AUTHORITY.keys())}

    within_limit = amount <= authority["single_po_limit"]
    needs_approval = amount > authority["requires_approval_above"]

    roles = list(BUDGET_AUTHORITY.keys())
    idx = roles.index(user_role) if user_role in roles else 0
    next_approver = roles[idx + 1] if idx + 1 < len(roles) else "Board"

    return {
        "authorized": within_limit,
        "role": user_role,
        "requested_amount": amount,
        "single_po_limit": authority["single_po_limit"],
        "needs_higher_approval": not within_limit,
        "needs_any_approval": needs_approval,
        "next_approver_role": next_approver if needs_approval or not within_limit else None,
    }


def check_regulatory_compliance(supplier_name: str = None, order_id: str = None, shipment_type: str = None, destination_country: str = "US", product_categories: list = None) -> dict:
    """Check regulatory requirements for a shipment/order/supplier."""
    if not shipment_type:
        shipment_type = "international_shipment" if destination_country != "US" else "standard"
    applicable_regs = []
    issues = []

    sanctioned_countries = ["CN", "RU", "IR", "KP", "CU", "SY", "VE"]
    if destination_country != "US" and destination_country in sanctioned_countries:
        issues.append({"rule": "SANCTIONS", "severity": "critical", "detail": f"Destination {destination_country} is sanctioned/restricted — BLOCKED"})

    if destination_country != "US":
        applicable_regs.append(REGULATORY_REQUIREMENTS["export_control"])

    if shipment_type == "hazardous_materials":
        applicable_regs.append(REGULATORY_REQUIREMENTS["hazmat_shipping"])

    if product_categories:
        for cat in product_categories:
            if cat.lower() in ["electronics", "metals", "sensors"]:
                applicable_regs.append(REGULATORY_REQUIREMENTS["conflict_minerals"])
                break

    return {
        "compliant": len(issues) == 0,
        "issues": issues,
        "applicable_regulations": applicable_regs,
        "required_documentation": [doc for reg in applicable_regs for doc in reg["required_docs"]],
        "destination_country": destination_country,
    }


def get_policy_rules(category: str = None) -> dict:
    """Return governance policies (business rules catalog)."""
    policies = POLICIES
    if category:
        policies = [p for p in policies if p["category"] == category]
    return {"policies": policies, "total": len(policies), "categories": ["procurement", "compliance", "regulatory", "finance"]}


# ═══════════════════════════════════════════════════════════════
# TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════
TOOLS = {
    "check_supplier_approval": check_supplier_approval,
    "validate_budget_authority": validate_budget_authority,
    "check_regulatory_compliance": check_regulatory_compliance,
    "get_policy_rules": get_policy_rules,
}

TOOL_SCHEMAS = [
    {"name": "check_supplier_approval", "description": "Validate supplier against governance rules (approval status, audit dates, risk tier, category authorization). Accepts supplier_id or supplier_name strings for lookup.",
     "inputSchema": {"type": "object", "properties": {
         "supplier_id": {"type": "string", "description": "Supplier ID or name to look up (e.g. 'GlobalParts', 'SUP-001')"},
         "supplier_name": {"type": "string", "description": "Supplier name to search for"},
         "supplier_data": {"type": "object", "description": "Full supplier data dict (optional, overrides ID/name lookup)"},
         "category": {"type": "string", "description": "Product category being ordered"},
         "po_value": {"type": "number", "description": "PO amount (for conditional supplier limit check)"},
     }}},
    {"name": "validate_budget_authority", "description": "Check if a user role has spending authority for a given amount. Returns limit, whether approval is needed, and next approver.",
     "inputSchema": {"type": "object", "properties": {
         "user_role": {"type": "string", "enum": ["Procurement Specialist", "Procurement Lead", "Supply Chain Manager", "VP Operations", "COO"]},
         "amount": {"type": "number"},
         "po_type": {"type": "string", "default": "standard"},
     }, "required": ["user_role", "amount"]}},
    {"name": "check_regulatory_compliance", "description": "Check regulatory requirements (export control, hazmat, sanctions, conflict minerals) for a shipment.",
     "inputSchema": {"type": "object", "properties": {
         "shipment_type": {"type": "string"},
         "destination_country": {"type": "string", "default": "US"},
         "product_categories": {"type": "array", "items": {"type": "string"}},
     }, "required": ["shipment_type"]}},
    {"name": "get_policy_rules", "description": "Return active governance policies. Filter by category: procurement, compliance, regulatory, finance.",
     "inputSchema": {"type": "object", "properties": {
         "category": {"type": "string", "enum": ["procurement", "compliance", "regulatory", "finance"]},
     }}},
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
            return {"content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"})}], "isError": True}

        # Call function with matching params
        import inspect
        sig = inspect.signature(func)
        valid_params = {k: v for k, v in parameters.items() if k in sig.parameters}
        result = func(**valid_params)

        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}], "isError": False}

    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}], "isError": True}
