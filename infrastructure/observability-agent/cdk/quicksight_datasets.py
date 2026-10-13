"""
QuickSight Athena data source + datasets over the observability data lake.

Creates one QuickSight Athena data source bound to the observability Athena
workgroup, then one SPICE dataset per observability table so an admin can
build detailed dashboards (per-user usage, per-agent hours, active-user
trends, audit trail, metric history, etc.) directly on top of the collected
data.

QuickSight principal ARNs (owner/admin) are supplied as a parameter because
they differ per account and identity setup. When one or more ARNs are given,
owner permissions are attached to the data source and every dataset via a
CloudFormation condition; when none are given, the resources are created
without explicit permissions (QuickSight account admins still retain access
through the service) so the stack deploys cleanly with no configuration.
"""

from __future__ import annotations

import re

from aws_cdk import Aws, Fn, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_quicksight as quicksight
from constructs import Construct

from cdk.table_schemas import ALL_TABLES, Column

# Map Hive/Athena types to QuickSight input-column types.
_QS_TYPE = {
    "string": "STRING",
    "bigint": "INTEGER",
    "double": "DECIMAL",
    "boolean": "BOOLEAN",
}

_DATA_SOURCE_ACTIONS = [
    "quicksight:DescribeDataSource",
    "quicksight:DescribeDataSourcePermissions",
    "quicksight:PassDataSource",
    "quicksight:UpdateDataSource",
    "quicksight:DeleteDataSource",
    "quicksight:UpdateDataSourcePermissions",
]

_DATA_SET_ACTIONS = [
    "quicksight:DescribeDataSet",
    "quicksight:DescribeDataSetPermissions",
    "quicksight:PassDataSet",
    "quicksight:DescribeIngestion",
    "quicksight:ListIngestions",
    "quicksight:UpdateDataSet",
    "quicksight:DeleteDataSet",
    "quicksight:CreateIngestion",
    "quicksight:CancelIngestion",
    "quicksight:UpdateDataSetPermissions",
]


class QuickSightDatasets(Construct):
    """Athena data source + one dataset per observability table."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        athena_workgroup: str,
        glue_database: str,
        data_bucket_arn: str,
        principal_arns: list[str],
    ) -> None:
        super().__init__(scope, construct_id)

        stack = Stack.of(self)
        account = stack.account
        region = stack.region

        # QuickSight validates an Athena data source at create time by running
        # a test query, which must write results to the Athena workgroup's S3
        # bucket. Grant the QuickSight service role access to our data-lake
        # bucket, Athena workgroup, and Glue catalog so that test passes.
        qs_service_role_arn = (
            f"arn:aws:iam::{account}:role/service-role/aws-quicksight-service-role-v0"
        )
        qs_access_policy = iam.ManagedPolicy(
            self,
            "QuickSightDataLakeAccess",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3:GetObject",
                        "s3:GetObjectVersion",
                        "s3:GetBucketLocation",
                        "s3:ListBucket",
                        "s3:ListBucketMultipartUploads",
                        "s3:ListMultipartUploadParts",
                        "s3:AbortMultipartUpload",
                        "s3:PutObject",
                    ],
                    resources=[data_bucket_arn, f"{data_bucket_arn}/*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "athena:StartQueryExecution",
                        "athena:GetQueryExecution",
                        "athena:GetQueryResults",
                        "athena:GetQueryResultsStream",
                        "athena:GetWorkGroup",
                        "athena:StopQueryExecution",
                        "athena:ListDataCatalogs",
                        "athena:ListDatabases",
                        "athena:GetTableMetadata",
                    ],
                    resources=[
                        f"arn:aws:athena:{region}:{account}:workgroup/{athena_workgroup}"
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
                        f"arn:aws:glue:{region}:{account}:catalog",
                        f"arn:aws:glue:{region}:{account}:database/{glue_database}",
                        f"arn:aws:glue:{region}:{account}:table/{glue_database}/*",
                    ],
                ),
            ],
            roles=[
                iam.Role.from_role_arn(
                    self,
                    "QuickSightServiceRole",
                    qs_service_role_arn,
                    mutable=False,
                )
            ],
        )

        # Attach permissions only when a principal ARN was supplied. The
        # parameter is a token at synth time, so decide with a CfnCondition on
        # whether the first element is a non-empty string.
        first_principal = Fn.select(0, principal_arns)
        has_principals = _condition(self, "HasQuickSightPrincipals", first_principal)

        data_source_permissions = Fn.condition_if(
            has_principals.logical_id,
            [
                quicksight.CfnDataSource.ResourcePermissionProperty(
                    principal=first_principal,
                    actions=_DATA_SOURCE_ACTIONS,
                )
            ],
            Aws.NO_VALUE,
        )

        self.data_source = quicksight.CfnDataSource(
            self,
            "AthenaDataSource",
            aws_account_id=account,
            data_source_id="quick-observability-athena",
            name="Quick Observability (Athena)",
            type="ATHENA",
            data_source_parameters=quicksight.CfnDataSource.DataSourceParametersProperty(
                athena_parameters=quicksight.CfnDataSource.AthenaParametersProperty(
                    work_group=athena_workgroup,
                )
            ),
            ssl_properties=quicksight.CfnDataSource.SslPropertiesProperty(
                disable_ssl=False
            ),
            permissions=data_source_permissions,
        )
        # The QuickSight role must be able to reach the bucket/workgroup before
        # the data source's connection test runs.
        self.data_source.node.add_dependency(qs_access_policy)

        data_set_permissions = Fn.condition_if(
            has_principals.logical_id,
            [
                quicksight.CfnDataSet.ResourcePermissionProperty(
                    principal=first_principal,
                    actions=_DATA_SET_ACTIONS,
                )
            ],
            Aws.NO_VALUE,
        )

        self.datasets: list[quicksight.CfnDataSet] = []
        self.dataset_by_table: dict[str, quicksight.CfnDataSet] = {}
        for table_name, columns in ALL_TABLES.items():
            dataset = self._build_dataset(
                account=account,
                glue_database=glue_database,
                table_name=table_name,
                columns=columns,
                permissions=data_set_permissions,
            )
            dataset.add_dependency(self.data_source)
            self.datasets.append(dataset)
            self.dataset_by_table[table_name] = dataset
            self._add_refresh_schedule(
                dataset, f"quick-obs-{table_name.replace('_', '-')}"
            )

        # Per-user summary dataset (custom SQL): one row per user with queries,
        # conversations, last-active, agent hours, and consumed storage —
        # exactly what the dashboard's per-user table needs. Joining three
        # tables requires custom SQL (a single Glue table can't express it).
        self.user_summary_dataset = self._build_user_summary_dataset(
            account=account,
            glue_database=glue_database,
            permissions=data_set_permissions,
        )
        self.user_summary_dataset.add_dependency(self.data_source)
        self.datasets.append(self.user_summary_dataset)
        self._add_refresh_schedule(self.user_summary_dataset, "quick-obs-user-summary")

    def _add_refresh_schedule(self, dataset, data_set_id: str) -> None:
        """Hourly SPICE refresh so the dashboard/datasets stay current (SPICE
        does not auto-refresh; without this the cache stays at its initial,
        possibly empty, snapshot)."""
        sched = quicksight.CfnRefreshSchedule(
            self,
            f"Refresh-{data_set_id}",
            aws_account_id=Stack.of(self).account,
            data_set_id=data_set_id,
            schedule=quicksight.CfnRefreshSchedule.RefreshScheduleMapProperty(
                schedule_id=f"{data_set_id}-hourly",
                refresh_type="FULL_REFRESH",
                schedule_frequency=quicksight.CfnRefreshSchedule.ScheduleFrequencyProperty(
                    interval="HOURLY",
                ),
            ),
        )
        sched.add_dependency(dataset)

    def _build_user_summary_dataset(
        self, *, account: str, glue_database: str, permissions
    ) -> quicksight.CfnDataSet:
        # QuickSight custom SQL is a static definition and cannot bind
        # parameters. glue_database is a fixed infrastructure identifier;
        # validate it, then substitute it into a literal SQL template.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", glue_database):
            raise ValueError(f"unsafe Glue database identifier: {glue_database!r}")
        sql_template = (
            "WITH chat AS ("
            "  SELECT user_arn, COUNT(*) AS total_queries, "
            "         COUNT(DISTINCT conversation_id) AS total_conversations, "
            "         MAX(event_timestamp) AS last_active_epoch "
            "  FROM __DB__.chat_events GROUP BY user_arn), "
            "hours AS ("
            "  SELECT user_arn, SUM(usage_hours) AS agent_hours "
            "  FROM __DB__.agent_hours GROUP BY user_arn), "
            "storage AS ("
            "  SELECT user_arn, "
            "         SUM(consumed_source_size) / 1073741824.0 AS consumed_source_gb, "
            "         SUM(consumed_index_size) / 1073741824.0 AS consumed_index_gb, "
            "         SUM(consumed_source_doc_count) AS documents "
            "  FROM __DB__.index_usage GROUP BY user_arn) "
            "SELECT COALESCE(chat.user_arn, hours.user_arn, storage.user_arn) AS user_arn, "
            "       COALESCE(chat.total_queries, 0) AS total_queries, "
            "       COALESCE(chat.total_conversations, 0) AS total_conversations, "
            "       date_format(from_unixtime(COALESCE(chat.last_active_epoch, 0)), '%Y-%m-%d %H:%i:%s') AS last_active, "
            "       COALESCE(hours.agent_hours, 0.0) AS agent_hours, "
            "       COALESCE(storage.consumed_source_gb, 0.0) AS consumed_source_gb, "
            "       COALESCE(storage.consumed_index_gb, 0.0) AS consumed_index_gb, "
            "       COALESCE(storage.documents, 0) AS documents "
            "FROM chat "
            "FULL OUTER JOIN hours ON chat.user_arn = hours.user_arn "
            "FULL OUTER JOIN storage "
            "  ON COALESCE(chat.user_arn, hours.user_arn) = storage.user_arn"
        )
        sql = sql_template.replace("__DB__", glue_database)
        cols = [
            ("user_arn", "STRING"),
            ("total_queries", "INTEGER"),
            ("total_conversations", "INTEGER"),
            ("last_active", "STRING"),
            ("agent_hours", "DECIMAL"),
            ("consumed_source_gb", "DECIMAL"),
            ("consumed_index_gb", "DECIMAL"),
            ("documents", "INTEGER"),
        ]
        return quicksight.CfnDataSet(
            self,
            "DataSet-user-summary",
            aws_account_id=account,
            data_set_id="quick-obs-user-summary",
            name="Quick Observability - user summary",
            import_mode="SPICE",
            physical_table_map={
                "usersummary": quicksight.CfnDataSet.PhysicalTableProperty(
                    custom_sql=quicksight.CfnDataSet.CustomSqlProperty(
                        data_source_arn=self.data_source.attr_arn,
                        name="user_summary",
                        sql_query=sql,
                        columns=[
                            quicksight.CfnDataSet.InputColumnProperty(name=n, type=t)
                            for n, t in cols
                        ],
                    )
                )
            },
            permissions=permissions,
        )

    def _build_dataset(
        self,
        *,
        account: str,
        glue_database: str,
        table_name: str,
        columns: list[Column],
        permissions,
    ) -> quicksight.CfnDataSet:
        input_columns = [
            quicksight.CfnDataSet.InputColumnProperty(
                name=col_name,
                type=_QS_TYPE.get(hive_type, "STRING"),
            )
            for col_name, hive_type in columns
        ]

        physical_table = quicksight.CfnDataSet.RelationalTableProperty(
            data_source_arn=self.data_source.attr_arn,
            catalog="AwsDataCatalog",
            schema=glue_database,
            name=table_name,
            input_columns=input_columns,
        )

        physical_table_key = table_name.replace("_", "")
        return quicksight.CfnDataSet(
            self,
            f"DataSet-{table_name}",
            aws_account_id=account,
            data_set_id=f"quick-obs-{table_name.replace('_', '-')}",
            name=f"Quick Observability - {table_name}",
            import_mode="SPICE",
            physical_table_map={
                physical_table_key: quicksight.CfnDataSet.PhysicalTableProperty(
                    relational_table=physical_table
                )
            },
            permissions=permissions,
        )


def _condition(scope: Construct, logical_id: str, first_principal: str):
    from aws_cdk import CfnCondition

    return CfnCondition(
        scope,
        logical_id,
        expression=Fn.condition_not(Fn.condition_equals(first_principal, "")),
    )
