"""
backups — pre-update backups, backup catalog, and restore support.

Writes encrypted per-asset snapshots to the backup bucket (in the runner
account), lists/reads them, captures dependency envelopes, and takes the
pre-update / post-migration snapshots the migrators and restore rely on.
"""

import json
import re
from datetime import UTC, datetime

from botocore.exceptions import ClientError
from common import (
    _RESOURCE_DESCRIBE_KEYS,
    classify_error,
    logger,
)


class BackupError(Exception):
    """Raised when a pre-update backup cannot be written (blocks the update)."""


def _safe_folder_component(name: str) -> str:
    """Make an asset name safe for use as a single S3 key path segment.

    Removes characters awkward in S3 keys — notably '/' (which would create
    phantom subfolders) and control chars — and collapses whitespace. Keeps it
    human-readable (spaces and parentheses are fine in S3 keys).
    """
    name = (name or "").strip()
    # Drop control characters and forward slashes / backslashes.
    cleaned = "".join(c for c in name if c not in "/\\" and (ord(c) >= 32)).strip()
    return cleaned or "unnamed"


def _next_backup_version(s3, bucket, prefix, ext):
    """Return the next version integer N for versioned backup objects under the
    asset folder. Matches both the current filename form '<asset_id>_v<N>.<ext>'
    and the legacy 'json_v<N>.<ext>' so version numbers keep incrementing across
    the rename. Returns max(N)+1 (1 if none).
    """
    highest = 0
    token = None
    # Any '<something>_v<N>.<ext>' at the end of the key (covers <id>_v.. + json_v..).
    pat = re.compile(rf"_v(\d+)\.{re.escape(ext)}$")
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            m = pat.search(obj["Key"])
            if m:
                highest = max(highest, int(m.group(1)))
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return highest + 1


def backup_target_resource(s3, bucket, rtype, resource_id, name, described_obj):
    """Write a pre-update snapshot of a target resource to the backup bucket.

    Layout:  "<Asset Name> (asset_id)/json_v<N>.json"  (N auto-increments).
    Returns the s3 key written. Raises BackupError if the backup cannot be
    written — callers MUST treat that as "do not proceed with the update".
    """
    if not bucket:
        raise BackupError(
            "no backup bucket is configured (BACKUP_BUCKET is empty); refusing to "
            "update without a backup"
        )
    folder = f"{_safe_folder_component(name or resource_id)} ({resource_id})"
    prefix = f"{folder}/"
    try:
        version = _next_backup_version(s3, bucket, prefix, "json")
        # Filename carries the asset id: "<asset_id>_v<N>.json".
        key = f"{prefix}{resource_id}_v{version}.json"
        body = json.dumps(described_obj, indent=2, default=str).encode("utf-8")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        logger.info(f"  ✓ Backup written: s3://{bucket}/{key}")
        return key
    except ClientError as e:
        raise BackupError(classify_error(e)["user_message"]) from e


BACKUP_SCHEMA_VERSION = 2  # v2 = dependency-capturing envelope


# ── Backup catalog + restore helpers ────────────────────────────────
_BACKUP_FOLDER_RE = re.compile(r"^(?P<name>.*) \((?P<id>[^()/]+)\)$")


def _parse_backup_folder(folder: str):
    """Split '<Name> (asset_id)' → (name, asset_id), or (None, None)."""
    m = _BACKUP_FOLDER_RE.match(folder)
    if not m:
        return None, None
    return m.group("name"), m.group("id")


def list_backup_catalog(s3, bucket, query=""):
    """List backup assets (optionally filtered by name/id substring) and their
    versions. Returns [{name, asset_id, folder, versions:[{version,key,
    last_modified,size}]}]. Read-only."""
    if not bucket:
        return [], "no backup bucket configured (BACKUP_BUCKET is empty)"
    q = (query or "").strip().lower()
    assets = {}  # folder -> {name, asset_id, versions:[]}
    # Matches both '<asset_id>_v<N>.json' (current) and 'json_v<N>.json' (legacy).
    ver_re = re.compile(r"_v(\d+)\.json$")
    token = None
    try:
        while True:
            kwargs = {"Bucket": bucket}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []) or []:
                key = obj["Key"]
                if "/" not in key:
                    continue
                folder, fname = key.rsplit("/", 1)
                m = ver_re.search(fname)
                if not m:
                    continue
                name, asset_id = _parse_backup_folder(folder)
                if asset_id is None:
                    continue
                # Filter by name or id substring.
                if q and q not in (name or "").lower() and q not in asset_id.lower():
                    continue
                a = assets.setdefault(
                    folder,
                    {
                        "name": name,
                        "asset_id": asset_id,
                        "folder": folder,
                        "versions": [],
                    },
                )
                a["versions"].append(
                    {
                        "version": int(m.group(1)),
                        "key": key,
                        "last_modified": obj.get("LastModified"),
                        "size": obj.get("Size"),
                    }
                )
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break
    except ClientError as e:
        return [], classify_error(e)["user_message"]

    out = list(assets.values())
    for a in out:
        a["versions"].sort(key=lambda v: v["version"])
        a["latest_version"] = a["versions"][-1]["version"] if a["versions"] else None
    out.sort(key=lambda a: (a["name"] or "").lower())
    return out, None


def read_backup_envelope(s3, bucket, key):
    """Read + parse a backup object into its envelope dict. Read-only."""
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    return json.loads(body)


def _collect_dependencies(target_qs, target_account_id, rtype, resource_obj):
    """Best-effort snapshot of a resource's dependencies (for restore context).

    Returns a dict describing what the resource links to. We capture the
    reference (arn/id) and, where cheap and safe, a describe of the dependency.
    Failures are non-fatal — a dependency we cannot read is recorded with an
    "error" note rather than aborting the backup.
    """
    deps = {}
    try:
        if rtype == "agent":
            # Agents reference action connectors by ARN.
            conn_arns = resource_obj.get("ActionConnectors", []) or []
            connectors = []
            for arn in conn_arns:
                cid = arn.split("/")[-1]
                entry = {"action_connector_id": cid, "arn": arn}
                try:
                    c = target_qs.describe_action_connector(
                        AwsAccountId=target_account_id, ActionConnectorId=cid
                    ).get("ActionConnector", {})
                    entry["name"] = c.get("Name")
                    entry["type"] = c.get("Type")
                except ClientError as e:
                    entry["error"] = classify_error(e)["user_message"]
                connectors.append(entry)
            deps["action_connectors"] = connectors

        elif rtype == "space":
            # Spaces link agents / connectors / KBs / flows by ARN.
            linked = []
            for r in resource_obj.get("resources", []) or []:
                arn = (r.get("resourceDetails") or {}).get("resourceArn")
                linked.append(
                    {
                        "resource_type": r.get("resourceType"),
                        "resource_arn": arn,
                        "resource_id": arn.split("/")[-1] if arn else None,
                    }
                )
            deps["linked_resources"] = linked

        elif rtype == "knowledge_base":
            # KBs reference a data source by ARN.
            ds_arn = resource_obj.get("DataSourceArn", "")
            entry = {"data_source_arn": ds_arn or None}
            if ds_arn:
                ds_id = ds_arn.split("/")[-1]
                entry["data_source_id"] = ds_id
            deps["data_source"] = entry

        elif rtype == "flow":
            # A flow's FlowDefinition is self-contained but may embed resource
            # ARNs; store the definition itself so restore can replay it.
            deps["flow_definition_present"] = bool(resource_obj.get("FlowDefinition"))

        # connectors have no outbound dependencies to capture.
    except Exception as exc:  # never let dependency capture break the backup
        deps["_capture_error"] = str(exc)
    return deps


def _build_backup_envelope(target_qs, target_account_id, rtype, resource_id, name):
    """Describe the target resource + capture its dependencies into an envelope.

    Envelope (schema v2):
      { schema_version, backed_up_at, account_id, resource_type, resource_id,
        name, resource: <full describe>, dependencies: {...} }
    """
    op_name, id_kwarg, obj_key = _RESOURCE_DESCRIBE_KEYS[rtype]
    op = getattr(target_qs, op_name)
    call_kwargs = {"AwsAccountId": target_account_id, id_kwarg: resource_id}
    if rtype == "flow":
        call_kwargs["PublishState"] = "PUBLISHED"  # DescribeFlow requires it
    raw = op(**call_kwargs)
    resource_obj = raw.get(obj_key, raw)
    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "backed_up_at": datetime.now(UTC).isoformat(),
        "account_id": target_account_id,
        "resource_type": rtype,
        "resource_id": resource_id,
        "name": name,
        "resource": resource_obj,
        "dependencies": _collect_dependencies(
            target_qs, target_account_id, rtype, resource_obj
        ),
    }


def snapshot_after_migrate(
    target_qs, s3_backup, target_account_id, rtype, resource_id, name, action, report
):
    """Write a post-migration snapshot of the target resource to the backup
    bucket, so EVERY successfully migrated asset (create OR update) is listed by
    list_backups. Non-fatal: a snapshot failure never fails the migration — it
    is only recorded in the report.

    action: "CREATED" | "UPDATED" — stamped into the envelope for the timeline.
    """
    if not common.BACKUP_BUCKET or not resource_id:
        return
    try:
        envelope = _build_backup_envelope(
            target_qs, target_account_id, rtype, resource_id, name
        )
        envelope["migration_action"] = action
        key = backup_target_resource(
            s3_backup, common.BACKUP_BUCKET, rtype, resource_id, name, envelope
        )
        report.setdefault("snapshots", []).append(
            {"resource_id": resource_id, "s3_key": key, "action": action}
        )
    except (ClientError, BackupError) as e:
        # Snapshot is best-effort — the migration already succeeded.
        logger.warning(
            f"  ⚠ post-migration snapshot failed for {rtype} '{name or resource_id}' "
            f"({resource_id}): {e}"
        )
        report.setdefault("snapshot_errors", []).append(
            {"resource_id": resource_id, "error": str(e)}
        )


def maybe_backup_before_update(
    target_qs, s3_backup, target_account_id, rtype, resource_id, name, report
):
    """Snapshot the current target resource (+ dependencies) before an update.

    Returns True if it is safe to proceed with the update (backup written), or
    False if the backup failed — in which case a meaningful error is recorded
    in the report and the caller MUST skip the update (fail-safe).
    """
    try:
        envelope = _build_backup_envelope(
            target_qs, target_account_id, rtype, resource_id, name
        )
    except ClientError as e:
        msg = (
            f"Unable to take a backup before updating {rtype} '{name or resource_id}' "
            f"({resource_id}): could not read the current target resource "
            f"({classify_error(e)['user_message']}). The resource was NOT modified."
        )
        report["errors"].append(
            {"context": f"backup({resource_id})", "user_message": msg}
        )
        return False
    try:
        key = backup_target_resource(
            s3_backup, common.BACKUP_BUCKET, rtype, resource_id, name, envelope
        )
        report.setdefault("backups", []).append(
            {"resource_id": resource_id, "s3_key": key, "bucket": common.BACKUP_BUCKET}
        )
        return True
    except BackupError as e:
        msg = (
            f"Unable to take a backup before updating {rtype} '{name or resource_id}' "
            f"({resource_id}): {e}. The resource was NOT modified. "
            f"Fix the backup bucket/permissions and retry."
        )
        report["errors"].append(
            {"context": f"backup({resource_id})", "user_message": msg}
        )
        return False
