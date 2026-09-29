"""
resources — resource selection + target lookup for the Quick Resource Migrator.

resolve_resource_ids (id | name | all selection), value coercion, and the
target existence / describe / permission helpers used by preview and migrate.
"""

import json
import re

from botocore.exceptions import ClientError
from common import (
    _MIGRATABLE_TYPES,
    _RESOURCE_DESCRIBE_KEYS,
    _RESOURCE_DESCRIBERS,
    _RESOURCE_LISTERS,
    _RESOURCE_PERMISSION_DESCRIBERS,
    _normalize_type,
    _paginate,
    _summary_id,
    classify_error,
)


def target_resource_exists(target_qs, target_account_id, rtype, resource_id):
    """Return (exists, error) for a resource id in the target account.

    The migrator reuses the source id in the target, so existence is a direct
    describe_* by that id. A ResourceNotFoundException means "does not exist"
    (exists=False, no error). Any other ClientError is surfaced as an error so
    the caller can decide (we do NOT silently treat it as absent, which could
    cause an unwanted create).
    """
    op_name, id_kwarg = _RESOURCE_DESCRIBERS[rtype]
    op = getattr(target_qs, op_name)
    try:
        op(**{"AwsAccountId": target_account_id, id_kwarg: resource_id})
        return True, None
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "NotFoundException"):
            return False, None
        return None, classify_error(e)["user_message"]


def describe_resource_permissions(qs, account_id, rtype, resource_id):
    """Return (permissions, error) for a resource's QuickSight permissions.

    permissions is a normalized list of {"principal": <arn>, "actions": [...]}
    (empty list if none). error is a user-facing string if the read failed
    (permissions is [] in that case). Read-only.
    """
    op_name, id_kwarg = _RESOURCE_PERMISSION_DESCRIBERS[rtype]
    op = getattr(qs, op_name)
    try:
        raw = op(**{"AwsAccountId": account_id, id_kwarg: resource_id}).get(
            "Permissions", []
        )
    except ClientError as e:
        return [], classify_error(e)["user_message"]
    normalized = [
        {
            "principal": p.get("Principal"),
            "actions": p.get("Actions", []),
        }
        for p in (raw or [])
    ]
    return normalized, None


def describe_target_resource(target_qs, target_account_id, rtype, resource_id):
    """Return (obj, exists, error) for a matched target resource.

    Only ever called for ids that already exist in the SOURCE — the target is
    never enumerated, and target-only assets are never fetched (migration scope
    is source→target). obj is a compact dict describing the target resource, or
    None when it does not exist (exists=False) or the lookup failed (error set).
    """
    op_name, id_kwarg, obj_key = _RESOURCE_DESCRIBE_KEYS[rtype]
    op = getattr(target_qs, op_name)
    # DescribeFlow requires PublishState; describe others take just the id.
    call_kwargs = {"AwsAccountId": target_account_id, id_kwarg: resource_id}
    if rtype == "flow":
        call_kwargs["PublishState"] = "PUBLISHED"
    try:
        raw = op(**call_kwargs)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "NotFoundException"):
            return None, False, None
        return None, None, classify_error(e)["user_message"]

    data = raw.get(obj_key, {}) or {}
    # Compact, FE-friendly shape (mirrors the source inventory fields).
    if rtype == "agent":
        obj = {
            "agent_id": data.get("AgentId", resource_id),
            "arn": data.get("Arn"),
            "name": data.get("Name"),
            "agent_status": data.get("AgentStatus"),
        }
    elif rtype == "connector":
        obj = {
            "connector_id": data.get("ActionConnectorId", resource_id),
            "arn": data.get("Arn"),
            "name": data.get("Name"),
            "type": data.get("Type", "UNKNOWN"),
            "status": data.get("Status"),
        }
    elif rtype == "knowledge_base":
        obj = {
            "knowledge_base_id": resource_id,
            "name": data.get("Name", resource_id),
            "type": data.get("Type", "UNKNOWN"),
            "status": data.get("Status", "UNKNOWN"),
        }
    else:  # space
        obj = {
            "space_id": resource_id,
            "name": data.get("name", resource_id),
            "description": data.get("description"),
        }
    return obj, True, None


_ID_PATTERN = re.compile(r"^[0-9a-zA-Z\-_=.+]+$")

# JSON keys a caller might wrap an id in, e.g. {"knowledge_base_id": "..."}.
_ID_KEYS = (
    "knowledge_base_id",
    "KnowledgeBaseId",
    "connector_id",
    "ActionConnectorId",
    "agent_id",
    "AgentId",
    "space_id",
    "spaceId",
    "resource_id",
    "id",
    "Id",
    "value",
)


def _extract_id_from_obj(obj):
    """Pull a bare id string out of a dict that wraps one under a known key."""
    for key in _ID_KEYS:
        if key in obj and isinstance(obj[key], str) and obj[key].strip():
            return obj[key].strip()
    return None


def _coerce_selection_value(value):
    """Normalize a search_by=id 'value' into a list of bare resource ids.

    A well-behaved caller passes a plain id (or comma-separated ids). Some
    callers instead pass a JSON object like {"knowledge_base_id": "<uuid>"},
    a JSON array, or a JSON-encoded string. Accept all of these, extract the
    bare id(s), and validate them against the QuickSight id pattern so a
    malformed selection fails with a clear message instead of being sent to
    the API verbatim (which surfaces as a confusing ValidationException).

    Returns (ids, error): ids is a list of validated id strings; error is a
    user-facing string or None.
    """
    raw = (value or "").strip()
    if not raw:
        return [], "search_by='id' requires a non-empty 'value'."

    candidates = []

    # Try to interpret the value as JSON (object, array, or quoted string).
    parsed = None
    if raw[0] in '[{"':
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None

    if isinstance(parsed, dict):
        extracted = _extract_id_from_obj(parsed)
        if not extracted:
            return [], (
                f"Could not find a resource id in the provided object {raw!r}. "
                f"Pass a bare id, or an object with one of: "
                f"knowledge_base_id, connector_id, agent_id, space_id, id."
            )
        candidates = [extracted]
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, str):
                candidates.append(item.strip())
            elif isinstance(item, dict):
                extracted = _extract_id_from_obj(item)
                if extracted:
                    candidates.append(extracted)
    elif isinstance(parsed, str):
        # JSON-encoded string, e.g. "\"abc-123\"" -> abc-123.
        candidates = [parsed.strip()]
    else:
        # Plain (non-JSON) input: allow a comma-separated list of ids.
        candidates = [part.strip() for part in raw.split(",")]

    candidates = [c for c in candidates if c]
    if not candidates:
        return [], f"No usable resource id found in 'value' {raw!r}."

    invalid = [c for c in candidates if not _ID_PATTERN.match(c)]
    if invalid:
        return [], (
            f"Invalid resource id(s) {invalid!r}: an id must match "
            f"[0-9a-zA-Z-_=.+]+ (no braces, quotes, or spaces). "
            f"Pass the bare resource id, not a wrapped/JSON value."
        )

    # De-duplicate while preserving order.
    seen = set()
    ids = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ids.append(c)
    return ids, None


def resolve_resource_ids(qs, account_id, resource_type, search_by, value):
    """Resolve a (resource_type, search_by, value) selection to concrete IDs.

    Args:
        qs: a QuickSight client (already assumed into the source account).
        account_id: source AWS account ID.
        resource_type: one of agent | connector | knowledge_base.
        search_by: one of id | name | all.
            - "all":  every resource of this type in the account.
            - "id":   the single resource whose id == value.
            - "name": every resource whose Name matches value (case-insensitive).
        value: the id or name to match; ignored when search_by == "all".

    Returns:
        (ids, error) — ids is a list of resource IDs (possibly empty); error is
        a user-facing string or None. Raises nothing for "not found" — that is
        surfaced via an empty list + error message.
    """
    rtype = _normalize_type(resource_type)
    if rtype not in _MIGRATABLE_TYPES:
        return [], (
            f"Invalid resource_type {resource_type!r}. "
            f"Valid: agent, connector, knowledge_base."
        )
    mode = (search_by or "all").strip().lower()
    if mode not in {"id", "name", "all"}:
        return [], f"Invalid search_by {search_by!r}. Valid: id, name, all."
    if mode in {"id", "name"} and not (value or "").strip():
        return [], f"search_by={mode!r} requires a non-empty 'value'."

    op_name, result_key, id_field, name_field = _RESOURCE_LISTERS[rtype]

    # search_by=id does not need a full listing — normalize the value into
    # bare id(s) (tolerating JSON/wrapped/comma-separated input) and validate
    # them, then let the downstream describe/create surface a not-found error
    # if an id is well-formed but bogus.
    if mode == "id":
        return _coerce_selection_value(value)

    try:
        summaries = _paginate(qs, op_name, result_key, AwsAccountId=account_id)
    except ClientError as e:
        return [], classify_error(e)["user_message"]

    if mode == "all":
        ids = [i for i in (_summary_id(s, id_field) for s in summaries) if i]
        return ids, None

    # mode == "name": case-insensitive exact match on the Name field.
    want = value.strip().lower()
    ids = [
        _summary_id(s, id_field)
        for s in summaries
        if (s.get(name_field, "") or "").strip().lower() == want
    ]
    ids = [i for i in ids if i]
    if not ids:
        return [], f"No {rtype} found with name {value!r} in account {account_id}."
    return ids, None
