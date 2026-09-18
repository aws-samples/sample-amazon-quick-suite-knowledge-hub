import json
from datetime import datetime
from shared_data import EMPLOYEES_DB, TIMESHEETS, _resp, _get_emp

def lambda_handler(event, context):
    eid, err = _get_emp(event)
    if err: return err
    entry = {
        "timesheet_id": f"TS{len(TIMESHEETS) + 1:04d}",
        "employee_id": eid,
        "week_ending": event.get("week_ending", ""),
        "hours_worked": float(event.get("hours_worked", 0)),
        "status": "submitted",
        "submitted_at": datetime.now().isoformat()
    }
    TIMESHEETS.append(entry)
    return _resp(200, {"success": True, "message": f"Timesheet {entry['timesheet_id']} submitted", "timesheet": entry})
