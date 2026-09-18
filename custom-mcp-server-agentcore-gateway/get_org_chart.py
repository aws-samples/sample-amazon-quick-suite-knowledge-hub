import json
from shared_data import EMPLOYEES_DB, _resp, _get_emp

def lambda_handler(event, context):
    eid, err = _get_emp(event)
    if err: return err
    emp = EMPLOYEES_DB[eid]
    direct_reports = []
    for rid in emp.get("direct_reports", []):
        if rid in EMPLOYEES_DB:
            r = EMPLOYEES_DB[rid]
            direct_reports.append({"employee_id": rid, "name": r["name"], "position": r["position"]})
    return _resp(200, {
        "employee_id": eid, "name": emp["name"], "position": emp["position"],
        "department": emp["department"], "manager": emp["manager"],
        "direct_reports": direct_reports
    })
