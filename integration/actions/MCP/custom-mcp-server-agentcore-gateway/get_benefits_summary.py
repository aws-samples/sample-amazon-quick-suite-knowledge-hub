from shared_data import EMPLOYEES_DB, _get_emp, _resp


def lambda_handler(event, context):
    eid, err = _get_emp(event)
    if err:
        return err
    emp = EMPLOYEES_DB[eid]
    return _resp(
        200, {"employee_id": eid, "name": emp["name"], "benefits": emp["benefits"]}
    )
