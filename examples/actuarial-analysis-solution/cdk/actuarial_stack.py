"""
Actuarial Analysis Solution MCP Integration Stack

CDK stack that deploys Actuarial Analysis tools with Amazon Quick
using MCP (Model Context Protocol) through native BedrockAgentCore Gateway constructs.
"""

import hashlib
import json
import os
import re
import shutil
import uuid

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_athena as athena,
)
from aws_cdk import (
    aws_bedrockagentcore as bedrockagentcore,
)
from aws_cdk import (
    aws_cognito as cognito,
)
from aws_cdk import (
    aws_glue as glue,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_deployment as s3deploy,
)
from aws_cdk import (
    custom_resources as cr,
)
from constructs import Construct


class ActuarialToolsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        unique_id = str(uuid.uuid4())[:8]

        # S3 Buckets
        claims_bucket = s3.Bucket(
            self,
            "ClaimsBucket",
            bucket_name=f"{self.stack_name}-claims-{unique_id}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        athena_results_bucket = s3.Bucket(
            self,
            "AthenaResultsBucket",
            bucket_name=f"{self.stack_name}-athena-results-{unique_id}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        s3deploy.BucketDeployment(
            self,
            "DeploySampleData",
            sources=[s3deploy.Source.asset("sample_data")],
            destination_bucket=claims_bucket,
            destination_key_prefix="claims/",
        )

        # Glue Database and Crawler
        glue_database = glue.CfnDatabase(
            self,
            "ClaimsDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=f"{self.stack_name}-claims-db-{unique_id}",
                description="Database for insurance claims data",
            ),
        )

        crawler_role = iam.Role(
            self,
            "GlueCrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSGlueServiceRole"
                )
            ],
        )
        claims_bucket.grant_read(crawler_role)

        glue_crawler = glue.CfnCrawler(
            self,
            "ClaimsCrawler",
            name=f"{self.stack_name}-claims-crawler-{unique_id}",
            role=crawler_role.role_arn,
            database_name=f"{self.stack_name}-claims-db-{unique_id}",
            targets=glue.CfnCrawler.TargetsProperty(
                s3_targets=[
                    glue.CfnCrawler.S3TargetProperty(
                        path=f"s3://{claims_bucket.bucket_name}/claims/"
                    )
                ]
            ),
            schema_change_policy=glue.CfnCrawler.SchemaChangePolicyProperty(
                update_behavior="UPDATE_IN_DATABASE", delete_behavior="LOG"
            ),
        )
        glue_crawler.add_dependency(glue_database)

        # Athena Workgroup
        athena.CfnWorkGroup(
            self,
            "ActuarialWorkGroup",
            name=f"actuarial-workgroup-{unique_id}",
            description="Workgroup for actuarial analysis queries",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{athena_results_bucket.bucket_name}/query-results/"
                ),
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
            ),
        )

        # Custom resource to start crawler
        cr.AwsCustomResource(
            self,
            "StartCrawlerCustomResource",
            on_create=cr.AwsSdkCall(
                service="Glue",
                action="startCrawler",
                parameters={"Name": glue_crawler.name},
                physical_resource_id=cr.PhysicalResourceId.of("start-crawler"),
                ignore_error_codes_matching="CrawlerRunningException|InvalidInputException",
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=["glue:StartCrawler"],
                        resources=[
                            f"arn:aws:glue:{self.region}:{self.account}:crawler/*"
                        ],
                    )
                ]
            ),
        )

        # Lambda Layers
        data_wrangler_layer = lambda_.LayerVersion.from_layer_version_arn(
            self,
            "AWSDataWranglerLayer",
            layer_version_arn=f"arn:aws:lambda:{self.region}:336392948345:layer:AWSSDKPandas-Python312:20",
        )

        # Lambda Build
        lambda_build_dir = "cdk.out/lambda_build"
        if os.path.exists(lambda_build_dir):
            shutil.rmtree(lambda_build_dir)
        os.makedirs(lambda_build_dir, exist_ok=True)
        shutil.copytree("tools", lambda_build_dir, dirs_exist_ok=True)

        # AgentCore Memory (native CFN construct)
        memory = bedrockagentcore.CfnMemory(
            self,
            "AgentCoreMemory",
            name=f"ActuarialAgentMemory_{unique_id}",
            event_expiry_duration=7,  # 7 days (minimum allowed)
            description="Memory for actuarial agent conversations and data",
            memory_strategies=[
                bedrockagentcore.CfnMemory.MemoryStrategyProperty(
                    summary_memory_strategy=bedrockagentcore.CfnMemory.SummaryMemoryStrategyProperty(
                        name="SessionSummarizer",
                        namespaces=["/summaries/{actorId}/{sessionId}"],
                    )
                )
            ],
        )
        memory_id = memory.attr_memory_id

        # Actuarial Lambda
        actuarial_lambda = self._create_actuarial_lambda(
            unique_id,
            claims_bucket,
            athena_results_bucket,
            data_wrangler_layer,
            memory_id,
            memory,
        )

        # Data Query Lambda
        data_query_lambda = self._create_data_query_lambda(
            unique_id,
            claims_bucket,
            athena_results_bucket,
            data_wrangler_layer,
            memory_id,
            memory,
        )

        # Native MCP Gateway (replacing nested stack)
        (
            gateway_url,
            client_id,
            client_secret,
            user_pool_id,
            token_endpoint,
            scope,
            domain_prefix,
        ) = self._create_native_gateway(actuarial_lambda, data_query_lambda)

        # Outputs
        self._create_outputs(
            claims_bucket,
            unique_id,
            glue_crawler,
            actuarial_lambda,
            data_query_lambda,
            gateway_url,
            client_id,
            client_secret,
            user_pool_id,
            token_endpoint,
            scope,
            domain_prefix,
        )

    def _create_actuarial_lambda(
        self,
        unique_id,
        claims_bucket,
        athena_results_bucket,
        data_wrangler_layer,
        memory_id,
        memory,
    ):
        lambda_role = iam.Role(
            self,
            "ActuarialLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        claims_bucket.grant_read(lambda_role)
        athena_results_bucket.grant_read_write(lambda_role)

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:StopQueryExecution",
                    "athena:GetWorkGroup",
                ],
                resources=["*"],
            )
        )

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions"],
                resources=[
                    f"arn:aws:glue:{self.region}:{self.account}:catalog",
                    f"arn:aws:glue:{self.region}:{self.account}:database/{self.stack_name}-claims-db-{unique_id}",
                    f"arn:aws:glue:{self.region}:{self.account}:table/{self.stack_name}-claims-db-{unique_id}/claims",
                ],
            )
        )

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:CreateMemory",
                    "bedrock:GetMemory",
                    "bedrock:ListMemories",
                    "bedrock:CreateEvent",
                    "bedrock:GetEvent",
                    "bedrock:ListEvents",
                    "bedrock-agentcore:CreateMemory",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:ListMemories",
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                ],
                resources=["*"],
            )
        )

        actuarial_lambda = lambda_.Function(
            self,
            "ActuarialToolsLambda",
            function_name=f"{self.stack_name}-actuarial-tools-{unique_id}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agentcore_lambda.lambda_handler",
            code=lambda_.Code.from_asset("cdk.out/lambda_build"),
            role=lambda_role,
            timeout=Duration.minutes(5),
            memory_size=1024,
            layers=[data_wrangler_layer],
            environment={
                "CLAIMS_BUCKET": claims_bucket.bucket_name,
                "ATHENA_DATABASE": f"{self.stack_name}-claims-db-{unique_id}",
                "ATHENA_TABLE": "claims",
                "DEFAULT_TABLE_NAME": "claims",
                "ATHENA_WORKGROUP": f"actuarial-workgroup-{unique_id}",
                "ATHENA_OUTPUT_LOCATION": f"s3://{athena_results_bucket.bucket_name}/query-results/",
                "AGENTCORE_MEMORY_ID": memory_id,
                "ACTOR_ID": "ActuarialAgent",
            },
        )

        actuarial_lambda.node.add_dependency(memory)
        actuarial_lambda.add_permission(
            "AllowAgentCoreInvoke",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction",
        )

        return actuarial_lambda

    def _create_data_query_lambda(
        self,
        unique_id,
        claims_bucket,
        athena_results_bucket,
        data_wrangler_layer,
        memory_id,
        memory,
    ):
        data_query_role = iam.Role(
            self,
            "DataQueryLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        data_query_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabases",
                    "glue:GetDatabase",
                    "glue:GetTables",
                    "glue:GetTable",
                    "glue:GetPartitions",
                ],
                resources=["*"],
            )
        )

        data_query_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:StopQueryExecution",
                    "athena:GetWorkGroup",
                ],
                resources=["*"],
            )
        )

        data_query_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:CreateMemory",
                    "bedrock:GetMemory",
                    "bedrock:ListMemories",
                    "bedrock:CreateEvent",
                    "bedrock:GetEvent",
                    "bedrock:ListEvents",
                    "bedrock-agentcore:CreateMemory",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:ListMemories",
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                ],
                resources=["*"],
            )
        )

        athena_results_bucket.grant_read_write(data_query_role)
        claims_bucket.grant_read(data_query_role)

        data_query_lambda = lambda_.Function(
            self,
            "DataQueryLambda",
            function_name=f"{self.stack_name}-data-query-tools-{unique_id}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="data_query_lambda.lambda_handler",
            code=lambda_.Code.from_asset("cdk.out/lambda_build"),
            role=data_query_role,
            timeout=Duration.minutes(5),
            memory_size=512,
            layers=[data_wrangler_layer],
            environment={
                "ATHENA_DATABASE": f"{self.stack_name}-claims-db-{unique_id}",
                "DEFAULT_TABLE_NAME": "claims",
                "ATHENA_WORKGROUP": f"actuarial-workgroup-{unique_id}",
                "ATHENA_OUTPUT_LOCATION": f"s3://{athena_results_bucket.bucket_name}/query-results/",
                "AGENTCORE_MEMORY_ID": memory_id,
                "ACTOR_ID": "ActuarialAgent",
            },
        )

        data_query_lambda.node.add_dependency(memory)
        data_query_lambda.add_permission(
            "AllowAgentCoreInvokeDataQuery",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction",
        )

        return data_query_lambda

    def _create_native_gateway(self, actuarial_lambda, data_query_lambda):
        # Cognito domain prefix (must be globally unique & match pattern)
        raw_prefix = f"{self.stack_name}-{self.account[-6:]}"
        sanitized = (
            re.sub("[^a-z0-9-]", "-", raw_prefix.lower()).strip("-")[:40] or "app"
        )
        h = hashlib.sha256(raw_prefix.encode("utf-8")).hexdigest()[:6]
        domain_prefix = f"{sanitized}-{h}"

        # Cognito User Pool (machine-to-machine auth via client credentials)
        user_pool = cognito.UserPool(
            self,
            "ActuarialUserPool",
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
            "ActuarialUserPoolDomain",
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
            "ActuarialResourceServer",
            identifier=resource_server_name,
            user_pool_resource_server_name=resource_server_name,
            scopes=[invoke_scope],
        )

        user_pool_client = cognito.UserPoolClient(
            self,
            "ActuarialUserPoolClient",
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
            "ActuarialGatewayRole",
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
            "ActuarialMCPGateway",
            name=f"{self.stack_name.lower()}-actuarial-gateway",
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

        # Load MCP tool schemas from JSON
        actuarial_tools_path = os.path.join(
            os.path.dirname(__file__), "..", "tools", "agentcore_tools.json"
        )
        data_query_tools_path = os.path.join(
            os.path.dirname(__file__), "..", "tools", "data_query_tools.json"
        )

        with open(actuarial_tools_path, encoding="utf-8") as f:
            actuarial_tools = json.load(f)

        with open(data_query_tools_path, encoding="utf-8") as f:
            data_query_tools = json.load(f)["tools"]

        # Gateway Targets
        actuarial_target = bedrockagentcore.CfnGatewayTarget(
            self,
            "ActuarialGatewayTarget",
            credential_provider_configurations=[
                bedrockagentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                )
            ],
            name="actuarial-lambda-target",
            gateway_identifier=mcp_gateway.attr_gateway_identifier,
            target_configuration=bedrockagentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=bedrockagentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=bedrockagentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=actuarial_lambda.function_arn,
                        tool_schema=bedrockagentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=actuarial_tools
                        ),
                    )
                )
            ),
        )

        data_query_target = bedrockagentcore.CfnGatewayTarget(
            self,
            "DataQueryGatewayTarget",
            credential_provider_configurations=[
                bedrockagentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                )
            ],
            name="data-query-lambda-target",
            gateway_identifier=mcp_gateway.attr_gateway_identifier,
            target_configuration=bedrockagentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=bedrockagentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=bedrockagentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=data_query_lambda.function_arn,
                        tool_schema=bedrockagentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=data_query_tools
                        ),
                    )
                )
            ),
        )

        actuarial_target.add_dependency(mcp_gateway)
        data_query_target.add_dependency(mcp_gateway)

        # Return values for outputs
        gateway_url = mcp_gateway.attr_gateway_url
        client_id = user_pool_client.user_pool_client_id
        client_secret = user_pool_client.user_pool_client_secret.unsafe_unwrap()
        user_pool_id = user_pool.user_pool_id
        token_endpoint = f"https://{user_pool_domain.domain_name}.auth.{self.region}.amazoncognito.com/oauth2/token"
        scope = f"{resource_server_name}/{custom_scope_name}"

        return (
            gateway_url,
            client_id,
            client_secret,
            user_pool_id,
            token_endpoint,
            scope,
            domain_prefix,
        )

    def _create_outputs(
        self,
        claims_bucket,
        unique_id,
        glue_crawler,
        actuarial_lambda,
        data_query_lambda,
        gateway_url,
        client_id,
        client_secret,
        user_pool_id,
        token_endpoint,
        scope,
        domain_prefix,
    ):
        # Infrastructure outputs
        CfnOutput(self, "ClaimsBucketName", value=claims_bucket.bucket_name)
        CfnOutput(
            self, "AthenaDatabase", value=f"{self.stack_name}-claims-db-{unique_id}"
        )
        CfnOutput(self, "GlueCrawlerName", value=glue_crawler.name)
        CfnOutput(self, "LambdaFunctionName", value=actuarial_lambda.function_name)
        CfnOutput(self, "LambdaFunctionArn", value=actuarial_lambda.function_arn)
        CfnOutput(self, "DataQueryLambdaArn", value=data_query_lambda.function_arn)

        # Gateway outputs (matching original deploy_gateway.py exactly)
        CfnOutput(
            self,
            "GatewayUrl",
            value=gateway_url,
            description="AgentCore Gateway URL",
        )
        CfnOutput(
            self,
            "ClientId",
            value=client_id,
            description="Cognito Client ID",
        )
        CfnOutput(
            self,
            "ClientSecret",
            value=client_secret,
            description="Cognito Client Secret",
        )
        CfnOutput(
            self,
            "UserPoolId",
            value=user_pool_id,
            description="Cognito User Pool ID",
        )
        CfnOutput(
            self,
            "TokenEndpoint",
            value=token_endpoint,
            description="OAuth Token Endpoint",
        )
        CfnOutput(self, "Scope", value=scope, description="OAuth Scope")
        CfnOutput(
            self,
            "DomainPrefix",
            value=domain_prefix,
            description="Cognito Domain Prefix",
        )
