"""
QuickSight dashboard for the Quick observability data lake.

Builds a `CfnDashboard` with a native, interactive visual definition that
closely mirrors the Amazon Quick "Quick Suite usage metrics" page:

Sheet 1 "Usage overview":
  * KPI tiles — active users, total conversations, total queries, avg queries
    per conversation, total agent hours.
  * Conversations, queries & active users by month (combo/bar).
  * Average queries per conversation by month (bar).
  * Query scope selection by month (line, split by message_scope).
  * Unanswered / error queries by status (bar).
  * Assets used — distinct agents / flows / connectors (bar).
  * Agent hours by subscription type (bar).
  * Interactive controls: a date-range picker and an agent dropdown, wired to
    the chat visuals via a filter group.

Sheet 2 "Per-user detail":
  * Table — per user: queries, conversations, last active, agent hours, and
    source/index storage used (from the custom-SQL user-summary dataset).

Timestamps in the lake are epoch SECONDS; the `month` calculated field derives
a date with truncDate(epochDate(event_timestamp)).
"""

from __future__ import annotations

from aws_cdk import Aws, Fn, Stack
from aws_cdk import aws_quicksight as quicksight
from constructs import Construct

_DASHBOARD_ACTIONS = [
    "quicksight:DescribeDashboard",
    "quicksight:ListDashboardVersions",
    "quicksight:QueryDashboard",
    "quicksight:UpdateDashboard",
    "quicksight:UpdateDashboardPermissions",
    "quicksight:DescribeDashboardPermissions",
    "quicksight:UpdateDashboardPublishedVersion",
    "quicksight:DeleteDashboard",
]

DS_USER = "user_summary"
DS_CHAT = "chat_events"
DS_HOURS = "agent_hours"
DS_FEEDBACK = "feedback_events"
DS_INDEX = "index_usage"
DS_KBSYNC = "kb_file_sync"
DS_DLP = "dlp_events"
DS_TRAIL = "cloudtrail_events"
DS_METRIC = "metric_snapshots"
DS_AGENTMETA = "agent_metadata"

# Calculated-field identifiers (defined on the chat dataset).
F_MONTH = "chat_month"
F_TRAIL_DAY = "trail_day"
F_INDEX_GB = "index_gb"
F_SOURCE_GB = "source_gb"
F_TRAIL_ACTION = "trail_action"
# Chat date/agent filter group + controls.
FG_CHAT = "fg_chat_date"
FLT_DATE = "flt_chat_date"
FLT_AGENT = "flt_agent"
CTRL_DATE = "ctrl_date"
CTRL_AGENT = "ctrl_agent"
# Subscription-type filter group + control (agent_hours dataset).
FG_SUB = "fg_hours_sub"
FLT_SUB = "flt_sub"
CTRL_SUB = "ctrl_sub"
# Per-user sheet filter group + control (user_summary dataset).
FG_PUSER = "fg_user_summary"
FLT_PUSER = "flt_puser"
CTRL_PUSER = "ctrl_puser"
# CloudTrail audit sheet filter group + controls (cloudtrail_events dataset).
FG_TRAIL = "fg_cloudtrail"
FLT_TRAIL_LIFECYCLE = "flt_trail_lifecycle"
FLT_TRAIL_EVENT = "flt_trail_event"
FLT_TRAIL_USER = "flt_trail_user"
CTRL_TRAIL_EVENT = "ctrl_trail_event"
CTRL_TRAIL_USER = "ctrl_trail_user"
CTRL_TRAIL_LIFECYCLE = "ctrl_trail_lifecycle"


def _col(ds_id, name):
    return quicksight.CfnDashboard.ColumnIdentifierProperty(
        data_set_identifier=ds_id, column_name=name
    )


def _num(fid, ds, col, agg):
    return quicksight.CfnDashboard.MeasureFieldProperty(
        numerical_measure_field=quicksight.CfnDashboard.NumericalMeasureFieldProperty(
            field_id=fid,
            column=_col(ds, col),
            aggregation_function=quicksight.CfnDashboard.NumericalAggregationFunctionProperty(
                simple_numerical_aggregation=agg
            ),
        )
    )


def _cat_measure(fid, ds, col, agg):
    return quicksight.CfnDashboard.MeasureFieldProperty(
        categorical_measure_field=quicksight.CfnDashboard.CategoricalMeasureFieldProperty(
            field_id=fid, column=_col(ds, col), aggregation_function=agg
        )
    )


def _cat_dim(fid, ds, col):
    return quicksight.CfnDashboard.DimensionFieldProperty(
        categorical_dimension_field=quicksight.CfnDashboard.CategoricalDimensionFieldProperty(
            field_id=fid, column=_col(ds, col)
        )
    )


def _date_dim(fid, ds, col, granularity="MONTH"):
    return quicksight.CfnDashboard.DimensionFieldProperty(
        date_dimension_field=quicksight.CfnDashboard.DateDimensionFieldProperty(
            field_id=fid, column=_col(ds, col), date_granularity=granularity
        )
    )


def _title(text):
    return quicksight.CfnDashboard.VisualTitleLabelOptionsProperty(
        visibility="VISIBLE",
        format_text=quicksight.CfnDashboard.ShortFormatTextProperty(plain_text=text),
    )


class QuickSightDashboard(Construct):
    def __init__(
        self,
        scope,
        construct_id,
        *,
        user_summary_dataset_arn,
        table_dataset_arns,
        principal_arns,
    ):
        super().__init__(scope, construct_id)
        account = Stack.of(self).account

        first_principal = Fn.select(0, principal_arns)
        has_principals = _dash_condition(self, "HasDashPrincipals", first_principal)
        permissions = Fn.condition_if(
            has_principals.logical_id,
            [
                quicksight.CfnDashboard.ResourcePermissionProperty(
                    principal=first_principal, actions=_DASHBOARD_ACTIONS
                )
            ],
            Aws.NO_VALUE,
        )

        identifiers = [
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_USER, data_set_arn=user_summary_dataset_arn
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_CHAT, data_set_arn=table_dataset_arns[DS_CHAT]
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_HOURS, data_set_arn=table_dataset_arns[DS_HOURS]
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_FEEDBACK, data_set_arn=table_dataset_arns[DS_FEEDBACK]
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_INDEX, data_set_arn=table_dataset_arns[DS_INDEX]
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_KBSYNC, data_set_arn=table_dataset_arns[DS_KBSYNC]
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_DLP, data_set_arn=table_dataset_arns[DS_DLP]
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_TRAIL, data_set_arn=table_dataset_arns[DS_TRAIL]
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_METRIC, data_set_arn=table_dataset_arns[DS_METRIC]
            ),
            quicksight.CfnDashboard.DataSetIdentifierDeclarationProperty(
                identifier=DS_AGENTMETA, data_set_arn=table_dataset_arns[DS_AGENTMETA]
            ),
        ]

        # Month bucket on the chat dataset (epoch seconds -> date, truncated).
        calc_fields = [
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_CHAT,
                name=F_MONTH,
                expression="truncDate('MM', epochDate(event_timestamp))",
            ),
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_FEEDBACK,
                name=F_MONTH,
                expression="truncDate('MM', epochDate(event_timestamp))",
            ),
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_KBSYNC,
                name=F_MONTH,
                expression="truncDate('MM', epochDate(event_timestamp))",
            ),
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_TRAIL,
                name=F_MONTH,
                expression="truncDate('MM', epochDate(event_time))",
            ),
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_TRAIL,
                name=F_TRAIL_DAY,
                expression="truncDate('DD', epochDate(event_time))",
            ),
            # Classify each CloudTrail write into an asset-lifecycle action.
            # Permission grants (share) are "Share"; everything that is not a
            # create/update/delete/share lifecycle call is "Other" (noise).
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_TRAIL,
                name=F_TRAIL_ACTION,
                expression=(
                    "ifelse("
                    "locate(event_name, 'Permission') > 0 OR locate(event_name, 'Share') > 0, 'Share', "
                    "left(event_name, 6) = 'Create', 'Create', "
                    "left(event_name, 6) = 'Delete', 'Delete', "
                    "left(event_name, 6) = 'Update', 'Update', "
                    "'Other')"
                ),
            ),
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_INDEX,
                name=F_INDEX_GB,
                expression="consumed_index_size / 1073741824.0",
            ),
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_INDEX,
                name=F_SOURCE_GB,
                expression="consumed_source_size / 1073741824.0",
            ),
            quicksight.CfnDashboard.CalculatedFieldProperty(
                data_set_identifier=DS_METRIC,
                name=F_MONTH,
                expression="truncDate('MM', epochDate(snapshot_timestamp))",
            ),
        ]

        # Interactive filter: a date range on chat_events.event_timestamp,
        # driven by a date-range picker control; plus an agent dropdown.
        filter_groups = [
            quicksight.CfnDashboard.FilterGroupProperty(
                filter_group_id=FG_CHAT,
                cross_dataset="SINGLE_DATASET",
                scope_configuration=quicksight.CfnDashboard.FilterScopeConfigurationProperty(
                    selected_sheets=quicksight.CfnDashboard.SelectedSheetsFilterScopeConfigurationProperty(
                        sheet_visual_scoping_configurations=[
                            quicksight.CfnDashboard.SheetVisualScopingConfigurationProperty(
                                sheet_id="usage", scope="ALL_VISUALS"
                            )
                        ]
                    )
                ),
                filters=[
                    quicksight.CfnDashboard.FilterProperty(
                        relative_dates_filter=quicksight.CfnDashboard.RelativeDatesFilterProperty(
                            filter_id=FLT_DATE,
                            column=_col(DS_CHAT, F_MONTH),
                            anchor_date_configuration=quicksight.CfnDashboard.AnchorDateConfigurationProperty(
                                anchor_option="NOW"
                            ),
                            time_granularity="MONTH",
                            relative_date_type="LAST",
                            relative_date_value=12,
                            null_option="ALL_VALUES",
                        )
                    ),
                    quicksight.CfnDashboard.FilterProperty(
                        category_filter=quicksight.CfnDashboard.CategoryFilterProperty(
                            filter_id=FLT_AGENT,
                            column=_col(DS_CHAT, "agent_id"),
                            configuration=quicksight.CfnDashboard.CategoryFilterConfigurationProperty(
                                filter_list_configuration=quicksight.CfnDashboard.FilterListConfigurationProperty(
                                    match_operator="CONTAINS",
                                    select_all_options="FILTER_ALL_VALUES",
                                )
                            ),
                        )
                    ),
                ],
            ),
            # Subscription-type filter on the agent_hours dataset, driving the
            # "Agent hours by subscription type" visual via a dropdown control.
            quicksight.CfnDashboard.FilterGroupProperty(
                filter_group_id=FG_SUB,
                cross_dataset="SINGLE_DATASET",
                scope_configuration=quicksight.CfnDashboard.FilterScopeConfigurationProperty(
                    selected_sheets=quicksight.CfnDashboard.SelectedSheetsFilterScopeConfigurationProperty(
                        sheet_visual_scoping_configurations=[
                            quicksight.CfnDashboard.SheetVisualScopingConfigurationProperty(
                                sheet_id="usage",
                                scope="SELECTED_VISUALS",
                                visual_ids=["hours_by_subscription"],
                            )
                        ]
                    )
                ),
                filters=[
                    quicksight.CfnDashboard.FilterProperty(
                        category_filter=quicksight.CfnDashboard.CategoryFilterProperty(
                            filter_id=FLT_SUB,
                            column=_col(DS_HOURS, "subscription_type"),
                            configuration=quicksight.CfnDashboard.CategoryFilterConfigurationProperty(
                                filter_list_configuration=quicksight.CfnDashboard.FilterListConfigurationProperty(
                                    match_operator="CONTAINS",
                                    select_all_options="FILTER_ALL_VALUES",
                                )
                            ),
                        )
                    )
                ],
            ),
            # Per-user filter on the user_summary dataset, driving the per-user
            # sheet (table + hours-by-user bar) via a user dropdown control.
            quicksight.CfnDashboard.FilterGroupProperty(
                filter_group_id=FG_PUSER,
                cross_dataset="SINGLE_DATASET",
                scope_configuration=quicksight.CfnDashboard.FilterScopeConfigurationProperty(
                    selected_sheets=quicksight.CfnDashboard.SelectedSheetsFilterScopeConfigurationProperty(
                        sheet_visual_scoping_configurations=[
                            quicksight.CfnDashboard.SheetVisualScopingConfigurationProperty(
                                sheet_id="per_user", scope="ALL_VISUALS"
                            )
                        ]
                    )
                ),
                filters=[
                    quicksight.CfnDashboard.FilterProperty(
                        category_filter=quicksight.CfnDashboard.CategoryFilterProperty(
                            filter_id=FLT_PUSER,
                            column=_col(DS_USER, "user_arn"),
                            configuration=quicksight.CfnDashboard.CategoryFilterConfigurationProperty(
                                filter_list_configuration=quicksight.CfnDashboard.FilterListConfigurationProperty(
                                    match_operator="CONTAINS",
                                    select_all_options="FILTER_ALL_VALUES",
                                )
                            ),
                        )
                    )
                ],
            ),
            # CloudTrail audit filters: event name + actor, scoped to the
            # CloudTrail sheet, driven by two dropdown controls.
            quicksight.CfnDashboard.FilterGroupProperty(
                filter_group_id=FG_TRAIL,
                cross_dataset="SINGLE_DATASET",
                scope_configuration=quicksight.CfnDashboard.FilterScopeConfigurationProperty(
                    selected_sheets=quicksight.CfnDashboard.SelectedSheetsFilterScopeConfigurationProperty(
                        sheet_visual_scoping_configurations=[
                            quicksight.CfnDashboard.SheetVisualScopingConfigurationProperty(
                                sheet_id="cloudtrail", scope="ALL_VISUALS"
                            )
                        ]
                    )
                ),
                filters=[
                    quicksight.CfnDashboard.FilterProperty(
                        category_filter=quicksight.CfnDashboard.CategoryFilterProperty(
                            filter_id=FLT_TRAIL_LIFECYCLE,
                            column=_col(DS_TRAIL, F_TRAIL_ACTION),
                            configuration=quicksight.CfnDashboard.CategoryFilterConfigurationProperty(
                                filter_list_configuration=quicksight.CfnDashboard.FilterListConfigurationProperty(
                                    match_operator="CONTAINS",
                                    category_values=[
                                        "Create",
                                        "Share",
                                        "Update",
                                        "Delete",
                                    ],
                                )
                            ),
                        )
                    ),
                    quicksight.CfnDashboard.FilterProperty(
                        category_filter=quicksight.CfnDashboard.CategoryFilterProperty(
                            filter_id=FLT_TRAIL_EVENT,
                            column=_col(DS_TRAIL, "event_name"),
                            configuration=quicksight.CfnDashboard.CategoryFilterConfigurationProperty(
                                filter_list_configuration=quicksight.CfnDashboard.FilterListConfigurationProperty(
                                    match_operator="CONTAINS",
                                    select_all_options="FILTER_ALL_VALUES",
                                )
                            ),
                        )
                    ),
                    quicksight.CfnDashboard.FilterProperty(
                        category_filter=quicksight.CfnDashboard.CategoryFilterProperty(
                            filter_id=FLT_TRAIL_USER,
                            column=_col(DS_TRAIL, "user_name"),
                            configuration=quicksight.CfnDashboard.CategoryFilterConfigurationProperty(
                                filter_list_configuration=quicksight.CfnDashboard.FilterListConfigurationProperty(
                                    match_operator="CONTAINS",
                                    select_all_options="FILTER_ALL_VALUES",
                                )
                            ),
                        )
                    ),
                ],
            ),
        ]

        usage_controls = [
            quicksight.CfnDashboard.SheetControlLayoutProperty(
                configuration=quicksight.CfnDashboard.SheetControlLayoutConfigurationProperty(
                    grid_layout=quicksight.CfnDashboard.GridLayoutConfigurationProperty(
                        elements=[
                            quicksight.CfnDashboard.GridLayoutElementProperty(
                                element_id=CTRL_DATE,
                                element_type="FILTER_CONTROL",
                                column_span=4,
                                row_span=1,
                            ),
                            quicksight.CfnDashboard.GridLayoutElementProperty(
                                element_id=CTRL_AGENT,
                                element_type="FILTER_CONTROL",
                                column_span=4,
                                row_span=1,
                            ),
                            quicksight.CfnDashboard.GridLayoutElementProperty(
                                element_id=CTRL_SUB,
                                element_type="FILTER_CONTROL",
                                column_span=4,
                                row_span=1,
                            ),
                        ]
                    )
                )
            )
        ]

        usage_filter_controls = [
            quicksight.CfnDashboard.FilterControlProperty(
                relative_date_time=quicksight.CfnDashboard.FilterRelativeDateTimeControlProperty(
                    filter_control_id=CTRL_DATE,
                    title="Date range",
                    source_filter_id=FLT_DATE,
                )
            ),
            quicksight.CfnDashboard.FilterControlProperty(
                dropdown=quicksight.CfnDashboard.FilterDropDownControlProperty(
                    filter_control_id=CTRL_AGENT,
                    title="Agent",
                    source_filter_id=FLT_AGENT,
                    type="MULTI_SELECT",
                )
            ),
            quicksight.CfnDashboard.FilterControlProperty(
                dropdown=quicksight.CfnDashboard.FilterDropDownControlProperty(
                    filter_control_id=CTRL_SUB,
                    title="Subscription type",
                    source_filter_id=FLT_SUB,
                    type="MULTI_SELECT",
                )
            ),
        ]

        usage_sheet = quicksight.CfnDashboard.SheetDefinitionProperty(
            sheet_id="usage",
            name="Usage overview",
            filter_controls=usage_filter_controls,
            sheet_control_layouts=usage_controls,
            layouts=[_usage_layout()],
            visuals=[
                self._kpi(
                    "kpi_active_users",
                    "Active users",
                    DS_CHAT,
                    "user_arn",
                    "DISTINCT_COUNT",
                ),
                self._kpi(
                    "kpi_conversations",
                    "Total conversations",
                    DS_CHAT,
                    "conversation_id",
                    "DISTINCT_COUNT",
                ),
                self._kpi(
                    "kpi_queries", "Total queries", DS_CHAT, "user_message_id", "COUNT"
                ),
                self._kpi(
                    "kpi_agent_hours",
                    "Total agent hours",
                    DS_HOURS,
                    "usage_hours",
                    "SUM",
                    numeric=True,
                ),
                self._kpi(
                    "kpi_index_storage",
                    "Index storage (GB)",
                    DS_INDEX,
                    F_INDEX_GB,
                    "SUM",
                    numeric=True,
                ),
                self._kpi(
                    "kpi_source_storage",
                    "Source storage (GB)",
                    DS_INDEX,
                    F_SOURCE_GB,
                    "SUM",
                    numeric=True,
                ),
                self._conversations_queries_by_month(),
                self._avg_queries_per_conversation(),
                self._query_scope_by_month(),
                self._unanswered_queries(),
                self._assets_used(),
                self._hours_by_subscription(),
                self._storage_by_source(),
            ],
        )

        per_user_sheet = quicksight.CfnDashboard.SheetDefinitionProperty(
            sheet_id="per_user",
            name="Per-user detail",
            filter_controls=[
                quicksight.CfnDashboard.FilterControlProperty(
                    dropdown=quicksight.CfnDashboard.FilterDropDownControlProperty(
                        filter_control_id=CTRL_PUSER,
                        title="User",
                        source_filter_id=FLT_PUSER,
                        type="MULTI_SELECT",
                    )
                )
            ],
            sheet_control_layouts=[
                quicksight.CfnDashboard.SheetControlLayoutProperty(
                    configuration=quicksight.CfnDashboard.SheetControlLayoutConfigurationProperty(
                        grid_layout=quicksight.CfnDashboard.GridLayoutConfigurationProperty(
                            elements=[
                                quicksight.CfnDashboard.GridLayoutElementProperty(
                                    element_id=CTRL_PUSER,
                                    element_type="FILTER_CONTROL",
                                    column_span=6,
                                    row_span=1,
                                )
                            ]
                        )
                    )
                )
            ],
            layouts=[_per_user_layout()],
            visuals=[self._hours_by_user(), self._user_table()],
        )

        feedback_sheet = quicksight.CfnDashboard.SheetDefinitionProperty(
            sheet_id="feedback",
            name="Feedback & governance",
            layouts=[_feedback_layout()],
            visuals=[
                self._kpi(
                    "kpi_feedback_total",
                    "Total feedback",
                    DS_FEEDBACK,
                    "user_message_id",
                    "COUNT",
                ),
                self._feedback_by_type(),
                self._feedback_by_reason(),
                self._feedback_trend(),
                self._kb_sync_result(),
                self._kb_sync_trend(),
                self._dlp_by_action(),
            ],
        )

        cloudtrail_sheet = quicksight.CfnDashboard.SheetDefinitionProperty(
            sheet_id="cloudtrail",
            name="Asset audit (CloudTrail)",
            filter_controls=[
                quicksight.CfnDashboard.FilterControlProperty(
                    dropdown=quicksight.CfnDashboard.FilterDropDownControlProperty(
                        filter_control_id=CTRL_TRAIL_LIFECYCLE,
                        title="Action type",
                        source_filter_id=FLT_TRAIL_LIFECYCLE,
                        type="MULTI_SELECT",
                    )
                ),
                quicksight.CfnDashboard.FilterControlProperty(
                    dropdown=quicksight.CfnDashboard.FilterDropDownControlProperty(
                        filter_control_id=CTRL_TRAIL_EVENT,
                        title="Event name",
                        source_filter_id=FLT_TRAIL_EVENT,
                        type="MULTI_SELECT",
                    )
                ),
                quicksight.CfnDashboard.FilterControlProperty(
                    dropdown=quicksight.CfnDashboard.FilterDropDownControlProperty(
                        filter_control_id=CTRL_TRAIL_USER,
                        title="Actor",
                        source_filter_id=FLT_TRAIL_USER,
                        type="MULTI_SELECT",
                    )
                ),
            ],
            sheet_control_layouts=[
                quicksight.CfnDashboard.SheetControlLayoutProperty(
                    configuration=quicksight.CfnDashboard.SheetControlLayoutConfigurationProperty(
                        grid_layout=quicksight.CfnDashboard.GridLayoutConfigurationProperty(
                            elements=[
                                quicksight.CfnDashboard.GridLayoutElementProperty(
                                    element_id=CTRL_TRAIL_LIFECYCLE,
                                    element_type="FILTER_CONTROL",
                                    column_span=4,
                                    row_span=1,
                                ),
                                quicksight.CfnDashboard.GridLayoutElementProperty(
                                    element_id=CTRL_TRAIL_EVENT,
                                    element_type="FILTER_CONTROL",
                                    column_span=5,
                                    row_span=1,
                                ),
                                quicksight.CfnDashboard.GridLayoutElementProperty(
                                    element_id=CTRL_TRAIL_USER,
                                    element_type="FILTER_CONTROL",
                                    column_span=5,
                                    row_span=1,
                                ),
                            ]
                        )
                    )
                )
            ],
            layouts=[_cloudtrail_layout()],
            visuals=[
                self._kpi(
                    "kpi_write_events",
                    "Asset changes",
                    DS_TRAIL,
                    "event_id",
                    "COUNT",
                ),
                self._kpi(
                    "kpi_distinct_actors",
                    "Distinct actors",
                    DS_TRAIL,
                    "user_identity_arn",
                    "DISTINCT_COUNT",
                ),
                self._trail_by_action(),
                self._trail_by_actor(),
                self._trail_trend(),
                self._trail_table(),
                self._agent_inventory(),
            ],
        )

        service_sheet = quicksight.CfnDashboard.SheetDefinitionProperty(
            sheet_id="service",
            name="Service & operations",
            layouts=[_service_layout()],
            visuals=[
                self._metric_by_name(),
                self._metric_trend(),
                self._metric_table(),
            ],
        )

        definition = quicksight.CfnDashboard.DashboardVersionDefinitionProperty(
            data_set_identifier_declarations=identifiers,
            calculated_fields=calc_fields,
            filter_groups=filter_groups,
            sheets=[
                usage_sheet,
                per_user_sheet,
                feedback_sheet,
                cloudtrail_sheet,
                service_sheet,
            ],
        )

        self.dashboard = quicksight.CfnDashboard(
            self,
            "UsageDashboard",
            aws_account_id=account,
            dashboard_id="quick-observability-usage",
            name="Quick Observability - Usage Metrics",
            definition=definition,
            permissions=permissions,
        )

    # ---- KPI --------------------------------------------------------------
    def _kpi(self, vid, title, ds_id, column, agg, numeric=False):
        field = (
            _num(f"{vid}_m", ds_id, column, agg)
            if numeric
            else _cat_measure(f"{vid}_m", ds_id, column, agg)
        )
        return quicksight.CfnDashboard.VisualProperty(
            kpi_visual=quicksight.CfnDashboard.KPIVisualProperty(
                visual_id=vid,
                title=_title(title),
                chart_configuration=quicksight.CfnDashboard.KPIConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.KPIFieldWellsProperty(
                        values=[field]
                    )
                ),
            )
        )

    # ---- Conversations, queries & active users by month -------------------
    def _conversations_queries_by_month(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="conv_queries_month",
                title=_title("Conversations, queries, and active users"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    bars_arrangement="CLUSTERED",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_date_dim("cqm_month", DS_CHAT, F_MONTH)],
                            values=[
                                _cat_measure(
                                    "cqm_conv",
                                    DS_CHAT,
                                    "conversation_id",
                                    "DISTINCT_COUNT",
                                ),
                                _cat_measure(
                                    "cqm_q", DS_CHAT, "user_message_id", "COUNT"
                                ),
                                _cat_measure(
                                    "cqm_users", DS_CHAT, "user_arn", "DISTINCT_COUNT"
                                ),
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- Average queries per conversation (by month) ----------------------
    def _avg_queries_per_conversation(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="avg_q_per_conv",
                title=_title("Average queries per conversation"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_date_dim("aqpc_month", DS_CHAT, F_MONTH)],
                            values=[
                                _cat_measure(
                                    "aqpc_q", DS_CHAT, "user_message_id", "COUNT"
                                )
                            ],
                        )
                    )
                ),
            )
        )

    # ---- Query scope selection by month (line split by message_scope) -----
    def _query_scope_by_month(self):
        return quicksight.CfnDashboard.VisualProperty(
            line_chart_visual=quicksight.CfnDashboard.LineChartVisualProperty(
                visual_id="query_scope",
                title=_title("Query scope selection"),
                chart_configuration=quicksight.CfnDashboard.LineChartConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.LineChartFieldWellsProperty(
                        line_chart_aggregated_field_wells=quicksight.CfnDashboard.LineChartAggregatedFieldWellsProperty(
                            category=[_date_dim("qs_month", DS_CHAT, F_MONTH)],
                            colors=[_cat_dim("qs_scope", DS_CHAT, "message_scope")],
                            values=[
                                _cat_measure(
                                    "qs_q", DS_CHAT, "user_message_id", "COUNT"
                                )
                            ],
                        )
                    )
                ),
            )
        )

    # ---- Unanswered / error queries by status -----------------------------
    def _unanswered_queries(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="unanswered",
                title=_title("Queries by status (unanswered / errors)"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("un_status", DS_CHAT, "status_code")],
                            values=[
                                _cat_measure(
                                    "un_q", DS_CHAT, "user_message_id", "COUNT"
                                )
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- Assets used (distinct agents / flows / connectors) ---------------
    def _assets_used(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="assets_used",
                title=_title("Assets used — agents"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("au_agent", DS_CHAT, "agent_id")],
                            values=[
                                _cat_measure(
                                    "au_q", DS_CHAT, "user_message_id", "COUNT"
                                )
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- Agent hours by subscription type ---------------------------------
    def _hours_by_subscription(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="hours_by_subscription",
                title=_title("Agent hours by subscription type"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[
                                _cat_dim("hbs_sub", DS_HOURS, "subscription_type")
                            ],
                            values=[_num("hbs_h", DS_HOURS, "usage_hours", "SUM")],
                        )
                    )
                ),
            )
        )

    # ---- Storage consumed by source (usage sheet) -------------------------
    def _storage_by_source(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="storage_by_source",
                title=_title("Index & source storage by source type (GB)"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    bars_arrangement="CLUSTERED",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("sbs_src", DS_INDEX, "source_type")],
                            values=[
                                _num("sbs_idx", DS_INDEX, F_INDEX_GB, "SUM"),
                                _num("sbs_srcb", DS_INDEX, F_SOURCE_GB, "SUM"),
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- Feedback by type (donut) -----------------------------------------
    def _feedback_by_type(self):
        return quicksight.CfnDashboard.VisualProperty(
            pie_chart_visual=quicksight.CfnDashboard.PieChartVisualProperty(
                visual_id="feedback_by_type",
                title=_title("Feedback by type"),
                chart_configuration=quicksight.CfnDashboard.PieChartConfigurationProperty(
                    donut_options=quicksight.CfnDashboard.DonutOptionsProperty(
                        arc_options=quicksight.CfnDashboard.ArcOptionsProperty(
                            arc_thickness="MEDIUM"
                        )
                    ),
                    field_wells=quicksight.CfnDashboard.PieChartFieldWellsProperty(
                        pie_chart_aggregated_field_wells=quicksight.CfnDashboard.PieChartAggregatedFieldWellsProperty(
                            category=[
                                _cat_dim("fbt_type", DS_FEEDBACK, "feedback_type")
                            ],
                            values=[
                                _cat_measure(
                                    "fbt_n", DS_FEEDBACK, "user_message_id", "COUNT"
                                )
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- Feedback by reason (bar) -----------------------------------------
    def _feedback_by_reason(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="feedback_by_reason",
                title=_title("Feedback by reason"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[
                                _cat_dim("fbr_reason", DS_FEEDBACK, "feedback_reason")
                            ],
                            values=[
                                _cat_measure(
                                    "fbr_n", DS_FEEDBACK, "user_message_id", "COUNT"
                                )
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- Feedback trend by month (line split by type) ---------------------
    def _feedback_trend(self):
        return quicksight.CfnDashboard.VisualProperty(
            line_chart_visual=quicksight.CfnDashboard.LineChartVisualProperty(
                visual_id="feedback_trend",
                title=_title("Feedback trend"),
                chart_configuration=quicksight.CfnDashboard.LineChartConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.LineChartFieldWellsProperty(
                        line_chart_aggregated_field_wells=quicksight.CfnDashboard.LineChartAggregatedFieldWellsProperty(
                            category=[_date_dim("fbtr_month", DS_FEEDBACK, F_MONTH)],
                            colors=[
                                _cat_dim("fbtr_type", DS_FEEDBACK, "feedback_type")
                            ],
                            values=[
                                _cat_measure(
                                    "fbtr_n", DS_FEEDBACK, "user_message_id", "COUNT"
                                )
                            ],
                        )
                    )
                ),
            )
        )

    # ---- KB file sync result (donut) --------------------------------------
    def _kb_sync_result(self):
        return quicksight.CfnDashboard.VisualProperty(
            pie_chart_visual=quicksight.CfnDashboard.PieChartVisualProperty(
                visual_id="kb_sync_result",
                title=_title("Knowledge base sync results"),
                chart_configuration=quicksight.CfnDashboard.PieChartConfigurationProperty(
                    donut_options=quicksight.CfnDashboard.DonutOptionsProperty(
                        arc_options=quicksight.CfnDashboard.ArcOptionsProperty(
                            arc_thickness="MEDIUM"
                        )
                    ),
                    field_wells=quicksight.CfnDashboard.PieChartFieldWellsProperty(
                        pie_chart_aggregated_field_wells=quicksight.CfnDashboard.PieChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("kbs_result", DS_KBSYNC, "sync_result")],
                            values=[
                                _cat_measure("kbs_n", DS_KBSYNC, "document_id", "COUNT")
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- KB file sync trend by month --------------------------------------
    def _kb_sync_trend(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="kb_sync_trend",
                title=_title("Documents synced by month"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_date_dim("kbt_month", DS_KBSYNC, F_MONTH)],
                            colors=[_cat_dim("kbt_result", DS_KBSYNC, "sync_result")],
                            values=[
                                _cat_measure("kbt_n", DS_KBSYNC, "document_id", "COUNT")
                            ],
                        )
                    )
                ),
            )
        )

    # ---- DLP events by policy action --------------------------------------
    def _dlp_by_action(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="dlp_by_action",
                title=_title("DLP events by policy action"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("dlp_action", DS_DLP, "policy_action")],
                            values=[
                                _cat_measure("dlp_n", DS_DLP, "request_id", "COUNT")
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- CloudTrail: assets created by action -----------------------------
    def _trail_by_action(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="trail_by_action",
                title=_title(
                    "Asset changes by action (create / share / update / delete)"
                ),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    bars_arrangement="STACKED",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("tba_action", DS_TRAIL, F_TRAIL_ACTION)],
                            colors=[_cat_dim("tba_event", DS_TRAIL, "event_name")],
                            values=[
                                _cat_measure("tba_n", DS_TRAIL, "event_id", "COUNT")
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- CloudTrail: who created assets (by actor) ------------------------
    def _trail_by_actor(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="trail_by_actor",
                title=_title("Asset changes by actor"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    bars_arrangement="STACKED",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("tbc_actor", DS_TRAIL, "user_name")],
                            colors=[_cat_dim("tbc_action", DS_TRAIL, F_TRAIL_ACTION)],
                            values=[
                                _cat_measure("tbc_n", DS_TRAIL, "event_id", "COUNT")
                            ],
                        )
                    ),
                ),
            )
        )

    # ---- CloudTrail: activity trend by month ------------------------------
    def _trail_trend(self):
        return quicksight.CfnDashboard.VisualProperty(
            line_chart_visual=quicksight.CfnDashboard.LineChartVisualProperty(
                visual_id="trail_trend",
                title=_title("Asset changes over time (by action)"),
                chart_configuration=quicksight.CfnDashboard.LineChartConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.LineChartFieldWellsProperty(
                        line_chart_aggregated_field_wells=quicksight.CfnDashboard.LineChartAggregatedFieldWellsProperty(
                            category=[_date_dim("ttr_month", DS_TRAIL, F_MONTH)],
                            colors=[_cat_dim("ttr_action", DS_TRAIL, F_TRAIL_ACTION)],
                            values=[
                                _cat_measure("ttr_n", DS_TRAIL, "event_id", "COUNT")
                            ],
                        )
                    )
                ),
            )
        )

    # ---- CloudTrail: detail table (who created what, when) ----------------
    def _trail_table(self):
        group_by = [
            _date_dim("tt_time", DS_TRAIL, F_TRAIL_DAY, "DAY"),
            _cat_dim("tt_action", DS_TRAIL, F_TRAIL_ACTION),
            _cat_dim("tt_event", DS_TRAIL, "event_name"),
            _cat_dim("tt_actor", DS_TRAIL, "user_name"),
            _cat_dim("tt_restype", DS_TRAIL, "resource_type"),
            _cat_dim("tt_resname", DS_TRAIL, "resource_name"),
        ]
        values = [_cat_measure("tt_n", DS_TRAIL, "event_id", "COUNT")]
        return quicksight.CfnDashboard.VisualProperty(
            table_visual=quicksight.CfnDashboard.TableVisualProperty(
                visual_id="trail_table",
                title=_title("Asset activity detail — who did what, when"),
                chart_configuration=quicksight.CfnDashboard.TableConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.TableFieldWellsProperty(
                        table_aggregated_field_wells=quicksight.CfnDashboard.TableAggregatedFieldWellsProperty(
                            group_by=group_by, values=values
                        )
                    )
                ),
            )
        )

    # ---- Agent inventory (agent_metadata) ---------------------------------
    def _agent_inventory(self):
        group_by = [
            _cat_dim("ai_name", DS_AGENTMETA, "agent_name"),
            _cat_dim("ai_status", DS_AGENTMETA, "agent_status"),
            _cat_dim("ai_action", DS_AGENTMETA, "update_action"),
        ]
        values = [_cat_measure("ai_n", DS_AGENTMETA, "agent_id", "DISTINCT_COUNT")]
        return quicksight.CfnDashboard.VisualProperty(
            table_visual=quicksight.CfnDashboard.TableVisualProperty(
                visual_id="agent_inventory",
                title=_title("Agent inventory (name, status, last action)"),
                chart_configuration=quicksight.CfnDashboard.TableConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.TableFieldWellsProperty(
                        table_aggregated_field_wells=quicksight.CfnDashboard.TableAggregatedFieldWellsProperty(
                            group_by=group_by, values=values
                        )
                    )
                ),
            )
        )

    # ---- Service metrics by name (metric_snapshots) -----------------------
    def _metric_by_name(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="metric_by_name",
                title=_title("Service metrics — average by metric"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("mbn_name", DS_METRIC, "metric_name")],
                            colors=[_cat_dim("mbn_group", DS_METRIC, "metric_group")],
                            values=[_num("mbn_v", DS_METRIC, "value", "AVERAGE")],
                        )
                    ),
                ),
            )
        )

    # ---- Service metrics trend (metric_snapshots) -------------------------
    def _metric_trend(self):
        return quicksight.CfnDashboard.VisualProperty(
            line_chart_visual=quicksight.CfnDashboard.LineChartVisualProperty(
                visual_id="metric_trend",
                title=_title("Service metrics over time (by group)"),
                chart_configuration=quicksight.CfnDashboard.LineChartConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.LineChartFieldWellsProperty(
                        line_chart_aggregated_field_wells=quicksight.CfnDashboard.LineChartAggregatedFieldWellsProperty(
                            category=[_date_dim("mt_month", DS_METRIC, F_MONTH)],
                            colors=[_cat_dim("mt_group", DS_METRIC, "metric_group")],
                            values=[_num("mt_v", DS_METRIC, "value", "SUM")],
                        )
                    )
                ),
            )
        )

    # ---- Service metrics table (metric_snapshots) -------------------------
    def _metric_table(self):
        group_by = [
            _cat_dim("mtbl_group", DS_METRIC, "metric_group"),
            _cat_dim("mtbl_name", DS_METRIC, "metric_name"),
            _cat_dim("mtbl_stat", DS_METRIC, "statistic"),
            _cat_dim("mtbl_unit", DS_METRIC, "unit"),
        ]
        values = [
            _num("mtbl_sum", DS_METRIC, "value", "SUM"),
            _num("mtbl_avg", DS_METRIC, "value", "AVERAGE"),
            _num("mtbl_max", DS_METRIC, "value", "MAX"),
        ]
        return quicksight.CfnDashboard.VisualProperty(
            table_visual=quicksight.CfnDashboard.TableVisualProperty(
                visual_id="metric_table",
                title=_title("Service metrics detail"),
                chart_configuration=quicksight.CfnDashboard.TableConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.TableFieldWellsProperty(
                        table_aggregated_field_wells=quicksight.CfnDashboard.TableAggregatedFieldWellsProperty(
                            group_by=group_by, values=values
                        )
                    )
                ),
            )
        )

    # ---- Per-user: agent hours by user (companion bar) --------------------
    def _hours_by_user(self):
        return quicksight.CfnDashboard.VisualProperty(
            bar_chart_visual=quicksight.CfnDashboard.BarChartVisualProperty(
                visual_id="pu_hours_by_user",
                title=_title("Agent hours by user"),
                chart_configuration=quicksight.CfnDashboard.BarChartConfigurationProperty(
                    orientation="HORIZONTAL",
                    field_wells=quicksight.CfnDashboard.BarChartFieldWellsProperty(
                        bar_chart_aggregated_field_wells=quicksight.CfnDashboard.BarChartAggregatedFieldWellsProperty(
                            category=[_cat_dim("puh_user", DS_USER, "user_arn")],
                            values=[_num("puh_h", DS_USER, "agent_hours", "SUM")],
                        )
                    ),
                ),
            )
        )

    # ---- Per-user table ---------------------------------------------------
    def _user_table(self):
        group_by = [
            _cat_dim("tbl_user", DS_USER, "user_arn"),
            _cat_dim("tbl_last", DS_USER, "last_active"),
        ]
        values = [
            _num("tbl_queries", DS_USER, "total_queries", "SUM"),
            _num("tbl_convs", DS_USER, "total_conversations", "SUM"),
            _num("tbl_hours", DS_USER, "agent_hours", "SUM"),
            _num("tbl_src", DS_USER, "consumed_source_gb", "SUM"),
            _num("tbl_idx", DS_USER, "consumed_index_gb", "SUM"),
            _num("tbl_docs", DS_USER, "documents", "SUM"),
        ]
        return quicksight.CfnDashboard.VisualProperty(
            table_visual=quicksight.CfnDashboard.TableVisualProperty(
                visual_id="user_table",
                title=_title("Per-user: queries, agent hours, and storage"),
                chart_configuration=quicksight.CfnDashboard.TableConfigurationProperty(
                    field_wells=quicksight.CfnDashboard.TableFieldWellsProperty(
                        table_aggregated_field_wells=quicksight.CfnDashboard.TableAggregatedFieldWellsProperty(
                            group_by=group_by, values=values
                        )
                    )
                ),
            )
        )


def _grid_el(element_id, column_index, row_index, column_span, row_span):
    return quicksight.CfnDashboard.GridLayoutElementProperty(
        element_id=element_id,
        element_type="VISUAL",
        column_index=column_index,
        row_index=row_index,
        column_span=column_span,
        row_span=row_span,
    )


def _grid_layout(elements):
    return quicksight.CfnDashboard.LayoutProperty(
        configuration=quicksight.CfnDashboard.LayoutConfigurationProperty(
            grid_layout=quicksight.CfnDashboard.GridLayoutConfigurationProperty(
                canvas_size_options=quicksight.CfnDashboard.GridLayoutCanvasSizeOptionsProperty(
                    screen_canvas_size_options=quicksight.CfnDashboard.GridLayoutScreenCanvasSizeOptionsProperty(
                        resize_option="FIXED", optimized_view_port_width="1600px"
                    )
                ),
                elements=elements,
            )
        )
    )


def _usage_layout():
    # 36-col grid. Six compact KPI tiles across the top (two rows of KPIs),
    # then charts two-up (18 cols x 8 rows) below.
    return _grid_layout(
        [
            _grid_el("kpi_active_users", 0, 0, 12, 3),
            _grid_el("kpi_conversations", 12, 0, 12, 3),
            _grid_el("kpi_queries", 24, 0, 12, 3),
            _grid_el("kpi_agent_hours", 0, 3, 12, 3),
            _grid_el("kpi_index_storage", 12, 3, 12, 3),
            _grid_el("kpi_source_storage", 24, 3, 12, 3),
            _grid_el("conv_queries_month", 0, 6, 18, 8),
            _grid_el("avg_q_per_conv", 18, 6, 18, 8),
            _grid_el("query_scope", 0, 14, 18, 8),
            _grid_el("unanswered", 18, 14, 18, 8),
            _grid_el("assets_used", 0, 22, 18, 8),
            _grid_el("hours_by_subscription", 18, 22, 18, 8),
            _grid_el("storage_by_source", 0, 30, 36, 8),
        ]
    )


def _per_user_layout():
    # Full-width table filling the sheet, with a compact companion chart on top.
    return _grid_layout(
        [
            _grid_el("pu_hours_by_user", 0, 0, 36, 8),
            _grid_el("user_table", 0, 8, 36, 16),
        ]
    )


def _feedback_layout():
    # KPI + feedback donut/bar/trend row, then KB-sync and DLP governance row.
    return _grid_layout(
        [
            _grid_el("kpi_feedback_total", 0, 0, 12, 3),
            _grid_el("feedback_by_type", 0, 3, 12, 8),
            _grid_el("feedback_by_reason", 12, 3, 12, 8),
            _grid_el("feedback_trend", 24, 3, 12, 8),
            _grid_el("kb_sync_result", 0, 11, 12, 8),
            _grid_el("kb_sync_trend", 12, 11, 24, 8),
            _grid_el("dlp_by_action", 0, 19, 36, 7),
        ]
    )


def _cloudtrail_layout():
    # Two KPIs, action/actor bars, trend line, detail audit table, inventory.
    return _grid_layout(
        [
            _grid_el("kpi_write_events", 0, 0, 12, 3),
            _grid_el("kpi_distinct_actors", 12, 0, 12, 3),
            _grid_el("trail_by_action", 0, 3, 18, 9),
            _grid_el("trail_by_actor", 18, 3, 18, 9),
            _grid_el("trail_trend", 0, 12, 36, 7),
            _grid_el("trail_table", 0, 19, 36, 12),
            _grid_el("agent_inventory", 0, 31, 36, 10),
        ]
    )


def _service_layout():
    return _grid_layout(
        [
            _grid_el("metric_by_name", 0, 0, 18, 10),
            _grid_el("metric_trend", 18, 0, 18, 10),
            _grid_el("metric_table", 0, 10, 36, 14),
        ]
    )


def _dash_condition(scope, logical_id, first_principal):
    from aws_cdk import CfnCondition

    return CfnCondition(
        scope,
        logical_id,
        expression=Fn.condition_not(Fn.condition_equals(first_principal, "")),
    )
