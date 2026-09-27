import json
import boto3

ssm = boto3.client('ssm', region_name='us-east-1')


def lambda_handler(event, context):
    body = json.loads(event['body']) if isinstance(event.get('body'), str) else event.get('body', {})
    
    # Handle if Quick sends JSON-RPC directly (not wrapped in body)
    if 'method' not in body and 'method' in event:
        body = event

    method = body.get('method', '')
    request_id = body.get('id')
    params = body.get('params', {})

    if method == 'initialize':
        return mcp_response(request_id, {
            'protocolVersion': '2024-11-05',
            'capabilities': {'tools': {}},
            'serverInfo': {
                'name': 'deploy-software-mcp',
                'version': '1.0.0'
            }
        })

    elif method == 'tools/list':
        return mcp_response(request_id, {
            'tools': [{
                'name': 'deploy_software',
                'description': 'Install a software package on an EC2 instance via SSM Run Command. Used for Just-in-Time software deployment.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'instance_id': {
                            'type': 'string',
                            'description': 'The EC2 instance ID to install software on'
                        },
                        'package': {
                            'type': 'string',
                            'description': 'The package name to install (e.g., docker, python3, git)'
                        },
                        'method': {
                            'type': 'string',
                            'description': 'Install method: dnf, yum, or pip3',
                            'enum': ['dnf', 'yum', 'pip3'],
                            'default': 'dnf'
                        }
                    },
                    'required': ['instance_id', 'package']
                }
            }]
        })

    elif method == 'tools/call':
        tool_name = params.get('name')
        arguments = params.get('arguments', {})

        if tool_name == 'deploy_software':
            result = deploy_software(arguments)
        else:
            return mcp_error(request_id, -32601, f'Tool not found: {tool_name}')

        return mcp_response(request_id, {
            'content': [{
                'type': 'text',
                'text': json.dumps(result, indent=2)
            }]
        })

    else:
        return mcp_error(request_id, -32601, f'Method not found: {method}')


def deploy_software(arguments):
    instance_id = arguments.get('instance_id')
    package = arguments.get('package')
    install_method = arguments.get('method', 'dnf')

    if not instance_id or not package:
        return {'status': 'error', 'message': 'Missing required fields: instance_id, package'}

    if install_method == 'pip3':
        install_cmd = f"pip3 install {package}"
    elif install_method == 'yum':
        install_cmd = f"yum install {package} -y"
    else:
        install_cmd = f"dnf install {package} -y"

    try:
        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName='AWS-RunShellScript',
            Parameters={'commands': [install_cmd]},
            Comment=f"JIT install: {package} on {instance_id}"
        )
        command_id = response['Command']['CommandId']
    except Exception as e:
        return {'status': 'error', 'message': f'SSM command failed: {str(e)}'}

    return {
        'status': 'success',
        'package': package,
        'install_command': install_cmd,
        'instance_id': instance_id,
        'ssm_command_id': command_id,
        'message': f"'{package}' installed on {instance_id}. Command ID: {command_id}"
    }


def mcp_response(request_id, result):
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        },
        'body': json.dumps({
            'jsonrpc': '2.0',
            'id': request_id,
            'result': result
        })
    }


def mcp_error(request_id, code, message):
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            'jsonrpc': '2.0',
            'id': request_id,
            'error': {
                'code': code,
                'message': message
            }
        })
    }
