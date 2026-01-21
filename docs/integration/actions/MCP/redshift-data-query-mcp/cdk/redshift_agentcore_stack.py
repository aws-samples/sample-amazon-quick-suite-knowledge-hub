"""
Amazon Redshift Data Query MCP Integration Stack

CDK stack that deploys Amazon Redshift integration with Amazon Quick Suite
using MCP (Model Context Protocol) through native BedrockAgentCore Gateway constructs.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from aws_cdk import (
    CfnOutput,
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
from aws_cdk import aws_lambda_python_alpha as lambda_python
from constructs import Construct


class RedshiftAgentCoreStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Cognito domain prefix (must be globally unique & match pattern)
        raw_prefix = f"{self.stack_name}-{self.account[-6:]}"
        sanitized = (
            re.sub("[^a-z0-9-]", "-", raw_prefix.lower()).strip("-")[:40] or "app"
        )
        h = hashlib.sha256(raw_prefix.encode("utf-8")).hexdigest()[:6]
        domain_prefix = f"{sanitized}-{h}"

        # IAM role for Redshift Lambda function
        lambda_role = iam.Role(
            self,
            "RedshiftLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
            inline_policies={
                "RedshiftDataAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                # Redshift Provisioned Clusters
                                "redshift:DescribeClusters",
                                # Redshift Serverless
                                "redshift-serverless:ListWorkgroups",
                                "redshift-serverless:GetWorkgroup",
                                "redshift-serverless:GetCredentials",
                                # Redshift Data API (core functionality)
                                "redshift-data:ExecuteStatement",
                                "redshift-data:DescribeStatement",
                                "redshift-data:GetStatementResult",
                                "redshift-data:ListStatements",
                                "redshift-data:CancelStatement",
                                # IAM for temporary credentials (if needed)
                                "sts:GetCallerIdentity",
                            ],
                            resources=["*"],
                        ),
                    ]
                )
            },
        )

        # Get path to tools directory
        entry_path = str(Path(__file__).parent.parent.joinpath("tools"))

        # Redshift Lambda function using PythonFunction
        redshift_lambda = lambda_python.PythonFunction(
            self,
            "RedshiftLambda",
            entry=entry_path,
            runtime=_lambda.Runtime.PYTHON_3_13,
            index="redshift_agentcore_lambda.py",
            handler="handler",
            role=lambda_role,
            timeout=Duration.minutes(15),
            memory_size=1024,
        )

        # Add permission for Bedrock AgentCore to invoke the Lambda
        redshift_lambda.add_permission(
            "RedshiftLambdaAllowAgentCoreInvoke",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction",
        )

        # Cognito User Pool (machine-to-machine auth via client credentials)
        user_pool = cognito.UserPool(
            self,
            "RedshiftUserPool",
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

        # Hosted Cognito domain
        user_pool_domain = user_pool.add_domain(
            "RedshiftUserPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )

        # Add custom resource scope for the gateway
        resource_server_name = f"{self.stack_name.lower()}-pool"
        custom_scope_name = "invoke"

        # Create the scope object first
        invoke_scope = cognito.ResourceServerScope(
            scope_name=custom_scope_name,
            scope_description="Scope for invoking the agentcore gateway",
        )

        resource_server = user_pool.add_resource_server(
            "RedshiftResourceServer",
            identifier=resource_server_name,
            user_pool_resource_server_name=resource_server_name,
            scopes=[invoke_scope],
        )

        user_pool_client = cognito.UserPoolClient(
            self,
            "RedshiftUserPoolClient",
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

        # IAM role for the AgentCore Gateway
        gateway_role = iam.Role(
            self,
            "RedshiftGatewayRole",
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

        # MCP Gateway (native L1 construct)
        mcp_gateway = bedrockagentcore.CfnGateway(
            self,
            "RedshiftMCPGateway",
            name=f"{self.stack_name.lower()}-redshift-gateway",
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

        # Add explicit dependency to ensure Cognito is created first
        mcp_gateway.add_dependency(user_pool.node.default_child)
        mcp_gateway.add_dependency(user_pool_client.node.default_child)

        # Load MCP tool schema from JSON
        redshift_tools_path = os.path.join(
            os.path.dirname(__file__), "..", "tools", "redshift_agentcore_tools.json"
        )

        with open(redshift_tools_path, encoding="utf-8") as f:
            redshift_tools = json.load(f)

        # Gateway Target pointing to the Lambda function (MCP Lambda target)
        gateway_target = bedrockagentcore.CfnGatewayTarget(
            self,
            "RedshiftGatewayTarget",
            credential_provider_configurations=[
                bedrockagentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                )
            ],
            name="redshift-lambda-target",
            gateway_identifier=mcp_gateway.attr_gateway_identifier,
            target_configuration=bedrockagentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=bedrockagentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=bedrockagentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=redshift_lambda.function_arn,
                        tool_schema=bedrockagentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=redshift_tools
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
