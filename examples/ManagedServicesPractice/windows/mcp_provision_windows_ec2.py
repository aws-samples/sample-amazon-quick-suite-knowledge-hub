import json

import boto3

ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")

TOOL_DEFINITION = {
    "name": "provision_windows_ec2",
    "description": "Provision a Windows Server 2022 EC2 instance with SSM access, Fleet Manager RDP ready, and Administrator password pre-configured",
    "inputSchema": {
        "type": "object",
        "properties": {
            "instance_name": {
                "type": "string",
                "description": "Name tag for the instance",
            },
            "client": {"type": "string", "description": "Client name for tagging"},
            "subnet_id": {"type": "string", "description": "Subnet ID for placement"},
            "security_group_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Security group IDs",
            },
            "instance_type": {
                "type": "string",
                "description": "EC2 instance type",
                "default": "t3.large",
            },
            "volume_size": {
                "type": "integer",
                "description": "Root volume size in GB",
                "default": 100,
            },
            "instance_profile": {
                "type": "string",
                "description": "IAM instance profile name",
                "default": "EC2-SSM-Role",
            },
            "environment": {
                "type": "string",
                "description": "Environment (Production, Staging, Dev)",
                "default": "Production",
            },
            "admin_password": {
                "type": "string",
                "description": "Administrator password for RDP access",
                "default": "MyDemo@2026!",
            },
        },
        "required": ["instance_name", "client", "subnet_id", "security_group_ids"],
    },
}


def lambda_handler(event, context):
    if isinstance(event.get("body"), str):
        body = json.loads(event["body"])
    elif "body" in event and event["body"]:
        body = event["body"]
    else:
        body = event

    method = body.get("method", "")
    request_id = body.get("id", 1)

    match method:
        case "initialize":
            return respond(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "mcp-provision-windows-ec2",
                        "version": "2.2.0",
                    },
                },
            )

        case "tools/list":
            return respond(request_id, {"tools": [TOOL_DEFINITION]})

        case "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name == "provision_windows_ec2":
                result = provision_instance(arguments)
                return respond(
                    request_id, {"content": [{"type": "text", "text": result}]}
                )
            else:
                return respond(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": f"Unknown tool: {tool_name}"}
                        ]
                    },
                    error=True,
                )

        case _:
            return respond(
                request_id,
                {"content": [{"type": "text", "text": "Unknown method"}]},
                error=True,
            )


def provision_instance(args):
    instance_name = args.get("instance_name")
    client = args.get("client")
    subnet_id = args.get("subnet_id")
    security_group_ids = args.get("security_group_ids", [])
    instance_type = args.get("instance_type", "t3.large")
    volume_size = args.get("volume_size", 100)
    instance_profile = args.get("instance_profile", "EC2-SSM-Role")
    environment = args.get("environment", "Production")
    admin_password = args.get("admin_password", "MyDemo@2026!")

    if not all([instance_name, client, subnet_id, security_group_ids]):
        return "Error: instance_name, client, subnet_id, and security_group_ids are required"

    # Get latest Windows Server 2022 AMI
    ami_response = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["Windows_Server-2022-English-Full-Base-*"]},
            {"Name": "state", "Values": ["available"]},
        ],
    )
    images = sorted(
        ami_response["Images"], key=lambda x: x["CreationDate"], reverse=True
    )
    if not images:
        return "Error: No Windows Server 2022 AMI found"
    ami_id = images[0]["ImageId"]

    # UserData script: set password + install Chocolatey
    userdata_script = f"""<powershell>
# Set Administrator password and enable account
net user Administrator "{admin_password}" /active:yes
wmic useraccount where "name='Administrator'" set PasswordExpires=FALSE

# Install Chocolatey
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# Install default software (uncomment below if needed)
choco install python312 git -y
</powershell>"""

    try:
        # Launch instance
        response = ec2.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            SecurityGroupIds=security_group_ids,
            IamInstanceProfile={"Name": instance_profile},
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "VolumeSize": volume_size,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                    },
                }
            ],
            UserData=userdata_script,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": instance_name},
                        {"Key": "Client", "Value": client},
                        {"Key": "Environment", "Value": environment},
                        {"Key": "ManagedBy", "Value": "MCP-Quick"},
                        {"Key": "Compliance", "Value": "SOC2"},
                        {"Key": "Patch Group", "Value": "Demo-Strict"},
                    ],
                }
            ],
        )

        instance = response["Instances"][0]
        instance_id = instance["InstanceId"]
        private_ip = instance.get("PrivateIpAddress", "pending")

        return (
            f"✅ Windows EC2 provisioned successfully!\n"
            f"Instance ID: {instance_id}\n"
            f"Private IP: {private_ip}\n"
            f"Type: {instance_type}\n"
            f"AMI: {ami_id} (Windows Server 2022)\n"
            f"Patch Group: Demo-Strict\n"
            f"Default Software: Chocolatey (package manager)\n"
            f"Access: Fleet Manager RDP (no key pair, no port 3389 needed)\n"
            f"Credentials: Administrator / {admin_password}\n"
            f"Next: Wait ~2 min for SSM registration, then scan for compliance"
        )

    except Exception as e:
        return f"❌ Error provisioning instance: {str(e)}"


def respond(request_id, result, error=False):
    body = {"jsonrpc": "2.0", "id": request_id}
    if error:
        body["error"] = {
            "code": -32000,
            "message": result.get("content", [{}])[0].get("text", "Error"),
        }
    else:
        body["result"] = result

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
