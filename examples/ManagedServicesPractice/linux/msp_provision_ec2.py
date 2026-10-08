"""Combined EC2 MCP Server - Lambda Handler
Provisions EC2 instances for MSP clients with JIT hook via User Data.
via Amazon QuickSight MCP Action Connector
"""

import base64
import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2 = boto3.client('ec2')


def lambda_handler(event, context):
    """Handle MCP requests for EC2 provisioning"""

    body = json.loads(event['body']) if isinstance(event.get('body'), str) else event.get('body', {})
    method = body.get('method')
    request_id = body.get('id')
    params = body.get('params', {})

    if method == 'initialize':
        return mcp_response(request_id, {
            'protocolVersion': '2024-11-05',
            'capabilities': {'tools': {}},
            'serverInfo': {
                'name': 'ec2-msp-mcp',
                'version': '2.0.0'
            }
        })

    elif method == 'tools/list':
        return mcp_response(request_id, {
            'tools': [
                {
                    'name': 'provision_ec2_instance',
                    'description': 'Provision an EC2 instance for an MSP client with standard tagging, security configuration, and JIT hook deployment via User Data.',
                    'inputSchema': {
                        'type': 'object',
                        'properties': {
                            'client': {
                                'type': 'string',
                                'description': 'Client name (e.g., NovaTech Solutions)'
                            },
                            'ami_id': {
                                'type': 'string',
                                'description': 'AMI ID to launch (e.g., ami-08e6829e013be2292)'
                            },
                            'instance_type': {
                                'type': 'string',
                                'description': 'EC2 instance type (e.g., t3.large, m5.xlarge)',
                                'default': 't3.large'
                            },
                            'subnet_id': {
                                'type': 'string',
                                'description': 'Subnet ID to launch in'
                            },
                            'security_groups': {
                                'type': 'array',
                                'items': {'type': 'string'},
                                'description': 'List of security group IDs'
                            },
                            'instance_profile': {
                                'type': 'string',
                                'description': 'IAM instance profile name',
                                'default': 'EC2-SSM-Role'
                            },
                            'key_name': {
                                'type': 'string',
                                'description': 'SSH key pair name'
                            },
                            'environment': {
                                'type': 'string',
                                'description': 'Environment tag (Production, Staging, Dev)',
                                'default': 'Production'
                            },
                            'cost_center': {
                                'type': 'string',
                                'description': 'Cost center code for billing'
                            },
                            'owner': {
                                'type': 'string',
                                'description': 'Owner of the instance',
                                'default': 'MSP Cloud Ops'
                            },
                            'instance_name': {
                                'type': 'string',
                                'description': 'Name tag for the instance'
                            },
                            'root_volume_size': {
                                'type': 'integer',
                                'description': 'Root EBS volume size in GB',
                                'default': 100
                            },
                            'patch_group': {
                                'type': 'string',
                                'description': 'Patch group for Systems Manager'
                            }
                        },
                        'required': ['client', 'ami_id', 'instance_type', 'subnet_id', 'security_groups', 'key_name']
                    }
                }
            ]
        })

    elif method == 'tools/call':
        tool_name = params.get('name')
        arguments = params.get('arguments', {})

        if tool_name == 'provision_ec2_instance':
            result = provision_instance(arguments)
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


# ═══════════════════════════════════════════
# TOOL: PROVISION EC2 INSTANCE
# ═══════════════════════════════════════════

def provision_instance(spec):
    """Provision an EC2 instance with MSP standard configuration and JIT hook via User Data"""

    client = spec['client']
    ami_id = spec['ami_id']
    instance_type = spec['instance_type']
    subnet_id = spec['subnet_id']
    security_groups = spec['security_groups']
    instance_profile = spec.get('instance_profile', 'EC2-SSM-Role')
    key_name = spec['key_name']
    environment = spec.get('environment', 'Production')
    cost_center = spec.get('cost_center', 'Unknown')
    owner = spec.get('owner', 'MSP Cloud Ops')
    name = spec.get('instance_name', f'{client}-instance')
    root_volume_size = spec.get('root_volume_size', 100)
    patch_group = spec.get('patch_group', f'{client}-Prod-Linux')

    tags = [
        {'Key': 'Name', 'Value': name},
        {'Key': 'Client', 'Value': client},
        {'Key': 'Environment', 'Value': environment},
        {'Key': 'CostCenter', 'Value': cost_center},
        {'Key': 'Owner', 'Value': owner},
        {'Key': 'ManagedBy', 'Value': 'MSP'},
        {'Key': 'PatchGroup', 'Value': patch_group},
        {'Key': 'BackupPlan', 'Value': spec.get('backup_plan', 'Standard')},
    ]

    # User Data script to deploy JIT hook at first boot
    user_data_script = f'''#!/bin/bash
# Deploy JIT hook for command_not_found
cat << 'HOOKEOF' > /etc/profile.d/jit_hook.sh
#!/bin/bash
command_not_found_handle() {{
    local instance_id=$(ec2-metadata -i | awk '{{print $2}}')
    local company="{client}"

    echo ""
    echo "⚠️  '$1' is not installed on this server."
    echo ""
    echo "To request installation, send an email to:"
    echo "  jit_install_flow@us-east-1.mail.quick.aws.com"
    echo ""
    echo "With subject:"
    echo "  INSTALL:$1:$instance_id:$company"
    echo ""
    return 127
}}
HOOKEOF
chmod 644 /etc/profile.d/jit_hook.sh
'''

    try:
        response = ec2.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type,
            SubnetId=subnet_id,
            SecurityGroupIds=security_groups,
            IamInstanceProfile={'Name': instance_profile},
            KeyName=key_name,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[
                {'ResourceType': 'instance', 'Tags': tags},
                {'ResourceType': 'volume', 'Tags': tags}
            ],
            BlockDeviceMappings=[{
                'DeviceName': '/dev/xvda',
                'Ebs': {
                    'VolumeSize': root_volume_size,
                    'VolumeType': 'gp3',
                    'Encrypted': True,
                    'Iops': 3000,
                    'Throughput': 125
                }
            }],
            MetadataOptions={
                'HttpTokens': 'required',
                'HttpEndpoint': 'enabled'
            },
            UserData=base64.b64encode(user_data_script.encode()).decode()
        )

        instance_id = response['Instances'][0]['InstanceId']
        private_ip = response['Instances'][0].get('PrivateIpAddress', 'pending')

        logger.info(f"Provisioned {instance_id} for {client}")

        return {
            'status': 'success',
            'instance_id': instance_id,
            'private_ip': private_ip,
            'client': client,
            'instance_type': instance_type,
            'environment': environment,
            'patch_group': patch_group,
            'jit_hook': 'deployed via User Data (runs at first boot)',
            'message': f"Instance {instance_id} launched for {client}. Name: {name}, IP: {private_ip}. JIT hook will be active on first login."
        }

    except Exception as e:
        logger.error(f"Provisioning failed for {client}: {str(e)}")
        return {
            'status': 'failed',
            'error': str(e),
            'client': client,
            'rollback': 'No resources created. No rollback needed.'
        }


# ═══════════════════════════════════════════
# MCP PROTOCOL HELPERS
# ═══════════════════════════════════════════

def mcp_response(request_id, result):
    """Format MCP success response"""
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
    """Format MCP error response"""
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

