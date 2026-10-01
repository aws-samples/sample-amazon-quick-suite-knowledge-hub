"""migrate_connectors — recreate/update action connectors in the target."""

from backups import maybe_backup_before_update
from botocore.exceptions import ClientError
from common import (
    classify_error,
    copy_connector_permissions,
    format_error_for_report,
    logger,
    sanitize_auth_config,
)


def _migrate_connectors(
    source_qs,
    target_qs,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    connector_ids,
    report,
):
    """Recreate the given connectors in the target + copy permissions (no spaces)."""
    for connector_id in connector_ids:
        try:
            src_cfg = source_qs.describe_action_connector(
                AwsAccountId=source_account_id, ActionConnectorId=connector_id
            )
            connector_data = src_cfg.get("ActionConnector", src_cfg)
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_action_connector({connector_id})", e)
            )
            report["migrated"]["connectors"].append(
                {
                    "connector_id": connector_id,
                    "name": connector_id,
                    "type": "UNKNOWN (failed to describe)",
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        ctype = connector_data.get("Type", "GENERIC_HTTP")

        source_auth = connector_data.get("AuthenticationConfig", {}) or {}
        auth_type = source_auth.get("AuthenticationType", "NONE")
        # Auth types that carry a provider secret (client secret / password /
        # API key). Their secret cannot be copied across accounts, and some
        # providers (e.g. Salesforce) VALIDATE the credential when the connector
        # is created — so a headless create with placeholder secrets is rejected
        # with a generic "invalid parameter" error. We detect that case and
        # report it as a clear SKIP with re-auth guidance instead of a raw
        # failure. NONE / IAM connectors have no copyable secret and create fine.
        credentialed = auth_type not in ("NONE", "IAM", "AWS_IAM", "")

        auth = sanitize_auth_config(
            connector_data.get(
                "AuthenticationConfig",
                {
                    "AuthenticationType": "NONE",
                    "AuthenticationMetadata": {
                        "NoneConnectionMetadata": {
                            "BaseEndpoint": "https://example.com"
                        }
                    },
                },
            )
        )
        try:
            target_qs.create_action_connector(
                AwsAccountId=target_account_id,
                ActionConnectorId=connector_id,
                Name=connector_data.get("Name", connector_id),
                Type=ctype,
                AuthenticationConfig=auth,
            )
            status = "CREATED"
        except ClientError as e:
            if e.response.get("Error", {}).get("Code", "") == "ResourceExistsException":
                if not maybe_backup_before_update(
                    target_qs,
                    backup_s3,
                    target_account_id,
                    "connector",
                    connector_id,
                    connector_data.get("Name", connector_id),
                    report,
                ):
                    status = "FAILED: backup before update failed (not modified)"
                    report["migrated"]["connectors"].append(
                        {
                            "connector_id": connector_id,
                            "name": connector_data.get("Name", connector_id),
                            "type": connector_data.get("Type", "UNKNOWN"),
                            "status": status,
                        }
                    )
                    continue
                try:
                    upd = {
                        "AwsAccountId": target_account_id,
                        "ActionConnectorId": connector_id,
                        "Name": connector_data.get("Name", connector_id),
                        "AuthenticationConfig": auth,
                    }
                    if connector_data.get("Description"):
                        upd["Description"] = connector_data["Description"]
                    target_qs.update_action_connector(**upd)
                    status = "UPDATED"
                except ClientError as ue:
                    report["errors"].append(
                        format_error_for_report(
                            f"update_action_connector({connector_id})", ue
                        )
                    )
                    status = f"FAILED: {classify_error(ue)['user_message']}"
            else:
                # Attempt-and-catch (no hardcoded type allowlist): the read model
                # can return connector types that create_action_connector does not
                # accept (e.g. MODEL_CONTEXT_PROTOCOL). Detect that class of error
                # and report it as SKIPPED with a clear message rather than a hard
                # failure — the migration of other resources should continue.
                code = e.response.get("Error", {}).get("Code", "")
                emsg = e.response.get("Error", {}).get("Message", "")
                unsupported = code in (
                    "ValidationException",
                    "InvalidParameterValueException",
                    "UnsupportedUserEditionException",
                ) and (ctype in emsg or "type" in emsg.lower())
                if unsupported:
                    msg = (
                        f"Action connector creation is not supported for type "
                        f"'{ctype}' via the QuickSight API. Recreate this connector "
                        f"manually in the target account."
                    )
                    logger.warning(f"  ⚠ connector '{connector_id}' ({ctype}): {msg}")
                    report["errors"].append(
                        {
                            "context": f"create_action_connector({connector_id})",
                            "user_message": msg,
                        }
                    )
                    status = f"SKIPPED: {msg}"
                else:
                    code = e.response.get("Error", {}).get("Code", "")
                    validation_error = code in (
                        "ValidationException",
                        "InvalidParameterValueException",
                    )
                    if credentialed and validation_error:
                        # A credential-backed connector (OAuth2 / Basic / API key)
                        # whose create was rejected on validation — almost always
                        # because the provider validates the (placeholder) secret
                        # at create time. The secret cannot be copied across
                        # accounts, so this connector must be recreated and
                        # re-authenticated in the target UI. Report a clear SKIP.
                        msg = (
                            f"Connector '{connector_data.get('Name', connector_id)}' "
                            f"({ctype}, {auth_type}) could not be created headless: "
                            f"its credentials/secret cannot be copied across accounts "
                            f"and the provider validates them on create. Recreate this "
                            f"connector in the target account and re-authenticate it, "
                            f"then re-run to migrate anything that depends on it."
                        )
                        logger.warning(
                            f"  ⚠ connector '{connector_id}' ({ctype}/{auth_type}): "
                            f"{msg}"
                        )
                        report["errors"].append(
                            {
                                "context": f"create_action_connector({connector_id})",
                                "user_message": msg,
                            }
                        )
                        status = f"SKIPPED: {msg}"
                    else:
                        report["errors"].append(
                            format_error_for_report(
                                f"create_action_connector({connector_id})", e
                            )
                        )
                        status = f"FAILED: {classify_error(e)['user_message']}"

        # Only copy permissions when the connector was actually created/updated.
        if "FAILED" not in status and "SKIPPED" not in status:
            copy_connector_permissions(
                source_qs,
                target_qs,
                source_account_id,
                target_account_id,
                region,
                connector_id,
                report,
            )
        report["migrated"]["connectors"].append(
            {
                "connector_id": connector_id,
                "name": connector_data.get("Name", connector_id),
                "type": connector_data.get("Type", "UNKNOWN"),
                "status": status,
            }
        )
