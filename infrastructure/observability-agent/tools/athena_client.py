"""
Minimal Athena query helper for the Quick observability MCP.

Runs a SQL statement against the observability Glue database in a dedicated
Athena workgroup, polls to completion, and returns rows as dicts. Used by the
deep-analysis MCP tools that query the data lake (historical, cross-table,
per-user) rather than live CloudWatch APIs.
"""

from __future__ import annotations

import os
from threading import Event

import boto3

_athena = boto3.client("athena")
_IDLE = Event()

ATHENA_DATABASE = os.environ.get("ATHENA_DATABASE", "quick_observability")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "quick-observability")
# Hard cap so a single tool call cannot run away.
MAX_WAIT_SECONDS = int(os.environ.get("ATHENA_MAX_WAIT_SECONDS", "45"))


def _wait(seconds: float) -> None:
    _IDLE.wait(timeout=seconds)


def run_query(sql: str, max_rows: int = 1000) -> dict:
    """Execute `sql` and return {status, columns, rows} or {status, error}."""
    try:
        start = _athena.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": ATHENA_DATABASE},
            WorkGroup=ATHENA_WORKGROUP,
        )
        qid = start["QueryExecutionId"]

        waited = 0.0
        delay = 0.5
        while True:
            status = _athena.get_query_execution(QueryExecutionId=qid)[
                "QueryExecution"
            ]["Status"]
            state = status["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                reason = status.get("StateChangeReason", "Query failed")
                return {"status": "failed", "error": reason}
            if waited >= MAX_WAIT_SECONDS:
                return {
                    "status": "timeout",
                    "error": f"Query exceeded {MAX_WAIT_SECONDS}s",
                }
            _wait(delay)
            waited += delay
            delay = min(delay * 1.5, 3.0)

        return _collect_rows(qid, max_rows)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}


def _collect_rows(qid: str, max_rows: int) -> dict:
    columns: list[str] = []
    rows: list[dict] = []
    next_token = None
    first_page = True

    while len(rows) < max_rows:
        params = {"QueryExecutionId": qid, "MaxResults": 1000}
        if next_token:
            params["NextToken"] = next_token
        result = _athena.get_query_results(**params)

        meta = result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
        if not columns:
            columns = [c["Label"] for c in meta]

        data_rows = result["ResultSet"]["Rows"]
        # The first row of the first page is the header.
        if first_page and data_rows:
            data_rows = data_rows[1:]
            first_page = False

        for row in data_rows:
            values = [field.get("VarCharValue") for field in row["Data"]]
            rows.append(dict(zip(columns, values, strict=False)))
            if len(rows) >= max_rows:
                break

        next_token = result.get("NextToken")
        if not next_token:
            break

    return {"status": "success", "columns": columns, "rows": rows}
