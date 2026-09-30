"""migrate_agents — recreate/update agents in the target (connectors re-attached)."""

from backups import maybe_backup_before_update
from botocore.exceptions import ClientError
from common import (
    classify_error,
    copy_agent_permissions,
    format_error_for_report,
    logger,
    remap_arn,
    target_link_exists,
    wait_for_active,
)


def _migrate_agents(
    source_qs,
    target_qs,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    agent_ids,
    report,
):
    """Recreate the given agents in the target + copy permissions.

    Action Connectors referenced by the agent are remapped to the target account
    and attached, exactly as in the source. Space linkage is handled separately
    by the space migration (which re-links its resources), not here.
    """
    for agent_id in agent_ids:
        try:
            agent = source_qs.describe_agent(
                AwsAccountId=source_account_id, AgentId=agent_id
            ).get("Agent", {})
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_agent({agent_id})", e)
            )
            report["migrated"]["agents"].append(
                {
                    "agent_id": agent_id,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        custom_prompt_input = None
        prompt = agent.get("CustomPromptInterface") or {}
        new_prompt = {
            k: v
            for k, v in {
                "CustomInstructions": prompt.get("CustomInstructions"),
                "Identity": prompt.get("Identity"),
                "Tone": prompt.get("Tone"),
                "OutputStyle": prompt.get("OutputStyle"),
                "ResponseLength": prompt.get("ResponseLength"),
            }.items()
            if v
        }
        if new_prompt:
            custom_prompt_input = {"NewPrompt": new_prompt}

        target_connector_arns = [
            remap_arn(a, target_account_id, region)
            for a in agent.get("ActionConnectors", [])
        ]
        # Only attach connectors confirmed to exist in the target, so a missing
        # or not-yet-created connector never fails the whole create_agent — the
        # agent is still created and existing/valid connectors are attached.
        create_connector_arns = [
            arn
            for arn in target_connector_arns
            if target_link_exists(target_qs, target_account_id, "action-connector", arn)
        ]
        if create_connector_arns != target_connector_arns:
            missing = [
                arn.split("/")[-1]
                for arn in target_connector_arns
                if arn not in create_connector_arns
            ]
            logger.warning(
                f"  ⚠ agent '{agent_id}': skipping connectors not confirmed in "
                f"target: {missing}"
            )
        create_params = {
            "AwsAccountId": target_account_id,
            "AgentId": agent_id,
            "Name": agent.get("Name", agent_id),
            "AgentLifecycle": agent.get("AgentLifecycle", "PUBLISHED"),
        }
        if create_connector_arns:
            create_params["ActionConnectors"] = create_connector_arns
        if agent.get("Description"):
            create_params["Description"] = agent["Description"]
        if custom_prompt_input:
            create_params["CustomPromptInput"] = custom_prompt_input
        if agent.get("StarterPrompts"):
            create_params["StarterPrompts"] = agent["StarterPrompts"]
        if agent.get("WelcomeMessage"):
            create_params["WelcomeMessage"] = agent["WelcomeMessage"]
        if agent.get("IconId"):
            create_params["IconId"] = agent["IconId"]

        try:
            target_qs.create_agent(**create_params)
            status = "CREATED"
        except ClientError as e:
            if e.response.get("Error", {}).get("Code", "") == "ResourceExistsException":
                if not maybe_backup_before_update(
                    target_qs,
                    backup_s3,
                    target_account_id,
                    "agent",
                    agent_id,
                    agent.get("Name", agent_id),
                    report,
                ):
                    report["migrated"]["agents"].append(
                        {
                            "agent_id": agent_id,
                            "name": agent.get("Name", agent_id),
                            "status": "FAILED: backup before update failed (not modified)",
                            "connectors": [
                                a.split("/")[-1]
                                for a in agent.get("ActionConnectors", [])
                            ],
                        }
                    )
                    continue
                try:
                    wait_for_active(target_qs, target_account_id, agent_id)
                    existing_connectors = set()
                    existing_connectors_known = True
                    try:
                        cur = target_qs.describe_agent(
                            AwsAccountId=target_account_id, AgentId=agent_id
                        ).get("Agent", {})
                        existing_connectors = set(cur.get("ActionConnectors", []) or [])
                    except ClientError as de:
                        existing_connectors_known = False
                        logger.warning(
                            f"  ⚠ describe_agent '{agent_id}' failed before update; "
                            "will add only connectors confirmed to exist in the target "
                            "and leave existing connectors untouched",
                            exc_info=de,
                        )
                    upd_params = {
                        "AwsAccountId": target_account_id,
                        "AgentId": agent_id,
                        "Name": agent.get("Name", agent_id),
                    }
                    if agent.get("Description"):
                        upd_params["Description"] = agent["Description"]
                    if custom_prompt_input:
                        upd_params["CustomPromptInput"] = custom_prompt_input
                    if agent.get("StarterPrompts"):
                        upd_params["StarterPrompts"] = agent["StarterPrompts"]
                    if agent.get("WelcomeMessage"):
                        upd_params["WelcomeMessage"] = agent["WelcomeMessage"]
                    if agent.get("IconId"):
                        upd_params["IconId"] = agent["IconId"]
                    if existing_connectors_known:
                        connectors_to_add = [
                            a
                            for a in target_connector_arns
                            if a not in existing_connectors
                        ]
                    else:
                        # Could not read the agent's current connectors. Add only
                        # connectors confirmed to exist in the target, so existing
                        # ones are never touched and only successfully created
                        # connectors are attached.
                        connectors_to_add = []
                        for arn in target_connector_arns:
                            if target_link_exists(
                                target_qs,
                                target_account_id,
                                "action-connector",
                                arn,
                            ):
                                connectors_to_add.append(arn)
                            else:
                                logger.warning(
                                    f"  ⚠ skipping connector '{arn.split('/')[-1]}' "
                                    f"for agent '{agent_id}': not confirmed in target"
                                )
                    if connectors_to_add:
                        upd_params["ActionConnectorsToAdd"] = connectors_to_add
                    target_qs.update_agent(**upd_params)
                    status = "UPDATED"
                except ClientError as ue:
                    ucode = ue.response.get("Error", {}).get("Code", "")
                    if ucode == "ConflictException":
                        status = "SKIPPED_UPDATING (agent busy — retry later)"
                        logger.warning(
                            f"  ⚠ update_agent '{agent_id}' conflict: agent is UPDATING"
                        )
                    else:
                        report["errors"].append(
                            format_error_for_report(f"update_agent({agent_id})", ue)
                        )
                        status = f"FAILED: {classify_error(ue)['user_message']}"
            else:
                report["errors"].append(
                    format_error_for_report(f"create_agent({agent_id})", e)
                )
                status = f"FAILED: {classify_error(e)['user_message']}"

        if "FAILED" not in status:
            wait_for_active(target_qs, target_account_id, agent_id)
            copy_agent_permissions(
                source_qs,
                target_qs,
                source_account_id,
                target_account_id,
                region,
                agent_id,
                report,
            )

        report["migrated"]["agents"].append(
            {
                "agent_id": agent_id,
                "name": agent.get("Name", agent_id),
                "status": status,
                "connectors": [
                    a.split("/")[-1] for a in agent.get("ActionConnectors", [])
                ],
            }
        )
