import json

import boto3

ssm = boto3.client("ssm")
ec2 = boto3.client("ec2")


def lambda_handler(event, context):
    # Parse JSON-RPC request - handle both API Gateway and direct invocation
    if isinstance(event.get("body"), str):
        body = json.loads(event["body"])
    elif "body" in event and event["body"]:
        body = event["body"]
    elif "method" in event:
        body = event
    else:
        body = event

    method = body.get("method", "")
    request_id = body.get("id", 1)

    # === INITIALIZE ===
    if method == "initialize":
        return respond(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mcp-patch-windows-ec2", "version": "1.1.0"},
            },
        )

    # === TOOLS/LIST ===
    elif method == "tools/list":
        return respond(
            request_id,
            {
                "tools": [
                    {
                        "name": "patch_fleet",
                        "description": "Apply Windows OS patches to EC2 instances using AWS Systems Manager. Scan is auto-approved. Install requires explicit approval.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "instance_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of Windows EC2 instance IDs to patch",
                                },
                                "operation": {
                                    "type": "string",
                                    "enum": ["Scan", "Install"],
                                    "description": "Scan to check compliance, Install to apply patches (requires approval)",
                                },
                                "reboot_option": {
                                    "type": "string",
                                    "enum": ["RebootIfNeeded", "NoReboot"],
                                    "description": "Reboot behavior after patching. Default: RebootIfNeeded",
                                },
                            },
                            "required": ["instance_ids", "operation"],
                        },
                    },
                    {
                        "name": "check_patch_status",
                        "description": "Check the status of a previously initiated patch operation by command ID",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "command_id": {
                                    "type": "string",
                                    "description": "SSM command ID returned from patch_fleet",
                                }
                            },
                            "required": ["command_id"],
                        },
                    },
                    {
                        "name": "check_compliance",
                        "description": "Check patch compliance status for Windows EC2 instances. Returns installed, missing, and failed patch counts per instance.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "instance_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of Windows EC2 instance IDs to check compliance for",
                                }
                            },
                            "required": ["instance_ids"],
                        },
                    },
                ]
            },
        )

    # === TOOLS/CALL ===
    elif method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "patch_fleet":
            return handle_patch_fleet(request_id, arguments)
        elif tool_name == "check_patch_status":
            return handle_check_status(request_id, arguments)
        elif tool_name == "check_compliance":
            return handle_check_compliance(request_id, arguments)
        else:
            return respond_error(request_id, f"Unknown tool: {tool_name}")

    else:
        return respond_error(request_id, f"Unknown method: {method}")


def handle_patch_fleet(request_id, arguments):
    instance_ids = arguments.get("instance_ids", [])
    operation = arguments.get("operation", "Scan")
    reboot_option = arguments.get("reboot_option", "RebootIfNeeded")

    if not instance_ids:
        return respond(
            request_id,
            {
                "content": [
                    {"type": "text", "text": "Error: No instance_ids provided."}
                ],
                "isError": True,
            },
        )

    try:
        # Pre-patch snapshot for Install operations (SOC2/HIPAA compliance)
        if operation == "Install":
            snapshot_results = []
            for iid in instance_ids:
                reservations = ec2.describe_instances(InstanceIds=[iid])
                for reservation in reservations["Reservations"]:
                    for instance in reservation["Instances"]:
                        for bdm in instance.get("BlockDeviceMappings", []):
                            vol_id = bdm["Ebs"]["VolumeId"]
                            snap = ec2.create_snapshot(
                                VolumeId=vol_id,
                                Description=f"Pre-Patch-Backup for {iid}",
                                TagSpecifications=[
                                    {
                                        "ResourceType": "snapshot",
                                        "Tags": [
                                            {
                                                "Key": "Purpose",
                                                "Value": "Pre-Patch-Backup",
                                            },
                                            {"Key": "InstanceId", "Value": iid},
                                            {
                                                "Key": "PatchOperation",
                                                "Value": "Windows-Install",
                                            },
                                        ],
                                    }
                                ],
                            )
                            snapshot_results.append(
                                {
                                    "instance_id": iid,
                                    "volume_id": vol_id,
                                    "snapshot_id": snap["SnapshotId"],
                                }
                            )

        # Send SSM patch command
        response = ssm.send_command(
            InstanceIds=instance_ids,
            DocumentName="AWS-RunPatchBaseline",
            Parameters={"Operation": [operation], "RebootOption": [reboot_option]},
            Comment=f"Windows patch {operation} via MCP connector",
        )

        command_id = response["Command"]["CommandId"]

        result = {
            "status": "success",
            "command_id": command_id,
            "operation": operation,
            "reboot_option": reboot_option,
            "instance_ids": instance_ids,
            "message": f"Patch {operation} initiated successfully. Command ID: {command_id}",
        }

        if operation == "Install" and snapshot_results:
            result["pre_patch_snapshots"] = snapshot_results

        return respond(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
        )

    except Exception as e:
        return respond(
            request_id,
            {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True,
            },
        )


def handle_check_status(request_id, arguments):
    command_id = arguments.get("command_id", "")

    if not command_id:
        return respond(
            request_id,
            {
                "content": [{"type": "text", "text": "Error: No command_id provided."}],
                "isError": True,
            },
        )

    try:
        response = ssm.list_command_invocations(CommandId=command_id, Details=True)

        results = []
        for inv in response.get("CommandInvocations", []):
            entry = {
                "instance_id": inv["InstanceId"],
                "status": inv["Status"],
                "status_details": inv.get("StatusDetails", ""),
                "requested_time": str(inv.get("RequestedDateTime", "")),
            }
            # Include output if available
            if inv.get("CommandPlugins"):
                plugin = inv["CommandPlugins"][0]
                entry["output"] = plugin.get("Output", "")[:2000]
            results.append(entry)

        return respond(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(results, indent=2)}]},
        )

    except Exception as e:
        return respond(
            request_id,
            {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True,
            },
        )


def handle_check_compliance(request_id, arguments):
    instance_ids = arguments.get("instance_ids", [])

    if not instance_ids:
        return respond(
            request_id,
            {
                "content": [
                    {"type": "text", "text": "Error: No instance_ids provided."}
                ],
                "isError": True,
            },
        )

    try:
        response = ssm.describe_instance_patch_states(InstanceIds=instance_ids)

        results = []
        for state in response.get("InstancePatchStates", []):
            installed = state.get("InstalledCount", 0)
            missing = state.get("MissingCount", 0)
            failed = state.get("FailedCount", 0)
            installed_other = state.get("InstalledOtherCount", 0)
            not_applicable = state.get("NotApplicableCount", 0)

            results.append(
                {
                    "instance_id": state["InstanceId"],
                    "baseline_id": state.get("BaselineId", ""),
                    "operation": state.get("Operation", ""),
                    "operation_time": str(state.get("OperationEndTime", "")),
                    "installed": installed,
                    "installed_other": installed_other,
                    "missing": missing,
                    "failed": failed,
                    "not_applicable": not_applicable,
                    "compliant": missing == 0 and failed == 0,
                }
            )

        # Flag instances with no compliance data
        reported_ids = [r["instance_id"] for r in results]
        for iid in instance_ids:
            if iid not in reported_ids:
                results.append(
                    {
                        "instance_id": iid,
                        "compliant": None,
                        "message": "No patch compliance data. Run a Scan first.",
                    }
                )

        return respond(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(results, indent=2)}]},
        )

    except Exception as e:
        return respond(
            request_id,
            {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True,
            },
        )


def respond(request_id, result):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}),
    }


def respond_error(request_id, message):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": message},
            }
        ),
    }
