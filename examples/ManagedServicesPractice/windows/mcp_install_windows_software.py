
import json
import boto3

ssm = boto3.client('ssm')

TOOL_DEFINITION = {
    "name": "install_software",
    "description": "Install software packages on a Windows EC2 instance using Chocolatey (e.g., docker-desktop, python3, git, vscode, nodejs). Returns immediately with a command ID - installation runs asynchronously.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "instance_id": {
                "type": "string",
                "description": "Target Windows EC2 instance ID"
            },
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of Chocolatey package names to install (e.g., docker-desktop, python3, git)"
            }
        },
        "required": ["instance_id", "packages"]
    }
}


def lambda_handler(event, context):
    if isinstance(event.get('body'), str):
        body = json.loads(event['body'])
    elif 'body' in event and event['body']:
        body = event['body']
    else:
        body = event

    method = body.get('method', '')
    request_id = body.get('id', 1)

    match method:
        case 'initialize':
            return respond(request_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mcp-install-windows-software", "version": "2.0.0"}
            })

        case 'tools/list':
            return respond(request_id, {"tools": [TOOL_DEFINITION]})

        case 'tools/call':
            params = body.get('params', {})
            tool_name = params.get('name', '')
            arguments = params.get('arguments', {})

            if tool_name == 'install_software':
                result = install_software(arguments)
                return respond(request_id, {"content": [{"type": "text", "text": result}]})
            else:
                return respond(request_id, {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]}, error=True)

        case _:
            return respond(request_id, {"content": [{"type": "text", "text": "Unknown method"}]}, error=True)


def install_software(args):
    instance_id = args.get('instance_id')
    packages = args.get('packages', [])

    if not instance_id or not packages:
        return "Error: instance_id and packages are required"

    package_list = ' '.join(packages)
    pkg_array = ', '.join([f'"{p}"' for p in packages])

    ps_script = f"""
# Install Chocolatey if not present
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {{
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    $env:Path += ";C:\\ProgramData\\chocolatey\\bin"
}}

# Install packages
$packages = @({pkg_array})
$results = @()
foreach ($pkg in $packages) {{
    Write-Output "Installing $pkg..."
    $output = choco install $pkg -y --no-progress 2>&1 | Out-String
    $results += "[$pkg] $output"
}}
$results -join "`n---`n"
"""

    try:
        # Send command and return immediately - NO waiting
        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName='AWS-RunPowerShellScript',
            Parameters={'commands': [ps_script]},
            TimeoutSeconds=600,
            Comment=f'MCP Install: {package_list}'
        )
        command_id = response['Command']['CommandId']

        return (
            f"✅ Install initiated on {instance_id}!\n"
            f"Packages: {package_list}\n"
            f"Command ID: {command_id}\n"
            f"Status: Running asynchronously\n"
            f"Note: Installation is running in the background. "
            f"Check instance in a few minutes to confirm."
        )

    except Exception as e:
        return f"❌ Error: {str(e)}"


def respond(request_id, result, error=False):
    body = {"jsonrpc": "2.0", "id": request_id}
    if error:
        body["error"] = {"code": -32000, "message": result.get("content", [{}])[0].get("text", "Error")}
    else:
        body["result"] = result

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body)
    }

