from aws_cdk import (
    Stack,
    aws_quicksight as quicksight,
    CfnOutput
)
from constructs import Construct

class QuickSightStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, 
                 database_name: str,
                 workgroup_name: str,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # QuickSight Data Source
        qs_data_source = quicksight.CfnDataSource(
            self, "QuickSightDataSource",
            aws_account_id=self.account,
            data_source_id="genai-ops-athena-source",
            name="GenAI-Ops-Athena",
            type="ATHENA",
            data_source_parameters=quicksight.CfnDataSource.DataSourceParametersProperty(
                athena_parameters=quicksight.CfnDataSource.AthenaParametersProperty(
                    work_group=workgroup_name
                )
            ),
            permissions=[
                quicksight.CfnDataSource.ResourcePermissionProperty(
                    principal=f"arn:aws:quicksight:{self.region}:{self.account}:user/default/{self.node.try_get_context('quicksight_user') or 'Admin/cmakinen'}",
                    actions=[
                        "quicksight:DescribeDataSource",
                        "quicksight:DescribeDataSourcePermissions",
                        "quicksight:PassDataSource",
                        "quicksight:UpdateDataSource",
                        "quicksight:DeleteDataSource",
                        "quicksight:UpdateDataSourcePermissions"
                    ]
                )
            ]
        )

        # QuickSight Dataset - Daily Invocations
        qs_dataset_daily = quicksight.CfnDataSet(
            self, "DailyInvocationsDataSet",
            aws_account_id=self.account,
            data_set_id="daily-bedrock-invocations",
            name="Daily Bedrock Invocations",
            import_mode="DIRECT_QUERY",
            physical_table_map={
                "daily_invocations": quicksight.CfnDataSet.PhysicalTableProperty(
                    custom_sql=quicksight.CfnDataSet.CustomSqlProperty(
                        data_source_arn=qs_data_source.attr_arn,
                        name="daily_invocations",
                        sql_query=f"""
                            SELECT 
                                DATE(from_iso8601_timestamp(timestamp)) as date,
                                modelId,
                                COUNT(*) as invocation_count,
                                AVG(output.outputBodyJson.metrics.latencyMs) as avg_latency_ms,
                                SUM(output.outputBodyJson.usage.inputTokens) as total_input_tokens,
                                SUM(output.outputBodyJson.usage.outputTokens) as total_output_tokens
                            FROM {database_name}.bedrock_invocation_logs
                            GROUP BY DATE(from_iso8601_timestamp(timestamp)), modelId
                        """,
                        columns=[
                            quicksight.CfnDataSet.InputColumnProperty(name="date", type="DATETIME"),
                            quicksight.CfnDataSet.InputColumnProperty(name="modelId", type="STRING"),
                            quicksight.CfnDataSet.InputColumnProperty(name="invocation_count", type="INTEGER"),
                            quicksight.CfnDataSet.InputColumnProperty(name="avg_latency_ms", type="DECIMAL"),
                            quicksight.CfnDataSet.InputColumnProperty(name="total_input_tokens", type="INTEGER"),
                            quicksight.CfnDataSet.InputColumnProperty(name="total_output_tokens", type="INTEGER")
                        ]
                    )
                )
            },
            permissions=[
                quicksight.CfnDataSet.ResourcePermissionProperty(
                    principal=f"arn:aws:quicksight:{self.region}:{self.account}:user/default/{self.node.try_get_context('quicksight_user') or 'Admin/cmakinen'}",
                    actions=[
                        "quicksight:DescribeDataSet",
                        "quicksight:DescribeDataSetPermissions",
                        "quicksight:PassDataSet",
                        "quicksight:DescribeIngestion",
                        "quicksight:ListIngestions",
                        "quicksight:UpdateDataSet",
                        "quicksight:DeleteDataSet",
                        "quicksight:CreateIngestion",
                        "quicksight:CancelIngestion",
                        "quicksight:UpdateDataSetPermissions"
                    ]
                )
            ]
        )

        # QuickSight Dataset - Model Performance
        qs_dataset_performance = quicksight.CfnDataSet(
            self, "ModelPerformanceDataSet",
            aws_account_id=self.account,
            data_set_id="model-performance-metrics",
            name="Model Performance Metrics",
            import_mode="DIRECT_QUERY",
            physical_table_map={
                "model_performance": quicksight.CfnDataSet.PhysicalTableProperty(
                    custom_sql=quicksight.CfnDataSet.CustomSqlProperty(
                        data_source_arn=qs_data_source.attr_arn,
                        name="model_performance",
                        sql_query=f"""
                            SELECT 
                                modelId,
                                COUNT(*) as total_requests,
                                AVG(output.outputBodyJson.metrics.latencyMs) as avg_latency_ms,
                                MIN(output.outputBodyJson.metrics.latencyMs) as min_latency_ms,
                                MAX(output.outputBodyJson.metrics.latencyMs) as max_latency_ms,
                                AVG(output.outputBodyJson.usage.totalTokens) as avg_tokens
                            FROM {database_name}.bedrock_invocation_logs
                            GROUP BY modelId
                        """,
                        columns=[
                            quicksight.CfnDataSet.InputColumnProperty(name="modelId", type="STRING"),
                            quicksight.CfnDataSet.InputColumnProperty(name="total_requests", type="INTEGER"),
                            quicksight.CfnDataSet.InputColumnProperty(name="avg_latency_ms", type="DECIMAL"),
                            quicksight.CfnDataSet.InputColumnProperty(name="min_latency_ms", type="DECIMAL"),
                            quicksight.CfnDataSet.InputColumnProperty(name="max_latency_ms", type="DECIMAL"),
                            quicksight.CfnDataSet.InputColumnProperty(name="avg_tokens", type="DECIMAL")
                        ]
                    )
                )
            },
            permissions=[
                quicksight.CfnDataSet.ResourcePermissionProperty(
                    principal=f"arn:aws:quicksight:{self.region}:{self.account}:user/default/{self.node.try_get_context('quicksight_user') or 'Admin/cmakinen'}",
                    actions=[
                        "quicksight:DescribeDataSet",
                        "quicksight:DescribeDataSetPermissions",
                        "quicksight:PassDataSet",
                        "quicksight:DescribeIngestion",
                        "quicksight:ListIngestions",
                        "quicksight:UpdateDataSet",
                        "quicksight:DeleteDataSet",
                        "quicksight:CreateIngestion",
                        "quicksight:CancelIngestion",
                        "quicksight:UpdateDataSetPermissions"
                    ]
                )
            ]
        )

        # QuickSight Dataset - Stop Reason Analysis
        qs_dataset_stop_reason = quicksight.CfnDataSet(
            self, "StopReasonDataSet",
            aws_account_id=self.account,
            data_set_id="stop-reason-analysis",
            name="Stop Reason Analysis",
            import_mode="DIRECT_QUERY",
            physical_table_map={
                "stop_reason_analysis": quicksight.CfnDataSet.PhysicalTableProperty(
                    custom_sql=quicksight.CfnDataSet.CustomSqlProperty(
                        data_source_arn=qs_data_source.attr_arn,
                        name="stop_reason_analysis",
                        sql_query=f"""
                            SELECT 
                                output.outputBodyJson.stopReason as stop_reason,
                                modelId,
                                COUNT(*) as occurrence_count
                            FROM {database_name}.bedrock_invocation_logs
                            GROUP BY output.outputBodyJson.stopReason, modelId
                        """,
                        columns=[
                            quicksight.CfnDataSet.InputColumnProperty(name="stop_reason", type="STRING"),
                            quicksight.CfnDataSet.InputColumnProperty(name="modelId", type="STRING"),
                            quicksight.CfnDataSet.InputColumnProperty(name="occurrence_count", type="INTEGER")
                        ]
                    )
                )
            },
            permissions=[
                quicksight.CfnDataSet.ResourcePermissionProperty(
                    principal=f"arn:aws:quicksight:{self.region}:{self.account}:user/default/{self.node.try_get_context('quicksight_user') or 'Admin/cmakinen'}",
                    actions=[
                        "quicksight:DescribeDataSet",
                        "quicksight:DescribeDataSetPermissions",
                        "quicksight:PassDataSet",
                        "quicksight:DescribeIngestion",
                        "quicksight:ListIngestions",
                        "quicksight:UpdateDataSet",
                        "quicksight:DeleteDataSet",
                        "quicksight:CreateIngestion",
                        "quicksight:CancelIngestion",
                        "quicksight:UpdateDataSetPermissions"
                    ]
                )
            ]
        )

        # Outputs
        CfnOutput(self, "QuickSightDataSourceId", value=qs_data_source.data_source_id)
        CfnOutput(self, "DataSetDailyInvocations", value=qs_dataset_daily.data_set_id)
        CfnOutput(self, "DataSetModelPerformance", value=qs_dataset_performance.data_set_id)
        CfnOutput(self, "DataSetStopReason", value=qs_dataset_stop_reason.data_set_id)
