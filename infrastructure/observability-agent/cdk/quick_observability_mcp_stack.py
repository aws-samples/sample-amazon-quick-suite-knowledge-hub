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
    CfnParameter,
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_bedrockagentcore as bedrockagentcore,
)
from aws_cdk import (
    aws_cognito as cognito,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kinesisfirehose as firehose,
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

from cdk.data_lake import ObservabilityDataLake
from cdk.quicksight_dashboard import QuickSightDashboard
from cdk.quicksight_datasets import QuickSightDatasets
from cdk.table_schemas import LOG_TYPE_BY_TABLE


class QuickObservabilityMCPStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Optional comma-separated QuickSight principal ARNs (users/groups) to
        # grant owner access to the created data source and datasets.
        quicksight_principals_param = CfnParameter(
            self,
            "QuickSightPrincipalArns",
            type="CommaDelimitedList",
            default="",
            description=(
                "Optional QuickSight user/group ARNs to own the observability "
                "data source and datasets (e.g. "
                "arn:aws:quicksight:us-east-1:111122223333:user/default/admin)."
            ),
        )

        # ====================================================================
        # PART 0: Observability data lake (S3 + Glue + Athena)
        # ====================================================================
        data_lake = ObservabilityDataLake(self, "DataLake")

        # ====================================================================
        # PART 0b: Firehose delivery streams for the vended logs
        #
        # Each of the 7 Quick vended log types is delivered via its own
        # Kinesis Data Firehose stream into the data-lake bucket (one prefix
        # per table), mirroring the quick-legal-hold-agent pattern of wiring a
        # CHAT_LOGS delivery source to a Firehose destination. GZIP-compressed
        # JSON is written; Athena/Glue read it transparently.
        # ====================================================================
        firehose_log_group = logs.LogGroup(
            self,
            "FirehoseLogGroup",
            log_group_name="/aws/kinesisfirehose/quick-observability",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        firehose_role = iam.Role(
            self,
            "FirehoseRole",
            assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"),
        )
        data_lake.bucket.grant_read_write(firehose_role)
        firehose_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["logs:PutLogEvents"],
                resources=[firehose_log_group.log_group_arn],
            )
        )

        # One Firehose stream per vended log type -> data lake bucket prefix.
        firehose_streams: dict[str, firehose.CfnDeliveryStream] = {}
        for table in LOG_TYPE_BY_TABLE:
            stream_name = f"quick-obs-{table.replace('_', '-')}"
            stream = firehose.CfnDeliveryStream(
                self,
                f"FirehoseStream-{table}",
                delivery_stream_name=stream_name,
                delivery_stream_type="DirectPut",
                extended_s3_destination_configuration=firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                    bucket_arn=data_lake.bucket.bucket_arn,
                    role_arn=firehose_role.role_arn,
                    prefix=f"{table}/!{{timestamp:yyyy/MM/dd}}/",
                    error_output_prefix=(
                        "errors/!{firehose:error-output-type}/!{timestamp:yyyy/MM/dd}/"
                    ),
                    buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                        interval_in_seconds=60, size_in_m_bs=64
                    ),
                    compression_format="GZIP",
                    cloud_watch_logging_options=firehose.CfnDeliveryStream.CloudWatchLoggingOptionsProperty(
                        enabled=True,
                        log_group_name=firehose_log_group.log_group_name,
                        log_stream_name=f"{table}-delivery",
                    ),
                ),
            )
            stream.node.add_dependency(firehose_role)
            firehose_streams[table] = stream

        # ====================================================================
        # PART 1: CloudWatch Log Groups
        # ====================================================================

        # Import existing log groups instead of creating new ones
        chat_log_group = logs.LogGroup.from_log_group_name(
            self, "ChatLogsGroup", log_group_name="/aws/quick/chat"
        )

        feedback_log_group = logs.LogGroup.from_log_group_name(
            self, "FeedbackLogsGroup", log_group_name="/aws/quick/feedback"
        )

        agent_hours_log_group = logs.LogGroup.from_log_group_name(
            self, "AgentHoursLogsGroup", log_group_name="/aws/quick/agent-hours"
        )

        # Add resource policies to allow CloudWatch Logs delivery service
        for log_group_name in [
            "/aws/quick/chat",
            "/aws/quick/feedback",
            "/aws/quick/agent-hours",
        ]:
            logs.CfnResourcePolicy(
                self,
                f"LogDeliveryPolicy{log_group_name.replace('/', '-')}",
                policy_name=f"QuickLogDeliveryPolicy{log_group_name.replace('/', '-')}",
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

        # Allow CloudWatch Logs vended-log delivery to write to the data-lake
        # bucket (S3 delivery destinations).
        data_lake.bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowLogDeliveryWrite",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("delivery.logs.amazonaws.com")],
                actions=["s3:PutObject"],
                resources=[f"{data_lake.bucket.bucket_arn}/*"],
                conditions={
                    "StringEquals": {
                        "s3:x-amz-acl": "bucket-owner-full-control",
                        "aws:SourceAccount": self.account,
                    }
                },
            )
        )
        data_lake.bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowLogDeliveryAclCheck",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("delivery.logs.amazonaws.com")],
                actions=["s3:GetBucketAcl", "s3:ListBucket"],
                resources=[data_lake.bucket.bucket_arn],
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            )
        )

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
                                "logs:PutDeliveryDestinationPolicy",
                                "logs:CreateDelivery",
                                "logs:CreateLogDelivery",
                                "logs:GetDeliverySource",
                                "logs:GetDeliveryDestination",
                                "logs:GetDelivery",
                                "logs:DescribeDeliveries",
                                "logs:DescribeDeliverySources",
                                "logs:DescribeDeliveryDestinations",
                            ],
                            resources=["*"],
                        ),
                    ]
                ),
                "FirehoseDeliveryAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "firehose:TagDeliveryStream",
                                "firehose:DescribeDeliveryStream",
                                "firehose:ListTagsForDeliveryStream",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["iam:CreateServiceLinkedRole"],
                            resources=["*"],
                            conditions={
                                "StringEquals": {
                                    "iam:AWSServiceName": "delivery.logs.amazonaws.com"
                                }
                            },
                        ),
                    ]
                ),
                "S3DeliveryBucketAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:GetBucketPolicy",
                                "s3:PutBucketPolicy",
                            ],
                            resources=[data_lake.bucket.bucket_arn],
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

        # Firehose-delivery config for all 7 vended log types. The source is
        # resolved at RUNTIME by logType (discover-and-adopt): if the account
        # already has a delivery source for that logType we reuse it; otherwise
        # we create one named `quick-<type>-logs-source`. AWS allows only ONE
        # delivery source per (resource, logType), so discovery is required to
        # stay compatible with other stacks (e.g. quick-legal-hold-agent) that
        # share the same vended-log sources.
        firehose_delivery_configs = [
            {
                "log_type": log_type,
                "fallback_source": f"quick-{table.replace('_', '-')}-source",
                "dest": f"quick-obs-{table.replace('_', '-')}-fh-dest",
                "firehose_arn": firehose_streams[table].attr_arn,
            }
            for table, log_type in LOG_TYPE_BY_TABLE.items()
        ]

        enable_logging_function = _lambda.Function(
            self,
            "EnableLoggingFunction",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="index.handler",
            role=enable_logging_role,
            timeout=Duration.seconds(300),
            environment={
                "DATA_BUCKET": data_lake.bucket.bucket_name,
                "FIREHOSE_CONFIGS": json.dumps(firehose_delivery_configs),
            },
            code=_lambda.InlineCode("""
import json
import os
import boto3
from threading import Event

logs_client = boto3.client('logs')

_POLL_IDLE = Event()


def _poll_wait(seconds):
    _POLL_IDLE.wait(timeout=seconds)


def _find_source_by_log_type(log_type):
    \"\"\"Return the name of an existing delivery source for this logType, or None.\"\"\"
    try:
        paginator = logs_client.get_paginator('describe_delivery_sources')
        for page in paginator.paginate():
            for s in page.get('deliverySources', []):
                if s.get('logType') == log_type:
                    return s['name']
    except Exception as exc:
        print(f"describe_delivery_sources ({log_type}): {exc}")
    return None


def _resolve_source(log_type, fallback_name, quicksight_arn):
    \"\"\"Discover-and-adopt: reuse an existing source for this logType, else
    create one named `fallback_name` (quick-*). Returns the source name.\"\"\"
    existing = _find_source_by_log_type(log_type)
    if existing:
        print(f"adopting existing source {existing} for {log_type}")
        return existing
    try:
        logs_client.put_delivery_source(name=fallback_name, resourceArn=quicksight_arn, logType=log_type)
        print(f"created source {fallback_name} for {log_type}")
    except Exception as exc:
        print(f"put_delivery_source {fallback_name}: {exc}")
    return fallback_name


def _create_delivery(source_name, dest_arn, label):
    \"\"\"create_delivery with retries for eventual consistency on the dest.\"\"\"
    for attempt in range(6):
        try:
            logs_client.create_delivery(deliverySourceName=source_name, deliveryDestinationArn=dest_arn)
            print(f"created delivery ({label}) {source_name} -> {dest_arn}")
            return
        except logs_client.exceptions.ConflictException:
            print(f"delivery already exists ({label}) {source_name}")
            return
        except Exception as exc:
            print(f"create_delivery ({label}) {source_name} attempt {attempt}: {exc}")
            _poll_wait(5)


def handler(event, context):
    request_type = event.get('RequestType', 'Create')
    account_id = context.invoked_function_arn.split(':')[4]
    region = context.invoked_function_arn.split(':')[3]
    quicksight_arn = f"arn:aws:quicksight:{region}:{account_id}:account/{account_id}"

    if request_type not in ['Create', 'Update']:
        return {'PhysicalResourceId': 'quick-logging-enabled'}

    # ---- Firehose deliveries for all 7 vended log types (data lake) -----
    # Discover-and-adopt the vended-log source per logType, then wire a
    # delivery to our own Firehose destination. Multiple deliveries per source
    # are allowed, so this coexists with any existing deliveries (customer
    # CloudWatch/S3 destinations, the quick-legal-hold-agent Firehose, etc.).
    fh_configs = json.loads(os.environ['FIREHOSE_CONFIGS'])
    for config in fh_configs:
        source_name = _resolve_source(config['log_type'], config['fallback_source'], quicksight_arn)
        try:
            logs_client.put_delivery_destination(
                name=config['dest'],
                deliveryDestinationConfiguration={'destinationResourceArn': config['firehose_arn']}
            )
        except Exception as exc:
            print(f"put_delivery_destination (fh) {config['dest']}: {exc}")
        _poll_wait(2)
        _create_delivery(source_name, f"arn:aws:logs:{region}:{account_id}:delivery-destination:{config['dest']}", 'fh')

    return {'PhysicalResourceId': 'quick-logging-enabled'}
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
        for _stream in firehose_streams.values():
            enable_logging_cr.node.add_dependency(_stream)

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
                ),
                "AthenaQueryAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "athena:StartQueryExecution",
                                "athena:GetQueryExecution",
                                "athena:GetQueryResults",
                                "athena:GetWorkGroup",
                                "athena:StopQueryExecution",
                            ],
                            resources=[
                                f"arn:aws:athena:{self.region}:{self.account}:"
                                f"workgroup/{data_lake.workgroup_name}"
                            ],
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "glue:GetDatabase",
                                "glue:GetDatabases",
                                "glue:GetTable",
                                "glue:GetTables",
                                "glue:GetPartition",
                                "glue:GetPartitions",
                            ],
                            resources=[
                                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                                f"arn:aws:glue:{self.region}:{self.account}:"
                                f"database/{data_lake.database_name}",
                                f"arn:aws:glue:{self.region}:{self.account}:"
                                f"table/{data_lake.database_name}/*",
                            ],
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:GetObject",
                                "s3:GetBucketLocation",
                                "s3:ListBucket",
                                "s3:PutObject",
                            ],
                            resources=[
                                data_lake.bucket.bucket_arn,
                                f"{data_lake.bucket.bucket_arn}/*",
                            ],
                        ),
                    ]
                ),
            },
        )

        # Lambda function
        tools_dir = str(Path(__file__).parent.parent.joinpath("tools"))

        mcp_function = _lambda.Function(
            self,
            "QuickObservabilityFunction",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="quick_observability_lambda.handler",
            code=_lambda.Code.from_asset(tools_dir),
            role=lambda_role,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "CHAT_LOG_GROUP": "/aws/quick/chat",
                "FEEDBACK_LOG_GROUP": "/aws/quick/feedback",
                "AGENT_HOURS_LOG_GROUP": "/aws/quick/agent-hours",
                "ATHENA_DATABASE": data_lake.database_name,
                "ATHENA_WORKGROUP": data_lake.workgroup_name,
                "DATA_BUCKET": data_lake.bucket.bucket_name,
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

        # Quick's MCP OAuth redirect URI (same value Quick uses for all MCP
        # connectors in this Region); overridable via context.
        quick_redirect_uri = (
            self.node.try_get_context("quickRedirectUri")
            or "https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback"
        )
        local_callback = "http://localhost:8765/callback"

        # User Pool Client.
        #
        # 3LO (authorization-code) auth, matching the quick-legal-hold-agent and
        # what Amazon Quick's MCP connector expects: each user signs in through
        # the Cognito hosted UI (browser), so the JWT presented to the Gateway
        # represents a real logged-in user. The dummy user below is a sample
        # login for evaluation.
        user_pool_client = cognito.UserPoolClient(
            self,
            "MCPUserPoolClient",
            user_pool=user_pool,
            user_pool_client_name=f"{self.stack_name}-client",
            generate_secret=True,
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
                admin_user_password=True,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True,
                ),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                    cognito.OAuthScope.resource_server(resource_server, invoke_scope),
                ],
                callback_urls=[quick_redirect_uri, local_callback],
                logout_urls=[quick_redirect_uri, local_callback],
            ),
            refresh_token_validity=Duration.days(30),
            auth_session_validity=Duration.minutes(3),
            enable_token_revocation=True,
        )

        # ----------------------------------------------------------------
        # Dummy Cognito user for the MCP client to authenticate as.
        #
        # DEMO ONLY: the username and password below are hardcoded so this
        # public sample works out of the box in the account where it is
        # deployed. Rotate or replace with a Secrets Manager-backed value
        # before using beyond evaluation.
        # ----------------------------------------------------------------
        observability_username = "observability_user"
        observability_password = "AmazonQuick1@"  # noqa: S105 - demo credential

        dummy_user = cognito.CfnUserPoolUser(
            self,
            "MCPDummyUser",
            user_pool_id=user_pool.user_pool_id,
            username=observability_username,
            message_action="SUPPRESS",
        )

        # CfnUserPoolUser creates the user in FORCE_CHANGE_PASSWORD state; set a
        # permanent password so the ROPC/USER_PASSWORD_AUTH flow works directly.
        set_password = cr.AwsCustomResource(
            self,
            "MCPDummyUserPassword",
            on_create=cr.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminSetUserPassword",
                parameters={
                    "UserPoolId": user_pool.user_pool_id,
                    "Username": observability_username,
                    "Password": observability_password,
                    "Permanent": True,
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"{observability_username}-password"
                ),
            ),
            on_update=cr.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminSetUserPassword",
                parameters={
                    "UserPoolId": user_pool.user_pool_id,
                    "Username": observability_username,
                    "Password": observability_password,
                    "Permanent": True,
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"{observability_username}-password"
                ),
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=["cognito-idp:AdminSetUserPassword"],
                        resources=[user_pool.user_pool_arn],
                    )
                ]
            ),
        )
        set_password.node.add_dependency(dummy_user)

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
        tools_json_path = os.path.join(tools_dir, "quick_observability_tools.json")
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

        # ====================================================================
        # PART 4: Poller Lambdas (CloudTrail + CloudWatch metrics -> S3)
        # ====================================================================
        pollers_dir = str(Path(__file__).parent.parent.joinpath("pollers"))

        poller_role = iam.Role(
            self,
            "PollerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
            inline_policies={
                "PollerSourceAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "cloudtrail:LookupEvents",
                                "cloudwatch:ListMetrics",
                                "cloudwatch:GetMetricData",
                            ],
                            resources=["*"],
                        ),
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["s3:PutObject"],
                            resources=[f"{data_lake.bucket.bucket_arn}/*"],
                        ),
                    ]
                )
            },
        )

        cloudtrail_poller = _lambda.Function(
            self,
            "CloudTrailPoller",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="cloudtrail_poller.handler",
            code=_lambda.Code.from_asset(pollers_dir),
            role=poller_role,
            timeout=Duration.seconds(300),
            memory_size=512,
            environment={
                "DATA_BUCKET": data_lake.bucket.bucket_name,
                "TABLE_PREFIX": "cloudtrail_events",
                "LOOKBACK_MINUTES": "15",
            },
        )

        metrics_poller = _lambda.Function(
            self,
            "MetricsPoller",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="metrics_poller.handler",
            code=_lambda.Code.from_asset(pollers_dir),
            role=poller_role,
            timeout=Duration.seconds(300),
            memory_size=512,
            environment={
                "DATA_BUCKET": data_lake.bucket.bucket_name,
                "TABLE_PREFIX": "metric_snapshots",
                "LOOKBACK_MINUTES": "15",
                "PERIOD_SECONDS": "300",
            },
        )

        # Run both pollers every 5 minutes; the 15-minute lookback window
        # overlaps so no data is missed between runs. This keeps the data lake
        # close to the ~10-minute refresh cadence of the Quick usage console.
        poller_schedule = events.Rule(
            self,
            "PollerSchedule",
            schedule=events.Schedule.rate(Duration.minutes(5)),
        )
        poller_schedule.add_target(targets.LambdaFunction(cloudtrail_poller))
        poller_schedule.add_target(targets.LambdaFunction(metrics_poller))

        # ====================================================================
        # PART 5: QuickSight Athena data source + datasets
        # ====================================================================
        quicksight_datasets = QuickSightDatasets(
            self,
            "QuickSightDatasets",
            athena_workgroup=data_lake.workgroup_name,
            glue_database=data_lake.database_name,
            data_bucket_arn=data_lake.bucket.bucket_arn,
            principal_arns=quicksight_principals_param.value_as_list,
        )
        quicksight_datasets.node.add_dependency(data_lake.athena_workgroup)

        # QuickSight dashboard over the datasets (usage KPIs + per-user table).
        quicksight_dashboard = QuickSightDashboard(
            self,
            "QuickSightDashboard",
            user_summary_dataset_arn=quicksight_datasets.user_summary_dataset.attr_arn,
            table_dataset_arns={
                name: ds.attr_arn
                for name, ds in quicksight_datasets.dataset_by_table.items()
            },
            principal_arns=quicksight_principals_param.value_as_list,
        )
        quicksight_dashboard.node.add_dependency(quicksight_datasets)

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
            "CognitoAuthorizeUrl",
            value=(
                f"https://{user_pool_domain.domain_name}.auth."
                f"{self.region}.amazoncognito.com/oauth2/authorize"
            ),
            description="Cognito OAuth2 Authorize URL (3LO)",
        )

        CfnOutput(
            self,
            "OAuthScopes",
            value=f"openid email profile {resource_server_name}/invoke",
            description="OAuth scopes for the Quick MCP connector",
        )

        CfnOutput(
            self,
            "QuickRedirectUri",
            value=quick_redirect_uri,
            description="OAuth redirect URI registered for the Quick MCP connector",
        )

        CfnOutput(
            self,
            "ObservabilityUsername",
            value=observability_username,
            description="Dummy Cognito username for user-based MCP auth (demo)",
        )

        CfnOutput(
            self,
            "ObservabilityPassword",
            value=observability_password,
            description="Dummy Cognito password for user-based MCP auth (demo only; rotate for real use)",
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

        CfnOutput(
            self,
            "DataLakeBucket",
            value=data_lake.bucket.bucket_name,
            description="S3 data-lake bucket for all observability data",
        )

        CfnOutput(
            self,
            "GlueDatabase",
            value=data_lake.database_name,
            description="Glue/Athena database for observability tables",
        )

        CfnOutput(
            self,
            "AthenaWorkGroup",
            value=data_lake.workgroup_name,
            description="Athena workgroup for observability queries",
        )

        CfnOutput(
            self,
            "FirehoseStreamNames",
            value=", ".join(s.delivery_stream_name for s in firehose_streams.values()),
            description="Firehose streams delivering vended logs to the data lake",
        )

        CfnOutput(
            self,
            "QuickSightDataSourceId",
            value=quicksight_datasets.data_source.data_source_id,
            description="QuickSight Athena data source ID for dashboards",
        )

        CfnOutput(
            self,
            "QuickSightDashboardId",
            value=quicksight_dashboard.dashboard.dashboard_id,
            description="QuickSight usage-metrics dashboard ID",
        )
