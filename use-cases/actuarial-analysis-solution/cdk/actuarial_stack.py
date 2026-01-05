from aws_cdk import (
    Stack,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_athena as athena,
    aws_glue as glue,
    Duration,
    RemovalPolicy,
    CfnOutput,
    CustomResource
)
from constructs import Construct
from .gateway_stack import AgentCoreGatewayStack
import os
import subprocess
import shutil
import uuid

class ActuarialToolsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket_suffix = str(uuid.uuid4())[:8]
        
        # S3 Buckets
        claims_bucket = s3.Bucket(
            self, "ClaimsBucket",
            bucket_name=f"actuarial-claims-{self.account}-{bucket_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        athena_results_bucket = s3.Bucket(
            self, "AthenaResultsBucket",
            bucket_name=f"actuarial-athena-results-{self.account}-{bucket_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        s3deploy.BucketDeployment(
            self, "DeploySampleData",
            sources=[s3deploy.Source.asset("sample_data")],
            destination_bucket=claims_bucket,
            destination_key_prefix="claims/"
        )

        # Glue Database and Crawler
        glue_database = glue.CfnDatabase(
            self, "ClaimsDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=f"claims_db_{bucket_suffix}",
                description="Database for insurance claims data"
            )
        )

        crawler_role = iam.Role(
            self, "GlueCrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")
            ]
        )
        claims_bucket.grant_read(crawler_role)

        glue_crawler = glue.CfnCrawler(
            self, "ClaimsCrawler",
            name=f"claims-crawler-{bucket_suffix}",
            role=crawler_role.role_arn,
            database_name=f"claims_db_{bucket_suffix}",
            targets=glue.CfnCrawler.TargetsProperty(
                s3_targets=[
                    glue.CfnCrawler.S3TargetProperty(
                        path=f"s3://{claims_bucket.bucket_name}/claims/"
                    )
                ]
            ),
            schema_change_policy=glue.CfnCrawler.SchemaChangePolicyProperty(
                update_behavior="UPDATE_IN_DATABASE",
                delete_behavior="LOG"
            )
        )
        glue_crawler.add_dependency(glue_database)

        # Custom resource to start crawler
        from aws_cdk import custom_resources as cr

        cr.AwsCustomResource(
            self, "StartCrawlerCustomResource",
            on_create=cr.AwsSdkCall(
                service="Glue",
                action="startCrawler",
                parameters={"Name": glue_crawler.name},
                physical_resource_id=cr.PhysicalResourceId.of("start-crawler"),
                ignore_error_codes_matching="CrawlerRunningException|InvalidInputException"
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["glue:StartCrawler"],
                    resources=[f"arn:aws:glue:{self.region}:{self.account}:crawler/*"]
                )
            ])
        )

        # Athena Workgroup
        athena_workgroup = athena.CfnWorkGroup(
            self, "ActuarialWorkGroup",
            name=f"actuarial-workgroup-{bucket_suffix}",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{athena_results_bucket.bucket_name}/query-results/"
                )
            )
        )

        # Lambda Layers
        self._build_agentcore_layer()
        
        data_wrangler_layer = lambda_.LayerVersion.from_layer_version_arn(
            self, "AWSDataWranglerLayer",
            layer_version_arn=f"arn:aws:lambda:{self.region}:336392948345:layer:AWSSDKPandas-Python312:20"
        )
        
        agentcore_layer = lambda_.LayerVersion(
            self, "AgentCoreLayer",
            code=lambda_.Code.from_asset("cdk.out/agentcore_layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="AgentCore layer built with Docker for Linux Lambda environment"
        )

        # Lambda Build
        lambda_build_dir = "cdk.out/lambda_build"
        if os.path.exists(lambda_build_dir):
            shutil.rmtree(lambda_build_dir)
        os.makedirs(lambda_build_dir, exist_ok=True)
        shutil.copytree("tools", lambda_build_dir, dirs_exist_ok=True)

        # AgentCore Memory
        memory_creator_lambda = self._create_memory_lambda(agentcore_layer)
        memory_resource = CustomResource(
            self, "AgentCoreMemory",
            service_token=memory_creator_lambda.function_arn,
            resource_type="Custom::AgentCoreMemory"
        )
        memory_id = memory_resource.get_att_string("MemoryId")

        # Actuarial Lambda
        actuarial_lambda = self._create_actuarial_lambda(
            bucket_suffix, claims_bucket, athena_results_bucket, 
            data_wrangler_layer, agentcore_layer, memory_id, memory_resource
        )

        # Data Query Lambda
        data_query_lambda = self._create_data_query_lambda(
            bucket_suffix, claims_bucket, athena_results_bucket,
            data_wrangler_layer, agentcore_layer, memory_id, memory_resource
        )

        # AgentCore Gateway (as nested stack)
        gateway_stack = AgentCoreGatewayStack(
            self, "GatewayStack",
            actuarial_lambda_arn=actuarial_lambda.function_arn,
            data_query_lambda_arn=data_query_lambda.function_arn
        )

        # Outputs
        self._create_outputs(
            claims_bucket, bucket_suffix, glue_crawler, 
            actuarial_lambda, data_query_lambda, gateway_stack
        )

    def _build_agentcore_layer(self):
        layer_dir = "cdk.out/agentcore_layer"
        
        if os.path.exists(layer_dir):
            shutil.rmtree(layer_dir)
        os.makedirs(layer_dir, exist_ok=True)
        
        with open(os.path.join(layer_dir, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write("bedrock-agentcore\n")
        
        shutil.copy2("cdk/Dockerfile.agentcore", os.path.join(layer_dir, "Dockerfile"))
        
        subprocess.run(["docker", "build", "-t", "agentcore-layer", layer_dir], check=True)
        subprocess.run(["docker", "run", "--rm", "-v", f"{os.path.abspath(layer_dir)}:/output", "agentcore-layer"], check=True)

    def _create_memory_lambda(self, agentcore_layer):
        memory_creator_role = iam.Role(
            self, "MemoryCreatorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )
        
        memory_creator_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:*", "bedrock-agentcore:*"],
            resources=["*"]
        ))
        
        return lambda_.Function(
            self, "MemoryCreatorLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            role=memory_creator_role,
            timeout=Duration.minutes(5),
            layers=[agentcore_layer],
            code=lambda_.Code.from_inline(self._get_memory_lambda_code())
        )

    def _create_actuarial_lambda(self, bucket_suffix, claims_bucket, athena_results_bucket, 
                                data_wrangler_layer, agentcore_layer, memory_id, memory_resource):
        lambda_role = iam.Role(
            self, "ActuarialLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )

        claims_bucket.grant_read(lambda_role)
        athena_results_bucket.grant_read_write(lambda_role)
        
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "athena:StartQueryExecution", "athena:GetQueryExecution", 
                "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"
            ],
            resources=["*"]
        ))
        
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions"],
            resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/claims_db_{bucket_suffix}",
                f"arn:aws:glue:{self.region}:{self.account}:table/claims_db_{bucket_suffix}/claims"
            ]
        ))
        
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                "bedrock:CreateMemory", "bedrock:GetMemory", "bedrock:ListMemories",
                "bedrock:CreateEvent", "bedrock:GetEvent", "bedrock:ListEvents",
                "bedrock-agentcore:CreateMemory", "bedrock-agentcore:GetMemory", 
                "bedrock-agentcore:ListMemories", "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents"
            ],
            resources=["*"]
        ))

        actuarial_lambda = lambda_.Function(
            self, "ActuarialToolsLambda",
            function_name=f"actuarial-tools-{bucket_suffix}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agentcore_lambda.lambda_handler",
            code=lambda_.Code.from_asset("cdk.out/lambda_build"),
            role=lambda_role,
            timeout=Duration.minutes(5),
            memory_size=1024,
            layers=[data_wrangler_layer, agentcore_layer],
            environment={
                "CLAIMS_BUCKET": claims_bucket.bucket_name,
                "ATHENA_DATABASE": f"claims_db_{bucket_suffix}",
                "ATHENA_TABLE": "claims",
                "DEFAULT_TABLE_NAME": "claims",
                "ATHENA_WORKGROUP": f"actuarial-workgroup-{bucket_suffix}",
                "ATHENA_OUTPUT_LOCATION": f"s3://{athena_results_bucket.bucket_name}/query-results/",
                "AGENTCORE_MEMORY_ID": memory_id,
                "ACTOR_ID": "ActuarialAgent"
            }
        )
        
        actuarial_lambda.node.add_dependency(memory_resource)
        actuarial_lambda.add_permission(
            "AllowAgentCoreInvoke",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction"
        )
        
        return actuarial_lambda

    def _create_data_query_lambda(self, bucket_suffix, claims_bucket, athena_results_bucket,
                                 data_wrangler_layer, agentcore_layer, memory_id, memory_resource):
        data_query_role = iam.Role(
            self, "DataQueryLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )

        data_query_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "glue:GetDatabases", "glue:GetDatabase", "glue:GetTables", 
                "glue:GetTable", "glue:GetPartitions"
            ],
            resources=["*"]
        ))
        
        data_query_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "athena:StartQueryExecution", "athena:GetQueryExecution",
                "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"
            ],
            resources=["*"]
        ))
        
        data_query_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:CreateMemory", "bedrock:GetMemory", "bedrock:ListMemories",
                "bedrock:CreateEvent", "bedrock:GetEvent", "bedrock:ListEvents",
                "bedrock-agentcore:CreateMemory", "bedrock-agentcore:GetMemory",
                "bedrock-agentcore:ListMemories", "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents"
            ],
            resources=["*"]
        ))

        athena_results_bucket.grant_read_write(data_query_role)
        claims_bucket.grant_read(data_query_role)

        data_query_lambda = lambda_.Function(
            self, "DataQueryLambda",
            function_name=f"data-query-tools-{bucket_suffix}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="data_query_lambda.lambda_handler",
            code=lambda_.Code.from_asset("cdk.out/lambda_build"),
            role=data_query_role,
            timeout=Duration.minutes(5),
            memory_size=512,
            layers=[data_wrangler_layer, agentcore_layer],
            environment={
                "ATHENA_DATABASE": f"claims_db_{bucket_suffix}",
                "DEFAULT_TABLE_NAME": "claims",
                "ATHENA_WORKGROUP": f"actuarial-workgroup-{bucket_suffix}",
                "ATHENA_OUTPUT_LOCATION": f"s3://{athena_results_bucket.bucket_name}/query-results/",
                "AGENTCORE_MEMORY_ID": memory_id,
                "ACTOR_ID": "ActuarialAgent"
            }
        )
        
        data_query_lambda.node.add_dependency(memory_resource)
        data_query_lambda.add_permission(
            "AllowAgentCoreInvokeDataQuery",
            principal=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            action="lambda:InvokeFunction"
        )
        
        return data_query_lambda

    def _create_outputs(self, claims_bucket, bucket_suffix, glue_crawler, 
                       actuarial_lambda, data_query_lambda, gateway_stack):
        # Infrastructure outputs
        CfnOutput(self, "ClaimsBucketName", value=claims_bucket.bucket_name)
        CfnOutput(self, "AthenaDatabase", value=f"claims_db_{bucket_suffix}")
        CfnOutput(self, "GlueCrawlerName", value=glue_crawler.name)
        CfnOutput(self, "LambdaFunctionName", value=actuarial_lambda.function_name)
        CfnOutput(self, "LambdaFunctionArn", value=actuarial_lambda.function_arn)
        CfnOutput(self, "DataQueryLambdaArn", value=data_query_lambda.function_arn)
        
        # Gateway outputs (matching original deploy_gateway.py exactly)
        CfnOutput(self, "GatewayUrl", 
                 value=gateway_stack.gateway_url,
                 description="AgentCore Gateway URL")
        CfnOutput(self, "GatewayId",
                 value=gateway_stack.gateway_id,
                 description="AgentCore Gateway ID") 
        CfnOutput(self, "ClientId",
                 value=gateway_stack.client_id,
                 description="Cognito Client ID")
        CfnOutput(self, "ClientSecret",
                 value=gateway_stack.client_secret,
                 description="Cognito Client Secret")
        CfnOutput(self, "UserPoolId",
                 value=gateway_stack.user_pool_id,
                 description="Cognito User Pool ID")
        CfnOutput(self, "TokenEndpoint",
                 value=gateway_stack.token_endpoint,
                 description="OAuth Token Endpoint")
        CfnOutput(self, "Scope",
                 value=gateway_stack.scope,
                 description="OAuth Scope")
        CfnOutput(self, "DomainPrefix",
                 value=gateway_stack.domain_prefix,
                 description="Cognito Domain Prefix")

    def _get_memory_lambda_code(self):
        return """
import json
import time
import cfnresponse
import traceback

def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    try:
        from bedrock_agentcore.memory import MemoryClient
        client = MemoryClient(region_name='us-east-1')
        
        if event['RequestType'] == 'Create':
            print("Creating AgentCore memory...")
            try:
                memory = client.create_memory(
                    name="ActuarialAgentMemory",
                    description="Memory for actuarial agent conversations and data",
                    strategies=[
                        {
                            'summaryMemoryStrategy': {
                                'name': 'SessionSummarizer',
                                'namespaces': ['/summaries/{actorId}/{sessionId}']
                            }
                        }
                    ]
                )
                
                memory_id = memory.get("id")
                if not memory_id:
                    raise Exception("Failed to get memory ID from creation response")
                    
                print(f"Memory created with ID: {memory_id}")
                
                max_wait = 300
                wait_time = 0
                while wait_time < max_wait:
                    try:
                        memories = client.list_memories()
                        current_memory = next((m for m in memories if m.get('id') == memory_id), None)
                        
                        if current_memory:
                            status = current_memory.get('status', 'UNKNOWN')
                            print(f"Memory status: {status}")
                            
                            if status == 'ACTIVE':
                                print("Memory resource is now ACTIVE.")
                                cfnresponse.send(event, context, cfnresponse.SUCCESS, 
                                               {"MemoryId": memory_id}, memory_id)
                                return
                            elif status == 'FAILED':
                                raise Exception(f"Memory resource creation FAILED with status: {status}")
                        
                        print("Waiting for memory to become active...")
                        time.sleep(10)
                        wait_time += 10
                        
                    except Exception as status_error:
                        print(f"Error checking memory status: {status_error}")
                        if wait_time > 60:
                            raise Exception(f"Persistent error checking memory status: {status_error}")
                        time.sleep(10)
                        wait_time += 10
                
                raise Exception(f"Timeout waiting for memory to become ACTIVE after {max_wait} seconds")
                
            except Exception as create_error:
                print(f"Error during memory creation: {create_error}")
                print(f"Traceback: {traceback.format_exc()}")
                raise create_error
        
        elif event['RequestType'] == 'Delete':
            memory_id = event.get('PhysicalResourceId')
            if memory_id and memory_id != 'PLACEHOLDER_MEMORY_ID':
                try:
                    print(f"Deleting memory: {memory_id}")
                    client.delete_memory(memory_id=memory_id)
                    print("Memory deletion initiated successfully")
                except Exception as delete_error:
                    print(f"Error deleting memory: {delete_error}")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        
        elif event['RequestType'] == 'Update':
            memory_id = event.get('PhysicalResourceId', 'PLACEHOLDER_MEMORY_ID')
            cfnresponse.send(event, context, cfnresponse.SUCCESS, 
                           {"MemoryId": memory_id}, memory_id)
        
        else:
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            
    except ImportError as import_error:
        error_msg = f"Failed to import bedrock_agentcore: {import_error}"
        print(error_msg)
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": error_msg})
        
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(error_msg)
        print(f"Full traceback: {traceback.format_exc()}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": error_msg})
"""
