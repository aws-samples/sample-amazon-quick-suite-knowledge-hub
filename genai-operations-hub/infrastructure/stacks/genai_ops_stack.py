from aws_cdk import (
    Stack,
    aws_s3 as s3,
    aws_glue as glue,
    aws_athena as athena,
    aws_quicksight as quicksight,
    aws_iam as iam,
    RemovalPolicy,
    CfnOutput
)
from constructs import Construct

class GenAIOperationsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 Bucket for Bedrock logs
        logs_bucket = s3.Bucket(
            self, "BedrockLogsBucket",
            bucket_name=f"genai-ops-bedrock-logs-{self.account}",
            versioned=False,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # S3 Bucket for Athena query results
        athena_results_bucket = s3.Bucket(
            self, "AthenaResultsBucket",
            bucket_name=f"genai-ops-athena-results-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # Glue Database
        database = glue.CfnDatabase(
            self, "BedrockLogsDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name="genai_ops_db",
                description="Database for Bedrock invocation logs"
            )
        )

        # Glue Table for Bedrock logs
        table = glue.CfnTable(
            self, "BedrockLogsTable",
            catalog_id=self.account,
            database_name=database.ref,
            table_input=glue.CfnTable.TableInputProperty(
                name="bedrock_invocation_logs",
                description="Bedrock model invocation logs",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "json",
                    "compressionType": "none"
                },
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    columns=[
                        glue.CfnTable.ColumnProperty(name="schemaType", type="string"),
                        glue.CfnTable.ColumnProperty(name="schemaVersion", type="string"),
                        glue.CfnTable.ColumnProperty(name="timestamp", type="string"),
                        glue.CfnTable.ColumnProperty(name="accountId", type="string"),
                        glue.CfnTable.ColumnProperty(name="identity", type="struct<arn:string>"),
                        glue.CfnTable.ColumnProperty(name="region", type="string"),
                        glue.CfnTable.ColumnProperty(name="requestId", type="string"),
                        glue.CfnTable.ColumnProperty(name="operation", type="string"),
                        glue.CfnTable.ColumnProperty(name="modelId", type="string"),
                        glue.CfnTable.ColumnProperty(name="input", type="struct<inputContentType:string,inputBodyJson:struct<messages:array<struct<role:string,content:array<struct<text:string>>>>,system:array<struct<text:string>>,inferenceConfig:struct<maxTokens:int,temperature:double,topP:double>>,inputTokenCount:int>"),
                        glue.CfnTable.ColumnProperty(name="output", type="struct<outputContentType:string,outputBodyJson:struct<output:struct<message:struct<role:string,content:array<struct<text:string>>>>,stopReason:string,metrics:struct<latencyMs:int>,usage:struct<inputTokens:int,outputTokens:int,totalTokens:int>>,outputTokenCount:int>")
                    ],
                    location=f"s3://{logs_bucket.bucket_name}/",
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.openx.data.jsonserde.JsonSerDe"
                    )
                )
            )
        )

        # Athena Workgroup
        workgroup = athena.CfnWorkGroup(
            self, "GenAIOpsWorkGroup",
            name="genai-ops-workgroup",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{athena_results_bucket.bucket_name}/"
                )
            )
        )

        # QuickSight Data Source IAM Role
        qs_role = iam.Role(
            self, "QuickSightRole",
            assumed_by=iam.ServicePrincipal("quicksight.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSQuickSightAthenaAccess")
            ]
        )

        logs_bucket.grant_read(qs_role)
        athena_results_bucket.grant_read_write(qs_role)

        # Expose properties for QuickSight stack
        self.database_name = database.ref
        self.workgroup_name = workgroup.name

        # Outputs
        CfnOutput(self, "LogsBucketName", value=logs_bucket.bucket_name)
        CfnOutput(self, "DatabaseName", value=database.ref)
        CfnOutput(self, "TableName", value="bedrock_invocation_logs")
        CfnOutput(self, "AthenaWorkGroup", value=workgroup.name)
        CfnOutput(self, "QuickSightRoleArn", value=qs_role.role_arn)

