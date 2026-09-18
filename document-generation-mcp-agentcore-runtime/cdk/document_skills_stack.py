"""CDK stack: DynamoDB, S3, Lambdas, Step Function, Gateway, Cognito for document skills."""

import os

import aws_cdk as cdk
from aws_cdk import (
    aws_bedrockagentcore as agentcore,
)
from aws_cdk import (
    aws_cloudfront as cloudfront,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
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
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct


class DocumentSkillsStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # ─── DynamoDB: Job tracking ───
        jobs_table = dynamodb.Table(
            self,
            "JobsTable",
            table_name="document-skill-jobs",
            partition_key=dynamodb.Attribute(
                name="job_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ─── S3: Generated documents ───
        docs_bucket = s3.Bucket(
            self,
            "DocsBucket",
            bucket_name=f"document-skills-output-{self.account}-{self.region}",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=cdk.Duration.days(7), id="expire-old-docs"),
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
        )

        # ─── CloudFront: Clean download URLs (avoids corporate proxy blocks on S3 URLs) ───
        # Unsigned — paths contain UUIDs (unguessable), files auto-expire after 7 days.
        # CloudFront signed URLs don't work with Quick (chat renderer strips ~ chars).
        distribution = cloudfront.Distribution(
            self,
            "DocsDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(docs_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            ),
            comment="Document Skills download distribution",
        )

        # ─── Shared Lambda config ───
        lambdas_dir = os.path.join(os.path.dirname(__file__), "..", "lambdas")
        shared_env = {
            "JOBS_TABLE": jobs_table.table_name,
            "DOCS_BUCKET": docs_bucket.bucket_name,
            "POWERTOOLS_SERVICE_NAME": "document-skills",
            "LOG_LEVEL": "INFO",
        }
        py_runtime = _lambda.Runtime.PYTHON_3_12

        # ─── AgentCore Runtime ID parameter ───
        agentcore_runtime_id = cdk.CfnParameter(
            self,
            "AgentCoreRuntimeId",
            type="String",
            description="AgentCore Runtime ID for the document skills agent.",
            default="",
        )

        # ─── Lambda: update_job (DynamoDB updater for Step Function) ───
        update_job_fn = _lambda.Function(
            self,
            "UpdateJobFn",
            function_name="document-skill-update-job",
            runtime=py_runtime,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(os.path.join(lambdas_dir, "update_job")),
            timeout=cdk.Duration.seconds(30),
            memory_size=128,
            environment=shared_env,
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        jobs_table.grant_read_write_data(update_job_fn)
        docs_bucket.grant_read(update_job_fn)

        # ─── Lambda: check_agent_status (polling for direct-persist) ───
        check_agent_fn = _lambda.Function(
            self,
            "CheckAgentStatusFn",
            function_name="document-skill-check-agent",
            runtime=py_runtime,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                os.path.join(lambdas_dir, "check_agent_status")
            ),
            timeout=cdk.Duration.seconds(15),
            memory_size=128,
            environment=shared_env,
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        jobs_table.grant_read_data(check_agent_fn)

        # ─── Step Function: polling loop ───
        # The agent runs on AgentCore Runtime (fire-and-forget from submit_job).
        # This Step Function just polls DynamoDB until the agent direct-persists
        # the result (COMPLETED) or the 45-min timeout expires.
        wait_for_agent = sfn.Wait(
            self,
            "WaitForAgent",
            time=sfn.WaitTime.duration(cdk.Duration.seconds(30)),
        )
        check_agent_task = tasks.LambdaInvoke(
            self,
            "CheckAgentStatus",
            lambda_function=check_agent_fn,
            output_path="$.Payload",
        )
        is_agent_done = sfn.Choice(self, "IsAgentDone")

        mark_completed = tasks.LambdaInvoke(
            self,
            "MarkCompleted",
            lambda_function=update_job_fn,
            payload=sfn.TaskInput.from_object(
                {
                    "job_id": sfn.JsonPath.string_at("$.job_id"),
                    "status": "COMPLETED",
                    "s3_key": sfn.JsonPath.string_at("$.s3_key"),
                    "filename": sfn.JsonPath.string_at("$.filename"),
                    "file_type": sfn.JsonPath.string_at("$.file_type"),
                }
            ),
            output_path="$.Payload",
        )
        mark_failed = tasks.LambdaInvoke(
            self,
            "MarkFailed",
            lambda_function=update_job_fn,
            payload=sfn.TaskInput.from_object(
                {
                    "job_id": sfn.JsonPath.string_at("$.job_id"),
                    "status": "FAILED",
                    "error": sfn.JsonPath.string_at("$.error_info.Cause"),
                }
            ),
            output_path="$.Payload",
        )

        # Wire: Wait → Check → Choice → (loop back or mark completed)
        wait_for_agent.next(check_agent_task)
        check_agent_task.add_catch(mark_failed, result_path="$.error_info")
        check_agent_task.next(is_agent_done)

        is_agent_done.when(
            sfn.Condition.boolean_equals("$.agent_pending", True),
            wait_for_agent,
        ).otherwise(mark_completed)

        # Entry point: start with the wait (agent was just invoked fire-and-forget)
        definition = wait_for_agent

        sfn_log_group = logs.LogGroup(
            self,
            "SfnLogs",
            log_group_name="/aws/stepfunction/document-skill-orchestrator",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        state_machine = sfn.StateMachine(
            self,
            "SkillStateMachine",
            state_machine_name="document-skill-orchestrator",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=cdk.Duration.minutes(45),
            tracing_enabled=True,
            logs=sfn.LogOptions(destination=sfn_log_group, level=sfn.LogLevel.ERROR),
        )

        # ─── Lambda: submit_job (Gateway target — fire-and-forget) ───
        submit_job_fn = _lambda.Function(
            self,
            "SubmitJobFn",
            function_name="document-skill-submit-job",
            runtime=py_runtime,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(os.path.join(lambdas_dir, "submit_job")),
            timeout=cdk.Duration.seconds(15),
            memory_size=256,
            environment={
                **shared_env,
                "STATE_MACHINE_ARN": state_machine.state_machine_arn,
                "AGENTCORE_RUNTIME_ID": agentcore_runtime_id.value_as_string,
            },
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        state_machine.grant_start_execution(submit_job_fn)
        jobs_table.grant_read_write_data(submit_job_fn)
        submit_job_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=["*"],
            )
        )

        # ─── Lambda: get_job_result (Gateway target) ───
        get_result_fn = _lambda.Function(
            self,
            "GetJobResultFn",
            function_name="document-skill-get-result",
            runtime=py_runtime,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(os.path.join(lambdas_dir, "get_job_result")),
            timeout=cdk.Duration.seconds(15),
            memory_size=256,
            environment={
                **shared_env,
                "PRESIGNED_URL_EXPIRY": "3600",
                "CLOUDFRONT_DOMAIN": distribution.distribution_domain_name,
            },
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )
        jobs_table.grant_read_data(get_result_fn)
        docs_bucket.grant_read(get_result_fn)

        # ─── Cognito: OAuth2 authorizer for Gateway ───
        gateway_name = "document-skills-gateway"

        user_pool = cognito.UserPool(
            self,
            "GatewayUserPool",
            user_pool_name=f"{gateway_name}-auth",
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        resource_server = user_pool.add_resource_server(
            "GatewayResourceServer",
            identifier=gateway_name,
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
                domain_prefix=f"docskills-{self.account[:8]}",
            ),
        )

        full_scope = cognito.OAuthScope.resource_server(
            resource_server,
            cognito.ResourceServerScope(
                scope_name="invoke",
                scope_description="Invoke gateway tools",
            ),
        )
        app_client = user_pool.add_client(
            "GatewayClient",
            user_pool_client_name=f"{gateway_name}-client",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[full_scope],
            ),
        )

        # ─── Gateway IAM Role ───
        gateway_role = iam.Role(
            self,
            "GatewayRole",
            role_name="DocumentSkillsGatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "InvokeLambdas": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["lambda:InvokeFunction"],
                            resources=[
                                submit_job_fn.function_arn,
                                get_result_fn.function_arn,
                            ],
                        )
                    ],
                ),
            },
        )

        submit_job_fn.grant_invoke(
            iam.ServicePrincipal("bedrock-agentcore.amazonaws.com")
        )
        get_result_fn.grant_invoke(
            iam.ServicePrincipal("bedrock-agentcore.amazonaws.com")
        )

        # ─── AgentCore Gateway ───
        discovery_url = f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}/.well-known/openid-configuration"

        gateway = agentcore.CfnGateway(
            self,
            "McpGateway",
            name=gateway_name,
            authorizer_type="CUSTOM_JWT",
            protocol_type="MCP",
            role_arn=gateway_role.role_arn,
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=discovery_url,
                    allowed_clients=[app_client.user_pool_client_id],
                    allowed_scopes=[f"{gateway_name}/invoke"],
                ),
            ),
            description="MCP Gateway for Quick document generation skills",
        )

        # ─── Gateway Targets ───
        agentcore.CfnGatewayTarget(
            self,
            "CreateDocTarget",
            name="create-document",
            gateway_identifier=gateway.attr_gateway_identifier,
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=submit_job_fn.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=[
                                agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                                    name="create_document",
                                    description="Submit a document creation job (docx, pdf, pptx, xlsx, or HTML). Returns a job_id. Use get_document_job_result to poll for the download link.",
                                    input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                                        type="object",
                                        properties={
                                            "skill_type": agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                                                type="string",
                                                description="Document type: docx, pdf, pptx, xlsx, or frontend-design",
                                            ),
                                            "prompt": agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                                                type="string",
                                                description="Detailed description of the document to create",
                                            ),
                                            "filename": agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                                                type="string",
                                                description="Desired filename (optional)",
                                            ),
                                        },
                                        required=["skill_type", "prompt"],
                                    ),
                                )
                            ],
                        ),
                    ),
                ),
            ),
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                ),
            ],
        )

        agentcore.CfnGatewayTarget(
            self,
            "GetResultTarget",
            name="get-document-job-result",
            gateway_identifier=gateway.attr_gateway_identifier,
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=get_result_fn.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=[
                                agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                                    name="get_document_job_result",
                                    description="Check document creation job status and get download link. Poll until COMPLETED or FAILED.",
                                    input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                                        type="object",
                                        properties={
                                            "job_id": agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                                                type="string",
                                                description="The job_id from create_document",
                                            ),
                                        },
                                        required=["job_id"],
                                    ),
                                )
                            ],
                        ),
                    ),
                ),
            ),
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                ),
            ],
        )

        # ─── Outputs ───
        cdk.CfnOutput(
            self,
            "McpUrl",
            value=gateway.attr_gateway_url,
            description="MCP URL for Quick configuration",
        )
        cdk.CfnOutput(
            self,
            "TokenUrl",
            value=f"https://docskills-{self.account[:8]}.auth.{self.region}.amazoncognito.com/oauth2/token",
            description="OAuth2 Token URL for Quick configuration",
        )
        cdk.CfnOutput(
            self,
            "ClientId",
            value=app_client.user_pool_client_id,
            description="Cognito Client ID for Quick configuration",
        )
        cdk.CfnOutput(
            self,
            "Scope",
            value=f"{gateway_name}/invoke",
            description="OAuth scope for Quick configuration",
        )
        cdk.CfnOutput(
            self,
            "UserPoolId",
            value=user_pool.user_pool_id,
            description="Cognito User Pool ID (use to retrieve client secret)",
        )

        cdk.CfnOutput(
            self,
            "SubmitJobFnArn",
            value=submit_job_fn.function_arn,
            description="ARN for submit_job Lambda",
        )
        cdk.CfnOutput(
            self,
            "GetJobResultFnArn",
            value=get_result_fn.function_arn,
            description="ARN for get_job_result Lambda",
        )
        cdk.CfnOutput(self, "DocsBucketName", value=docs_bucket.bucket_name)
        cdk.CfnOutput(self, "JobsTableName", value=jobs_table.table_name)
        cdk.CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        cdk.CfnOutput(
            self,
            "GatewayId",
            value=gateway.attr_gateway_identifier,
            description="AgentCore Gateway ID",
        )
        cdk.CfnOutput(
            self,
            "CloudFrontDomain",
            value=distribution.distribution_domain_name,
            description="CloudFront domain for document downloads",
        )
