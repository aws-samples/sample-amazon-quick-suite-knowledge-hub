"""
CloudFormation custom-resource handler (Provider framework) that wires the
account's QuickSuite CHAT_LOGS vended-log delivery source to OUR Firehose
delivery-destination, idempotently, using an ADOPT-not-OWN model.

ResourceProperties (from the stack):
  FirehoseStreamArn      : ARN of our CfnDeliveryStream (never hardcoded).
  DestinationName        : name for our delivery-destination
                           (e.g. "quick-legalhold-mcp-fh-destination").
  ChatLogsSourceName     : optional explicit source name override; if empty we
                           auto-detect the CHAT_LOGS source for this account.
  QuickSightResourceArn  : the QuickSight account resource arn used when we must
                           ENABLE chat logging (the "source not found" branch).
  ManageSourceLifecycle  : "true"/"false". When "false" (default) we NEVER delete
                           the shared source on stack delete, even if we created it.

CREATE/UPDATE (idempotent, safe every deploy):
  1. Resolve the CHAT_LOGS delivery source (override -> auto-detect -> enable).
  2. Ensure our delivery-destination exists (put_delivery_destination is upsert).
  3. Ensure a delivery source->our-destination exists (adopt existing / create).

DELETE (stack destroy):
  - Delete ONLY our delivery (source->our-destination) and our delivery-destination.
  - NEVER delete the shared source or any other (CWL / other-S3) delivery.
  - Only delete a source we created if ManageSourceLifecycle == "true".
"""

import json

import boto3
from botocore.exceptions import ClientError

logs = boto3.client("logs")

CHAT_LOGS_TYPE = "CHAT_LOGS"


def _find_chat_logs_source(account: str):
    """Return (name, arn) of the CHAT_LOGS source for this account, or (None, None)."""
    paginator = logs.get_paginator("describe_delivery_sources")
    for page in paginator.paginate():
        for s in page.get("deliverySources", []):
            if s.get("logType") != CHAT_LOGS_TYPE:
                continue
            # Match a QuickSight resource in this account.
            arns = s.get("resourceArns", []) or []
            if (
                any((":" + account + ":") in a and ":quicksight:" in a for a in arns)
                or arns
            ):
                return s["name"], (arns[0] if arns else None)
    return None, None


def _enable_chat_logs_source(quicksight_resource_arn: str):
    """Enable Quick chat logging by creating the CHAT_LOGS delivery source.
    Only used when no source exists. Returns the created source name."""
    name = "quicksuite-chat-logs-source"
    logs.put_delivery_source(
        name=name,
        resourceArn=quicksight_resource_arn,
        logType=CHAT_LOGS_TYPE,
    )
    return name


def _ensure_destination(name: str, firehose_arn: str) -> str:
    """put_delivery_destination is an upsert; safe if it already exists.
    Returns the destination ARN."""
    resp = logs.put_delivery_destination(
        name=name,
        deliveryDestinationConfiguration={"destinationResourceArn": firehose_arn},
    )
    return resp["deliveryDestination"]["arn"]


def _find_our_delivery(source_name: str, dest_arn: str):
    """Return the delivery id linking source_name -> dest_arn, or None (adopt)."""
    paginator = logs.get_paginator("describe_deliveries")
    for page in paginator.paginate():
        for d in page.get("deliveries", []):
            if (
                d.get("deliverySourceName") == source_name
                and d.get("deliveryDestinationArn") == dest_arn
            ):
                return d["id"]
    return None


def _ensure_delivery(source_name: str, dest_arn: str) -> str:
    existing = _find_our_delivery(source_name, dest_arn)
    if existing:
        print(
            f"[wiring] adopting existing delivery {existing} ({source_name} -> {dest_arn})"
        )
        return existing
    resp = logs.create_delivery(
        deliverySourceName=source_name,
        deliveryDestinationArn=dest_arn,
    )
    did = resp["delivery"]["id"]
    print(f"[wiring] created delivery {did} ({source_name} -> {dest_arn})")
    return did


def _on_create_update(props, account):
    dest_name = props["DestinationName"]
    firehose_arn = props["FirehoseStreamArn"]
    override = (props.get("ChatLogsSourceName") or "").strip()
    qs_resource = (props.get("QuickSightResourceArn") or "").strip()

    created_source = False
    if override:
        source_name = override
        print(f"[wiring] using source override: {source_name}")
    else:
        source_name, _arn = _find_chat_logs_source(account)
        if not source_name:
            if not qs_resource:
                raise RuntimeError(
                    "No CHAT_LOGS delivery source found and no QuickSightResourceArn "
                    "provided to enable one; cannot proceed."
                )
            print("[wiring] no CHAT_LOGS source found; enabling chat logging")
            source_name = _enable_chat_logs_source(qs_resource)
            created_source = True

    dest_arn = _ensure_destination(dest_name, firehose_arn)
    delivery_id = _ensure_delivery(source_name, dest_arn)

    return {
        "SourceName": source_name,
        "DestinationName": dest_name,
        "DestinationArn": dest_arn,
        "DeliveryId": delivery_id,
        "CreatedSource": str(created_source).lower(),
    }


def _on_delete(props, physical_id, account):
    """Remove ONLY our delivery + our destination. Never the shared source
    (unless ManageSourceLifecycle == 'true' and we created it) and never any
    other delivery."""
    dest_name = props["DestinationName"]
    manage_source = (
        props.get("ManageSourceLifecycle") or "false"
    ).strip().lower() == "true"
    override = (props.get("ChatLogsSourceName") or "").strip()

    # Resolve source name to scope our delivery deletion.
    source_name = override or _find_chat_logs_source(account)[0]

    dest_arn = f"arn:aws:logs:{boto3.session.Session().region_name}:{account}:delivery-destination:{dest_name}"
    # Delete our delivery (source -> our destination) only.
    if source_name:
        did = _find_our_delivery(source_name, dest_arn)
        if did:
            try:
                logs.delete_delivery(id=did)
                print(f"[wiring] deleted our delivery {did}")
            except ClientError as exc:
                print(f"[wiring] delete_delivery {did} error (continuing): {exc}")

    # Delete our delivery-destination.
    try:
        logs.delete_delivery_destination(name=dest_name)
        print(f"[wiring] deleted delivery-destination {dest_name}")
    except ClientError as exc:
        print(
            f"[wiring] delete_delivery_destination {dest_name} error (continuing): {exc}"
        )

    # Only delete the source if we manage its lifecycle (default: leave it).
    if manage_source and source_name:
        print(
            f"[wiring] ManageSourceLifecycle=true: NOT implemented to delete shared "
            f"source {source_name} by default; leaving it to protect other deliveries."
        )


def handler(event, context):
    print(
        f"[wiring] event={json.dumps({k: event.get(k) for k in ('RequestType', 'LogicalResourceId', 'PhysicalResourceId')})}"
    )
    request_type = event["RequestType"]
    props = event.get("ResourceProperties", {}) or {}
    account = context.invoked_function_arn.split(":")[4]

    if request_type in ("Create", "Update"):
        data = _on_create_update(props, account)
        physical_id = f"chatlogs-fh-wiring-{data['DestinationName']}"
        return {"PhysicalResourceId": physical_id, "Data": data}

    if request_type == "Delete":
        physical_id = event.get("PhysicalResourceId", "chatlogs-fh-wiring")
        _on_delete(props, physical_id, account)
        return {"PhysicalResourceId": physical_id}

    raise RuntimeError(f"unexpected RequestType: {request_type}")
