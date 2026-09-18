"""
HR Gateway Lambda Functions — One function per tool

Each function handles a single HR operation. AgentCore Gateway routes
tool invocations to the correct Lambda, sending only the input arguments.
"""

import json
from datetime import datetime

# ── Shared mock data ─────────────────────────────────────────────────

EMPLOYEES_DB = {
    "EMP001": {
        "name": "Alice Johnson", "department": "Engineering", "position": "Senior Engineer",
        "manager": "Carol Williams", "salary": 145000, "pay_frequency": "bi-weekly",
        "benefits": {"health": "Premium PPO", "dental": "Standard", "vision": "Standard",
                     "retirement_401k": "6% match", "pto_policy": "Unlimited"},
        "direct_reports": ["EMP003", "EMP004"]
    },
    "EMP002": {
        "name": "Bob Smith", "department": "HR", "position": "HR Manager",
        "manager": "Carol Williams", "salary": 120000, "pay_frequency": "bi-weekly",
        "benefits": {"health": "Standard HMO", "dental": "Standard", "vision": "Basic",
                     "retirement_401k": "4% match", "pto_policy": "20 days"},
        "direct_reports": []
    },
    "EMP003": {
        "name": "Diana Lee", "department": "Engineering", "position": "Software Engineer",
        "manager": "Alice Johnson", "salary": 110000, "pay_frequency": "bi-weekly",
        "benefits": {"health": "Standard HMO", "dental": "Standard", "vision": "Standard",
                     "retirement_401k": "4% match", "pto_policy": "Unlimited"},
        "direct_reports": []
    },
    "EMP004": {
        "name": "Evan Park", "department": "Engineering", "position": "Software Engineer",
        "manager": "Alice Johnson", "salary": 105000, "pay_frequency": "bi-weekly",
        "benefits": {"health": "Premium PPO", "dental": "Premium", "vision": "Standard",
                     "retirement_401k": "6% match", "pto_policy": "Unlimited"},
        "direct_reports": []
    }
}

TIMESHEETS = []

def _resp(status, body):
    return {"statusCode": status, "body": json.dumps(body, indent=2)}

def _get_emp(event):
    eid = event.get("employee_id", "")
    if eid not in EMPLOYEES_DB:
        return None, _resp(400, {"error": f"Employee {eid} not found"})
    return eid, None
