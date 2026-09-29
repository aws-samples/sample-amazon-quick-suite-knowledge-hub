"""migrate_flows — migrate flows (matched by NAME) and remap embedded ARNs.

Flows are matched by name because CreateFlow assigns a new FlowId in the target.
Helpers here (_describe_flow, _remap_flow_definition, _extract_flow_linked_resources,
_find_target_flows_by_name) are also used by preview/restore in server.py.
"""

import json
import re

from backups import maybe_backup_before_update
from botocore.exceptions import ClientError
from common import (
    classify_error,
    copy_flow_permissions,
    format_error_for_report,
)


def _describe_flow(qs, account_id, flow_id):
    """Describe a flow. DescribeFlow REQUIRES PublishState; we always use the
    PUBLISHED version (that's what gets migrated/backed up). Returns the Flow
    dict."""
    resp = qs.describe_flow(
        AwsAccountId=account_id, FlowId=flow_id, PublishState="PUBLISHED"
    )
    return resp.get("Flow", {})


def _remap_flow_definition(definition, source_account_id, target_account_id):
    """Rewrite embedded SOURCE-account ARNs in a FlowDefinition to the TARGET
    account. Flow definitions can embed resource ARNs (e.g. space/...) that carry
    the source account id in the ARN's account field (":<acct>:"); left as-is the
    migrated flow would point at source-account resources. We replace only the
    account-id-in-ARN token (":<source>:" -> ":<target>:"), which is precise —
    the account id appears in the definition only inside ARNs. Returns a new
    definition (unchanged if source/target match or nothing to remap)."""
    if not definition or not source_account_id or not target_account_id:
        return definition
    if source_account_id == target_account_id:
        return definition
    raw = json.dumps(definition)
    remapped = raw.replace(f":{source_account_id}:", f":{target_account_id}:")
    if remapped == raw:
        return definition
    return json.loads(remapped)


def _extract_flow_linked_resources(definition):
    """Scan a FlowDefinition for embedded QuickSight resource ARNs and return a
    deduped list of {resource_type, resource_id, arn}. Lets the FE show a flow's
    linked resources (spaces/agents/connectors/KBs) and offer to migrate them,
    the same way agents/spaces expose their children."""
    if not definition:
        return []
    raw = json.dumps(definition, default=str)
    # arn:aws:quicksight:<region>:<account>:<type>/<id>
    pat = re.compile(
        r"arn:aws:quicksight:[a-z0-9-]*:\d+:([a-z0-9-]+)/([0-9a-zA-Z._=+-]+)"
    )
    seen = set()
    out = []
    for arn in re.findall(r"arn:aws:quicksight:[^\"\s]+", raw):
        m = pat.match(arn)
        if not m:
            continue
        rtype_seg, rid = m.group(1), m.group(2)
        key = (rtype_seg, rid)
        if key in seen:
            continue
        seen.add(key)
        # Normalize the ARN's resource segment to our resource_type vocabulary.
        rtype_map = {
            "space": "space",
            "agent": "agent",
            "action-connector": "connector",
            "knowledge-base": "knowledge_base",
            "flow": "flow",
        }
        out.append(
            {
                "resource_type": rtype_map.get(rtype_seg, rtype_seg),
                "resource_id": rid,
                "arn": arn,
            }
        )
    return out


def _find_target_flows_by_name(target_qs, target_account_id, name):
    """Return the list of target FlowIds whose name matches (case-insensitive).

    Flows are matched by NAME (not id) because CreateFlow mints a new FlowId in
    the target — the source id can never be reused there.
    """
    want = (name or "").strip().lower()
    matches = []
    token = None
    while True:
        kwargs = {"AwsAccountId": target_account_id}
        if token:
            kwargs["NextToken"] = token
        resp = target_qs.list_flows(**kwargs)
        for s in resp.get("FlowSummaryList", []) or []:
            if (s.get("Name", "") or "").strip().lower() == want:
                fid = s.get("FlowId")
                if fid:
                    matches.append(fid)
        token = resp.get("NextToken")
        if not token:
            break
    return matches


def _migrate_flows(
    source_qs,
    target_qs,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    flow_ids,
    report,
):
    """Migrate flows. Unlike the other types, flows are matched by NAME because
    CreateFlow does not accept a client-supplied FlowId (the target assigns a
    new one). If a target flow with the same name exists it is updated (after a
    pre-update backup); otherwise a new flow is created. Duplicate names in the
    target are treated as ambiguous and skipped (no mutation)."""
    for flow_id in flow_ids:
        try:
            flow = _describe_flow(source_qs, source_account_id, flow_id)
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_flow({flow_id})", e)
            )
            report["migrated"]["flows"].append(
                {
                    "flow_id": flow_id,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        name = flow.get("Name", flow_id)
        definition = flow.get("FlowDefinition")
        description = flow.get("Description")
        if not definition:
            report["migrated"]["flows"].append(
                {
                    "flow_id": flow_id,
                    "name": name,
                    "status": "FAILED: source flow has no FlowDefinition to copy",
                }
            )
            continue
        # Rewrite any embedded source-account ARNs (e.g. space/...) to the target
        # account so the migrated flow references target resources, not source.
        definition = _remap_flow_definition(
            definition, source_account_id, target_account_id
        )

        # Match by name in the target.
        try:
            existing = _find_target_flows_by_name(target_qs, target_account_id, name)
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"list_flows(match {name!r})", e)
            )
            report["migrated"]["flows"].append(
                {
                    "flow_id": flow_id,
                    "name": name,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        if len(existing) > 1:
            report["migrated"]["flows"].append(
                {
                    "flow_id": flow_id,
                    "name": name,
                    "status": (
                        f"SKIPPED: {len(existing)} target flows already named "
                        f"{name!r} — ambiguous, not modified"
                    ),
                }
            )
            continue

        target_flow_id = None
        if len(existing) == 1:
            # UPDATE the existing target flow (back it up first).
            target_flow_id = existing[0]
            if not maybe_backup_before_update(
                target_qs,
                backup_s3,
                target_account_id,
                "flow",
                target_flow_id,
                name,
                report,
            ):
                report["migrated"]["flows"].append(
                    {
                        "flow_id": flow_id,
                        "target_flow_id": target_flow_id,
                        "name": name,
                        "status": "FAILED: backup before update failed (not modified)",
                    }
                )
                continue
            try:
                upd = {
                    "AwsAccountId": target_account_id,
                    "FlowId": target_flow_id,
                    "Name": name,
                    "FlowDefinition": definition,
                }
                if description:
                    upd["Description"] = description
                target_qs.update_flow(**upd)
                status = "UPDATED"
            except ClientError as e:
                report["errors"].append(
                    format_error_for_report(f"update_flow({target_flow_id})", e)
                )
                status = f"FAILED: {classify_error(e)['user_message']}"
        else:
            # CREATE a new target flow (target assigns the id).
            try:
                params = {
                    "AwsAccountId": target_account_id,
                    "Name": name,
                    "FlowDefinition": definition,
                }
                if description:
                    params["Description"] = description
                resp = target_qs.create_flow(**params)
                target_flow_id = resp.get("FlowId")
                status = "CREATED"
            except ClientError as e:
                report["errors"].append(
                    format_error_for_report(f"create_flow({name})", e)
                )
                status = f"FAILED: {classify_error(e)['user_message']}"

        if "FAILED" not in status and target_flow_id:
            copy_flow_permissions(
                source_qs,
                target_qs,
                source_account_id,
                target_account_id,
                region,
                flow_id,
                target_flow_id,
                report,
            )

        report["migrated"]["flows"].append(
            {
                "flow_id": flow_id,
                "target_flow_id": target_flow_id,
                "name": name,
                "status": status,
                "matched_by": "name",
            }
        )
