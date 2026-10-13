"""
Observability data lake: S3 bucket + Glue Data Catalog + Athena workgroup.

This construct is the storage and query layer that the deep-analysis MCP tools
and the QuickSight datasets read from:

* One S3 bucket holds every observability data set, one prefix per table.
* A Glue database with one external table per prefix makes the data queryable
  with Athena SQL (JSON SerDe, so no transform step is needed for either the
  vended logs or the poller output).
* A dedicated Athena workgroup writes query results to an `athena-results/`
  prefix in the same bucket.

Vended-log tables and poller tables are modelled identically (external Glue
tables over an S3 prefix); only who writes the objects differs.
"""

from __future__ import annotations

from aws_cdk import RemovalPolicy
from aws_cdk import aws_athena as athena
from aws_cdk import aws_glue as glue
from aws_cdk import aws_s3 as s3
from constructs import Construct

from cdk.table_schemas import ALL_TABLES, Column


class ObservabilityDataLake(Construct):
    """S3 + Glue + Athena storage layer for Quick observability data."""

    def __init__(self, scope: Construct, construct_id: str) -> None:
        super().__init__(scope, construct_id)

        stack = _stack_of(self)
        self.database_name = "quick_observability"
        self.athena_results_prefix = "athena-results/"

        # ---- S3 data-lake bucket -----------------------------------------
        self.bucket = s3.Bucket(
            self,
            "DataLakeBucket",
            bucket_name=f"quick-observability-{stack.account}-{stack.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            versioned=False,
        )

        # ---- Glue database ------------------------------------------------
        self.glue_database = glue.CfnDatabase(
            self,
            "GlueDatabase",
            catalog_id=stack.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=self.database_name,
                description="Amazon Quick observability data lake",
            ),
        )

        # ---- One external Glue table per logical table --------------------
        self.table_names: list[str] = []
        for table_name, columns in ALL_TABLES.items():
            cfn_table = self._build_table(stack, table_name, columns)
            cfn_table.add_dependency(self.glue_database)
            self.table_names.append(table_name)

        # ---- Athena workgroup --------------------------------------------
        self.workgroup_name = "quick-observability"
        self.athena_workgroup = athena.CfnWorkGroup(
            self,
            "AthenaWorkGroup",
            name=self.workgroup_name,
            recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{self.bucket.bucket_name}/{self.athena_results_prefix}",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3"
                    ),
                ),
            ),
        )

    def table_s3_uri(self, table_name: str) -> str:
        return f"s3://{self.bucket.bucket_name}/{table_name}/"

    def _build_table(
        self, stack, table_name: str, columns: list[Column]
    ) -> glue.CfnTable:
        glue_columns = [
            glue.CfnTable.ColumnProperty(name=name, type=hive_type)
            for name, hive_type in columns
        ]

        return glue.CfnTable(
            self,
            f"GlueTable-{table_name}",
            catalog_id=stack.account,
            database_name=self.database_name,
            table_input=glue.CfnTable.TableInputProperty(
                name=table_name,
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "json",
                    "jsonPath": "$[*]",
                },
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    columns=glue_columns,
                    location=self.table_s3_uri(table_name),
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    output_format=(
                        "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
                    ),
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.openx.data.jsonserde.JsonSerDe",
                        parameters={"ignore.malformed.json": "true"},
                    ),
                ),
            ),
        )


def _stack_of(scope: Construct):
    from aws_cdk import Stack

    return Stack.of(scope)
