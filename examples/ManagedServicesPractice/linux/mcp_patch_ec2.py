import json
from threading import Event

import boto3

ssm = boto3.client("ssm")
ec2 = boto3.client("ec2")

_POLL_IDLE = Event()


def _poll_wait(seconds):
    """Wait `seconds` between polls."""
    _POLL_IDLE.wait(timeout=seconds)


STANDARD_PATCHES = ["amazon-ssm-agent", "amazon-cloudwatch-agent"]

# --- MCP Protocol Handlers ---


def handle_initialize(request_id):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "mcp-patch-ec2", "version": "1.2.0"},
        },
    }


def handle_tools_list(request_id):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "tools": [
                {
                    "name": "patch_fleet",
                    "description": "Execute patching on a fleet of EC2 instances using SSM Patch Manager. Targets instances by PatchGroup tag.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "client": {
                                "type": "string",
                                "description": "Client name (e.g., NovaTech Solutions)",
                            },
                            "patch_group": {
                                "type": "string",
                                "description": "PatchGroup tag value to target (e.g., NovaTech-Prod-Linux)",
                            },
                            "operation": {
                                "type": "string",
                                "default": "Install",
                                "description": "Scan or Install",
                            },
                            "reboot_option": {
                                "type": "string",
                                "default": "RebootIfNeeded",
                                "description": "RebootIfNeeded or NoReboot",
                            },
                            "snapshot_before": {
                                "type": "boolean",
                                "default": True,
                                "description": "Take EBS snapshots before patching for rollback",
                            },
                        },
                        "required": ["client", "patch_group"],
                    },
                },
                {
                    "name": "check_patch_status",
                    "description": "Check patch compliance status for a fleet after patching. Returns per-instance compliance results.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "patch_group": {
                                "type": "string",
                                "description": "PatchGroup tag value to check compliance for",
                            },
                            "command_id": {
                                "type": "string",
                                "description": "Optional: SSM command ID from patch_fleet to check specific run status",
                            },
                        },
                        "required": ["patch_group"],
                    },
                },
            ]
        },
    }


def handle_tools_call(request_id, params):
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    try:
        if tool_name == "patch_fleet":
            result = patch_fleet(arguments)
        elif tool_name == "check_patch_status":
            result = check_patch_status(arguments)
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "status": "error",
                                "message": f"Tool execution failed: {str(e)}",
                            },
                            indent=2,
                        ),
                    }
                ]
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
    }


# --- Core Functions ---


def get_instances_by_patch_group(patch_group):
    """Get all instance IDs with the given PatchGroup tag."""
    response = ec2.describe_instances(
        Filters=[
            {"Name": "tag:PatchGroup", "Values": [patch_group]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    instance_ids = []
    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            instance_ids.append(instance["InstanceId"])
    return instance_ids


def take_snapshots(instance_ids, client, patch_group):
    """Take EBS snapshots with rate limiting and retry logic."""
    snapshot_ids = []
    for instance_id in instance_ids:
        volumes = ec2.describe_volumes(
            Filters=[{"Name": "attachment.instance-id", "Values": [instance_id]}]
        )
        for volume in volumes["Volumes"]:
            retries = 3
            for attempt in range(retries):
                try:
                    snapshot = ec2.create_snapshot(
                        VolumeId=volume["VolumeId"],
                        Description=f"Pre-patch snapshot for {client} - {patch_group}",
                        TagSpecifications=[
                            {
                                "ResourceType": "snapshot",
                                "Tags": [
                                    {"Key": "Client", "Value": client},
                                    {"Key": "PatchGroup", "Value": patch_group},
                                    {"Key": "Purpose", "Value": "Pre-Patch-Backup"},
                                    {"Key": "ManagedBy", "Value": "MSP"},
                                    {"Key": "CreatedBy", "Value": "mcp-patch-ec2"},
                                ],
                            }
                        ],
                    )
                    snapshot_ids.append(snapshot["SnapshotId"])
                    _poll_wait(1)  # Rate limit: 1 snapshot per second per volume
                    break
                except Exception as e:
                    if "RateExceeded" in str(e) and attempt < retries - 1:
                        _poll_wait(3 * (attempt + 1))  # Exponential backoff
                    elif attempt < retries - 1:
                        _poll_wait(2)
                    else:
                        # Log but don't fail the entire operation
                        snapshot_ids.append(f"FAILED:{volume['VolumeId']}:{str(e)}")
    return snapshot_ids


def install_standard_packages(instance_ids, client, patch_group):
    """Install standard packages via AWS-RunShellScript."""
    install_commands = [
        "#!/bin/bash",
        "set -e",
        "echo '=== Installing standard packages ==='",
    ]

    for package in STANDARD_PATCHES:
        install_commands.append(f"echo 'Installing {package}...'")
        install_commands.append(
            f"if command -v dnf &> /dev/null; then "
            f"dnf install -y {package}; "
            f"elif command -v yum &> /dev/null; then "
            f"yum install -y {package}; "
            f"else echo 'No package manager found'; fi"
        )

    # Enable and start agents
    install_commands.extend(
        [
            "echo 'Starting amazon-ssm-agent...'",
            "systemctl enable amazon-ssm-agent 2>/dev/null && systemctl restart amazon-ssm-agent 2>/dev/null || true",
            "echo 'Starting amazon-cloudwatch-agent...'",
            "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c ssm:AmazonCloudWatch-linux 2>/dev/null || true",
            "systemctl enable amazon-cloudwatch-agent 2>/dev/null && systemctl restart amazon-cloudwatch-agent 2>/dev/null || true",
            "echo '=== Standard package install complete ==='",
        ]
    )

    response = ssm.send_command(
        InstanceIds=instance_ids,
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": install_commands, "executionTimeout": ["600"]},
        Comment=f"Standard package install for {client} - {patch_group}",
        TimeoutSeconds=600,
    )

    return {
        "packages": STANDARD_PATCHES,
        "command_id": response["Command"]["CommandId"],
    }


def patch_fleet(arguments):
    """Execute patching on a fleet of EC2 instances."""
    client = arguments.get("client", "Unknown")
    patch_group = arguments.get("patch_group")
    operation = arguments.get("operation", "Install")
    reboot_option = arguments.get("reboot_option", "RebootIfNeeded")
    snapshot_before = arguments.get("snapshot_before", True)

    # Get target instances
    instance_ids = get_instances_by_patch_group(patch_group)

    if not instance_ids:
        return {
            "status": "error",
            "message": f"No running instances found with PatchGroup tag: {patch_group}",
        }

    # Blast radius check (max 20 instances per batch)
    if len(instance_ids) > 20:
        return {
            "status": "error",
            "message": f"Blast radius limit exceeded. Found {len(instance_ids)} instances. Max 20 per batch.",
        }

    # Take pre-patch snapshots (Install only)
    snapshots_taken = False
    snapshot_error = None
    if operation == "Install" and snapshot_before:
        try:
            take_snapshots(instance_ids, client, patch_group)
            snapshots_taken = True
        except Exception as e:
            snapshot_error = str(e)
            # Continue with patching even if snapshots fail
            snapshots_taken = False

    # Run AWS-RunPatchBaseline (OS patches)
    patch_response = ssm.send_command(
        InstanceIds=instance_ids,
        DocumentName="AWS-RunPatchBaseline",
        Parameters={"Operation": [operation], "RebootOption": [reboot_option]},
        Comment=f"Patching {client} - {patch_group} - {operation}",
        TimeoutSeconds=3600,
    )

    patch_command_id = patch_response["Command"]["CommandId"]

    # Install standard packages (Install only)
    standard_patch_result = None
    if operation == "Install":
        try:
            standard_patch_result = install_standard_packages(
                instance_ids, client, patch_group
            )
        except Exception as e:
            standard_patch_result = {"packages": STANDARD_PATCHES, "error": str(e)}

    result = {
        "status": "success",
        "command_id": patch_command_id,
        "standard_patch_result": standard_patch_result,
        "client": client,
        "patch_group": patch_group,
        "operation": operation,
        "target_count": len(instance_ids),
        "instance_ids": instance_ids,
        "snapshots_taken": snapshots_taken,
        "reboot_option": reboot_option,
        "standard_patches": STANDARD_PATCHES,
        "message": (
            f"Patching initiated for {len(instance_ids)} instances in {patch_group}. "
            f"Command ID: {patch_command_id}. Operation: {operation}. "
            f"Pre-patch snapshots: {'Yes' if snapshots_taken else 'No'}. "
            f"Standard patches: {', '.join(STANDARD_PATCHES)}."
        ),
    }

    if snapshot_error:
        result["snapshot_warning"] = (
            f"Snapshots failed: {snapshot_error}. Patching continued."
        )

    return result


def check_patch_status(arguments):
    """Check patch compliance status for a fleet."""
    patch_group = arguments.get("patch_group")
    command_id = arguments.get("command_id")

    instance_ids = get_instances_by_patch_group(patch_group)

    if command_id:
        # Check specific command status
        response = ssm.list_command_invocations(CommandId=command_id, Details=True)
        results = []
        for invocation in response["CommandInvocations"]:
            results.append(
                {
                    "instance_id": invocation["InstanceId"],
                    "status": invocation["Status"],
                    "status_details": invocation["StatusDetails"],
                }
            )
        return {
            "status": "success",
            "command_id": command_id,
            "patch_group": patch_group,
            "results": results,
        }
    else:
        # Check overall patch compliance
        compliance_results = []
        for instance_id in instance_ids:
            try:
                response = ssm.describe_instance_patch_states(InstanceIds=[instance_id])
                for state in response["InstancePatchStates"]:
                    compliance_results.append(
                        {
                            "instance_id": state["InstanceId"],
                            "installed": state.get("InstalledCount", 0),
                            "missing": state.get("MissingCount", 0),
                            "failed": state.get("FailedCount", 0),
                            "operation_end_time": str(
                                state.get("OperationEndTime", "N/A")
                            ),
                        }
                    )
            except Exception as e:
                compliance_results.append({"instance_id": instance_id, "error": str(e)})
        return {
            "status": "success",
            "patch_group": patch_group,
            "total_instances": len(instance_ids),
            "compliance": compliance_results,
        }


# --- Lambda Handler ---


def lambda_handler(event, context):
    """Handle both API Gateway proxy and direct invocations."""
    # Parse the request - handle both API Gateway and direct event
    if "body" in event:
        try:
            body = event["body"]
            request = json.loads(body) if isinstance(body, str) else body
        except (json.JSONDecodeError, TypeError) as e:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {str(e)}"},
                    }
                ),
            }
    elif "method" in event:
        request = event
    else:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32600,
                        "message": "Invalid request - no body or method found",
                    },
                }
            ),
        }

    method = request.get("method", "")
    request_id = request.get("id", 1)
    params = request.get("params", {})

    # Route to handler
    if method == "initialize":
        result = handle_initialize(request_id)
    elif method == "tools/list":
        result = handle_tools_list(request_id)
    elif method == "tools/call":
        result = handle_tools_call(request_id, params)
    else:
        result = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(result),
    }
