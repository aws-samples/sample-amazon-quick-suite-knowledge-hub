"""
Redshift AgentCore Gateway - Amazon Redshift MCP Integration

CDK stack for Amazon Redshift MCP integration with QuickSuite
following the actuarial-analysis-solution pattern.
"""

import os
import shutil
import subprocess

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
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
from constructs import Construct

from .gateway_stack import AgentCoreGatewayStack


class RedshiftAgentCoreStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create Lambda execution role with Redshift permissions
        lambda_role = iam.Role(
            self,
            "RedshiftAgentCoreLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
            inline_policies={
                "RedshiftReadOnlyAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "redshift:DescribeClusters",
                                "redshift-data:DescribeStatement",
                                "redshift-data:GetStatementResult",
                                "redshift-data:ListStatements",
                                "redshift-serverless:ListWorkgroups",
                                "redshift-serverless:GetWorkgroup",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "redshift-data:ExecuteStatement",
                            ],
                            resources=["*"],
                            conditions={
                                "StringLike": {
                                    "redshift-data:StatementName": "read-only-*"
                                }
                            },
                        ),
                    ]
                )
            },
        )

        # Lambda Layers
        self._build_agentcore_layer()

        agentcore_layer = _lambda.LayerVersion(
            self,
            "RedshiftAgentCoreLayer",
            code=_lambda.Code.from_asset("cdk.out/agentcore_layer"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_12],
            description="AgentCore layer built with Docker for Linux Lambda environment",
        )

        # Create AgentCore Lambda function
        agentcore_lambda = _lambda.Function(
            self,
            "RedshiftAgentCoreLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="redshift_agentcore_lambda.handler",
            code=_lambda.Code.from_asset("tools"),
            role=lambda_role,
            layers=[agentcore_layer],
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "LOG_LEVEL": "INFO",
                "POWERTOOLS_SERVICE_NAME": "redshift-agentcore",
            },
            log_retention=logs.RetentionDays.ONE_MONTH,
            reserved_concurrent_executions=10,
        )

        # AgentCore Gateway (as nested stack)
        gateway_stack = AgentCoreGatewayStack(
            self,
            "RedshiftGatewayStack",
            redshift_lambda_arn=agentcore_lambda.function_arn,
        )

        # Outputs
        CfnOutput(
            self,
            "GatewayUrl",
            value=gateway_stack.gateway_url,
            description="AgentCore Gateway URL",
        )
        CfnOutput(
            self,
            "GatewayId",
            value=gateway_stack.gateway_id,
            description="AgentCore Gateway ID",
        )
        CfnOutput(
            self,
            "ClientId",
            value=gateway_stack.client_id,
            description="Cognito Client ID",
        )
        CfnOutput(
            self,
            "ClientSecret",
            value=gateway_stack.client_secret,
            description="Cognito Client Secret",
        )
        CfnOutput(
            self,
            "UserPoolId",
            value=gateway_stack.user_pool_id,
            description="Cognito User Pool ID",
        )
        CfnOutput(
            self,
            "TokenEndpoint",
            value=gateway_stack.token_endpoint,
            description="OAuth Token Endpoint",
        )
        CfnOutput(self, "Scope", value=gateway_stack.scope, description="OAuth Scope")
        CfnOutput(
            self,
            "DomainPrefix",
            value=gateway_stack.domain_prefix,
            description="Cognito Domain Prefix",
        )

    def _build_agentcore_layer(self):
        layer_dir = "cdk.out/agentcore_layer"

        if os.path.exists(layer_dir):
            shutil.rmtree(layer_dir)
        os.makedirs(layer_dir, exist_ok=True)

        with open(
            os.path.join(layer_dir, "requirements.txt"), "w", encoding="utf-8"
        ) as f:
            f.write("bedrock-agentcore\n")

        shutil.copy2("cdk/Dockerfile.agentcore", os.path.join(layer_dir, "Dockerfile"))

        subprocess.run(
            ["docker", "build", "-t", "agentcore-layer", layer_dir], check=True
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{os.path.abspath(layer_dir)}:/output",
                "agentcore-layer",
            ],
            check=True,
        )
