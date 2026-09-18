"""
Utility Functions for HR Gateway Workshop

Handles Lambda creation, IAM role setup, Cognito configuration,
and Gateway management for the AgentCore Gateway workshop.
"""

import boto3
import json
import time
import zipfile
import io
import os
from botocore.exceptions import ClientError


def get_account_id():
    return boto3.client('sts').get_caller_identity()['Account']


def get_region():
    return boto3.Session().region_name or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')


def create_lambda_execution_role(role_name="hr-gateway-lambda-role"):
    """Create IAM role for the HR Lambda functions."""
    iam = boto3.client('iam')
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }
    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for HR Gateway Lambda functions"
        )
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
        )
        print(f"✓ Lambda execution role created: {role_name}")
        time.sleep(10)
        return role['Role']['Arn']
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=role_name)
        print(f"ℹ️  Lambda execution role already exists: {role_name}")
        return role['Role']['Arn']


def _deploy_one_lambda(function_name, handler_file, role_arn, description):
    """Deploy a single Lambda function, packaging handler + shared_data."""
    region = get_region()
    lambda_client = boto3.client('lambda', region_name=region)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_file, os.path.basename(handler_file))
        zf.write('shared_data.py', 'shared_data.py')
    zip_buffer.seek(0)
    zip_bytes = zip_buffer.read()

    try:
        resp = lambda_client.create_function(
            FunctionName=function_name,
            Runtime='python3.12',
            Role=role_arn,
            Handler=f'{os.path.basename(handler_file).replace(".py", "")}.lambda_handler',
            Code={'ZipFile': zip_bytes},
            Timeout=30, MemorySize=128,
            Description=description
        )
        print(f"  ✓ Created: {function_name}")
        return resp['FunctionArn']
    except lambda_client.exceptions.ResourceConflictException:
        lambda_client.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
        resp = lambda_client.get_function(FunctionName=function_name)
        print(f"  ℹ️  Updated: {function_name}")
        return resp['Configuration']['FunctionArn']


# Tool definitions: (function_name, handler_file, description)
TOOL_LAMBDAS = [
    ("hr-gw-get-payroll", "get_payroll_info.py", "Get employee payroll and compensation"),
    ("hr-gw-get-org-chart", "get_org_chart.py", "Get org chart and reporting structure"),
    ("hr-gw-submit-timesheet", "submit_timesheet.py", "Submit weekly timesheet"),
    ("hr-gw-get-benefits", "get_benefits_summary.py", "Get employee benefits summary"),
]


def create_all_hr_lambdas():
    """Create all 4 HR Lambda functions."""
    role_arn = create_lambda_execution_role()
    results = {}
    print("\nCreating Lambda functions:")
    for func_name, handler_file, desc in TOOL_LAMBDAS:
        arn = _deploy_one_lambda(func_name, handler_file, role_arn, desc)
        results[func_name] = arn
    return results


def create_agentcore_gateway_role(role_name="hr-agentcore-gateway-role"):
    """Create IAM role for AgentCore Gateway to invoke Lambda."""
    iam = boto3.client('iam')
    account_id = get_account_id()

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": account_id}}
        }]
    }
    lambda_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["lambda:InvokeFunction"],
            "Resource": f"arn:aws:lambda:*:{account_id}:function:hr-gw-*"
        }]
    }

    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for AgentCore Gateway to invoke Lambda"
        )
        iam.put_role_policy(
            RoleName=role_name, PolicyName="gateway-lambda-invoke",
            PolicyDocument=json.dumps(lambda_policy)
        )
        print(f"✓ Gateway IAM role created: {role_name}")
        time.sleep(10)
        return role['Role']['Arn']
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=role_name)
        print(f"ℹ️  Gateway IAM role already exists: {role_name}")
        return role['Role']['Arn']


def setup_cognito_for_gateway():
    """Create Cognito resources for Gateway inbound OAuth authentication."""
    cognito = boto3.client('cognito-idp')
    region = get_region()
    ts = int(time.time())

    pool_name = f"gateway-hr-pool-{ts}"
    pool_resp = cognito.create_user_pool(
        PoolName=pool_name,
        Policies={'PasswordPolicy': {'MinimumLength': 8, 'RequireUppercase': False,
                                      'RequireLowercase': False, 'RequireNumbers': False,
                                      'RequireSymbols': False}}
    )
    pool_id = pool_resp['UserPool']['Id']
    print(f"✓ Cognito User Pool created: {pool_id}")

    domain_prefix = f"gw-hr-{ts}"
    cognito.create_user_pool_domain(UserPoolId=pool_id, Domain=domain_prefix)
    token_url = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/token"
    print(f"✓ Cognito domain: {domain_prefix}")

    resource_server_id = "hr-gateway"
    cognito.create_resource_server(
        UserPoolId=pool_id, Identifier=resource_server_id, Name='HR Gateway',
        Scopes=[
            {'ScopeName': 'read', 'ScopeDescription': 'Read access'},
            {'ScopeName': 'write', 'ScopeDescription': 'Write access'}
        ]
    )
    scope_string = f"{resource_server_id}/read {resource_server_id}/write"
    print(f"✓ Resource server with scopes: {scope_string}")

    client_resp = cognito.create_user_pool_client(
        UserPoolId=pool_id, ClientName='gateway-hr-client', GenerateSecret=True,
        AllowedOAuthFlows=['client_credentials'],
        AllowedOAuthScopes=[f'{resource_server_id}/read', f'{resource_server_id}/write'],
        AllowedOAuthFlowsUserPoolClient=True
    )
    client_id = client_resp['UserPoolClient']['ClientId']
    client_secret = client_resp['UserPoolClient']['ClientSecret']
    print(f"✓ App client created: {client_id}")

    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"

    return {
        'pool_id': pool_id, 'client_id': client_id, 'client_secret': client_secret,
        'token_url': token_url, 'discovery_url': discovery_url,
        'scope_string': scope_string, 'region': region
    }


def get_oauth_token(cognito_config):
    """Request an OAuth access token from Cognito using client_credentials flow."""
    import requests
    import base64

    auth_string = base64.b64encode(
        f"{cognito_config['client_id']}:{cognito_config['client_secret']}".encode()
    ).decode()

    resp = requests.post(
        cognito_config['token_url'],
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Authorization': f'Basic {auth_string}'},
        data={'grant_type': 'client_credentials', 'scope': cognito_config['scope_string']}
    )
    resp.raise_for_status()
    return resp.json()['access_token']


def delete_gateway(gateway_client, gateway_id):
    """Delete a Gateway and its targets."""
    try:
        targets = gateway_client.list_gateway_targets(gatewayIdentifier=gateway_id)
        for target in targets.get('items', []):
            gateway_client.delete_gateway_target(
                gatewayIdentifier=gateway_id, targetIdentifier=target['targetId'])
            print(f"✓ Deleted target: {target['name']}")
        gateway_client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"✓ Deleted gateway: {gateway_id}")
    except Exception as e:
        print(f"Error deleting gateway: {e}")
