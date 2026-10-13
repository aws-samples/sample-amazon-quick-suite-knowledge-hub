"""
CloudWatch metrics poller for Amazon Quick observability.

The AWS/QuickSight metrics carry no per-user dimension and CloudWatch only
retains them for 15 months with coarsening resolution, so to build historical
QuickSight dashboards we snapshot every metric on a schedule into the data
lake as `metric_snapshots` rows (newline-delimited JSON).

The poller discovers the live metrics with `list_metrics` (so it picks up new
dashboards, datasets, connectors, automations, etc. automatically) and then
pulls datapoints with `get_metric_data`, one query per (metric, dimension-set)
using the statistic the Quick docs call out as most useful for that metric.
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

cloudwatch = boto3.client("cloudwatch")
s3 = boto3.client("s3")

DATA_BUCKET = os.environ["DATA_BUCKET"]
TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "metric_snapshots")
NAMESPACE = "AWS/QuickSight"
# Snapshot window; overlaps the schedule so gaps don't open between runs.
LOOKBACK_MINUTES = int(os.environ.get("LOOKBACK_MINUTES", "75"))
PERIOD_SECONDS = int(os.environ.get("PERIOD_SECONDS", "300"))

# metric name -> (metric_group, statistic, unit) from the AWS/QuickSight docs.
# statistic is the "most useful" statistic the docs recommend for the metric.
METRIC_CATALOG: dict[str, tuple[str, str, str]] = {
    # dashboard
    "DashboardViewCount": ("dashboard", "Sum", "Count"),
    "DashboardViewLoadTime": ("dashboard", "Average", "Milliseconds"),
    # ingestion
    "IngestionErrorCount": ("ingestion", "Sum", "Count"),
    "IngestionInvocationCount": ("ingestion", "Sum", "Count"),
    "IngestionRowCount": ("ingestion", "Sum", "Count"),
    "IngestionLatency": ("ingestion", "Average", "Seconds"),
    # visual
    "VisualLoadTime": ("visual", "Average", "Milliseconds"),
    "VisualLoadErrorCount": ("visual", "Sum", "Count"),
    # unstructured dataset / knowledge base
    "QuickIndexDocumentCount": ("kb", "Average", "Count"),
    "QuickIndexExtractedTextSize": ("kb", "Average", "Bytes"),
    "QuickIndexPurchasedInMB": ("kb", "Average", "Megabytes"),
    "QuickIndexCapacityConsumedRawFileSizeInGB": ("kb", "Average", "Gigabytes"),
    "QuickIndexCapacityRawFileSizeLimitInGB": ("kb", "Average", "Gigabytes"),
    "DocumentsCrawled": ("kb", "Sum", "Count"),
    "DocumentsIndexed": ("kb", "Sum", "Count"),
    "DocumentsDeleted": ("kb", "Sum", "Count"),
    "DocumentsModified": ("kb", "Sum", "Count"),
    "DocumentsFailedToIndex": ("kb", "Sum", "Count"),
    "ExtractedTextSize": ("kb", "Sum", "Megabytes"),
    # connector
    "ActionInvocationCount": ("connector", "Sum", "Count"),
    "ActionInvocationError": ("connector", "Sum", "Count"),
    # automation
    "TotalAutomationRunCount": ("automation", "Sum", "Count"),
    "SuccessfulAutomationRunCount": ("automation", "Sum", "Count"),
    "FailedAutomationRunCount": ("automation", "Sum", "Count"),
    "StoppedAutomationRunCount": ("automation", "Sum", "Count"),
    "AutomationRunDuration": ("automation", "Average", "Seconds"),
    # SPICE capacity (aggregate only, no dimensions)
    "SPICECapacityLimitInMB": ("spice", "Maximum", "Megabytes"),
    "SPICECapacityConsumedInMB": ("spice", "Maximum", "Megabytes"),
}

# CloudWatch dimension name -> metric_snapshots column name.
DIMENSION_COLUMN: dict[str, str] = {
    "DashboardId": "dashboard_id",
    "DatasetId": "dataset_id",
    "SheetId": "sheet_id",
    "VisualId": "visual_id",
    "KnowledgeBaseId": "knowledge_base_id",
    "QuickInstanceId": "quick_instance_id",
    "ActionConnectorId": "action_connector_id",
    "ActionConnectorType": "action_connector_type",
    "ActionId": "action_id",
    "AutomationId": "automation_id",
    "InvokeErrorCode": "invoke_error_code",
}

DIMENSION_COLUMNS = list(DIMENSION_COLUMN.values())


def _epoch_s(dt: datetime) -> int:
    """Epoch SECONDS, matching the unit Quick vended logs use for timestamps."""
    return int(dt.timestamp())


def _discover_metric_instances() -> list[dict]:
    """List every live (metric_name, dimensions) pair in AWS/QuickSight."""
    instances: list[dict] = []
    paginator = cloudwatch.get_paginator("list_metrics")
    for page in paginator.paginate(Namespace=NAMESPACE):
        for metric in page.get("Metrics", []):
            name = metric["MetricName"]
            if name not in METRIC_CATALOG:
                continue
            instances.append(
                {
                    "name": name,
                    "dimensions": metric.get("Dimensions", []),
                }
            )
    return instances


def _build_queries(instances: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Build get_metric_data queries; return queries and an id->meta map."""
    queries: list[dict] = []
    meta: dict[str, dict] = {}
    for idx, inst in enumerate(instances):
        name = inst["name"]
        group, statistic, unit = METRIC_CATALOG[name]
        qid = f"m{idx}"
        queries.append(
            {
                "Id": qid,
                "MetricStat": {
                    "Metric": {
                        "Namespace": NAMESPACE,
                        "MetricName": name,
                        "Dimensions": inst["dimensions"],
                    },
                    "Period": PERIOD_SECONDS,
                    "Stat": statistic,
                },
                "ReturnData": True,
            }
        )
        meta[qid] = {
            "name": name,
            "group": group,
            "statistic": statistic,
            "unit": unit,
            "dimensions": inst["dimensions"],
        }
    return queries, meta


def _row_for(meta: dict, timestamp: datetime, value: float) -> dict:
    row = dict.fromkeys(DIMENSION_COLUMNS)
    row.update(
        {
            "snapshot_timestamp": _epoch_s(timestamp),
            "metric_group": meta["group"],
            "metric_name": meta["name"],
            "statistic": meta["statistic"],
            "value": value,
            "unit": meta["unit"],
        }
    )
    for dim in meta["dimensions"]:
        col = DIMENSION_COLUMN.get(dim["Name"])
        if col:
            row[col] = dim["Value"]
    return row


def _fetch_rows(start_time: datetime, end_time: datetime) -> list[dict]:
    instances = _discover_metric_instances()
    logger.info("Discovered %d metric instances", len(instances))
    if not instances:
        return []

    rows: list[dict] = []
    # get_metric_data accepts up to 500 queries per call.
    for batch_start in range(0, len(instances), 500):
        batch = instances[batch_start : batch_start + 500]
        queries, meta = _build_queries(batch)

        next_token = None
        while True:
            params = {
                "MetricDataQueries": queries,
                "StartTime": start_time,
                "EndTime": end_time,
                "ScanBy": "TimestampAscending",
            }
            if next_token:
                params["NextToken"] = next_token
            resp = cloudwatch.get_metric_data(**params)

            for result in resp.get("MetricDataResults", []):
                qmeta = meta.get(result["Id"])
                if not qmeta:
                    continue
                for ts, val in zip(
                    result.get("Timestamps", []),
                    result.get("Values", []),
                    strict=False,
                ):
                    rows.append(_row_for(qmeta, ts, val))

            next_token = resp.get("NextToken")
            if not next_token:
                break
    return rows


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

    rows = _fetch_rows(start_time, now)
    logger.info("Collected %d metric datapoints", len(rows))

    if not rows:
        return {"datapoints_written": 0, "key": None}

    key = _write_ndjson(rows, now)
    logger.info("Wrote %d datapoints to s3://%s/%s", len(rows), DATA_BUCKET, key)
    return {"datapoints_written": len(rows), "key": key}
