"""
Quick Observability MCP Stack

Deploys:
1. CloudWatch Log Groups for Quick
2. Log delivery configuration
3. AgentCore Gateway with Lambda target
4. Cognito for OAuth2
5. MCP observability tools
"""

import hashlib
import json
import os
import re
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    CustomResource,
    Duration,
    Stack,
)
from aws_cdk import (
    aws_bedrockagentcore as bedrockagentcore,
)
from aws_cdk import (
    aws_cognito as cognito,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as _lambda,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    custom_resources as cr,
)
from constructs import Construct


class QuickSuiteObservabilityMCPStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ====================================================================
        # PART 1: CloudWatch Log Groups
        # ====================================================================

        # Import existing log groups instead of creating new ones
        chat_log_group = logs.LogGroup.from_log_group_name(
            self, "ChatLogsGroup", log_group_name="/aws/quicksuite/chat"
        )

        feedback_log_group = logs.LogGroup.from_log_group_name(
            self, "FeedbackLogsGroup", log_group_name="/aws/quicksuite/feedback"
        )

        agent_hours_log_group = logs.LogGroup.from_log_group_name(
            self, "AgentHoursLogsGroup", log_group_name="/aws/quicksuite/agent-hours"
        )

        # Add resource policies to allow CloudWatch Logs delivery service
        for log_group_name in [
            "/aws/quicksuite/chat",
            "/aws/quicksuite/feedback",
            "/aws/quicksuite/agent-hours",
        ]:
            logs.CfnResourcePolicy(
                self,
                f"LogDeliveryPolicy{log_group_name.replace('/', '-')}",
                policy_name=f"QuickSuiteLogDeliveryPolicy{log_group_name.replace('/', '-')}",
                policy_document=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"Service": "delivery.logs.amazonaws.com"},
                                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                                "Resource": f"arn:aws:logs:{self.region}:{self.account}:log-group:{log_group_name}:*",
                            }
                        ],
                    }
                ),
            )

        # ====================================================================
        # PART 2: Enable Log Delivery (Custom Resource)
        # ====================================================================

        enable_logging_role = iam.Role(
            self,
            "EnableLoggingRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "LambdaBasicExecution": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            resources=["arn:aws:logs:*:*:*"],
                        ),
                    ]
                ),
                "LogsDeliveryAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:PutDeliverySource",
                                "logs:PutDeliveryDestination",
                                "logs:CreateDelivery",
                                "logs:GetDeliverySource",
                                "logs:GetDeliveryDestination",
                                "logs:GetDelivery",
                            ],
                            resources=["*"],
                        ),
                    ]
                ),
                "QuickSightLogDelivery": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "quicksight:AllowVendedLogDeliveryForResource",
                            ],
                            resources=["*"],
                        ),
                    ]
                ),
            },
        )

        enable_logging_function = _lambda.Function(
            self,
            "EnableLoggingFunction",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            role=enable_logging_role,
            timeout=Duration.seconds(60),
            code=_lambda.InlineCode("""
import json
import boto3
import time

logs_client = boto3.client('logs')

def handler(event, context):
    request_type = event.get('RequestType', 'Create')
    account_id = context.invoked_function_arn.split(':')[4]
    region = context.invoked_function_arn.split(':')[3]

    if request_type in ['Create', 'Update']:
        configs = [
            {'log_type': 'CHAT_LOGS', 'source': 'quicksuite-chat-logs-source', 'log_group': '/aws/quicksuite/chat', 'dest': 'quicksuite-chat-logs-destination'},
            {'log_type': 'FEEDBACK_LOGS', 'source': 'quicksuite-feedback-logs-source', 'log_group': '/aws/quicksuite/feedback', 'dest': 'quicksuite-feedback-logs-destination'},
            {'log_type': 'AGENT_HOURS_LOGS', 'source': 'quicksuite-agent-hours-logs-source', 'log_group': '/aws/quicksuite/agent-hours', 'dest': 'quicksuite-agent-hours-logs-destination'}
        ]

        for config in configs:
            # Check if delivery already exists
            try:
                response = logs_client.describe_deliveries()
                if config['source'] in [d['deliverySourceName'] for d in response.get('deliveries', [])]:
                    continue
            except: pass

            # Create destination
            try:
                logs_client.put_delivery_destination(
                    name=config['dest'],
                    outputFormat='json',
                    deliveryDestinationConfiguration={'destinationResourceArn': f"arn:aws:logs:{region}:{account_id}:log-group:{config['log_group']}"}
                )
            except: pass

            time.sleep(1)

            # Create source
            try:
                logs_client.put_delivery_source(
                    name=config['source'],
                    resourceArn=f"arn:aws:quicksight:{region}:{account_id}:account/{account_id}",
                    logType=config['log_type']
                )
            except: pass

            time.sleep(1)

            # Create delivery
            try:
                logs_client.create_delivery(
                    deliverySourceName=config['source'],
                    deliveryDestinationArn=f"arn:aws:logs:{region}:{account_id}:delivery-destination:{config['dest']}"
                )
            except: pass

    return {'PhysicalResourceId': 'quicksuite-logging-enabled'}
            """),
        )

        enable_logging_provider = cr.Provider(
            self,
            "EnableLoggingProvider",
            on_event_handler=enable_logging_function,
        )

        enable_logging_cr = CustomResource(
            self,
            "EnableLoggingCustomResource",
            service_token=enable_logging_provider.service_token,
        )

        enable_logging_cr.node.add_dependency(chat_log_group)
        enable_logging_cr.node.add_dependency(feedback_log_group)
        enable_logging_cr.node.add_dependency(agent_hours_log_group)

        # ====================================================================
        # PART 3: MCP Lambda and AgentCore Gateway
        # ====================================================================

        # Cognito domain prefix (must be globally unique)
        raw_prefix = f"{self.stack_name}-{self.account[-6:]}"
        sanitized = (
            re.sub("[^a-z0-9-]", "-", raw_prefix.lower()).strip("-")[:40] or "app"
        )
        h = hashlib.sha256(raw_prefix.encode("utf-8")).hexdigest()[:6]
        domain_prefix = f"{sanitized}-{h}"

        # Lambda role with CloudWatch permissions
        lambda_role = iam.Role(
            self,
            "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
            inline_policies={
                "CloudWatchAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:StartQuery",
                                "logs:GetQueryResults",
                                "logs:DescribeLogGroups",
                                "cloudwatch:GetMetricStatistics",
                                "cloudwatch:ListMetrics",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cloudtrail:LookupEvents",
                            ],
                            resources=["*"],
                        ),
                    ]
                )
            },
        )

        # Lambda function
        tools_dir = str(Path(__file__).parent.parent.joinpath("tools"))

        mcp_function = _lambda.Function(
            self,
            "QuickSuiteObservabilityFunction",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="quicksuite_observability_lambda.handler",
            code=_lambda.Code.from_asset(tools_dir),
            role=lambda_role,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "CHAT_LOG_GROUP": "/aws/quicksuite/chat",
                "FEEDBACK_LOG_GROUP": "/aws/quicksuite/feedback",
                "AGENT_HOURS_LOG_GROUP": "/aws/quicksuite/agent-hours",
            },
        )

        # Add permission for Bedrock AgentCore to invoke Lambda
        mcp_function.add_permission(
            "AllowAgentCoreInvoke",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction",
        )

        # Cognito User Pool
        user_pool = cognito.UserPool(
            self,
            "MCPUserPool",
            user_pool_name=f"{self.stack_name}-user-pool",
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_uppercase=True,
                require_lowercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            mfa=cognito.Mfa.OFF,
            account_recovery=cognito.AccountRecovery.EMAIL_AND_PHONE_WITHOUT_MFA,
        )

        # Cognito domain
        user_pool_domain = user_pool.add_domain(
            "MCPUserPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )

        # Resource server with scope
        resource_server_name = f"{self.stack_name.lower()}-pool"
        invoke_scope = cognito.ResourceServerScope(
            scope_name="invoke",
            scope_description="Scope for invoking the agentcore gateway",
        )

        resource_server = user_pool.add_resource_server(
            "MCPResourceServer",
            identifier=resource_server_name,
            user_pool_resource_server_name=resource_server_name,
            scopes=[invoke_scope],
        )

        # User Pool Client
        user_pool_client = cognito.UserPoolClient(
            self,
            "MCPUserPoolClient",
            user_pool=user_pool,
            user_pool_client_name=f"{self.stack_name}-client",
            generate_secret=True,
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[
                    cognito.OAuthScope.resource_server(resource_server, invoke_scope)
                ],
            ),
            refresh_token_validity=Duration.days(30),
            auth_session_validity=Duration.minutes(3),
            enable_token_revocation=True,
        )

        # Gateway IAM role
        gateway_role = iam.Role(
            self,
            "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "GatewayPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="BedrockAgentCoreFullAccess",
                            effect=iam.Effect.ALLOW,
                            actions=["bedrock-agentcore:*"],
                            resources=["arn:aws:bedrock-agentcore:*:*:*"],
                        ),
                        iam.PolicyStatement(
                            sid="GetSecretValue",
                            effect=iam.Effect.ALLOW,
                            actions=["secretsmanager:GetSecretValue"],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            sid="LambdaInvokeAccess",
                            effect=iam.Effect.ALLOW,
                            actions=["lambda:InvokeFunction"],
                            resources=["arn:aws:lambda:*:*:function:*"],
                        ),
                    ]
                )
            },
        )

        # MCP Gateway
        mcp_gateway = bedrockagentcore.CfnGateway(
            self,
            "MCPGateway",
            name=f"{self.stack_name.lower()}-gateway",
            protocol_type="MCP",
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=bedrockagentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=bedrockagentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}/.well-known/openid-configuration",
                    allowed_clients=[user_pool_client.user_pool_client_id],
                )
            ),
            role_arn=gateway_role.role_arn,
        )

        mcp_gateway.add_dependency(user_pool.node.default_child)
        mcp_gateway.add_dependency(user_pool_client.node.default_child)

        # Load tool schema
        tools_json_path = os.path.join(tools_dir, "quicksuite_observability_tools.json")
        with open(tools_json_path, encoding="utf-8") as f:
            tools_list = json.load(f)  # Direct array now

            # Convert to CDK ToolDefinitionProperty format
            tools_schema = []
            for tool in tools_list:
                tools_schema.append(
                    bedrockagentcore.CfnGatewayTarget.ToolDefinitionProperty(
                        name=tool["name"],
                        description=tool["description"],
                        input_schema=tool["inputSchema"],
                    )
                )

        # Gateway Target
        gateway_target = bedrockagentcore.CfnGatewayTarget(
            self,
            "GatewayTarget",
            credential_provider_configurations=[
                bedrockagentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                )
            ],
            name="observability-lambda-target",
            gateway_identifier=mcp_gateway.attr_gateway_identifier,
            target_configuration=bedrockagentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=bedrockagentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=bedrockagentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=mcp_function.function_arn,
                        tool_schema=bedrockagentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=tools_schema
                        ),
                    )
                )
            ),
        )

        gateway_target.add_dependency(mcp_gateway)

        # Outputs
        CfnOutput(
            self,
            "GatewayUrl",
            value=mcp_gateway.attr_gateway_url,
            description="MCP Gateway URL",
        )

        CfnOutput(
            self,
            "ClientId",
            value=user_pool_client.user_pool_client_id,
            description="Cognito Client ID",
        )

        CfnOutput(
            self,
            "ClientSecret",
            value=user_pool_client.user_pool_client_secret.unsafe_unwrap(),
            description="Cognito Client Secret",
        )

        CfnOutput(
            self,
            "UserPoolId",
            value=user_pool.user_pool_id,
            description="Cognito User Pool ID",
        )

        CfnOutput(
            self,
            "CognitoTokenUrl",
            value=(
                f"https://{user_pool_domain.domain_name}.auth."
                f"{self.region}.amazoncognito.com/oauth2/token"
            ),
            description="Cognito OAuth2 Token URL",
        )

        CfnOutput(
            self,
            "FunctionArn",
            value=mcp_function.function_arn,
            description="Lambda Function ARN",
        )

        CfnOutput(
            self,
            "ChatLogGroup",
            value=chat_log_group.log_group_name,
            description="Chat logs CloudWatch Log Group",
        )

        CfnOutput(
            self,
            "FeedbackLogGroup",
            value=feedback_log_group.log_group_name,
            description="Feedback logs CloudWatch Log Group",
        )

        CfnOutput(
            self,
            "AgentHoursLogGroup",
            value=agent_hours_log_group.log_group_name,
            description="Agent hours logs CloudWatch Log Group",
        )
