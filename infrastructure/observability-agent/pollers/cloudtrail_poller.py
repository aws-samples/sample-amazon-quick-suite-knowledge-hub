"""
CloudTrail poller for Amazon Quick observability.

CloudTrail has no vended-log delivery to S3 for Quick events, and
LookupEvents only serves a rolling 90-day window, so this Lambda runs on a
schedule, pulls recent `quicksight.amazonaws.com` events, flattens each to the
`cloudtrail_events` table schema, and appends them to the observability data
lake as newline-delimited JSON.

Both management events (who created/updated/deleted which asset) and
InvokeAction connector data events are captured. Deduplication across
overlapping poll windows is handled at query time (events carry a unique
`event_id`); the Lambda intentionally overlaps windows so no event is missed
between runs.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cloudtrail = boto3.client("cloudtrail")
s3 = boto3.client("s3")

DATA_BUCKET = os.environ["DATA_BUCKET"]
TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "cloudtrail_events")
# Overlap the look-back a little beyond the schedule so no event slips
# between consecutive runs; duplicates are removed at query time by event_id.
LOOKBACK_MINUTES = int(os.environ.get("LOOKBACK_MINUTES", "75"))


def _epoch_s(dt: datetime) -> int:
    """Epoch SECONDS, matching the unit Quick vended logs use for timestamps."""
    return int(dt.timestamp())


def _flatten_event(record: dict) -> dict:
    """Map one CloudTrail event (CloudTrailEvent JSON) to the table schema."""
    detail = json.loads(record.get("CloudTrailEvent", "{}"))

    user_identity = detail.get("userIdentity", {}) or {}
    on_behalf_of = user_identity.get("onBehalfOf", {}) or {}
    request_params = detail.get("requestParameters") or {}
    response_elems = detail.get("responseElements") or {}

    resources = record.get("Resources") or []
    resource_type = resources[0].get("ResourceType") if resources else None
    resource_name = resources[0].get("ResourceName") if resources else None

    invoke_output = (response_elems.get("invokeActionOutput") or {}).get(
        "httpInvokeActionOutput"
    ) or {}

    event_time = detail.get("eventTime")
    if event_time:
        ts = _epoch_s(
            datetime.strptime(event_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        )
    else:
        ts = _epoch_s(record.get("EventTime") or datetime.now(UTC))

    return {
        "event_time": ts,
        "event_id": detail.get("eventID") or record.get("EventId"),
        "event_name": detail.get("eventName") or record.get("EventName"),
        "event_source": detail.get("eventSource"),
        "event_category": detail.get("eventCategory", "Management"),
        "aws_region": detail.get("awsRegion"),
        "user_name": record.get("Username") or user_identity.get("userName"),
        "user_identity_type": user_identity.get("type"),
        "user_identity_arn": user_identity.get("arn"),
        "on_behalf_of_user_id": on_behalf_of.get("userId"),
        "source_ip_address": detail.get("sourceIPAddress"),
        "user_agent": detail.get("userAgent"),
        "read_only": detail.get("readOnly"),
        "error_code": detail.get("errorCode"),
        "error_message": detail.get("errorMessage"),
        "resource_type": resource_type,
        "resource_name": resource_name,
        "action_connector_id": request_params.get("actionConnectorId"),
        "action_id": request_params.get("actionId"),
        "end_user_arn": request_params.get("endUserArn"),
        "invoke_action_status": response_elems.get("invokeActionStatus"),
        "invoke_http_status_code": invoke_output.get("httpStatusCode"),
    }


def _lookup_all(start_time: datetime, end_time: datetime) -> list[dict]:
    events: list[dict] = []
    paginator = cloudtrail.get_paginator("lookup_events")
    for page in paginator.paginate(
        LookupAttributes=[
            {
                "AttributeKey": "EventSource",
                "AttributeValue": "quicksight.amazonaws.com",
            }
        ],
        StartTime=start_time,
        EndTime=end_time,
    ):
        for record in page.get("Events", []):
            try:
                events.append(_flatten_event(record))
            except Exception:  # noqa: BLE001
                logger.exception("Failed to flatten CloudTrail event")
    return events


def _write_ndjson(rows: list[dict], now: datetime) -> str:
    body = "\n".join(json.dumps(r, default=str) for r in rows) + "\n"
    key = (
        f"{TABLE_PREFIX}/"
        f"year={now:%Y}/month={now:%m}/day={now:%d}/"
        f"{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex}.json"
    )
    s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body.encode("utf-8"))
    return key


def handler(event, context):
    now = datetime.now(UTC)
    start_time = now - timedelta(minutes=LOOKBACK_MINUTES)

    rows = _lookup_all(start_time, now)
    logger.info("Fetched %d CloudTrail events", len(rows))

    if not rows:
        return {"events_written": 0, "key": None}

    key = _write_ndjson(rows, now)
    logger.info("Wrote %d events to s3://%s/%s", len(rows), DATA_BUCKET, key)
    return {"events_written": len(rows), "key": key}
