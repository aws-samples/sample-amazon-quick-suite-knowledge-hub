"""
CDK Stack: Compliance Assistant infrastructure.

Creates DynamoDB, S3, Lambda, Cognito (OAuth), AgentCore Gateway,
Gateway Target with 3 MCP tool schemas, and all IAM roles/policies.
"""

import os

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    CfnResource,
    Duration,
    RemovalPolicy,
)
from aws_cdk import (
    aws_cognito as cognito,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as _lambda,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct


class ComplianceAssistantStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project_name: str,
        runtime_arn: str,
        agent_id: str,
        agent_alias_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---------------------------------------------------------------
        # DynamoDB: job state tracking
        # ---------------------------------------------------------------
        jobs_table = dynamodb.Table(
            self,
            "JobsTable",
            table_name=f"{project_name}-jobs",
            partition_key=dynamodb.Attribute(
                name="job_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ---------------------------------------------------------------
        # S3: report storage
        # ---------------------------------------------------------------
        reports_bucket = s3.Bucket(
            self,
            "ReportsBucket",
            bucket_name=f"{project_name}-reports-{cdk.Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(id="ExpireOldReports", expiration=Duration.days(30))
            ],
        )

        # ---------------------------------------------------------------
        # Lambda: Gateway target (MCP tool orchestrator)
        # ---------------------------------------------------------------
        # Constructed ARN to break circular dependencies
        lambda_arn_for_gateway = (
            f"arn:aws:lambda:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}"
            f":function:{project_name}-gateway-target"
        )

        lambda_fn = _lambda.Function(
            self,
            "GatewayTargetLambda",
            function_name=f"{project_name}-gateway-target",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "..", "runtime"),
                exclude=["*.md", "*.yaml", "*.txt", "agent.py"],
            ),
            timeout=Duration.seconds(600),
            memory_size=256,
            environment={
                "JOBS_TABLE": jobs_table.table_name,
                "REPORTS_BUCKET": reports_bucket.bucket_name,
                "DEPLOY_REGION": cdk.Aws.REGION,
                "RUNTIME_ARN": runtime_arn,
                "FUNCTION_NAME": f"{project_name}-gateway-target",
            },
        )

        jobs_table.grant_read_write_data(lambda_fn)
        reports_bucket.grant_read(lambda_fn, "reports/*")

        if runtime_arn:
            lambda_fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["bedrock-agentcore:*"],
                    resources=[runtime_arn, f"{runtime_arn}/*"],
                )
            )

        # Self-invoke for async fire-and-forget pattern
        lambda_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[lambda_arn_for_gateway],
            )
        )

        # ---------------------------------------------------------------
        # Cognito: OAuth2 for Gateway authentication
        # ---------------------------------------------------------------
        user_pool = cognito.UserPool(
            self,
            "GatewayUserPool",
            user_pool_name=f"{project_name}-gateway-auth",
            removal_policy=RemovalPolicy.DESTROY,
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_uppercase=True,
                require_lowercase=True,
                require_digits=True,
                require_symbols=False,
            ),
        )

        resource_server_identifier = f"agentcore-{project_name}"

        resource_server = user_pool.add_resource_server(
            "GatewayResourceServer",
            identifier=resource_server_identifier,
            user_pool_resource_server_name=f"{project_name} Resource Server",
            scopes=[
                cognito.ResourceServerScope(
                    scope_name="invoke",
                    scope_description="Invoke gateway tools",
                )
            ],
        )

        user_pool.add_domain(
            "GatewayDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"{project_name}-gw-{cdk.Aws.ACCOUNT_ID}",
            ),
        )

        app_client = user_pool.add_client(
            "GatewayAppClient",
            user_pool_client_name=f"{project_name}-gateway-client",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[
                    cognito.OAuthScope.resource_server(
                        resource_server,
                        cognito.ResourceServerScope(
                            scope_name="invoke",
                            scope_description="Invoke gateway tools",
                        ),
                    )
                ],
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
        )

        # ---------------------------------------------------------------
        # AgentCore Gateway: MCP server endpoint
        # ---------------------------------------------------------------
        gateway_role = iam.Role(
            self,
            "GatewayRole",
            role_name=f"{project_name}-gateway-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="AgentCore Gateway execution role",
        )

        gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[lambda_arn_for_gateway],
            )
        )

        discovery_url = (
            f"https://cognito-idp.{cdk.Aws.REGION}.amazonaws.com/"
            f"{user_pool.user_pool_id}/.well-known/openid-configuration"
        )

        gateway = CfnResource(
            self,
            "AgentCoreGateway",
            type="AWS::BedrockAgentCore::Gateway",
            properties={
                "Name": f"{project_name}-gateway",
                "RoleArn": gateway_role.role_arn,
                "ProtocolType": "MCP",
                "ProtocolConfiguration": {
                    "Mcp": {
                        "SupportedVersions": ["2025-03-26"],
                        "Instructions": (
                            "This gateway provides compliance analysis tools. "
                            "Use start_compliance_analysis to begin an analysis, "
                            "get_analysis_status to check progress, and "
                            "get_analysis_report to retrieve the final report."
                        ),
                        "SearchType": "SEMANTIC",
                    }
                },
                "AuthorizerType": "CUSTOM_JWT",
                "AuthorizerConfiguration": {
                    "CustomJWTAuthorizer": {
                        "DiscoveryUrl": discovery_url,
                        "AllowedClients": [app_client.user_pool_client_id],
                    }
                },
            },
        )

        # ---------------------------------------------------------------
        # Gateway Target: Lambda with 3 MCP tool schemas
        # ---------------------------------------------------------------
        tool_schemas = [
            {
                "Name": "start_compliance_analysis",
                "Description": (
                    "Start a compliance analysis on a given regulatory topic. "
                    "This kicks off a multi-agent analysis pipeline that typically takes 3-5 minutes. "
                    "Returns a job_id. Use get_analysis_status to check progress and "
                    "get_analysis_report to retrieve the final report when complete."
                ),
                "InputSchema": {
                    "Type": "object",
                    "Properties": {
                        "topic": {
                            "Type": "string",
                            "Description": "The compliance or regulatory topic to analyze.",
                        }
                    },
                    "Required": ["topic"],
                },
            },
            {
                "Name": "get_analysis_status",
                "Description": (
                    "Check the current status of a compliance analysis job. "
                    "Returns status (PENDING, RUNNING, COMPLETED, FAILED) and progress details."
                ),
                "InputSchema": {
                    "Type": "object",
                    "Properties": {
                        "job_id": {
                            "Type": "string",
                            "Description": "The job ID returned by start_compliance_analysis.",
                        }
                    },
                    "Required": ["job_id"],
                },
            },
            {
                "Name": "get_analysis_report",
                "Description": (
                    "Retrieve the completed compliance analysis report. "
                    "Returns the full markdown report if the job is complete, "
                    "or current status if still running."
                ),
                "InputSchema": {
                    "Type": "object",
                    "Properties": {
                        "job_id": {
                            "Type": "string",
                            "Description": "The job ID returned by start_compliance_analysis.",
                        }
                    },
                    "Required": ["job_id"],
                },
            },
        ]

        gateway_target = CfnResource(
            self,
            "GatewayTarget",
            type="AWS::BedrockAgentCore::GatewayTarget",
            properties={
                "GatewayIdentifier": gateway.get_att("GatewayIdentifier"),
                "Name": f"{project_name}-tools",
                "CredentialProviderConfigurations": [
                    {"CredentialProviderType": "GATEWAY_IAM_ROLE"}
                ],
                "TargetConfiguration": {
                    "Mcp": {
                        "Lambda": {
                            "LambdaArn": lambda_arn_for_gateway,
                            "ToolSchema": {"InlinePayload": tool_schemas},
                        }
                    }
                },
            },
        )

        gateway_target.add_dependency(gateway)
        gateway_target.add_dependency(lambda_fn.node.default_child)

        # Allow AgentCore Gateway service to invoke the Lambda
        # (standalone CfnResource to avoid circular dependency)
        gateway_invoke_permission = _lambda.CfnPermission(
            self,
            "AgentCoreGatewayInvoke",
            function_name=f"{project_name}-gateway-target",
            action="lambda:InvokeFunction",
            principal="bedrock-agentcore.amazonaws.com",
        )
        gateway_invoke_permission.add_dependency(lambda_fn.node.default_child)

        # ---------------------------------------------------------------
        # Outputs
        # ---------------------------------------------------------------
        CfnOutput(
            self,
            "LambdaArn",
            description="Lambda ARN for the MCP tool orchestrator",
            value=lambda_fn.function_arn,
        )

        CfnOutput(
            self,
            "JobsTableName",
            description="DynamoDB table for job tracking",
            value=jobs_table.table_name,
        )

        CfnOutput(
            self,
            "ReportsBucketName",
            description="S3 bucket for compliance reports",
            value=reports_bucket.bucket_name,
        )

        CfnOutput(
            self,
            "CognitoUserPoolId",
            description="Cognito User Pool ID",
            value=user_pool.user_pool_id,
        )

        CfnOutput(
            self,
            "CognitoClientId",
            description="Cognito App Client ID (use with client_secret for OAuth)",
            value=app_client.user_pool_client_id,
        )

        CfnOutput(
            self,
            "CognitoTokenUrl",
            description="OAuth2 token endpoint",
            value=(
                f"https://{project_name}-gw-{cdk.Aws.ACCOUNT_ID}"
                f".auth.{cdk.Aws.REGION}.amazoncognito.com/oauth2/token"
            ),
        )

        CfnOutput(
            self,
            "CognitoScopes",
            description="OAuth2 scopes for Gateway access",
            value=f"{resource_server_identifier}/invoke",
        )

        CfnOutput(
            self,
            "GatewayId",
            description="AgentCore Gateway ID",
            value=gateway.get_att("GatewayIdentifier").to_string(),
        )

        CfnOutput(
            self,
            "GatewayUrl",
            description="AgentCore Gateway URL (MCP Server endpoint for Quick)",
            value=gateway.get_att("GatewayUrl").to_string(),
        )

        CfnOutput(
            self,
            "GatewayTargetId",
            description="Gateway Target ID",
            value=gateway_target.get_att("TargetId").to_string(),
        )

        CfnOutput(
            self,
            "AgentId",
            description="Bedrock Agent ID (passed as input)",
            value=agent_id,
        )

        CfnOutput(
            self,
            "AgentAliasId",
            description="Bedrock Agent Alias ID (passed as input)",
            value=agent_alias_id,
        )
