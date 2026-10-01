"""migrate_knowledge_bases — recreate/update knowledge bases in the target.

Branches on the SOURCE KB type (S3 / WEB_CRAWLER / credentialed). S3 buckets are
created lazily and must keep the ``knowledge-base-`` prefix so they stay within
the migrator role's S3 grant.
"""

import copy
import re
import uuid

from backups import maybe_backup_before_update
from botocore.exceptions import ClientError
from common import (
    MEDIA_EXTRACTION_CONFIG,
    classify_error,
    copy_kb_permissions,
    create_kb_bucket,
    format_error_for_report,
    logger,
    verify_qs_service_role,
    wait_for_kb_active,
)


def _derive_kb_bucket_name(source_bucket, target_account_id):
    """Derive a globally-unique target bucket name from the source bucket name +
    target account id. Must start with "knowledge-base-" so it stays within the
    migrator role's S3 grant (arn:aws:s3:::knowledge-base-*). S3 rules: lowercase,
    no underscores, 3–63 chars.

    If the source bucket already starts with "knowledge-base-", it's reused as the
    base; otherwise the prefix is prepended.
    """
    base = (source_bucket or "").lower().replace("_", "-")
    base = re.sub(r"[^a-z0-9.-]", "-", base).strip("-.")
    if not base.startswith("knowledge-base-"):
        base = f"knowledge-base-{base}" if base else "knowledge-base"
    suffix = f"-{target_account_id}"
    base = base[: (63 - len(suffix))].strip("-.")
    return f"{base}{suffix}"


def _source_kb_bucket(kb_config):
    """Pull the source S3 bucket name out of an S3 KB's template config."""
    try:
        tmpl = kb_config["templateConfiguration"]["template"]
        return tmpl.get("connectionConfiguration", {}).get("bucketName")
    except (KeyError, TypeError):
        return None


# KB source types whose data source carries credentials/auth that CANNOT be
# copied across accounts — they need a pre-existing target data source.
_CREDENTIALED_KB_TYPES = {"SHAREPOINT", "CONFLUENCE", "GOOGLE_DRIVE", "QBUSINESS"}


def _migrate_knowledge_bases(
    source_qs,
    target_qs,
    target_s3,
    target_iam,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    kb_ids,
    target_env,
    qs_service_role,
    target_kb_bucket,
    target_data_source_arn,
    report,
):
    """Migrate knowledge bases, branching on the SOURCE KB type:

      S3_KNOWLEDGE_BASE : create/reuse an S3 bucket (target_kb_bucket, else
                          "<source-bucket>-<account>") + S3 data source + S3V2 KB.
      WEB_CRAWLER       : replay the source data source (NO_AUTH) + KB config.
      SHAREPOINT/CONFLUENCE/GOOGLE_DRIVE/QBUSINESS : credential-backed — the data
                          source cannot be recreated headless. If
                          target_data_source_arn is provided, recreate the KB
                          against it; otherwise SKIP with guidance.

    The pre-update backup is taken before any update. S3 buckets are created
    lazily (only when an S3 KB is actually migrated).
    """
    _s3_role_ok = None  # lazily verify the QS service role only for S3 KBs
    _s3_buckets_made = set()

    def _ensure_s3_bucket(bucket_name):
        nonlocal _s3_role_ok
        if _s3_role_ok is None:
            _s3_role_ok = verify_qs_service_role(
                target_iam, target_account_id, qs_service_role, report
            )
        if not _s3_role_ok:
            return False
        if bucket_name not in _s3_buckets_made:
            create_kb_bucket(
                target_s3,
                bucket_name,
                region,
                target_account_id,
                qs_service_role,
                report,
            )
            _s3_buckets_made.add(bucket_name)
            report["migrated"]["buckets"].append(
                {"bucket": bucket_name, "account": target_account_id}
            )
        return True

    for kb_id in kb_ids:
        try:
            kb_detail = source_qs.describe_knowledge_base(
                AwsAccountId=source_account_id, KnowledgeBaseId=kb_id
            ).get("KnowledgeBase", {})
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_knowledge_base({kb_id})", e)
            )
            report["migrated"]["knowledge_bases"].append(
                {
                    "knowledge_base_id": kb_id,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        kb_name = kb_detail.get("Name", kb_id)
        kb_type = kb_detail.get("Type", "S3_KNOWLEDGE_BASE")
        src_config = kb_detail.get("KnowledgeBaseConfiguration") or {}
        media_config = (
            kb_detail.get("MediaExtractionConfiguration") or MEDIA_EXTRACTION_CONFIG
        )

        # ── Resolve the target data source ARN + KB config per type ──
        ds_arn = None  # target data source ARN to attach
        kb_config = None  # KnowledgeBaseConfiguration to send
        skip_msg = None

        if kb_type == "S3_KNOWLEDGE_BASE":
            src_bucket = _source_kb_bucket(src_config)
            bucket = target_kb_bucket or _derive_kb_bucket_name(
                src_bucket, target_account_id
            )
            # The migrator's S3 grant is scoped to knowledge-base-* buckets. A
            # user-supplied bucket outside that prefix can't be managed by this role.
            if target_kb_bucket and not target_kb_bucket.startswith("knowledge-base-"):
                skip_msg = (
                    f"target_kb_bucket '{target_kb_bucket}' must start with "
                    f"'knowledge-base-' — the migrator role's S3 permissions are "
                    f"scoped to knowledge-base-* buckets."
                )
            elif not _ensure_s3_bucket(bucket):
                report["steps"].append(
                    {"step": "kb_service_role_check", "status": "FAILED"}
                )
                report["migrated"]["knowledge_bases"].append(
                    {
                        "knowledge_base_id": kb_id,
                        "name": kb_name,
                        "type": kb_type,
                        "status": "FAILED: QuickSight service role preflight failed",
                    }
                )
                continue
            # Preserve the SOURCE KB's template configuration (filterConfiguration,
            # deletionProtectionConfiguration, and any other S3V2 template fields
            # the user set) and only override the target-specific connection —
            # the bucket name and its owning account. Hardcoding an empty
            # filterConfiguration here previously dropped the user's inclusion/
            # exclusion patterns, prefixes, and max-file-size on migration.
            src_template = {}
            try:
                src_template = copy.deepcopy(
                    src_config["templateConfiguration"]["template"]
                )
            except (KeyError, TypeError):
                src_template = {}

            template = src_template or {}
            template["type"] = "S3V2"
            # Only fill defaults when the source didn't carry these.
            template.setdefault(
                "deletionProtectionConfiguration",
                {
                    "enableDeletionProtection": "false",
                    "deletionProtectionThreshold": "15",
                },
            )
            template.setdefault(
                "filterConfiguration",
                {
                    "inclusionPatterns": [],
                    "maxFileSizeInMegaBytes": "10240",
                    "inclusionPrefixes": [],
                    "exclusionPatterns": [],
                    "exclusionPrefixes": [],
                },
            )
            # The connection ALWAYS points at the target bucket/account.
            template["connectionConfiguration"] = {
                "bucketName": bucket,
                "bucketOwnerAccountId": target_account_id,
            }
            kb_config = {"templateConfiguration": {"template": template}}
            s3_ds_params = {
                "S3KnowledgeBaseParameters": {"BucketUrl": f"s3://{bucket}"}
            }

        elif kb_type == "WEB_CRAWLER":
            # NO_AUTH — replay the source data source + KB config verbatim.
            kb_config = src_config

        elif kb_type in _CREDENTIALED_KB_TYPES:
            if not target_data_source_arn:
                skip_msg = (
                    f"Knowledge base type '{kb_type}' uses a data source with "
                    f"credentials that cannot be copied across accounts. Create the "
                    f"connection/data source in the target account, then re-run with "
                    f"target_data_source_arn set to migrate this KB's structure."
                )
            else:
                ds_arn = target_data_source_arn
                kb_config = src_config
        else:
            skip_msg = (
                f"Knowledge base migration is not supported for type '{kb_type}'."
            )

        if skip_msg:
            logger.warning(f"  ⚠ knowledge_base '{kb_id}' ({kb_type}): {skip_msg}")
            report["errors"].append(
                {
                    "context": f"migrate_knowledge_base({kb_id})",
                    "user_message": skip_msg,
                }
            )
            report["migrated"]["knowledge_bases"].append(
                {
                    "knowledge_base_id": kb_id,
                    "name": kb_name,
                    "type": kb_type,
                    "status": f"SKIPPED: {skip_msg}",
                }
            )
            continue

        # ── Does the KB already exist in the target? (upsert) ──
        existing_kb = None
        try:
            existing_kb = target_qs.describe_knowledge_base(
                AwsAccountId=target_account_id, KnowledgeBaseId=kb_id
            ).get("KnowledgeBase")
        except ClientError as e:
            if (
                e.response.get("Error", {}).get("Code", "")
                != "ResourceNotFoundException"
            ):
                logger.warning(
                    f"  ⚠ describe target KB '{kb_id}': {classify_error(e)['user_message']}"
                )

        if existing_kb:
            if not maybe_backup_before_update(
                target_qs,
                backup_s3,
                target_account_id,
                "knowledge_base",
                kb_id,
                kb_name,
                report,
            ):
                report["migrated"]["knowledge_bases"].append(
                    {
                        "knowledge_base_id": kb_id,
                        "name": kb_name,
                        "type": kb_type,
                        "status": "FAILED: backup before update failed (not modified)",
                    }
                )
                continue
            status = "UPDATED"
            # For S3, keep the existing data source pointed at the (possibly new) bucket.
            if kb_type == "S3_KNOWLEDGE_BASE":
                existing_ds_arn = existing_kb.get("DataSourceArn", "")
                existing_ds_id = (
                    existing_ds_arn.split("/")[-1] if existing_ds_arn else None
                )
                if existing_ds_id:
                    try:
                        target_qs.update_data_source(
                            AwsAccountId=target_account_id,
                            DataSourceId=existing_ds_id,
                            Name=f"{kb_name} - datasource",
                            DataSourceParameters=s3_ds_params,
                        )
                    except ClientError as ue:
                        logger.warning(
                            f"  ⚠ update_data_source(kb {kb_id}): {classify_error(ue)['user_message']}"
                        )
            try:
                target_qs.update_knowledge_base(
                    AwsAccountId=target_account_id,
                    KnowledgeBaseId=kb_id,
                    Name=kb_name,
                    KnowledgeBaseConfiguration=kb_config,
                    MediaExtractionConfiguration=media_config,
                )
            except ClientError as ue:
                report["errors"].append(
                    format_error_for_report(f"update_knowledge_base({kb_id})", ue)
                )
                status = f"FAILED: {classify_error(ue)['user_message']}"
        else:
            # ── Create path ──
            status = "FAILED: data source not created"
            if kb_type == "S3_KNOWLEDGE_BASE":
                new_ds_id = str(uuid.uuid4())
                try:
                    ds_resp = target_qs.create_data_source(
                        AwsAccountId=target_account_id,
                        DataSourceId=new_ds_id,
                        Name=f"{kb_name} - datasource",
                        Type="S3_KNOWLEDGE_BASE",
                        DataSourceParameters=s3_ds_params,
                    )
                    ds_arn = ds_resp.get(
                        "Arn",
                        f"arn:aws:quicksight:{region}:{target_account_id}:datasource/{new_ds_id}",
                    )
                except ClientError as e:
                    report["errors"].append(
                        format_error_for_report(f"create_data_source(kb {kb_id})", e)
                    )
            elif kb_type == "WEB_CRAWLER":
                # Replay the source WEB_CRAWLER data source (NO_AUTH).
                try:
                    src_ds_arn = kb_detail.get("DataSourceArn", "")
                    src_ds = source_qs.describe_data_source(
                        AwsAccountId=source_account_id,
                        DataSourceId=src_ds_arn.split("/")[-1],
                    )["DataSource"]
                    new_ds_id = str(uuid.uuid4())
                    ds_kwargs = {
                        "AwsAccountId": target_account_id,
                        "DataSourceId": new_ds_id,
                        "Name": f"{kb_name} - datasource",
                        "Type": src_ds["Type"],
                    }
                    if src_ds.get("DataSourceParameters"):
                        ds_kwargs["DataSourceParameters"] = src_ds[
                            "DataSourceParameters"
                        ]
                    ds_resp = target_qs.create_data_source(**ds_kwargs)
                    ds_arn = ds_resp.get(
                        "Arn",
                        f"arn:aws:quicksight:{region}:{target_account_id}:datasource/{new_ds_id}",
                    )
                except ClientError as e:
                    report["errors"].append(
                        format_error_for_report(f"create_data_source(kb {kb_id})", e)
                    )
            # credentialed types: ds_arn already set to target_data_source_arn

            if ds_arn:
                try:
                    target_qs.create_knowledge_base(
                        AwsAccountId=target_account_id,
                        KnowledgeBaseId=kb_id,
                        Name=kb_name,
                        DataSourceArn=ds_arn,
                        KnowledgeBaseConfiguration=kb_config,
                        MediaExtractionConfiguration=media_config,
                    )
                    status = "CREATED"
                except ClientError as e:
                    report["errors"].append(
                        format_error_for_report(f"create_knowledge_base({kb_id})", e)
                    )
                    status = f"FAILED: {classify_error(e)['user_message']}"

        if "FAILED" not in status:
            wait_for_kb_active(target_qs, target_account_id, kb_id)
            copy_kb_permissions(
                source_qs,
                target_qs,
                source_account_id,
                target_account_id,
                region,
                kb_id,
                report,
            )
        report["migrated"]["knowledge_bases"].append(
            {
                "knowledge_base_id": kb_id,
                "name": kb_name,
                "type": kb_type,
                "status": status,
            }
        )
