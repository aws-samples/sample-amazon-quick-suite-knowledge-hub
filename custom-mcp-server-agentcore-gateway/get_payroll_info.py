import json
from shared_data import EMPLOYEES_DB, _resp, _get_emp

def lambda_handler(event, context):
    eid, err = _get_emp(event)
    if err: return err
    emp = EMPLOYEES_DB[eid]
    return _resp(200, {
        "employee_id": eid, "name": emp["name"], "position": emp["position"],
        "department": emp["department"], "salary": emp["salary"],
        "pay_frequency": emp["pay_frequency"], "annual_compensation": emp["salary"]
    })
