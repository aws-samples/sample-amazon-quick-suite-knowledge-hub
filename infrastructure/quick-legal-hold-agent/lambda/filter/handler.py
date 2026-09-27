"""
Firehose transform Lambda ("filter Lambda").

For each incoming CHAT_LOGS-shaped record, extract user_arn, look it up in the
DynamoDB held-user table, and return Firehose result:
  - "Ok"      -> keep the record (delivered to the S3 WORM bucket) when an item
                 exists for that user_arn with status == "active".
  - "Dropped" -> drop the record (never reaches S3) otherwise.

Records for users not on active hold must never reach S3.

Expected CHAT_LOGS-shaped record (JSON), tolerant of a couple of shapes:
  { "user_arn": "...", "prompt": "...", "response": "...", "timestamp": "...", ... }
or nested under "identity"/"principal".
"""
import base64
import json
import os
import re

import boto3

USERS_TABLE = os.environ["USERS_TABLE"]

_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(USERS_TABLE)


def _s3_safe(token: str, fallback: str) -> str:
    """Sanitize a token for use as a plain S3 path segment: keep only
    S3-friendly chars, no spaces/slashes. No hash suffix appended."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", (token or "").strip()).strip("-")
    return safe or fallback


def _extract_user_arn(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    # Direct field first.
    for key in ("user_arn", "userArn", "principalArn", "arn"):
        if payload.get(key):
            return str(payload[key])
    # Common nested locations for Quick CHAT_LOGS.
    for parent in ("identity", "principal", "user", "actor"):
        node = payload.get(parent)
        if isinstance(node, dict):
            for key in ("user_arn", "userArn", "arn"):
                if node.get(key):
                    return str(node[key])
    return None


def _active_hold_item(user_arn: str):
    """Return the DynamoDB hold item for user_arn if it exists AND is active,
    else None. The stored partition key equals the CHAT_LOGS user_arn
    (QuickSight ARN); we only strip a stray trailing slash. Fail closed (None)
    on lookup error."""
    key = (user_arn or "").rstrip("/")
    try:
        resp = _table.get_item(Key={"user_arn": key})
    except Exception as exc:  # noqa: BLE001 - fail closed (drop) on lookup error
        print(f"[filter] DynamoDB get_item error for {key}: {exc}")
        return None
    item = resp.get("Item")
    if item and item.get("status") == "active":
        return item
    return None


def _partition_keys(item: dict, user_arn: str) -> dict:
    """Group-first partition: grp = group_name (or '_direct' for a directly-held
    user), usr = bare clean UserName. Plain folder names, no hash, no key=value.
    Layout: chat-logs/<grp>/<usr>/YYYY/MM/DD/."""
    grp_raw = item.get("group_name")
    grp = _s3_safe(grp_raw, "_direct") if grp_raw else "_direct"
    # Prefer the directory UserName captured on the hold item; fall back to the
    # trailing token of the matched ARN. This equals the CHAT_LOGS
    # user/default/<UserName> token.
    usr_raw = item.get("idc_user_name") or item.get("subject_key") \
        or user_arn.rstrip("/").split("/")[-1]
    usr = _s3_safe(usr_raw, "user")
    return {"grp": grp, "usr": usr}


def handler(event, context):
    records = event.get("records", [])
    output = []
    kept = 0
    dropped = 0

    for rec in records:
        record_id = rec["recordId"]
        result = "Dropped"
        data = rec.get("data", "")
        matched_item = None
        matched_arn = None

        try:
            raw = base64.b64decode(data)
            payload = json.loads(raw.decode("utf-8"))
            user_arn = _extract_user_arn(payload)
            if user_arn:
                item = _active_hold_item(user_arn)
                if item:
                    result = "Ok"
                    matched_item = item
                    matched_arn = user_arn
        except Exception as exc:  # noqa: BLE001 - malformed record -> drop
            print(f"[filter] error processing record {record_id}: {exc}")
            result = "ProcessingFailed"

        if result == "Ok":
            kept += 1
            # Group-first dynamic partitioning: grp + usr. Only KEPT records
            # carry partition keys. Writes under chat-logs/<grp>/<usr>/Y/M/D/.
            pk = _partition_keys(matched_item, matched_arn)
            print(f"[filter] keep grp={pk['grp']} usr={pk['usr']}")
            output.append({
                "recordId": record_id,
                "result": "Ok",
                "data": data,  # original data unchanged
                "metadata": {"partitionKeys": pk},
            })
        else:
            dropped += 1
            output.append({"recordId": record_id, "result": result, "data": data})

    print(f"[filter] processed={len(records)} kept={kept} dropped={dropped}")
    return {"records": output}
