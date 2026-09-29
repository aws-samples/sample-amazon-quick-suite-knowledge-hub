"""migrate_spaces — recreate/update spaces in the target and re-link resources."""

from backups import maybe_backup_before_update
from botocore.exceptions import ClientError
from common import (
    classify_error,
    copy_space_permissions,
    format_error_for_report,
    remap_arn,
)


def _migrate_spaces(
    source_qs,
    target_qs,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    space_ids,
    report,
):
    """Recreate the given spaces in the target, re-link their resources, copy permissions.

    Each space's linked resources (agents, connectors, knowledge bases) are
    referenced by ARN; those ARNs are remapped to the target account before the
    space is re-linked. The linked resources themselves must already be migrated
    for the target ARNs to resolve.
    """
    for space_id in space_ids:
        try:
            space = source_qs.describe_space(
                AwsAccountId=source_account_id, SpaceId=space_id
            ).get("Space", {})
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_space({space_id})", e)
            )
            report["migrated"]["spaces"].append(
                {
                    "space_id": space_id,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        space_name = space.get("name", space_id)
        create_params = {
            "AwsAccountId": target_account_id,
            "SpaceId": space_id,
            "Name": space_name,
        }
        if space.get("description"):
            create_params["Description"] = space["description"]

        try:
            target_qs.create_space(**create_params)
            status = "CREATED"
        except ClientError as e:
            if e.response.get("Error", {}).get("Code", "") == "ResourceExistsException":
                if not maybe_backup_before_update(
                    target_qs,
                    backup_s3,
                    target_account_id,
                    "space",
                    space_id,
                    space_name,
                    report,
                ):
                    status = "FAILED: backup before update failed (not modified)"
                else:
                    try:
                        upd = {
                            "AwsAccountId": target_account_id,
                            "SpaceId": space_id,
                            "Name": space_name,
                        }
                        if space.get("description"):
                            upd["Description"] = space["description"]
                        target_qs.update_space(**upd)
                        status = "UPDATED"
                    except ClientError as ue:
                        report["errors"].append(
                            format_error_for_report(f"update_space({space_id})", ue)
                        )
                        status = f"FAILED: {classify_error(ue)['user_message']}"
            else:
                report["errors"].append(
                    format_error_for_report(f"create_space({space_id})", e)
                )
                status = f"FAILED: {classify_error(e)['user_message']}"

        # Re-link the space's resources, remapping each ARN to the target account.
        linked = []
        if "FAILED" not in status:
            add_resources = []
            for res in space.get("resources", []) or []:
                src_arn = (res.get("resourceDetails") or {}).get("resourceArn")
                if not src_arn:
                    continue
                add_resources.append(
                    {
                        "ResourceType": res.get("resourceType"),
                        "ResourceDetails": {
                            "resourceArn": remap_arn(src_arn, target_account_id, region)
                        },
                    }
                )
                linked.append(src_arn.split("/")[-1])
            if add_resources:
                try:
                    target_qs.update_space_resources(
                        AwsAccountId=target_account_id,
                        SpaceId=space_id,
                        AddResources=add_resources,
                    )
                except ClientError as e:
                    report["errors"].append(
                        format_error_for_report(
                            f"update_space_resources({space_id})", e
                        )
                    )

            copy_space_permissions(
                source_qs,
                target_qs,
                source_account_id,
                target_account_id,
                region,
                space_id,
                report,
            )

        report["migrated"]["spaces"].append(
            {
                "space_id": space_id,
                "name": space_name,
                "status": status,
                "linked_resources": linked,
            }
        )
