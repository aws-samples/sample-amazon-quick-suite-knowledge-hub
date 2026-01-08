from aws_cdk import CfnOutput, CustomResource, Duration, NestedStack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class AgentCoreGatewayStack(NestedStack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        actuarial_lambda_arn: str,
        data_query_lambda_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Gateway Creator Role
        gateway_creator_role = iam.Role(
            self,
            "GatewayCreatorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        gateway_creator_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:*",
                    "cognito-idp:*",
                    "iam:CreateRole",
                    "iam:AttachRolePolicy",
                    "iam:PassRole",
                    "iam:GetRole",
                    "iam:DeleteRole",
                    "iam:DetachRolePolicy",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:PutDeliverySource",
                    "logs:PutDeliveryDestination",
                    "logs:CreateDelivery",
                    "logs:DeleteDelivery",
                    "logs:GetDelivery",
                    "logs:ListDeliveries",
                    "logs:PutDestination",
                    "logs:PutDestinationPolicy",
                    "logs:DescribeDestinations",
                    "logs:DeleteDestination",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                resources=["*"],
            )
        )

        # Add specific permissions for observability delivery destination
        gateway_creator_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:PutResourcePolicy",
                    "logs:DescribeResourcePolicies",
                    "logs:DeleteResourcePolicy",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:destination:*",
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/vendedlogs/bedrock-agentcore/*",
                ],
            )
        )

        # Gateway Creator Lambda
        gateway_lambda_code = self._get_gateway_lambda_code()

        gateway_creator_lambda = lambda_.Function(
            self,
            "GatewayCreatorLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            role=gateway_creator_role,
            timeout=Duration.minutes(15),
            memory_size=512,
            environment={
                "ACTUARIAL_LAMBDA_ARN": actuarial_lambda_arn,
                "DATA_QUERY_LAMBDA_ARN": data_query_lambda_arn,
            },
            code=lambda_.Code.from_inline(gateway_lambda_code),
        )

        # Custom Resource for Gateway Creation
        gateway_resource = CustomResource(
            self,
            "AgentCoreGateway",
            service_token=gateway_creator_lambda.function_arn,
            resource_type="Custom::AgentCoreGateway",
        )

        # Store outputs as properties for parent stack access
        self.gateway_url = gateway_resource.get_att_string("GatewayUrl")
        self.gateway_id = gateway_resource.get_att_string("GatewayId")
        self.client_id = gateway_resource.get_att_string("ClientId")
        self.client_secret = gateway_resource.get_att_string("ClientSecret")
        self.user_pool_id = gateway_resource.get_att_string("UserPoolId")
        self.token_endpoint = gateway_resource.get_att_string("TokenEndpoint")
        self.scope = gateway_resource.get_att_string("Scope")
        self.domain_prefix = gateway_resource.get_att_string("DomainPrefix")

        # Outputs (for nested stack)
        CfnOutput(
            self,
            "GatewayUrl",
            value=self.gateway_url,
            description="AgentCore Gateway URL",
        )
        CfnOutput(
            self, "GatewayId", value=self.gateway_id, description="AgentCore Gateway ID"
        )
        CfnOutput(
            self, "ClientId", value=self.client_id, description="Cognito Client ID"
        )
        CfnOutput(
            self,
            "ClientSecret",
            value=self.client_secret,
            description="Cognito Client Secret",
        )
        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool_id,
            description="Cognito User Pool ID",
        )
        CfnOutput(
            self,
            "TokenEndpoint",
            value=self.token_endpoint,
            description="OAuth Token Endpoint",
        )
        CfnOutput(self, "Scope", value=self.scope, description="OAuth Scope")
        CfnOutput(
            self,
            "DomainPrefix",
            value=self.domain_prefix,
            description="Cognito Domain Prefix",
        )

    def _get_gateway_lambda_code(self):
        # Load JSON tool definitions
        import json
        import os

        # Load actuarial tools
        actuarial_tools_path = os.path.join(
            os.path.dirname(__file__), "..", "tools", "agentcore_tools.json"
        )
        with open(actuarial_tools_path, encoding="utf-8") as f:
            actuarial_tools_json = json.dumps(json.load(f))

        # Load data query tools
        data_query_tools_path = os.path.join(
            os.path.dirname(__file__), "..", "tools", "data_query_tools.json"
        )
        with open(data_query_tools_path, encoding="utf-8") as f:
            data_query_tools_json = json.dumps(json.load(f))

        # Get base lambda code and inject JSON
        lambda_code = self._get_base_gateway_lambda_code()
        lambda_code = lambda_code.replace(
            "ACTUARIAL_TOOLS_PLACEHOLDER", actuarial_tools_json
        )
        lambda_code = lambda_code.replace(
            "DATA_QUERY_TOOLS_PLACEHOLDER", data_query_tools_json
        )

        return lambda_code

    def _get_base_gateway_lambda_code(self):
        return '''
import json
import time
import cfnresponse
import traceback
import os
import subprocess
import sys

def handler(event, context):
    print(f"Event: {json.dumps(event)}")

    try:
        # Set timeout protection
        remaining_time = context.get_remaining_time_in_millis()
        if remaining_time < 30000:  # Less than 30 seconds remaining
            raise Exception("Insufficient time remaining for gateway creation")

        if event['RequestType'] == 'Create':
            print("Installing bedrock-agentcore-starter-toolkit...")
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "--target", "/tmp",
                              "bedrock-agentcore-starter-toolkit"],
                              check=True, timeout=120, capture_output=True)
                sys.path.insert(0, '/tmp')
            except subprocess.TimeoutExpired:
                raise Exception("Timeout installing bedrock-agentcore-starter-toolkit")
            except subprocess.CalledProcessError as e:
                raise Exception(f"Failed to install toolkit: {e.stderr.decode()}")

            try:
                from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient
            except ImportError as e:
                raise Exception(f"Failed to import GatewayClient: {str(e)}")

            client = GatewayClient(region_name='us-east-1')
            import random
            gateway_name = f"actuarial-gateway-{random.randint(1000, 9999)}"

            print("Creating Cognito authorizer...")
            try:
                cognito_response = client.create_oauth_authorizer_with_cognito(f"{gateway_name}-pool")
            except Exception as e:
                raise Exception(f"Failed to create Cognito authorizer: {str(e)}")

            print("Creating AgentCore Gateway...")
            try:
                gateway = client.create_mcp_gateway(
                    name=gateway_name,
                    role_arn=None,
                    authorizer_config=cognito_response["authorizer_config"],
                    enable_semantic_search=True
                )
            except Exception as e:
                raise Exception(f"Failed to create gateway: {str(e)}")

            # Wait with timeout protection
            print("Waiting for gateway setup...")
            max_wait = min(60, (remaining_time - 60000) // 1000)  # Leave 1 min buffer
            time.sleep(min(10, max_wait))

            # Load tool schemas from embedded JSON with error handling
            try:
                actuarial_tools_json = """ACTUARIAL_TOOLS_PLACEHOLDER"""
                actuarial_tools = json.loads(actuarial_tools_json)

                data_query_tools_json = """DATA_QUERY_TOOLS_PLACEHOLDER"""
                data_query_config = json.loads(data_query_tools_json)
                data_query_tools = data_query_config.get('tools', [])
            except json.JSONDecodeError as e:
                raise Exception(f"Failed to parse tool schemas: {str(e)}")

            print("Adding Lambda targets...")
            try:
                client.create_mcp_gateway_target(
                    gateway=gateway, name="actuarial-tools-target", target_type="lambda",
                    target_payload={"lambdaArn": os.environ['ACTUARIAL_LAMBDA_ARN'],
                                  "toolSchema": {"inlinePayload": actuarial_tools}}
                )

                client.create_mcp_gateway_target(
                    gateway=gateway, name="data-query-target", target_type="lambda",
                    target_payload={"lambdaArn": os.environ['DATA_QUERY_LAMBDA_ARN'],
                                  "toolSchema": {"inlinePayload": data_query_tools}}
                )
            except Exception as e:
                raise Exception(f"Failed to add Lambda targets: {str(e)}")

            client_info = cognito_response.get("client_info", {})
            gateway_url = f"https://{gateway['gatewayId']}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"

            response_data = {
                "GatewayUrl": gateway_url,
                "GatewayId": gateway['gatewayId'],
                "ClientId": client_info.get('client_id', ''),
                "ClientSecret": client_info.get('client_secret', ''),
                "UserPoolId": client_info.get('user_pool_id', ''),
                "TokenEndpoint": client_info.get('token_endpoint', ''),
                "Scope": client_info.get('scope', ''),
                "DomainPrefix": client_info.get('domain_prefix', '')
            }

            print(f"Gateway created: {gateway_url}")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data, gateway['gatewayId'])

        elif event['RequestType'] == 'Delete':
            gateway_id = event.get('PhysicalResourceId')
            if gateway_id and gateway_id != 'PLACEHOLDER':
                try:
                    import boto3
                    bedrock = boto3.client('bedrock-agentcore', region_name='us-east-1')
                    bedrock.delete_gateway(gatewayIdentifier=gateway_id)
                    print("Gateway deleted successfully")
                except Exception as e:
                    print(f"Delete error (non-fatal): {e}")
                    # Don't fail on delete errors - allow stack deletion to proceed
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})

        else:
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})

    except Exception as e:
        error_msg = f"Gateway deployment failed: {str(e)}"
        print(error_msg)
        print(f"Full traceback: {traceback.format_exc()}")

        # Always send failure response to prevent infinite wait
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": error_msg})
'''
