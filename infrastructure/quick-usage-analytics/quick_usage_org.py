"""Add an org view to Amazon Quick usage analytics: an org dataset of IAM Identity Center users,
each usage dataset LEFT-joined to it, and an org dashboard over the joined datasets.

Run it after quick_usage_datasets_setup.py has set up the usage datasets. It runs on its own: it
needs no other file.
  python3 quick_usage_org.py
It works out the region, bucket and dataset owner the same way the setup script does, shows them,
and asks you to confirm. It checks (read-only) that Quick and the usage datasets are set up, then
creates the org part. Re-run it to refresh the org data.

Quick must sign in through IAM Identity Center: the joined datasets match usage records to users
on the Quick user name, which equals the Identity Center user name.

Optional overrides:
  --region us-east-1      default: your session's region
  --bucket NAME           default: quick-usage-<account>-<region code>
  --quick-admin-arn ARN   default: you, if you are a Quick admin
  --profile NAME          AWS CLI profile, if you run outside CloudShell
  --no-dashboard          skip the org dashboard
  --dry-run               print the plan, change nothing
  --destroy               remove what this script created (asks before each removal)
"""

import argparse
import csv
import datetime
import hashlib
import json
import shlex
import sys
import tempfile
import time
import traceback
import urllib.parse
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ParamValidationError

# Standard retry mode backs off on throttling and transient errors. 10 attempts absorb
# throttling on large accounts; explicit timeouts (botocore's 60s read default, 10s connect)
# keep a dead connection from hanging forever. Progress lines show which call is running.
BOTO_CONFIG = Config(
    retries={"mode": "standard", "max_attempts": 10},
    connect_timeout=10,
    read_timeout=60,
)


DEFAULT_TIMEZONE = "UTC"


PREFIX_ROOT = "quick-usage-analytics"


LOG_PREFIX = f"{PREFIX_ROOT}/_setup-confirmation-logs"


# One dataset per Quick usage log type. These are the usage analytics log types, which leave
# out content: no chat or answer text, no file attachments, no feedback comments, no document
# or knowledge-base names (cited resources keep only type and id). Fields follow the Quick
# usage log schema.
# Chat, feedback and agent-hours logs use camelCase logType/accountId; index usage uses
# snake_case. user_id (Quick identity-store id) can be empty on some records.
COMMON_CAMEL = ["resource_arn", ("event_timestamp", "INTEGER"), "logType", "accountId"]


COMMON_SNAKE = [
    "resource_arn",
    ("event_timestamp", "INTEGER"),
    "log_type",
    "account_id",
]


DATASETS = {
    "chat": (
        "USAGE_CHAT_LOGS",
        COMMON_CAMEL
        + [
            "user_arn",
            "user_id",
            "user_type",
            "namespace",
            "status_code",
            "conversation_id",
            "system_message_id",
            ("latency", "INTEGER"),
            ("time_to_first_token", "INTEGER"),
            "message_scope",
            "user_message_id",
            "agent_id",
            "flow_id",
            "surface_type",
            "web_search",
            "user_selected_resources",
            "action_connectors",
            "cited_resource",
        ],
    ),
    "chat_feedback": (
        "USAGE_FEEDBACK_LOGS",
        COMMON_CAMEL
        + [
            "user_arn",
            "user_id",
            "user_type",
            "namespace",
            "status_code",
            "conversation_id",
            "research_id",
            "system_message_id",
            "user_message_id",
            "feedback_type",
            "rating",
        ],
    ),
    "agent_hours": (
        "USAGE_AGENT_HOURS_LOGS",
        COMMON_CAMEL
        + [
            "user_arn",
            "user_id",
            "subscription_type",
            "reporting_service",
            "usage_group",
            ("usage_hours", "DECIMAL"),
            "service_resource_arn",
        ],
    ),
    "index_usage": (
        "USAGE_INDEX_USAGE_LOGS",
        COMMON_SNAKE
        + [
            "user_arn",
            "user_id",
            ("consumed_index_size", "INTEGER"),
            "source_type",
            "source_arn",
            ("consumed_source_size", "INTEGER"),
            ("consumed_source_doc_count", "INTEGER"),
        ],
    ),
}


ATHENA_WORKGROUP = "quick-usage-analytics"


DATASET_ACTIONS = [
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


DATA_SOURCE_ACTIONS = [
    "quicksight:DescribeDataSource",
    "quicksight:DescribeDataSourcePermissions",
    "quicksight:PassDataSource",
    "quicksight:UpdateDataSource",
    "quicksight:DeleteDataSource",
    "quicksight:UpdateDataSourcePermissions",
]


# Step 8: creates an analysis and a dashboard.
ANALYSIS_ID = "quick-usage-analysis"


DASHBOARD_ID = "quick-usage-dashboard"


DASHBOARD_NAME = "Amazon Quick usage"


# Version of the starter dashboard definition. Bump it whenever dashboard_definition() changes,
# so a re-run offers to update analyses and dashboards built from a previous definition.
# The script tags both with it; assets from before this tag existed count as version 1.
DASHBOARD_VERSION = 5


DASHBOARD_VERSION_TAG = "quick-usage-dashboard-version"


# The starter dashboard reads these four datasets.
DASHBOARD_DATASETS = ["chat", "chat_feedback", "agent_hours", "index_usage"]


ANALYSIS_ACTIONS = [
    "quicksight:RestoreAnalysis",
    "quicksight:UpdateAnalysisPermissions",
    "quicksight:DeleteAnalysis",
    "quicksight:DescribeAnalysisPermissions",
    "quicksight:QueryAnalysis",
    "quicksight:DescribeAnalysis",
    "quicksight:UpdateAnalysis",
]


DASHBOARD_ACTIONS = [
    "quicksight:DescribeDashboard",
    "quicksight:ListDashboardVersions",
    "quicksight:UpdateDashboardPermissions",
    "quicksight:QueryDashboard",
    "quicksight:UpdateDashboard",
    "quicksight:DeleteDashboard",
    "quicksight:DescribeDashboardPermissions",
    "quicksight:UpdateDashboardPublishedVersion",
]


class DashboardSpec:
    """One analysis + dashboard pair the script creates, and the version of its definition."""

    def __init__(self, analysis_id, dashboard_id, name, version, version_tag):
        self.analysis_id, self.dashboard_id, self.name = analysis_id, dashboard_id, name
        self.version, self.version_tag = version, version_tag
        self.assets = (("analysis", analysis_id), ("dashboard", dashboard_id))


STARTER_DASHBOARD = DashboardSpec(
    ANALYSIS_ID, DASHBOARD_ID, DASHBOARD_NAME, DASHBOARD_VERSION, DASHBOARD_VERSION_TAG
)


CREATE_TIMEOUT_SECONDS = 300


REFRESH_SCHEDULE_ID = "quick-usage-daily"


DESTROY_WORD = "DESTROY"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()  # noqa: UP017 - CloudShell runs Python 3.9


# Short region codes for the default bucket name, e.g. us-east-1 -> use1, ap-southeast-2 -> apse2.
DIRECTION_CODES = {
    "east": "e",
    "west": "w",
    "north": "n",
    "south": "s",
    "central": "c",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}


def link(url):
    """A clickable hyperlink (OSC 8) in terminals that support it; plain text otherwise."""
    if sys.stdout.isatty():
        return f"\033]8;;{url}\033\\{url}\033]8;;\033\\"
    return url


def progress(text, done=False):
    """A status line that updates in place in a terminal, or a plain line otherwise (logs, pipes)."""
    if sys.stdout.isatty():
        print(f"\r\033[K{text}", end="\n" if done else "", flush=True)
    else:
        print(text, flush=True)


def region_code(region):
    parts = region.split("-")
    return parts[0] + "".join(DIRECTION_CODES.get(p, p) for p in parts[1:])


def retry_on_client_error(fn, retryable, attempts, delay, on_retry=None, give_up=None):
    """Call fn(), retrying while it raises a ClientError that retryable(error) accepts.

    error is the botocore Error dict (Code, Message). A ClientError retryable rejects is raised
    at once. Between attempts this waits delay seconds and calls on_retry(attempt, error) if
    given. After the final failed attempt it calls give_up(exc) if given (to raise a clearer
    message); if give_up doesn't raise, the original ClientError propagates.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ClientError as exc:
            error = exc.response["Error"]
            if not retryable(error):
                raise
            if attempt == attempts:
                if give_up:
                    give_up(exc)
                raise
            if on_retry:
                on_retry(attempt, error)
            time.sleep(delay)


class Abort(Exception):
    """Stop the run with a message. resume_step overrides the step the resume command uses."""

    def __init__(self, message, resume_step=None):
        super().__init__(message)
        self.resume_step = resume_step


class StepState:
    """Result of a read-only completion check for one step.

    done: nothing left to do. unknown: the check itself couldn't run (e.g. missing permission).
    todo: what's left, per dataset (step-specific shape).
    """

    def __init__(self, done, lines, todo=None, unknown=False):
        self.done = done
        self.lines = lines
        self.todo = todo if todo is not None else {}
        self.unknown = unknown


class ConfirmationLog:
    """Append-only JSON Lines record of every prompt, answer and step outcome."""

    def __init__(self, path, base):
        self.path = path
        self.base = base
        self.file = open(path, "a", encoding="utf-8")

    def write(self, **fields):
        self.file.write(json.dumps({**self.base, "timestamp": now(), **fields}) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


def dashboard_definition(dataset_arns):
    """Definition of the starter analysis and dashboard (sheets: Overview, Chat adoption &
    performance, Agent hours, Feedback, Agents & resources, Monthly report).

    It reads the chat, chat_feedback, agent_hours and index_usage datasets. Every column it reads is a
    dataset column from DATASETS or a calculated field in the definition. The definition is
    kept as JSON (DASHBOARD_DEFINITION_JSON below) so it can be edited in Quick, exported with
    describe-analysis-definition and pasted back. dataset_arns maps dataset name to ARN.
    """
    definition = json.loads(DASHBOARD_DEFINITION_JSON)
    for declaration in definition["DataSetIdentifierDeclarations"]:
        declaration["DataSetArn"] = dataset_arns[declaration["Identifier"]]
    return definition


DASHBOARD_DEFINITION_JSON = r"""
{
 "DataSetIdentifierDeclarations": [
  {
   "Identifier": "chat",
   "DataSetArn": "${DATASET_ARN:chat}"
  },
  {
   "Identifier": "agent_hours",
   "DataSetArn": "${DATASET_ARN:agent_hours}"
  },
  {
   "Identifier": "chat_feedback",
   "DataSetArn": "${DATASET_ARN:chat_feedback}"
  },
  {
   "Identifier": "index_usage",
   "DataSetArn": "${DATASET_ARN:index_usage}"
  }
 ],
 "Sheets": [
  {
   "SheetId": "s1",
   "Name": "Overview",
   "ParameterControls": [
    {
     "DateTimePicker": {
      "ParameterControlId": "s1-c-pStart",
      "Title": "From",
      "SourceParameterName": "pStart",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "DateTimePicker": {
      "ParameterControlId": "s1-c-pEnd",
      "Title": "To",
      "SourceParameterName": "pEnd",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "Dropdown": {
      "ParameterControlId": "s1-c-gran",
      "Title": "Time scale",
      "SourceParameterName": "pGranularity",
      "DisplayOptions": {},
      "Type": "SINGLE_SELECT",
      "SelectableValues": {
       "Values": [
        "Day",
        "Week",
        "Month"
       ]
      }
     }
    }
   ],
   "FilterControls": [],
   "Visuals": [
    {
     "KPIVisual": {
      "VisualId": "s1-k1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active Quick users</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Latest period vs the one before (Time scale)"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m1",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Active users"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": [
         {
          "CategoricalDimensionField": {
           "FieldId": "s1-k1-period",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Period"
           }
          }
         }
        ]
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        },
        "TrendArrows": {
         "Visibility": "VISIBLE"
        },
        "SecondaryValue": {
         "Visibility": "VISIBLE"
        },
        "Comparison": {
         "ComparisonMethod": "PERCENT_DIFFERENCE"
        },
        "Sparkline": {
         "Visibility": "VISIBLE",
         "Type": "AREA",
         "Color": "#2E6DB4",
         "TooltipVisibility": "VISIBLE"
        }
       },
       "SortConfiguration": {
        "TrendGroupSort": [
         {
          "FieldSort": {
           "FieldId": "s1-k1-period",
           "Direction": "ASC"
          }
         }
        ]
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s1-k3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Conversations</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m5",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Conversations"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s1-k4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Agent hours</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Latest period vs the one before (Time scale)"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m6",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Agent hours"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": [
         {
          "CategoricalDimensionField": {
           "FieldId": "s1-k4-period",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Period"
           }
          }
         }
        ]
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        },
        "TrendArrows": {
         "Visibility": "VISIBLE"
        },
        "SecondaryValue": {
         "Visibility": "VISIBLE"
        },
        "Comparison": {
         "ComparisonMethod": "PERCENT_DIFFERENCE"
        },
        "Sparkline": {
         "Visibility": "VISIBLE",
         "Type": "AREA",
         "Color": "#2E6DB4",
         "TooltipVisibility": "VISIBLE"
        }
       },
       "SortConfiguration": {
        "TrendGroupSort": [
         {
          "FieldSort": {
           "FieldId": "s1-k4-period",
           "Direction": "ASC"
          }
         }
        ]
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s1-k5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Extra hours %</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m8",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Extra %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s1-k6",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Useful feedback %</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m9",
           "Column": {
            "DataSetIdentifier": "chat_feedback",
            "ColumnName": "Useful %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "LineChartVisual": {
      "VisualId": "s1-v1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active Quick users</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "LineChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "t10",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t10"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m11",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Active users"
            }
           }
          }
         ],
         "Colors": []
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "t10",
           "Direction": "ASC"
          }
         }
        ]
       },
       "Type": "LINE",
       "XAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "PrimaryYAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "HIDDEN"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       },
       "VisualPalette": {
        "ChartColor": "#2E6DB4"
       }
      },
      "Actions": [],
      "ColumnHierarchies": [
       {
        "DateTimeHierarchy": {
         "HierarchyId": "t10"
        }
       }
      ]
     }
    },
    {
     "BarChartVisual": {
      "VisualId": "s1-v3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Agent hours: included vs extra</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "BarChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "t14",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t14"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m15",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          }
         ],
         "Colors": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d16",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Usage group"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "t14",
           "Direction": "ASC"
          }
         }
        ]
       },
       "Orientation": "VERTICAL",
       "BarsArrangement": "STACKED",
       "CategoryLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "ValueLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "VISIBLE",
        "Title": {
         "Visibility": "HIDDEN"
        },
        "Position": "BOTTOM"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       }
      },
      "Actions": [],
      "ColumnHierarchies": [
       {
        "DateTimeHierarchy": {
         "HierarchyId": "t14"
        }
       }
      ]
     }
    },
    {
     "InsightVisual": {
      "VisualId": "s1-v4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Biggest movers: agent hours by service</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "InsightConfiguration": {
       "Computations": [
        {
         "TopBottomMovers": {
          "ComputationId": "s1-mov",
          "Name": "Movers",
          "Time": {
           "DateDimensionField": {
            "FieldId": "t17",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Month"
            },
            "DateGranularity": "MONTH"
           }
          },
          "Category": {
           "CategoricalDimensionField": {
            "FieldId": "d18",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Service"
            }
           }
          },
          "Value": {
           "NumericalMeasureField": {
            "FieldId": "m19",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          },
          "MoverSize": 3,
          "SortOrder": "PERCENT_DIFFERENCE",
          "Type": "TOP"
         }
        }
       ]
      },
      "Actions": [],
      "DataSetIdentifier": "agent_hours"
     }
    },
    {
     "LineChartVisual": {
      "VisualId": "s1-v6",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Index size (MB)</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Total size of the index used, per period (Time scale)"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "LineChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "s1-v6-period",
            "Column": {
             "DataSetIdentifier": "index_usage",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "s1-v6-period"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "s1-v6-size",
            "Column": {
             "DataSetIdentifier": "index_usage",
             "ColumnName": "Index size (MB)"
            }
           }
          }
         ],
         "Colors": []
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "s1-v6-period",
           "Direction": "ASC"
          }
         }
        ]
       },
       "Type": "LINE",
       "XAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "PrimaryYAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "HIDDEN"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       },
       "VisualPalette": {
        "ChartColor": "#2E6DB4"
       }
      },
      "Actions": [],
      "ColumnHierarchies": [
       {
        "DateTimeHierarchy": {
         "HierarchyId": "s1-v6-period"
        }
       }
      ]
     }
    }
   ],
   "TextBoxes": [
    {
     "SheetTextBoxId": "s1-title",
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\">\u00a0| Overview</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">Who uses Quick, how much, and how usage is trending.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s1-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"26px\"><b>Quick</b></inline>\n  </block>\n</text-box>"
    }
   ],
   "Layouts": [
    {
     "Configuration": {
      "GridLayout": {
       "Elements": [
        {
         "ElementId": "s1-title",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 4,
         "ColumnSpan": 32,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-c-pStart",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 0,
         "ColumnSpan": 12,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-c-pEnd",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 12,
         "ColumnSpan": 12,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-c-gran",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 24,
         "ColumnSpan": 12,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-k1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-k3",
         "ElementType": "VISUAL",
         "ColumnIndex": 7,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-k4",
         "ElementType": "VISUAL",
         "ColumnIndex": 14,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-k5",
         "ElementType": "VISUAL",
         "ColumnIndex": 21,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-k6",
         "ElementType": "VISUAL",
         "ColumnIndex": 28,
         "ColumnSpan": 8,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-v1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 13,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-v3",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 24,
         "RowIndex": 25,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-v4",
         "ElementType": "VISUAL",
         "ColumnIndex": 24,
         "ColumnSpan": 12,
         "RowIndex": 25,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-logo",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "Padding": "4px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-v6",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 37,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        }
       ],
       "CanvasSizeOptions": {
        "ScreenCanvasSizeOptions": {
         "ResizeOption": "FIXED",
         "OptimizedViewPortWidth": "1600px"
        }
       }
      }
     }
    }
   ],
   "ContentType": "INTERACTIVE"
  },
  {
   "SheetId": "s2",
   "Name": "Chat adoption & performance",
   "ParameterControls": [
    {
     "DateTimePicker": {
      "ParameterControlId": "s2-c-pStart",
      "Title": "From",
      "SourceParameterName": "pStart",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "DateTimePicker": {
      "ParameterControlId": "s2-c-pEnd",
      "Title": "To",
      "SourceParameterName": "pEnd",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "Dropdown": {
      "ParameterControlId": "s2-c-gran",
      "Title": "Time scale",
      "SourceParameterName": "pGranularity",
      "DisplayOptions": {},
      "Type": "SINGLE_SELECT",
      "SelectableValues": {
       "Values": [
        "Day",
        "Week",
        "Month"
       ]
      }
     }
    }
   ],
   "FilterControls": [
    {
     "Dropdown": {
      "FilterControlId": "s2-c-surface",
      "Title": "Surface group",
      "SourceFilterId": "f-s2-surface",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    },
    {
     "Dropdown": {
      "FilterControlId": "s2-c-agenttype",
      "Title": "Agent type",
      "SourceFilterId": "f-s2-agenttype",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    },
    {
     "Dropdown": {
      "FilterControlId": "s2-c-outcome",
      "Title": "Outcome",
      "SourceFilterId": "f-s2-outcome",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    },
    {
     "Dropdown": {
      "FilterControlId": "s2-c-usertype",
      "Title": "User type",
      "SourceFilterId": "f-s2-usertype",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    }
   ],
   "Visuals": [
    {
     "KPIVisual": {
      "VisualId": "s2-k1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Messages</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m29",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Messages"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s2-k2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active users</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Latest period vs the one before (Time scale)"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m30",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Active users"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": [
         {
          "CategoricalDimensionField": {
           "FieldId": "s2-k2-period",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Period"
           }
          }
         }
        ]
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        },
        "TrendArrows": {
         "Visibility": "VISIBLE"
        },
        "SecondaryValue": {
         "Visibility": "VISIBLE"
        },
        "Comparison": {
         "ComparisonMethod": "PERCENT_DIFFERENCE"
        },
        "Sparkline": {
         "Visibility": "VISIBLE",
         "Type": "AREA",
         "Color": "#2E6DB4",
         "TooltipVisibility": "VISIBLE"
        }
       },
       "SortConfiguration": {
        "TrendGroupSort": [
         {
          "FieldSort": {
           "FieldId": "s2-k2-period",
           "Direction": "ASC"
          }
         }
        ]
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s2-k3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Messages per user</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m32",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Messages per user"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s2-k4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Desktop reach</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Users on the desktop app / all chat users"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m33",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Desktop reach %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s2-k5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Success rate</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m34",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Success %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s2-k6",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>95th percentile response time (s)</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m35",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "P95 response time (s)"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "LineChartVisual": {
      "VisualId": "s2-v1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active users by surface</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "People use several surfaces, so lines are not added up"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "LineChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "t36",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t36"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m37",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Active users"
            }
           }
          }
         ],
         "Colors": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d38",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Surface group"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "t36",
           "Direction": "ASC"
          }
         }
        ]
       },
       "Type": "LINE",
       "XAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "PrimaryYAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "VISIBLE",
        "Title": {
         "Visibility": "HIDDEN"
        },
        "Position": "BOTTOM"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       }
      },
      "Actions": [],
      "ColumnHierarchies": [
       {
        "DateTimeHierarchy": {
         "HierarchyId": "t36"
        }
       }
      ]
     }
    },
    {
     "BarChartVisual": {
      "VisualId": "s2-v2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Outcome share</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "BarChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "t39",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t39"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m40",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Messages"
            }
           }
          }
         ],
         "Colors": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d41",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Outcome"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "t39",
           "Direction": "ASC"
          }
         }
        ]
       },
       "Orientation": "VERTICAL",
       "BarsArrangement": "STACKED_PERCENT",
       "CategoryLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "ValueLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "VISIBLE",
        "Title": {
         "Visibility": "HIDDEN"
        },
        "Position": "BOTTOM"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       }
      },
      "Actions": [],
      "ColumnHierarchies": [
       {
        "DateTimeHierarchy": {
         "HierarchyId": "t39"
        }
       }
      ]
     }
    },
    {
     "HeatMapVisual": {
      "VisualId": "s2-v3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Busiest hours (UTC)</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "HeatMapAggregatedFieldWells": {
         "Rows": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d42",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Weekday"
            }
           }
          }
         ],
         "Columns": [
          {
           "NumericalDimensionField": {
            "FieldId": "s2-hour",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Hour (UTC)"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m43",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Messages"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "HeatMapColumnSort": [
         {
          "FieldSort": {
           "FieldId": "s2-hour",
           "Direction": "ASC"
          }
         }
        ]
       },
       "ColorScale": {
        "Colors": [
         {
          "Color": "#EAF2FB"
         },
         {
          "Color": "#2E6DB4"
         }
        ],
        "ColorFillType": "GRADIENT",
        "NullValueColor": {
         "Color": "#F2F2F2"
        }
       },
       "Legend": {
        "Visibility": "HIDDEN"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       }
      },
      "ColumnHierarchies": [],
      "Actions": []
     }
    },
    {
     "LineChartVisual": {
      "VisualId": "s2-v4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Response time (seconds)</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "LineChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "t44",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t44"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m45",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Median response time (s)"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m46",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "P95 response time (s)"
            }
           }
          }
         ],
         "Colors": []
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "t44",
           "Direction": "ASC"
          }
         }
        ]
       },
       "Type": "LINE",
       "XAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "PrimaryYAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "VISIBLE",
        "Title": {
         "Visibility": "HIDDEN"
        },
        "Position": "BOTTOM"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       },
       "VisualPalette": {
        "ColorMap": [
         {
          "Element": {
           "FieldId": "m46",
           "FieldValue": "P95 response time (s)"
          },
          "Color": "#2E6DB4"
         },
         {
          "Element": {
           "FieldId": "m45",
           "FieldValue": "Median response time (s)"
          },
          "Color": "#4F9DA6"
         }
        ]
       }
      },
      "Actions": [],
      "ColumnHierarchies": [
       {
        "DateTimeHierarchy": {
         "HierarchyId": "t44"
        }
       }
      ]
     }
    },
    {
     "TableVisual": {
      "VisualId": "s2-v5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Agents</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Named agents are shown by the first 8 characters of their ID"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "TableAggregatedFieldWells": {
         "GroupBy": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d47",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Agent"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m48",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Messages"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m49",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Active users"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m50",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "No-answer %"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m51",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "P95 response time (s)"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "RowSort": [
         {
          "FieldSort": {
           "FieldId": "m48",
           "Direction": "DESC"
          }
         }
        ]
       },
       "TableOptions": {
        "HeaderStyle": {
         "FontConfiguration": {
          "FontColor": "#FFFFFF",
          "FontWeight": {
           "Name": "BOLD"
          }
         },
         "BackgroundColor": "#2E4A62"
        }
       }
      },
      "Actions": []
     }
    }
   ],
   "TextBoxes": [
    {
     "SheetTextBoxId": "s2-title",
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\">\u00a0| Chat adoption &amp; performance</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">How people use chat, on which surfaces, and how well it responds.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s2-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"26px\"><b>Quick</b></inline>\n  </block>\n</text-box>"
    }
   ],
   "Layouts": [
    {
     "Configuration": {
      "GridLayout": {
       "Elements": [
        {
         "ElementId": "s2-title",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 4,
         "ColumnSpan": 32,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-c-pStart",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-c-pEnd",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 4,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-c-gran",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 8,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-c-surface",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 12,
         "ColumnSpan": 5,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-c-agenttype",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 17,
         "ColumnSpan": 5,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-c-outcome",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 22,
         "ColumnSpan": 5,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-c-usertype",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 27,
         "ColumnSpan": 9,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-k1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 6,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-k2",
         "ElementType": "VISUAL",
         "ColumnIndex": 6,
         "ColumnSpan": 6,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-k3",
         "ElementType": "VISUAL",
         "ColumnIndex": 12,
         "ColumnSpan": 6,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-k4",
         "ElementType": "VISUAL",
         "ColumnIndex": 18,
         "ColumnSpan": 6,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-k5",
         "ElementType": "VISUAL",
         "ColumnIndex": 24,
         "ColumnSpan": 6,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-k6",
         "ElementType": "VISUAL",
         "ColumnIndex": 30,
         "ColumnSpan": 6,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-v1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 18,
         "RowIndex": 13,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-v2",
         "ElementType": "VISUAL",
         "ColumnIndex": 18,
         "ColumnSpan": 18,
         "RowIndex": 13,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-v3",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 18,
         "RowIndex": 25,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-v4",
         "ElementType": "VISUAL",
         "ColumnIndex": 18,
         "ColumnSpan": 18,
         "RowIndex": 25,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-v5",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 37,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s2-logo",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "Padding": "4px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        }
       ],
       "CanvasSizeOptions": {
        "ScreenCanvasSizeOptions": {
         "ResizeOption": "FIXED",
         "OptimizedViewPortWidth": "1600px"
        }
       }
      }
     }
    }
   ],
   "ContentType": "INTERACTIVE"
  },
  {
   "SheetId": "s3",
   "Name": "Agent hours",
   "ParameterControls": [
    {
     "DateTimePicker": {
      "ParameterControlId": "s3-c-pStart",
      "Title": "From",
      "SourceParameterName": "pStart",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "DateTimePicker": {
      "ParameterControlId": "s3-c-pEnd",
      "Title": "To",
      "SourceParameterName": "pEnd",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "Dropdown": {
      "ParameterControlId": "s3-c-gran",
      "Title": "Time scale",
      "SourceParameterName": "pGranularity",
      "DisplayOptions": {},
      "Type": "SINGLE_SELECT",
      "SelectableValues": {
       "Values": [
        "Day",
        "Week",
        "Month"
       ]
      }
     }
    },
    {
     "Dropdown": {
      "ParameterControlId": "s3-c-topn",
      "Title": "Top N",
      "SourceParameterName": "pTopN",
      "DisplayOptions": {},
      "Type": "SINGLE_SELECT",
      "SelectableValues": {
       "Values": [
        "5",
        "10",
        "15",
        "25",
        "50"
       ]
      }
     }
    }
   ],
   "FilterControls": [
    {
     "Dropdown": {
      "FilterControlId": "s3-c-service",
      "Title": "Service",
      "SourceFilterId": "f-s3-service",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    },
    {
     "Dropdown": {
      "FilterControlId": "s3-c-group",
      "Title": "Usage group",
      "SourceFilterId": "f-s3-group",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    },
    {
     "Dropdown": {
      "FilterControlId": "s3-c-sub",
      "Title": "Subscription",
      "SourceFilterId": "f-s3-sub",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    }
   ],
   "Visuals": [
    {
     "KPIVisual": {
      "VisualId": "s3-k1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Agent hours</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Latest period vs the one before (Time scale)"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m52",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Agent hours"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": [
         {
          "CategoricalDimensionField": {
           "FieldId": "s3-k1-period",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Period"
           }
          }
         }
        ]
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        },
        "TrendArrows": {
         "Visibility": "VISIBLE"
        },
        "SecondaryValue": {
         "Visibility": "VISIBLE"
        },
        "Comparison": {
         "ComparisonMethod": "PERCENT_DIFFERENCE"
        },
        "Sparkline": {
         "Visibility": "VISIBLE",
         "Type": "AREA",
         "Color": "#2E6DB4",
         "TooltipVisibility": "VISIBLE"
        }
       },
       "SortConfiguration": {
        "TrendGroupSort": [
         {
          "FieldSort": {
           "FieldId": "s3-k1-period",
           "Direction": "ASC"
          }
         }
        ]
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s3-k2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Included hours</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m54",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Included hours"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s3-k3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Extra hours</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m55",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Extra hours"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s3-k4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Extra hours %</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m56",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Extra %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s3-k5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Users with agent hours</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m57",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Users with agent hours"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "BarChartVisual": {
      "VisualId": "s3-v1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Agent hours by service</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Hover a bar for the included / extra detail"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "BarChartAggregatedFieldWells": {
         "Category": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d58",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Service"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m59",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          }
         ],
         "Colors": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d60",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Usage group"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "m59",
           "Direction": "DESC"
          }
         }
        ]
       },
       "Orientation": "HORIZONTAL",
       "BarsArrangement": "STACKED",
       "CategoryLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "ValueLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "VISIBLE",
        "Title": {
         "Visibility": "HIDDEN"
        },
        "Position": "BOTTOM"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       },
       "Tooltip": {
        "TooltipVisibility": "VISIBLE",
        "SelectedTooltipType": "SHEET",
        "FieldBasedTooltip": {
         "AggregationVisibility": "HIDDEN",
         "TooltipTitleType": "NONE",
         "TooltipFields": []
        },
        "SheetTooltip": {
         "SheetId": "tt-service"
        }
       }
      },
      "Actions": [
       {
        "CustomActionId": "s3-a1",
        "Name": "Filter this sheet",
        "Status": "ENABLED",
        "Trigger": "DATA_POINT_CLICK",
        "ActionOperations": [
         {
          "FilterOperation": {
           "SelectedFieldsConfiguration": {
            "SelectedFieldOptions": "ALL_FIELDS"
           },
           "TargetVisualsConfiguration": {
            "SameSheetTargetVisualConfiguration": {
             "TargetVisualOptions": "ALL_VISUALS"
            }
           }
          }
         }
        ]
       }
      ],
      "ColumnHierarchies": []
     }
    },
    {
     "ComboChartVisual": {
      "VisualId": "s3-v2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Records vs agent hours by service</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Many metering records do not mean many hours"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "ComboChartAggregatedFieldWells": {
         "Category": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d61",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Service"
            }
           }
          }
         ],
         "BarValues": [
          {
           "NumericalMeasureField": {
            "FieldId": "m62",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Metering records"
            }
           }
          }
         ],
         "Colors": [],
         "LineValues": [
          {
           "NumericalMeasureField": {
            "FieldId": "m63",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "m62",
           "Direction": "DESC"
          }
         }
        ]
       },
       "BarsArrangement": "CLUSTERED",
       "CategoryLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "PrimaryYAxisLabelOptions": {
        "Visibility": "VISIBLE",
        "AxisLabelOptions": [
         {
          "CustomLabel": "Metering records",
          "ApplyTo": {
           "FieldId": "m62",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Metering records"
           }
          }
         }
        ]
       },
       "SecondaryYAxisLabelOptions": {
        "Visibility": "VISIBLE",
        "AxisLabelOptions": [
         {
          "CustomLabel": "Agent hours",
          "ApplyTo": {
           "FieldId": "m63",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Agent hours"
           }
          }
         }
        ]
       },
       "Legend": {
        "Visibility": "VISIBLE",
        "Title": {
         "Visibility": "HIDDEN"
        },
        "Position": "BOTTOM"
       },
       "BarDataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       },
       "LineDataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       },
       "VisualPalette": {
        "ColorMap": [
         {
          "Element": {
           "FieldId": "m62",
           "FieldValue": "Metering records"
          },
          "Color": "#2E6DB4"
         },
         {
          "Element": {
           "FieldId": "m63",
           "FieldValue": "Agent hours"
          },
          "Color": "#4F9DA6"
         }
        ]
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "InsightVisual": {
      "VisualId": "s3-v4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Top 3 services by agent hours</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "InsightConfiguration": {
       "Computations": [
        {
         "TopBottomRanked": {
          "ComputationId": "s3-top",
          "Name": "Top",
          "Category": {
           "CategoricalDimensionField": {
            "FieldId": "d67",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Service"
            }
           }
          },
          "Value": {
           "NumericalMeasureField": {
            "FieldId": "m68",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          },
          "ResultSize": 3,
          "Type": "TOP"
         }
        }
       ],
       "CustomNarrative": {
        "Narrative": "<narrative>\n  Services using the most agent hours in the selected period:\n  <br/>\n  <for level=\"block\" listExpression=\"Top.items\">\n    <ul>\n      <li>\n        <inline color=\"Accent\">\n          <b>\n            <expression>Top.items[index].categoryValue.formattedValue</expression>\n          </b>\n        </inline>\n        \u00a0with\u00a0\n        <expression>Top.items[index].metricValue.formattedValue</expression>\n      </li>\n    </ul>\n  </for>\n</narrative>"
       }
      },
      "Actions": [],
      "DataSetIdentifier": "agent_hours"
     }
    },
    {
     "TableVisual": {
      "VisualId": "s3-v5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Top users by agent hours</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Red rows with a flag: extra hours at or above the review threshold"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "TableAggregatedFieldWells": {
         "GroupBy": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d69",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "User"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m72",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m73",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Included hours"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "s3-extra",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Extra hours"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m74",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Extra %"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "RowSort": [
         {
          "FieldSort": {
           "FieldId": "m72",
           "Direction": "DESC"
          }
         }
        ]
       },
       "TableOptions": {
        "HeaderStyle": {
         "FontConfiguration": {
          "FontColor": "#FFFFFF",
          "FontWeight": {
           "Name": "BOLD"
          }
         },
         "BackgroundColor": "#2E4A62"
        }
       }
      },
      "ConditionalFormatting": {
       "ConditionalFormattingOptions": [
        {
         "Row": {
          "BackgroundColor": {
           "Solid": {
            "Expression": "{Extra hours} >= 3.0",
            "Color": "#FBE4E6"
           }
          }
         }
        },
        {
         "Row": {
          "TextColor": {
           "Solid": {
            "Expression": "{Extra hours} >= 3.0",
            "Color": "#B3261E"
           }
          }
         }
        },
        {
         "Cell": {
          "FieldId": "s3-extra",
          "TextFormat": {
           "Icon": {
            "CustomCondition": {
             "Expression": "{Extra hours} >= 3.0",
             "IconOptions": {
              "Icon": "FLAG"
             },
             "Color": "#B3261E"
            }
           }
          }
         }
        }
       ]
      },
      "Actions": [
       {
        "CustomActionId": "s3-a2",
        "Name": "Show this user only",
        "Status": "ENABLED",
        "Trigger": "DATA_POINT_CLICK",
        "ActionOperations": [
         {
          "FilterOperation": {
           "SelectedFieldsConfiguration": {
            "SelectedFieldOptions": "ALL_FIELDS"
           },
           "TargetVisualsConfiguration": {
            "SameSheetTargetVisualConfiguration": {
             "TargetVisualOptions": "ALL_VISUALS"
            }
           }
          }
         }
        ]
       }
      ]
     }
    }
   ],
   "TextBoxes": [
    {
     "SheetTextBoxId": "s3-title",
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\">\u00a0| Agent hours</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">Where agent hours go: services, surfaces and heavy users.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s3-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"26px\"><b>Quick</b></inline>\n  </block>\n</text-box>"
    }
   ],
   "Layouts": [
    {
     "Configuration": {
      "GridLayout": {
       "Elements": [
        {
         "ElementId": "s3-title",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 4,
         "ColumnSpan": 32,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-c-pStart",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-c-pEnd",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 4,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-c-gran",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 8,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-c-service",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 12,
         "ColumnSpan": 5,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-c-group",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 17,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-c-sub",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 21,
         "ColumnSpan": 5,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-c-topn",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 26,
         "ColumnSpan": 10,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-k1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-k2",
         "ElementType": "VISUAL",
         "ColumnIndex": 7,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-k3",
         "ElementType": "VISUAL",
         "ColumnIndex": 14,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-k4",
         "ElementType": "VISUAL",
         "ColumnIndex": 21,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-k5",
         "ElementType": "VISUAL",
         "ColumnIndex": 28,
         "ColumnSpan": 8,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-v1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 18,
         "RowIndex": 13,
         "RowSpan": 14,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-v2",
         "ElementType": "VISUAL",
         "ColumnIndex": 18,
         "ColumnSpan": 18,
         "RowIndex": 13,
         "RowSpan": 14,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-v4",
         "ElementType": "VISUAL",
         "ColumnIndex": 24,
         "ColumnSpan": 12,
         "RowIndex": 27,
         "RowSpan": 14,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-v5",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 24,
         "RowIndex": 27,
         "RowSpan": 14,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s3-logo",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "Padding": "4px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        }
       ],
       "CanvasSizeOptions": {
        "ScreenCanvasSizeOptions": {
         "ResizeOption": "FIXED",
         "OptimizedViewPortWidth": "1600px"
        }
       }
      }
     }
    }
   ],
   "ContentType": "INTERACTIVE"
  },
  {
   "SheetId": "s4",
   "Name": "Feedback",
   "ParameterControls": [
    {
     "DateTimePicker": {
      "ParameterControlId": "s4-c-pStart",
      "Title": "From",
      "SourceParameterName": "pStart",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "DateTimePicker": {
      "ParameterControlId": "s4-c-pEnd",
      "Title": "To",
      "SourceParameterName": "pEnd",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "Dropdown": {
      "ParameterControlId": "s4-c-gran",
      "Title": "Time scale",
      "SourceParameterName": "pGranularity",
      "DisplayOptions": {},
      "Type": "SINGLE_SELECT",
      "SelectableValues": {
       "Values": [
        "Day",
        "Week",
        "Month"
       ]
      }
     }
    }
   ],
   "FilterControls": [
    {
     "Dropdown": {
      "FilterControlId": "s4-c-type",
      "Title": "Feedback",
      "SourceFilterId": "f-s4-type",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    }
   ],
   "Visuals": [
    {
     "KPIVisual": {
      "VisualId": "s4-k1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Feedback given</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m75",
           "Column": {
            "DataSetIdentifier": "chat_feedback",
            "ColumnName": "Feedback given"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s4-k2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Useful</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Latest period vs the one before (Time scale)"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m76",
           "Column": {
            "DataSetIdentifier": "chat_feedback",
            "ColumnName": "Useful %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": [
         {
          "CategoricalDimensionField": {
           "FieldId": "s4-k2-period",
           "Column": {
            "DataSetIdentifier": "chat_feedback",
            "ColumnName": "Period"
           }
          }
         }
        ]
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        },
        "TrendArrows": {
         "Visibility": "VISIBLE"
        },
        "SecondaryValue": {
         "Visibility": "VISIBLE"
        },
        "Comparison": {
         "ComparisonMethod": "PERCENT_DIFFERENCE"
        },
        "Sparkline": {
         "Visibility": "VISIBLE",
         "Type": "AREA",
         "Color": "#2E6DB4",
         "TooltipVisibility": "VISIBLE"
        }
       },
       "SortConfiguration": {
        "TrendGroupSort": [
         {
          "FieldSort": {
           "FieldId": "s4-k2-period",
           "Direction": "ASC"
          }
         }
        ]
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s4-k3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Not Useful</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m78",
           "Column": {
            "DataSetIdentifier": "chat_feedback",
            "ColumnName": "Not Useful %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s4-k4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Responses with feedback</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m79",
           "Column": {
            "DataSetIdentifier": "chat_feedback",
            "ColumnName": "Responses with feedback"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s4-k5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Users giving feedback</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m80",
           "Column": {
            "DataSetIdentifier": "chat_feedback",
            "ColumnName": "Users giving feedback"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "LineChartVisual": {
      "VisualId": "s4-v1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Useful feedback %</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Share of feedback events rated Useful (by feedback date)"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "LineChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "t81",
            "Column": {
             "DataSetIdentifier": "chat_feedback",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t81"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m82",
            "Column": {
             "DataSetIdentifier": "chat_feedback",
             "ColumnName": "Useful %"
            }
           }
          }
         ],
         "Colors": []
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "t81",
           "Direction": "ASC"
          }
         }
        ]
       },
       "Type": "LINE",
       "XAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "PrimaryYAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "HIDDEN"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       },
       "VisualPalette": {
        "ChartColor": "#2E6DB4"
       }
      },
      "Actions": [],
      "ColumnHierarchies": [
       {
        "DateTimeHierarchy": {
         "HierarchyId": "t81"
        }
       }
      ]
     }
    }
   ],
   "TextBoxes": [
    {
     "SheetTextBoxId": "s4-title",
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\">\u00a0| Feedback</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">How useful people find the answers.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s4-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"26px\"><b>Quick</b></inline>\n  </block>\n</text-box>"
    }
   ],
   "Layouts": [
    {
     "Configuration": {
      "GridLayout": {
       "Elements": [
        {
         "ElementId": "s4-title",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 4,
         "ColumnSpan": 32,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-c-pStart",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-c-pEnd",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 4,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-c-gran",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 8,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-c-type",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 12,
         "ColumnSpan": 24,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-k1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-k2",
         "ElementType": "VISUAL",
         "ColumnIndex": 7,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-k3",
         "ElementType": "VISUAL",
         "ColumnIndex": 14,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-k4",
         "ElementType": "VISUAL",
         "ColumnIndex": 21,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-k5",
         "ElementType": "VISUAL",
         "ColumnIndex": 28,
         "ColumnSpan": 8,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-v1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 13,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s4-logo",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "Padding": "4px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        }
       ],
       "CanvasSizeOptions": {
        "ScreenCanvasSizeOptions": {
         "ResizeOption": "FIXED",
         "OptimizedViewPortWidth": "1600px"
        }
       }
      }
     }
    }
   ],
   "ContentType": "INTERACTIVE"
  },
  {
   "SheetId": "s5",
   "Name": "Agents & resources",
   "ParameterControls": [
    {
     "DateTimePicker": {
      "ParameterControlId": "s5-c-pStart",
      "Title": "From",
      "SourceParameterName": "pStart",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "DateTimePicker": {
      "ParameterControlId": "s5-c-pEnd",
      "Title": "To",
      "SourceParameterName": "pEnd",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "Dropdown": {
      "ParameterControlId": "s5-c-gran",
      "Title": "Time scale",
      "SourceParameterName": "pGranularity",
      "DisplayOptions": {},
      "Type": "SINGLE_SELECT",
      "SelectableValues": {
       "Values": [
        "Day",
        "Week",
        "Month"
       ]
      }
     }
    }
   ],
   "FilterControls": [
    {
     "Dropdown": {
      "FilterControlId": "s5-c-surface",
      "Title": "Surface group",
      "SourceFilterId": "f-s5-surface",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    },
    {
     "Dropdown": {
      "FilterControlId": "s5-c-agent",
      "Title": "Agent",
      "SourceFilterId": "f-s5-agent",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    },
    {
     "Dropdown": {
      "FilterControlId": "s5-c-scope",
      "Title": "Message scope",
      "SourceFilterId": "f-s5-scope",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       }
      },
      "Type": "MULTI_SELECT"
     }
    }
   ],
   "Visuals": [
    {
     "KPIVisual": {
      "VisualId": "s5-k1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Named-agent messages</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m93",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Named-agent messages %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s5-k2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Flow messages</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m94",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Flow messages"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s5-k4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Responses with citations</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m96",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Responses with citations %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s5-k5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Messages using web search</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m97",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Web search %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "LineChartVisual": {
      "VisualId": "s5-v1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Default vs named agents</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "LineChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "t98",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t98"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m99",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Messages"
            }
           }
          }
         ],
         "Colors": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d100",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Agent type"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "t98",
           "Direction": "ASC"
          }
         }
        ]
       },
       "Type": "LINE",
       "XAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "PrimaryYAxisLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "VISIBLE",
        "Title": {
         "Visibility": "HIDDEN"
        },
        "Position": "BOTTOM"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       }
      },
      "Actions": [],
      "ColumnHierarchies": [
       {
        "DateTimeHierarchy": {
         "HierarchyId": "t98"
        }
       }
      ]
     }
    },
    {
     "BarChartVisual": {
      "VisualId": "s5-v2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Top named agents by messages</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "BarChartAggregatedFieldWells": {
         "Category": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d101",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Agent"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m102",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Messages"
            }
           }
          }
         ],
         "Colors": []
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "m102",
           "Direction": "DESC"
          }
         }
        ],
        "CategoryItemsLimit": {
         "ItemsLimit": 15,
         "OtherCategories": "EXCLUDE"
        },
        "ColorItemsLimit": {
         "OtherCategories": "EXCLUDE"
        },
        "SmallMultiplesLimitConfiguration": {
         "OtherCategories": "EXCLUDE"
        }
       },
       "Orientation": "HORIZONTAL",
       "BarsArrangement": "CLUSTERED",
       "CategoryLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "ValueLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "HIDDEN"
       },
       "DataLabels": {
        "Visibility": "VISIBLE",
        "Overlap": "DISABLE_OVERLAP"
       },
       "VisualPalette": {
        "ChartColor": "#2E6DB4"
       }
      },
      "Actions": [
       {
        "CustomActionId": "s5-a1",
        "Name": "Filter this sheet",
        "Status": "ENABLED",
        "Trigger": "DATA_POINT_CLICK",
        "ActionOperations": [
         {
          "FilterOperation": {
           "SelectedFieldsConfiguration": {
            "SelectedFieldOptions": "ALL_FIELDS"
           },
           "TargetVisualsConfiguration": {
            "SameSheetTargetVisualConfiguration": {
             "TargetVisualOptions": "ALL_VISUALS"
            }
           }
          }
         }
        ]
       }
      ],
      "ColumnHierarchies": []
     }
    },
    {
     "TableVisual": {
      "VisualId": "s5-v4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Data quality by surface</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Missing timing excludes blocked requests, which never have timings"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "TableAggregatedFieldWells": {
         "GroupBy": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d106",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Surface group"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m107",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Messages"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m108",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Missing surface %"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m109",
            "Column": {
             "DataSetIdentifier": "chat",
             "ColumnName": "Missing timing %"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "RowSort": [
         {
          "FieldSort": {
           "FieldId": "m107",
           "Direction": "DESC"
          }
         }
        ]
       },
       "TableOptions": {
        "HeaderStyle": {
         "FontConfiguration": {
          "FontColor": "#FFFFFF",
          "FontWeight": {
           "Name": "BOLD"
          }
         },
         "BackgroundColor": "#2E4A62"
        }
       }
      },
      "Actions": []
     }
    }
   ],
   "TextBoxes": [
    {
     "SheetTextBoxId": "s5-title",
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\">\u00a0| Agents &amp; resources</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">Default vs named agents, flows, attachments, citations and data quality.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s5-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"26px\"><b>Quick</b></inline>\n  </block>\n</text-box>"
    }
   ],
   "Layouts": [
    {
     "Configuration": {
      "GridLayout": {
       "Elements": [
        {
         "ElementId": "s5-title",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 4,
         "ColumnSpan": 32,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-c-pStart",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-c-pEnd",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 4,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-c-gran",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 8,
         "ColumnSpan": 4,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-c-surface",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 12,
         "ColumnSpan": 5,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-c-agent",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 17,
         "ColumnSpan": 6,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-c-scope",
         "ElementType": "FILTER_CONTROL",
         "ColumnIndex": 23,
         "ColumnSpan": 13,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-k1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 9,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-k2",
         "ElementType": "VISUAL",
         "ColumnIndex": 9,
         "ColumnSpan": 9,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-k4",
         "ElementType": "VISUAL",
         "ColumnIndex": 18,
         "ColumnSpan": 9,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-k5",
         "ElementType": "VISUAL",
         "ColumnIndex": 27,
         "ColumnSpan": 9,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-v1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 18,
         "RowIndex": 13,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-v2",
         "ElementType": "VISUAL",
         "ColumnIndex": 18,
         "ColumnSpan": 18,
         "RowIndex": 13,
         "RowSpan": 12,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-v4",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 25,
         "RowSpan": 14,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s5-logo",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "Padding": "4px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        }
       ],
       "CanvasSizeOptions": {
        "ScreenCanvasSizeOptions": {
         "ResizeOption": "FIXED",
         "OptimizedViewPortWidth": "1600px"
        }
       }
      }
     }
    }
   ],
   "ContentType": "INTERACTIVE"
  },
  {
   "SheetId": "s7",
   "Name": "Monthly report",
   "ParameterControls": [
    {
     "DateTimePicker": {
      "ParameterControlId": "s7-c-month",
      "Title": "Month",
      "SourceParameterName": "pMonth",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       },
       "DateTimeFormat": "MMMM YYYY"
      }
     }
    }
   ],
   "FilterControls": [],
   "Visuals": [
    {
     "KPIVisual": {
      "VisualId": "s7-k1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active Quick users</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m145",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Active users"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s7-k2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Messages</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m146",
           "Column": {
            "DataSetIdentifier": "chat",
            "ColumnName": "Messages"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s7-k3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Agent hours</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m147",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Agent hours"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s7-k4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Extra hours %</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m148",
           "Column": {
            "DataSetIdentifier": "agent_hours",
            "ColumnName": "Extra %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "KPIVisual": {
      "VisualId": "s7-k5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Useful feedback %</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "Values": [
         {
          "NumericalMeasureField": {
           "FieldId": "m149",
           "Column": {
            "DataSetIdentifier": "chat_feedback",
            "ColumnName": "Useful %"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": []
       },
       "KPIOptions": {
        "PrimaryValueDisplayType": "ACTUAL",
        "PrimaryValueFontConfiguration": {
         "FontSize": {
          "Relative": "LARGE"
         }
        }
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "InsightVisual": {
      "VisualId": "s7-v2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Top services this month</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "InsightConfiguration": {
       "Computations": [
        {
         "TopBottomRanked": {
          "ComputationId": "s7-tops",
          "Name": "TopSvc",
          "Category": {
           "CategoricalDimensionField": {
            "FieldId": "d153",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Service"
            }
           }
          },
          "Value": {
           "NumericalMeasureField": {
            "FieldId": "m154",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          },
          "ResultSize": 3,
          "Type": "TOP"
         }
        }
       ],
       "CustomNarrative": {
        "Narrative": "<narrative>\n  Services using the most agent hours:\n  <br/>\n  <for level=\"block\" listExpression=\"TopSvc.items\">\n    <ul>\n      <li>\n        <inline color=\"Accent\">\n          <b>\n            <expression>TopSvc.items[index].categoryValue.formattedValue</expression>\n          </b>\n        </inline>\n        \u00a0with\u00a0\n        <expression>TopSvc.items[index].metricValue.formattedValue</expression>\n      </li>\n    </ul>\n  </for>\n</narrative>"
       }
      },
      "Actions": [],
      "DataSetIdentifier": "agent_hours"
     }
    },
    {
     "BarChartVisual": {
      "VisualId": "s7-v4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Agent hours by service</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "BarChartAggregatedFieldWells": {
         "Category": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d160",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Service"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m161",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          }
         ],
         "Colors": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d162",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Usage group"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "CategorySort": [
         {
          "FieldSort": {
           "FieldId": "m161",
           "Direction": "DESC"
          }
         }
        ]
       },
       "Orientation": "HORIZONTAL",
       "BarsArrangement": "STACKED",
       "CategoryLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "ValueLabelOptions": {
        "Visibility": "HIDDEN"
       },
       "Legend": {
        "Visibility": "VISIBLE",
        "Title": {
         "Visibility": "HIDDEN"
        },
        "Position": "BOTTOM"
       },
       "DataLabels": {
        "Visibility": "HIDDEN",
        "Overlap": "DISABLE_OVERLAP"
       }
      },
      "Actions": [],
      "ColumnHierarchies": []
     }
    }
   ],
   "TextBoxes": [
    {
     "SheetTextBoxId": "s7-title",
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\">\u00a0| Monthly report</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">One-page monthly summary for admins. Choose a month; print or export as PDF.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s7-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"26px\"><b>Quick</b></inline>\n  </block>\n</text-box>"
    }
   ],
   "Layouts": [
    {
     "Configuration": {
      "GridLayout": {
       "Elements": [
        {
         "ElementId": "s7-title",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 4,
         "ColumnSpan": 32,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-c-month",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 3,
         "RowSpan": 3,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-k1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-k2",
         "ElementType": "VISUAL",
         "ColumnIndex": 7,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-k3",
         "ElementType": "VISUAL",
         "ColumnIndex": 14,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-k4",
         "ElementType": "VISUAL",
         "ColumnIndex": 21,
         "ColumnSpan": 7,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-k5",
         "ElementType": "VISUAL",
         "ColumnIndex": 28,
         "ColumnSpan": 8,
         "RowIndex": 6,
         "RowSpan": 7,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-v2",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 12,
         "RowIndex": 13,
         "RowSpan": 14,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-v4",
         "ElementType": "VISUAL",
         "ColumnIndex": 12,
         "ColumnSpan": 24,
         "RowIndex": 13,
         "RowSpan": 14,
         "BorderRadius": "8px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-logo",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
         "RowIndex": 0,
         "RowSpan": 3,
         "BorderRadius": "8px",
         "Padding": "4px",
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        }
       ],
       "CanvasSizeOptions": {
        "ScreenCanvasSizeOptions": {
         "ResizeOption": "FIXED",
         "OptimizedViewPortWidth": "1600px"
        }
       }
      }
     }
    }
   ],
   "ContentType": "INTERACTIVE"
  }
 ],
 "TooltipSheets": [
  {
   "SheetId": "tt-service",
   "Name": "Service detail",
   "Visuals": [
    {
     "TableVisual": {
      "VisualId": "tt-service-v1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Included vs extra</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "ChartConfiguration": {
       "FieldWells": {
        "TableAggregatedFieldWells": {
         "GroupBy": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d166",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Usage group"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m167",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Agent hours"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m168",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Metering records"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m169",
            "Column": {
             "DataSetIdentifier": "agent_hours",
             "ColumnName": "Hours per record"
            }
           }
          }
         ]
        }
       },
       "SortConfiguration": {
        "RowSort": [
         {
          "FieldSort": {
           "FieldId": "m167",
           "Direction": "DESC"
          }
         }
        ]
       },
       "TableOptions": {
        "HeaderStyle": {
         "FontConfiguration": {
          "FontColor": "#FFFFFF",
          "FontWeight": {
           "Name": "BOLD"
          }
         },
         "BackgroundColor": "#2E4A62"
        }
       }
      },
      "Actions": []
     }
    }
   ],
   "Images": [],
   "Layouts": [
    {
     "Configuration": {
      "FreeFormLayout": {
       "Elements": [
        {
         "ElementId": "tt-service-v1",
         "ElementType": "VISUAL",
         "XAxisLocation": "0px",
         "YAxisLocation": "0px",
         "Width": "420px",
         "Height": "190px"
        }
       ]
      }
     }
    }
   ]
  }
 ],
 "CalculatedFields": [
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Event time",
   "Expression": "epochDate({event_timestamp})"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Agent hours",
   "Expression": "sum({usage_hours})"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Extra %",
   "Expression": "sum(ifelse({usage_group} = \"Extra\", {usage_hours}, 0)) / sum({usage_hours})"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Extra hours",
   "Expression": "sum(ifelse({usage_group} = \"Extra\", {usage_hours}, 0))"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Hours per record",
   "Expression": "sum({usage_hours}) / count({usage_hours})"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Included hours",
   "Expression": "sum(ifelse({usage_group} = \"Included\", {usage_hours}, 0))"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Metering records",
   "Expression": "count({usage_hours})"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Month",
   "Expression": "truncDate(\"MM\", {Event time})"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Period",
   "Expression": "ifelse(${pGranularity} = \"Day\", truncDate(\"DD\", {Event time}), ${pGranularity} = \"Month\", truncDate(\"MM\", {Event time}), truncDate(\"WK\", {Event time}))"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Service",
   "Expression": "ifelse({reporting_service} = \"PAGES_RUNTIME\", \"Pages (viewing)\", {reporting_service} = \"QUICK_WORK\", \"Quick Work\", {reporting_service} = \"GENERAL_CHAT\", \"General chat\", {reporting_service} = \"OUTLOOK_EXTENSION\", \"Outlook add-in\", {reporting_service} = \"EXCEL_EXTENSION\", \"Excel add-in\", {reporting_service} = \"WORD_EXTENSION\", \"Word add-in\", {reporting_service} = \"PPT_EXTENSION\", \"PowerPoint add-in\", {reporting_service} = \"FLOW\", \"Flows\", {reporting_service} = \"ARTIFACT_GENERATION\", \"Artifact generation\", {reporting_service} = \"STRUCTURED_DATA_QUERY\", \"Structured data query\", {reporting_service} = \"AUTOMATION\", \"Automation\", {reporting_service} = \"DATA_LAKE\", \"Data lake\", {reporting_service} = \"PAGES_AUTHORING\", \"Pages (authoring)\", {reporting_service} = \"RESEARCH\", \"Research\", {reporting_service} = \"CUSTOM_AGENT\", \"Custom agents\", {reporting_service})"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Subscription",
   "Expression": "ifelse({subscription_type} = \"ENTERPRISE\", \"Enterprise\", {subscription_type} = \"PROFESSIONAL\", \"Professional\", {subscription_type} = \"BI_AUTHOR\", \"BI Author\", {subscription_type})"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Usage group",
   "Expression": "{usage_group}"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "User",
   "Expression": "split({user_arn}, '/', 3)"
  },
  {
   "DataSetIdentifier": "agent_hours",
   "Name": "Users with agent hours",
   "Expression": "distinct_count({user_arn})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Event time",
   "Expression": "epochDate(decimalToInt({event_timestamp} / 1000))"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Active users",
   "Expression": "distinct_count({user_arn})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Agent type",
   "Expression": "ifelse(isNull({agent_id}) OR {agent_id} = \"\" OR {agent_id} = \"SYSTEM\" OR {agent_id} = \"-\", \"Default agent\", \"Named agent\")"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Agent",
   "Expression": "ifelse({Agent type} = \"Default agent\", \"Default agent\", concat(\"Agent \", left({agent_id}, 8)))"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Conversations",
   "Expression": "distinct_count({conversation_id})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Surface group",
   "Expression": "ifelse(locate(coalesce({surface_type}, \"-\"), \"QUICK_WORK_DESKTOP\") > 0, \"Desktop app\", locate(coalesce({surface_type}, \"-\"), \"ADD_IN\") > 0 OR {surface_type} = \"OFFICE_ADDIN\", \"Office add-ins\", locate(coalesce({surface_type}, \"-\"), \"BROWSER_EXTENSION\") > 0, \"Browser extension\", {surface_type} = \"WEB_EXPERIENCE\", \"Web\", {surface_type} = \"MOBILE\", \"Mobile\", {surface_type} = \"SLACK\", \"Slack\", \"Background / unknown\")"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Desktop reach %",
   "Expression": "distinct_countIf({user_arn}, {Surface group} = \"Desktop app\") / distinct_count({user_arn})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Flow messages",
   "Expression": "countIf({user_message_id}, {flow_id} <> \"-\")"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Hour (UTC)",
   "Expression": "extract(\"HH\", {Event time})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Response time (s)",
   "Expression": "latency / 1000"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Median response time (s)",
   "Expression": "percentileDisc({Response time (s)}, 50)"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Message scope",
   "Expression": "ifelse({message_scope} = \"all_resources\", \"All resources\", {message_scope} = \"specific_resource\", \"Specific resource\", \"No resources\")"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Messages",
   "Expression": "count({user_message_id})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Messages per user",
   "Expression": "count({user_message_id}) / distinct_count({user_arn})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Missing surface %",
   "Expression": "countIf({user_message_id}, {Surface group} = \"Background / unknown\") / count({user_message_id})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Missing timing %",
   "Expression": "countIf({user_message_id}, isNull(latency) AND {status_code} <> \"request_blocked\") / count({user_message_id})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Month",
   "Expression": "truncDate(\"MM\", {Event time})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Named-agent messages %",
   "Expression": "countIf({user_message_id}, {Agent type} = \"Named agent\") / count({user_message_id})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "No-answer %",
   "Expression": "countIf({user_message_id}, {status_code} = \"no_answer_found\") / count({user_message_id})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Outcome",
   "Expression": "ifelse({status_code} = \"success\", \"Success\", {status_code} = \"no_answer_found\", \"No answer\", \"Blocked\")"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "P95 response time (s)",
   "Expression": "percentileDisc({Response time (s)}, 95)"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Time to first token (s)",
   "Expression": "{time_to_first_token} / 1000"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "P95 time to first token (s)",
   "Expression": "percentileDisc({Time to first token (s)}, 95)"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Period",
   "Expression": "ifelse(${pGranularity} = \"Day\", truncDate(\"DD\", {Event time}), ${pGranularity} = \"Month\", truncDate(\"MM\", {Event time}), truncDate(\"WK\", {Event time}))"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Responses with citations %",
   "Expression": "countIf({user_message_id}, strlen({cited_resource}) > 2) / countIf({user_message_id}, {status_code} = \"success\")"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Success %",
   "Expression": "countIf({user_message_id}, {status_code} = \"success\") / count({user_message_id})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Surface",
   "Expression": "ifelse(locate(coalesce({surface_type}, \"-\"), \"M365_WORD\") > 0, \"Word add-in\", locate(coalesce({surface_type}, \"-\"), \"M365_EXCEL\") > 0, \"Excel add-in\", locate(coalesce({surface_type}, \"-\"), \"M365_OUTLOOK\") > 0, \"Outlook add-in\", locate(coalesce({surface_type}, \"-\"), \"M365_POWERPOINT\") > 0, \"PowerPoint add-in\", {surface_type} = \"OFFICE_ADDIN\", \"Office add-in\", {surface_type} = \"QUICK_WORK_DESKTOP_MAC\", \"Desktop app (Mac)\", {surface_type} = \"QUICK_WORK_DESKTOP_WINDOWS\", \"Desktop app (Windows)\", {surface_type} = \"BROWSER_EXTENSION_CHROME\", \"Browser extension (Chrome)\", {surface_type} = \"BROWSER_EXTENSION\", \"Browser extension\", {surface_type} = \"WEB_EXPERIENCE\", \"Web\", {surface_type} = \"MOBILE\", \"Mobile\", {surface_type} = \"SLACK\", \"Slack\", \"Unknown\")"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "User",
   "Expression": "split({user_arn}, '/', 3)"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "User type",
   "Expression": "ifelse({user_type} = \"READER_PRO\", \"Reader Pro\", {user_type} = \"AUTHOR_PRO\", \"Author Pro\", {user_type} = \"ADMIN_PRO\", \"Admin Pro\", {user_type} = \"READER\", \"Reader\", {user_type} = \"AUTHOR\", \"Author\", {user_type} = \"ADMIN\", \"Admin\", {user_type})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Web search %",
   "Expression": "countIf({user_message_id}, toLower({web_search}) = \"true\") / count({user_message_id})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Weekday number",
   "Expression": "extract(\"WD\", {Event time})"
  },
  {
   "DataSetIdentifier": "chat",
   "Name": "Weekday",
   "Expression": "ifelse({Weekday number} = 2, \"1 Mon\", {Weekday number} = 3, \"2 Tue\", {Weekday number} = 4, \"3 Wed\", {Weekday number} = 5, \"4 Thu\", {Weekday number} = 6, \"5 Fri\", {Weekday number} = 7, \"6 Sat\", \"7 Sun\")"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Event time",
   "Expression": "epochDate(decimalToInt({event_timestamp} / 1000))"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Feedback",
   "Expression": "{feedback_type}"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Feedback given",
   "Expression": "count({system_message_id})"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Month",
   "Expression": "truncDate(\"MM\", {Event time})"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Not Useful",
   "Expression": "countIf({system_message_id}, {feedback_type} = \"Not Useful\")"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Not Useful %",
   "Expression": "countIf({system_message_id}, {feedback_type} = \"Not Useful\") / count({system_message_id})"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Period",
   "Expression": "ifelse(${pGranularity} = \"Day\", truncDate(\"DD\", {Event time}), ${pGranularity} = \"Month\", truncDate(\"MM\", {Event time}), truncDate(\"WK\", {Event time}))"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Responses with feedback",
   "Expression": "distinct_count({system_message_id})"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Useful",
   "Expression": "countIf({system_message_id}, {feedback_type} = \"Useful\")"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Useful %",
   "Expression": "countIf({system_message_id}, {feedback_type} = \"Useful\") / count({system_message_id})"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "User",
   "Expression": "split({user_arn}, '/', 3)"
  },
  {
   "DataSetIdentifier": "chat_feedback",
   "Name": "Users giving feedback",
   "Expression": "distinct_count({user_arn})"
  },
  {
   "DataSetIdentifier": "index_usage",
   "Name": "Event time",
   "Expression": "epochDate(decimalToInt({event_timestamp} / 1000))"
  },
  {
   "DataSetIdentifier": "index_usage",
   "Name": "Period",
   "Expression": "ifelse(${pGranularity} = \"Day\", truncDate(\"DD\", {Event time}), ${pGranularity} = \"Month\", truncDate(\"MM\", {Event time}), truncDate(\"WK\", {Event time}))"
  },
  {
   "DataSetIdentifier": "index_usage",
   "Name": "Index size (MB)",
   "Expression": "max({consumed_index_size}) / 1048576"
  }
 ],
 "ParameterDeclarations": [
  {
   "IntegerParameterDeclaration": {
    "ParameterValueType": "SINGLE_VALUED",
    "Name": "pTopN",
    "DefaultValues": {
     "StaticValues": [
      15
     ]
    },
    "ValueWhenUnset": {
     "ValueWhenUnsetOption": "NULL"
    }
   }
  },
  {
   "StringParameterDeclaration": {
    "ParameterValueType": "SINGLE_VALUED",
    "Name": "pGranularity",
    "DefaultValues": {
     "StaticValues": [
      "Week"
     ]
    },
    "ValueWhenUnset": {
     "ValueWhenUnsetOption": "NULL"
    }
   }
  },
  {
   "DateTimeParameterDeclaration": {
    "Name": "pStart",
    "DefaultValues": {
     "RollingDate": {
      "Expression": "addDateTime(-90, 'DD', truncDate('DD', now()))"
     }
    },
    "TimeGranularity": "DAY"
   }
  },
  {
   "DateTimeParameterDeclaration": {
    "Name": "pEnd",
    "DefaultValues": {
     "RollingDate": {
      "Expression": "truncDate('DD', now())"
     }
    },
    "TimeGranularity": "DAY"
   }
  },
  {
   "DateTimeParameterDeclaration": {
    "Name": "pMonth",
    "DefaultValues": {
     "RollingDate": {
      "Expression": "truncDate('MM', now())"
     }
    },
    "TimeGranularity": "MONTH"
   }
  }
 ],
 "FilterGroups": [
  {
   "FilterGroupId": "fg-date-chat",
   "Filters": [
    {
     "TimeRangeFilter": {
      "FilterId": "f-date-chat",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Event time"
      },
      "IncludeMinimum": true,
      "IncludeMaximum": true,
      "RangeMinimumValue": {
       "Parameter": "pStart"
      },
      "RangeMaximumValue": {
       "Parameter": "pEnd"
      },
      "NullOption": "NON_NULLS_ONLY",
      "TimeGranularity": "DAY"
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s1",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s2",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s3",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s4",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s5",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-month-chat",
   "Filters": [
    {
     "TimeEqualityFilter": {
      "FilterId": "f-month-chat",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Event time"
      },
      "ParameterName": "pMonth",
      "TimeGranularity": "MONTH"
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s7",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-date-ah",
   "Filters": [
    {
     "TimeRangeFilter": {
      "FilterId": "f-date-ah",
      "Column": {
       "DataSetIdentifier": "agent_hours",
       "ColumnName": "Event time"
      },
      "IncludeMinimum": true,
      "IncludeMaximum": true,
      "RangeMinimumValue": {
       "Parameter": "pStart"
      },
      "RangeMaximumValue": {
       "Parameter": "pEnd"
      },
      "NullOption": "NON_NULLS_ONLY",
      "TimeGranularity": "DAY"
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s1",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s2",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s3",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s4",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s5",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-month-ah",
   "Filters": [
    {
     "TimeEqualityFilter": {
      "FilterId": "f-month-ah",
      "Column": {
       "DataSetIdentifier": "agent_hours",
       "ColumnName": "Event time"
      },
      "ParameterName": "pMonth",
      "TimeGranularity": "MONTH"
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s7",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-date-fb",
   "Filters": [
    {
     "TimeRangeFilter": {
      "FilterId": "f-date-fb",
      "Column": {
       "DataSetIdentifier": "chat_feedback",
       "ColumnName": "Event time"
      },
      "IncludeMinimum": true,
      "IncludeMaximum": true,
      "RangeMinimumValue": {
       "Parameter": "pStart"
      },
      "RangeMaximumValue": {
       "Parameter": "pEnd"
      },
      "NullOption": "NON_NULLS_ONLY",
      "TimeGranularity": "DAY"
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s1",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s2",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s3",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s4",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s5",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-month-fb",
   "Filters": [
    {
     "TimeEqualityFilter": {
      "FilterId": "f-month-fb",
      "Column": {
       "DataSetIdentifier": "chat_feedback",
       "ColumnName": "Event time"
      },
      "ParameterName": "pMonth",
      "TimeGranularity": "MONTH"
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s7",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s2-surface",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s2-surface",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Surface group"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s2",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s2-agenttype",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s2-agenttype",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Agent type"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s2",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s2-outcome",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s2-outcome",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Outcome"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s2",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s2-usertype",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s2-usertype",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "User type"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s2",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s3-service",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s3-service",
      "Column": {
       "DataSetIdentifier": "agent_hours",
       "ColumnName": "Service"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s3",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s3-group",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s3-group",
      "Column": {
       "DataSetIdentifier": "agent_hours",
       "ColumnName": "Usage group"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s3",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s3-sub",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s3-sub",
      "Column": {
       "DataSetIdentifier": "agent_hours",
       "ColumnName": "Subscription"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s3",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s3-v5-topn",
   "Filters": [
    {
     "TopBottomFilter": {
      "FilterId": "f-s3-v5-topn",
      "Column": {
       "DataSetIdentifier": "agent_hours",
       "ColumnName": "User"
      },
      "AggregationSortConfigurations": [
       {
        "Column": {
         "DataSetIdentifier": "agent_hours",
         "ColumnName": "usage_hours"
        },
        "SortDirection": "DESC",
        "AggregationFunction": {
         "NumericalAggregationFunction": {
          "SimpleNumericalAggregation": "SUM"
         }
        }
       }
      ],
      "ParameterName": "pTopN"
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s3",
       "Scope": "SELECTED_VISUALS",
       "VisualIds": [
        "s3-v5"
       ]
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s4-type",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s4-type",
      "Column": {
       "DataSetIdentifier": "chat_feedback",
       "ColumnName": "Feedback"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s4",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s5-surface",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s5-surface",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Surface group"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s5",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s5-agent",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s5-agent",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Agent"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s5",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s5-scope",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s5-scope",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Message scope"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "SelectAllOptions": "FILTER_ALL_VALUES",
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s5",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s5-v2-only",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s5-v2-only",
      "Column": {
       "DataSetIdentifier": "chat",
       "ColumnName": "Agent type"
      },
      "Configuration": {
       "FilterListConfiguration": {
        "MatchOperator": "CONTAINS",
        "CategoryValues": [
         "Named agent"
        ],
        "NullOption": "ALL_VALUES"
       }
      }
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s5",
       "Scope": "SELECTED_VISUALS",
       "VisualIds": [
        "s5-v2"
       ]
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-date-index",
   "Filters": [
    {
     "TimeRangeFilter": {
      "FilterId": "f-date-index",
      "Column": {
       "DataSetIdentifier": "index_usage",
       "ColumnName": "Event time"
      },
      "IncludeMinimum": true,
      "IncludeMaximum": true,
      "RangeMinimumValue": {
       "Parameter": "pStart"
      },
      "RangeMaximumValue": {
       "Parameter": "pEnd"
      },
      "NullOption": "NON_NULLS_ONLY",
      "TimeGranularity": "DAY"
     }
    }
   ],
   "ScopeConfiguration": {
    "SelectedSheets": {
     "SheetVisualScopingConfigurations": [
      {
       "SheetId": "s1",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  }
 ],
 "ColumnConfigurations": [
  {
   "Column": {
    "DataSetIdentifier": "agent_hours",
    "ColumnName": "Agent hours"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "agent_hours",
    "ColumnName": "Extra %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "agent_hours",
    "ColumnName": "Extra hours"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "agent_hours",
    "ColumnName": "Hours per record"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 3
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "agent_hours",
    "ColumnName": "Included hours"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "agent_hours",
    "ColumnName": "Metering records"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "agent_hours",
    "ColumnName": "Users with agent hours"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Active users"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Conversations"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Desktop reach %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Flow messages"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Median response time (s)"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Messages"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Messages per user"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Missing surface %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Missing timing %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Named-agent messages %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "No-answer %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "P95 response time (s)"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "P95 time to first token (s)"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Responses with citations %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Success %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Web search %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat_feedback",
    "ColumnName": "Feedback given"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat_feedback",
    "ColumnName": "Not Useful"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat_feedback",
    "ColumnName": "Not Useful %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat_feedback",
    "ColumnName": "Responses with feedback"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat_feedback",
    "ColumnName": "Useful"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat_feedback",
    "ColumnName": "Useful %"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "PercentageDisplayFormatConfiguration": {
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       }
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat_feedback",
    "ColumnName": "Users giving feedback"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 0
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "agent_hours",
    "ColumnName": "Usage group"
   },
   "ColorsConfiguration": {
    "CustomColors": [
     {
      "FieldValue": "Extra",
      "Color": "#2E6DB4"
     },
     {
      "FieldValue": "Included",
      "Color": "#4F9DA6"
     }
    ]
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Outcome"
   },
   "ColorsConfiguration": {
    "CustomColors": [
     {
      "FieldValue": "Blocked",
      "Color": "#2E6DB4"
     },
     {
      "FieldValue": "No answer",
      "Color": "#4F9DA6"
     },
     {
      "FieldValue": "Success",
      "Color": "#F2A541"
     }
    ]
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Agent type"
   },
   "ColorsConfiguration": {
    "CustomColors": [
     {
      "FieldValue": "Default agent",
      "Color": "#2E6DB4"
     },
     {
      "FieldValue": "Named agent",
      "Color": "#4F9DA6"
     }
    ]
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "chat",
    "ColumnName": "Surface group"
   },
   "ColorsConfiguration": {
    "CustomColors": [
     {
      "FieldValue": "Background / unknown",
      "Color": "#2E6DB4"
     },
     {
      "FieldValue": "Browser extension",
      "Color": "#4F9DA6"
     },
     {
      "FieldValue": "Desktop app",
      "Color": "#F2A541"
     },
     {
      "FieldValue": "Mobile",
      "Color": "#6C8EBF"
     },
     {
      "FieldValue": "Office add-ins",
      "Color": "#8BBF8A"
     },
     {
      "FieldValue": "Slack",
      "Color": "#9A7AA0"
     },
     {
      "FieldValue": "Web",
      "Color": "#C8A27C"
     }
    ]
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "index_usage",
    "ColumnName": "Index size (MB)"
   },
   "FormatConfiguration": {
    "NumberFormatConfiguration": {
     "FormatConfiguration": {
      "NumberDisplayFormatConfiguration": {
       "SeparatorConfiguration": {
        "ThousandsSeparator": {
         "Symbol": "COMMA",
         "Visibility": "VISIBLE"
        }
       },
       "DecimalPlacesConfiguration": {
        "DecimalPlaces": 1
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  }
 ],
 "AnalysisDefaults": {
  "DefaultNewSheetConfiguration": {
   "InteractiveLayoutConfiguration": {
    "Grid": {
     "CanvasSizeOptions": {
      "ScreenCanvasSizeOptions": {
       "ResizeOption": "FIXED",
       "OptimizedViewPortWidth": "1600px"
      }
     }
    }
   },
   "SheetContentType": "INTERACTIVE"
  }
 },
 "Options": {
  "WeekStart": "MONDAY"
 }
}
"""


STEPS = [
    ("Pre-checks", "precheck", False),
    ("Create or prepare the S3 bucket", "bucket", True),
    ("Start log delivery to the bucket", "deliveries", True),
    (
        "Deny default access to AWS data (optional, recommended, manual)",
        "deny_default",
        True,
    ),
    ("Grant Quick access to the bucket (not needed, skipped)", "quick_access", True),
    ("Create the Athena and Glue resources", "athena", True),
    ("Create Quick data sources and datasets", "datasets", True),
    ("Daily refresh (optional)", "refresh", True),
    ("Starter dashboard (optional)", "dashboard", True),
    ("Verify", "verify", False),
]


STEP = {key: number for number, (_, key, _) in enumerate(STEPS, start=1)}


DATASETS_STEP = STEP["datasets"]


DASHBOARD_STEP = STEP["dashboard"]


STOP_WORD = "stop"


SEARCH_WORD = "SEARCH"  # at the dataset-owner question: list every Quick admin instead


def mismatch_hint(expected, typed):
    """A short, friendly explanation of why the answer didn't match."""
    t = typed.strip()
    if not t:
        return f"Nothing was typed. Type: {expected}"
    if expected.isdigit():
        digits = "".join(c for c in t if c.isdigit())
        if digits == expected:
            return "Close. Type only the 12 digits, with no spaces or dashes."
        if len(digits) != len(expected):
            return f"You typed {len(digits)} digits, but the account ID has {len(expected)}. Check for a missing or extra digit."
        return "The digits don't match the account ID shown."
    if t.upper() == expected:
        return f"Almost there. It needs CAPITAL letters: {expected}"
    if " ".join(t.split()).upper() == expected:
        return f"Check the spacing: one space between each word, like this: {expected}"
    return (
        f"That doesn't match. Type it exactly as shown, in CAPITAL letters: {expected}"
    )


SKIP_WORD = "SKIP"


def ask_until(
    log, step, step_name, prompt, expected, allow_skip=False, alternatives=None
):
    """Ask until the exact phrase is typed.

    Returns True (confirmed), False (stopped), "skip" (only when allow_skip is set), or the
    value of an alternatives entry ({typed phrase: value}) when that phrase is typed.
    """
    alternatives = alternatives or {}
    print(f"\n{prompt}")
    print(f"  Type: {expected}")
    for phrase in alternatives:
        print(f"  Or type: {phrase}")
    if allow_skip:
        print(f"  Or type {SKIP_WORD} to skip this optional step.")
    print(
        f"  (To stop without changing anything else, type '{STOP_WORD}' or press Ctrl-C.)"
    )
    attempt = 0
    while True:
        attempt += 1
        try:
            answer = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            log.write(
                event="confirmation",
                step=step,
                step_name=step_name,
                prompt=expected,
                typed=None,
                attempt=attempt,
                outcome="stopped_interrupt",
            )
            return False
        if answer.strip() == expected:
            log.write(
                event="confirmation",
                step=step,
                step_name=step_name,
                prompt=expected,
                typed=answer,
                attempt=attempt,
                outcome="confirmed",
            )
            return True
        if answer.strip() in alternatives:
            log.write(
                event="confirmation",
                step=step,
                step_name=step_name,
                prompt=expected,
                typed=answer,
                attempt=attempt,
                outcome=f"chose_{alternatives[answer.strip()]}",
            )
            return alternatives[answer.strip()]
        if allow_skip and answer.strip().upper() == SKIP_WORD:
            log.write(
                event="confirmation",
                step=step,
                step_name=step_name,
                prompt=expected,
                typed=answer,
                attempt=attempt,
                outcome="skipped",
            )
            return "skip"
        if answer.strip().lower() == STOP_WORD:
            log.write(
                event="confirmation",
                step=step,
                step_name=step_name,
                prompt=expected,
                typed=answer,
                attempt=attempt,
                outcome="stopped",
            )
            return False
        log.write(
            event="confirmation",
            step=step,
            step_name=step_name,
            prompt=expected,
            typed=answer,
            attempt=attempt,
            outcome="mismatch",
        )
        print(f"  {mismatch_hint(expected, answer)}")
        print(f"  Try again, or type '{STOP_WORD}' to stop.")


def confirm(log, number, name, phrase, allow_skip=False, alternatives=None):
    return ask_until(
        log,
        number,
        name,
        f"Ready to run step {number} ({name})? Confirm by typing what it does.",
        phrase,
        allow_skip=allow_skip,
        alternatives=alternatives,
    )


def resolve_region(args, session):
    """Region to use, and where it came from."""
    import os

    if args.region:
        return args.region, "from --region"
    region = session.region_name
    if not region:
        return None, None
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        if os.environ.get(var) == region:
            return region, f"from {var}, which CloudShell sets to the console's region"
    return region, "from your AWS profile"


def describe_error(e):
    """One line for the customer; the full trace goes to the confirmation log."""
    if isinstance(e, ClientError):
        err = e.response.get("Error", {})
        op = getattr(e, "operation_name", "AWS call")
        hint = ""
        if err.get("Code") in (
            "ExpiredToken",
            "ExpiredTokenException",
            "RequestExpired",
        ):
            hint = " Your AWS credentials expired; refresh them and re-run."
        elif "AccessDenied" in err.get("Code", ""):
            hint = " Your AWS identity is missing a permission for this call."
        return f"{op} returned {err.get('Code')}: {err.get('Message')}.{hint}"
    return repr(e)


def describe_profile(args):
    """Which profile boto3 will use, and why."""
    import os

    if args.profile:
        return args.profile, "from --profile"
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return (
            "(environment credentials)",
            "AWS_ACCESS_KEY_ID is set; profiles are ignored",
        )
    if os.environ.get("AWS_PROFILE"):
        return os.environ["AWS_PROFILE"], "from AWS_PROFILE environment variable"
    return "default", "no --profile or AWS_PROFILE given"


def upload_log(setup, log_path, args):
    if args.dry_run:
        return
    key = f"{LOG_PREFIX}/{log_path}"
    try:
        if setup.bucket_exists():
            setup.s3.upload_file(log_path, args.bucket, key)
            print(f"Confirmation log uploaded to s3://{args.bucket}/{key}")
            return
    except ClientError as e:
        print(f"Could not upload confirmation log: {e}")
    print(f"Confirmation log kept locally at {log_path}")


# Org dashboard: a second analysis and dashboard, the starter one plus the org (department, division,
# cost center, user) visuals and filters. It reads the org-joined copies of the same three
# datasets, which hold every usage column plus these org_ columns. It is a separate dashboard so
# that people the starter dashboard is shared with never see org data.
ORG_ANALYSIS_ID = "quick-usage-org-analysis"


ORG_DASHBOARD_ID = "quick-usage-org-dashboard"


ORG_DASHBOARD_NAME = "Amazon Quick usage by organization"


ORG_DASHBOARD_COLUMNS = [
    "org_department",
    "org_division",
    "org_cost_center",
    "org_user_name",
]


# Version of the org definition, tagged like DASHBOARD_VERSION. Bump it whenever
# org_dashboard_definition() changes. It builds on the starter definition, so a DASHBOARD_VERSION
# bump needs an ORG_DASHBOARD_VERSION bump too; ORG_DASHBOARD_STARTER_VERSION records the
# starter version this one is built on, and a test fails when the two drift apart.
ORG_DASHBOARD_VERSION = 3


ORG_DASHBOARD_STARTER_VERSION = 5


ORG_DASHBOARD_VERSION_TAG = "quick-usage-org-dashboard-version"


ORG_DASHBOARD = DashboardSpec(
    ORG_ANALYSIS_ID,
    ORG_DASHBOARD_ID,
    ORG_DASHBOARD_NAME,
    ORG_DASHBOARD_VERSION,
    ORG_DASHBOARD_VERSION_TAG,
)


# ---- Org dataset of IAM Identity Center users ----
# One CSV row per Identity Center user, loaded into a SPICE dataset. The CSV holds PII (names,
# emails, phones, addresses), so it lives under its own prefix, outside PREFIX_ROOT: the usage
# data source role, the owner's IAM policy and the usage-log lifecycle rules don't cover it. Quick
# reads it through a separate Quick-only role that can read nothing else, and the CSV is deleted
# as soon as SPICE has loaded it. Only the small manifest stays in the bucket.
ORG_PREFIX = "quick-org-dataset"


ORG_CSV_KEY = f"{ORG_PREFIX}/users.csv"


ORG_MANIFEST_KEY = f"{ORG_PREFIX}/_manifests/users.json"


ORG_DATA_SET_ID = "quick-org-identity-center-users"


ORG_DATA_SET_NAME = "Organization - Identity Center users"


ORG_ROLE_POLICY_NAME = "QuickOrgDatasetRead"


IDENTITY_STORE_ENTERPRISE_EXTENSION = "aws:identitystore:enterprise"


ORG_INGESTION_START_WAIT_SECONDS = 30


# Each usage dataset also gets a child dataset LEFT-joined to the org dataset, for dashboards. The
# join key is the usage record's user_arn (arn:...:user/default/<name>) against the org dataset's
# quick_user_arn, which the script builds from the Identity Center UserName (in an Identity Center
# Quick account, the Quick user name is the Identity Center UserName). user_id is not used: it
# can be empty on some usage records. Both sides of the join must be plain columns: the Quick
# console supports only cast, rename, tag and parameter transforms on the sides of a join.
ORG_JOIN_KEY_COLUMN = "quick_user_arn"


# Transforms the Quick console accepts on a logical table that feeds a join.
JOIN_SIDE_TRANSFORMS = {
    "CastColumnTypeOperation",
    "RenameColumnOperation",
    "TagColumnOperation",
    "OverrideDatasetParameterOperation",
}


ORG_JOINED_COLUMN_PREFIX = "org_"


ORG_JOINED_REFRESH_DELAY = datetime.timedelta(hours=1)


ORG_NO_REFRESH_NOTE = "Note: the org data does not refresh on its own. To refresh it, run this script again."


# The only org columns copied into the joined datasets (as org_<name>). The org dataset keeps
# every Identity Center field, but the joined datasets hold every usage row and get shared
# with dashboard authors, so contact details, addresses, IDs and preferences stay out of them.
# The org dashboard needs department, division, cost center and user name; quick_user_arn is
# the join key; the rest are job fields useful for grouping and filtering.
ORG_JOINED_COLUMNS = [
    "user_name",
    "quick_user_arn",
    "title",
    "user_type",
    "cost_center",
    "organization",
    "division",
    "department",
    "manager_user_name",
    "snapshot_time",
]


ORG_COLUMNS = [
    "user_id",
    "user_name",
    "quick_user_arn",
    "user_status",
    "email",
    "given_name",
    "family_name",
    "display_name",
    "nickname",
    "phone_number",
    "website",
    "user_type",
    "title",
    "roles",
    "employee_number",
    "cost_center",
    "organization",
    "division",
    "department",
    "manager_user_id",
    "manager_user_name",
    "street_address",
    "locality",
    "region",
    "postal_code",
    "country",
    "formatted_address",
    "preferred_language",
    "locale",
    "timezone",
    "photo_url",
    "external_id",
    "external_id_issuer",
    "created_at",
    "updated_at",
    "snapshot_time",
]


def _common_descriptions(camel, event="the event happened", unit="milliseconds"):
    return {
        "resource_arn": "ARN of your Quick account.",
        "event_timestamp": f"When {event}, as Unix epoch time in {unit}. "
        "For dates and times, use event_datetime.",
        ("logType" if camel else "log_type"): "Log type of the record. "
        "It is the same on every row of this dataset.",
        (
            "accountId" if camel else "account_id"
        ): "AWS account ID of your Quick account.",
        "event_datetime": f"When {event}, as a UTC date and time.",
    }


_USER_ID = "Stable Quick ID of the user (principal ID)."
_USER_TYPE = "Quick user type of the user, for example ADMIN_PRO."
_NAMESPACE = "Quick namespace of the user."

# Text for each usage column's Description in Quick, the same as quick_usage_datasets_setup.py
# sets on the usage datasets. A joined dataset doesn't inherit its parents' descriptions, so the
# joined datasets set them again. Quick's natural-language features read them. Max 500 characters.
COLUMN_DESCRIPTIONS = {
    "chat": {
        **_common_descriptions(camel=True),
        "user_arn": "Quick user ARN of the person who sent the chat message. "
        "It includes the Quick user name.",
        "user_id": _USER_ID,
        "user_type": _USER_TYPE,
        "namespace": _NAMESPACE,
        "status_code": "Result of the chat request, for example success, request_blocked "
        "or no_answer_found.",
        "conversation_id": "Unique ID of the conversation. All messages in one conversation "
        "share it.",
        "system_message_id": "Unique ID of the answer Quick gave to the message.",
        "latency": "Time from the request to the end of the answer, in milliseconds.",
        "time_to_first_token": "Time from the request to the first part of the answer, in "
        "milliseconds, or 0 when not measured.",
        "message_scope": "Which resources were available to the agent for this message: "
        "all_resources, specific_resource or no_resources.",
        "user_message_id": "Unique ID of the message the user sent.",
        "agent_id": "ID of the chat agent that answered, or SYSTEM for the default agent.",
        "flow_id": "ID of the Quick Flow that sent the message, or - when it was not a flow.",
        "surface_type": "Client the message came from, for example WEB_EXPERIENCE, "
        "QUICK_DESKTOP, BROWSER_EXTENSION_CHROME, MOBILE_EXPERIENCE or SLACK.",
        "web_search": "true when the web search connector was available for the message. "
        "It does not show whether a web search ran.",
        "user_selected_resources": "JSON list of the resources the user selected for the "
        "message (type and ID).",
        "action_connectors": "JSON list of the action connectors available in the conversation.",
        "cited_resource": "JSON list of the resources cited in the answer (type and ID only, "
        "no names).",
    },
    "chat_feedback": {
        **_common_descriptions(camel=True),
        "user_arn": "Quick user ARN of the person who gave the feedback. "
        "It includes the Quick user name.",
        "user_id": _USER_ID,
        "user_type": _USER_TYPE,
        "namespace": _NAMESPACE,
        "status_code": "Status of the feedback event.",
        "conversation_id": "ID of the conversation the feedback is about. Same as "
        "conversation_id in the chat dataset.",
        "research_id": "ID of the research the feedback is about, when the feedback is on "
        "research.",
        "system_message_id": "ID of the answer the feedback is about. Same as "
        "system_message_id in the chat dataset.",
        "user_message_id": "ID of the user message the answer was for.",
        "feedback_type": "Type of feedback, for example Useful or Not Useful.",
        "rating": "Rating the user gave, when there is one.",
    },
    "agent_hours": {
        **_common_descriptions(
            camel=True, event="the usage was reported", unit="seconds"
        ),
        "user_arn": "Quick user ARN of the person whose work used the agent hours. "
        "It includes the Quick user name.",
        "user_id": _USER_ID,
        "subscription_type": "Subscription tier of the user: ENTERPRISE or PROFESSIONAL.",
        "reporting_service": "Quick feature that used the agent hours, for example FLOW, "
        "AUTOMATION or RESEARCH.",
        "usage_group": "Included when the hours are within the monthly subscription grant. "
        "Extra when they are overage, billed on use.",
        "usage_hours": "Agent hours used in this record.",
        "service_resource_arn": "ARN of what used the hours, for example a flow, automation "
        "or research session.",
    },
    "index_usage": {
        **_common_descriptions(camel=False),
        "user_arn": "Quick user ARN of the person linked to the event. "
        "It includes the Quick user name.",
        "user_id": _USER_ID,
        "consumed_index_size": "Total size of the whole index, in bytes.",
        "source_type": "Type of source: SPACE or KB (knowledge base).",
        "source_arn": "ARN of the Space or knowledge base. For current size, use the latest "
        "row per source_arn.",
        "consumed_source_size": "Size this source uses in the index, in bytes.",
        "consumed_source_doc_count": "Number of documents in this source.",
    },
}


# Description of each org dataset column, also used for its org_ copy in the joined datasets.
ORG_COLUMN_DESCRIPTIONS = {
    "user_id": "IAM Identity Center user ID.",
    "user_name": "IAM Identity Center user name. It is also the user's Quick user name.",
    "quick_user_arn": "Quick user ARN of the user, made from the user name. It matches "
    "user_arn in the usage datasets.",
    "user_status": "IAM Identity Center status of the user: ENABLED or DISABLED.",
    "email": "Primary email address of the user.",
    "given_name": "Given (first) name of the user.",
    "family_name": "Family (last) name of the user.",
    "display_name": "Display name of the user.",
    "nickname": "Nickname of the user.",
    "phone_number": "Primary phone number of the user.",
    "website": "Website of the user.",
    "user_type": "User type set in IAM Identity Center, for example Employee or Contractor.",
    "title": "Job title of the user.",
    "roles": "Roles of the user in IAM Identity Center, separated by semicolons.",
    "employee_number": "Employee number of the user.",
    "cost_center": "Cost center of the user.",
    "organization": "Organization of the user.",
    "division": "Division of the user.",
    "department": "Department of the user.",
    "manager_user_id": "IAM Identity Center user ID of the user's manager.",
    "manager_user_name": "User name of the user's manager.",
    "street_address": "Street of the user's primary address.",
    "locality": "City or locality of the user's primary address.",
    "region": "State or region of the user's primary address.",
    "postal_code": "Postal code of the user's primary address.",
    "country": "Country of the user's primary address.",
    "formatted_address": "The user's primary address as one line of text.",
    "preferred_language": "Preferred language of the user.",
    "locale": "Locale of the user, for example en-US.",
    "timezone": "Time zone of the user.",
    "photo_url": "URL of the user's primary photo.",
    "external_id": "ID of the user in the external identity provider that syncs users to IAM "
    "Identity Center.",
    "external_id_issuer": "Issuer of external_id (the external identity provider).",
    "created_at": "When the user was created in IAM Identity Center, as ISO 8601 text.",
    "updated_at": "When the user was last updated in IAM Identity Center, as ISO 8601 text.",
    "snapshot_time": "When quick_usage_org.py read the users from IAM Identity Center. The org "
    "data is as of this time; re-run the script to refresh it.",
}


def column_description_transforms(descriptions):
    """One TagColumnOperation per column, setting its Description in Quick."""
    return [
        {
            "TagColumnOperation": {
                "ColumnName": name,
                "Tags": [{"ColumnDescription": {"Text": text}}],
            }
        }
        for name, text in descriptions.items()
    ]


def primary_or_first_entry(entries):
    """Identity Center keeps lists (emails, phones, addresses); use the primary one, else the first."""
    if not entries:
        return {}
    return next((entry for entry in entries if entry.get("Primary")), entries[0])


def as_iso_text(value):
    return value.isoformat() if isinstance(value, datetime.datetime) else (value or "")


def org_identity_and_contact_fields(user):
    name = user.get("Name", {})
    external_id = primary_or_first_entry(user.get("ExternalIds"))
    return {
        "user_id": user["UserId"],
        "user_name": user.get("UserName", ""),
        "user_status": user.get("UserStatus", ""),
        "email": primary_or_first_entry(user.get("Emails")).get("Value", ""),
        "given_name": name.get("GivenName", ""),
        "family_name": name.get("FamilyName", ""),
        "display_name": user.get("DisplayName", ""),
        "nickname": user.get("NickName", ""),
        "phone_number": primary_or_first_entry(user.get("PhoneNumbers")).get(
            "Value", ""
        ),
        "website": user.get("Website", ""),
        "photo_url": primary_or_first_entry(user.get("Photos")).get("Value", ""),
        "external_id": external_id.get("Id", ""),
        "external_id_issuer": external_id.get("Issuer", ""),
    }


def org_job_fields(user, user_name_by_user_id):
    enterprise = user.get("Extensions", {}).get(IDENTITY_STORE_ENTERPRISE_EXTENSION, {})
    manager_user_id = (enterprise.get("manager") or {}).get("value", "")
    return {
        "user_type": user.get("UserType", ""),
        "title": user.get("Title", ""),
        "roles": ";".join(role.get("Value", "") for role in user.get("Roles", [])),
        "employee_number": enterprise.get("employeeNumber", ""),
        "cost_center": enterprise.get("costCenter", ""),
        "organization": enterprise.get("organization", ""),
        "division": enterprise.get("division", ""),
        "department": enterprise.get("department", ""),
        "manager_user_id": manager_user_id,
        "manager_user_name": user_name_by_user_id.get(manager_user_id, ""),
    }


def org_address_and_preference_fields(user):
    address = primary_or_first_entry(user.get("Addresses"))
    return {
        "street_address": address.get("StreetAddress", ""),
        "locality": address.get("Locality", ""),
        "region": address.get("Region", ""),
        "postal_code": address.get("PostalCode", ""),
        "country": address.get("Country", ""),
        "formatted_address": address.get("Formatted", ""),
        "preferred_language": user.get("PreferredLanguage", ""),
        "locale": user.get("Locale", ""),
        "timezone": user.get("Timezone", ""),
    }


def org_row_for_identity_center_user(user, user_name_by_user_id, snapshot_time):
    return {
        **org_identity_and_contact_fields(user),
        **org_job_fields(user, user_name_by_user_id),
        **org_address_and_preference_fields(user),
        "created_at": as_iso_text(user.get("CreatedAt")),
        "updated_at": as_iso_text(user.get("UpdatedAt")),
        "snapshot_time": snapshot_time,
    }


def org_rows_for_identity_center_users(user_pages, quick_user_arn_prefix=""):
    """One row per user, built a page at a time so the raw user objects aren't all held at once.

    quick_user_arn_prefix is arn:<partition>:quicksight:<identity region>:<account>:user/default/;
    each row's quick_user_arn is that plus the UserName, the join key for the usage datasets.
    """
    snapshot_time = now()
    user_name_by_user_id, rows = {}, []
    for users in user_pages:
        for user in users:
            user_name_by_user_id[user["UserId"]] = user.get("UserName", "")
            row = org_row_for_identity_center_user(user, {}, snapshot_time)
            row["quick_user_arn"] = (
                f"{quick_user_arn_prefix}{row['user_name']}"
                if quick_user_arn_prefix and row["user_name"]
                else ""
            )
            rows.append(row)
    for row in rows:  # a manager can be on a later page than their reports
        row["manager_user_name"] = user_name_by_user_id.get(row["manager_user_id"], "")
    return rows


def write_org_csv(rows, file):
    writer = csv.DictWriter(file, fieldnames=ORG_COLUMNS, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(rows)


def org_spice_capacity_abort(account):
    return Abort(
        "Quick has no free SPICE capacity for the org dataset.\n  Buy SPICE capacity or turn on "
        "auto-purchase in Quick under Manage account > SPICE capacity (or run: aws quicksight "
        f"update-spice-capacity-configuration --aws-account-id {account} --purchase-mode "
        "AUTO_PURCHASE), then re-run this script."
    )


def org_manifest(bucket):
    return {
        "fileLocations": [{"URIs": [f"s3://{bucket}/{ORG_CSV_KEY}"]}],
        "globalUploadSettings": {
            "format": "CSV",
            "delimiter": ",",
            "textqualifier": '"',
            "containsHeader": "true",
        },
    }


def upsert(items, additions, key):
    """Replace the item with the same key in place, or append a new one."""
    position = {key(item): i for i, item in enumerate(items)}
    for addition in additions:
        if key(addition) in position:
            items[position[key(addition)]] = addition
        else:
            items.append(addition)


def org_dashboard_definition(dataset_arns):
    """The starter definition plus ORG_DASHBOARD_ADDITIONS_JSON (the org dashboard).

    dataset_arns maps chat, chat_feedback, agent_hours and index_usage to their org-joined datasets. The
    additions are pieces of an analysis definition, in the same JSON as DASHBOARD_DEFINITION_JSON:
    calculated fields and filter groups to add, and per sheet the visuals and filter controls to
    add (a visual with an existing VisualId replaces it), layout elements to add or move (matched
    on ElementId), and InsertRows, which moves every tile at or below RowIndex down by RowSpan to
    make room for the org filter row. Everything else is the starter dashboard, unchanged.
    """
    definition = dashboard_definition(dataset_arns)
    additions = json.loads(ORG_DASHBOARD_ADDITIONS_JSON)
    definition["CalculatedFields"] += additions["CalculatedFields"]
    definition["FilterGroups"] += additions["FilterGroups"]
    sheets = {sheet["SheetId"]: sheet for sheet in definition["Sheets"]}
    for addition in additions["Sheets"]:
        sheet = sheets[addition["SheetId"]]
        elements = sheet["Layouts"][0]["Configuration"]["GridLayout"]["Elements"]
        insert = addition.get("InsertRows")
        if insert:
            for element in elements:
                if element["RowIndex"] >= insert["RowIndex"]:
                    element["RowIndex"] += insert["RowSpan"]
        upsert(
            sheet["Visuals"],
            addition.get("Visuals", []),
            lambda v: v[next(iter(v))]["VisualId"],
        )
        sheet.setdefault("FilterControls", []).extend(
            addition.get("FilterControls", [])
        )
        upsert(elements, addition.get("Elements", []), lambda e: e["ElementId"])
    return definition


# What the org dashboard adds to DASHBOARD_DEFINITION_JSON for the org dashboard (see org_dashboard_definition).
# On the Overview, the starter's index graph (s1-v6) moves below the org's s1-v5.
# Org visuals and Division -> Department -> Cost center -> User filters, with roster columns
# mapped to the org_ columns and the same tile style and chart
# colour as the starter visuals. Not included: Kiro visuals, and a Useful % by department and
# surface visual (chat_feedback has no surface_type).
ORG_DASHBOARD_ADDITIONS_JSON = r"""
{
  "CalculatedFields": [
    {
      "DataSetIdentifier": "agent_hours",
      "Name": "Cost center",
      "Expression": "ifelse(isNull({org_cost_center}) OR {org_cost_center} = \"\", \"(no org data)\", {org_cost_center})"
    },
    {
      "DataSetIdentifier": "agent_hours",
      "Name": "Department",
      "Expression": "ifelse(isNull({org_department}) OR {org_department} = \"\", \"(no org data)\", {org_department})"
    },
    {
      "DataSetIdentifier": "chat",
      "Name": "Cost center",
      "Expression": "ifelse(isNull({org_cost_center}) OR {org_cost_center} = \"\", \"(no org data)\", {org_cost_center})"
    },
    {
      "DataSetIdentifier": "chat",
      "Name": "Department",
      "Expression": "ifelse(isNull({org_department}) OR {org_department} = \"\", \"(no org data)\", {org_department})"
    },
    {
      "DataSetIdentifier": "chat",
      "Name": "Division",
      "Expression": "ifelse(isNull({org_division}) OR {org_division} = \"\", \"(no org data)\", {org_division})"
    },
    {
      "DataSetIdentifier": "chat_feedback",
      "Name": "Department",
      "Expression": "ifelse(isNull({org_department}) OR {org_department} = \"\", \"(no org data)\", {org_department})"
    }
  ],
  "FilterGroups": [
    {
      "FilterGroupId": "fg-org-div",
      "Filters": [
        {
          "CategoryFilter": {
            "FilterId": "f-org-div",
            "Column": {
              "DataSetIdentifier": "chat",
              "ColumnName": "org_division"
            },
            "Configuration": {
              "FilterListConfiguration": {
                "MatchOperator": "CONTAINS",
                "SelectAllOptions": "FILTER_ALL_VALUES",
                "NullOption": "ALL_VALUES"
              }
            },
            "DefaultFilterControlConfiguration": {
              "Title": "Division",
              "ControlOptions": {
                "DefaultDropdownOptions": {
                  "DisplayOptions": {
                    "SelectAllOptions": {
                      "Visibility": "VISIBLE"
                    },
                    "TitleOptions": {
                      "Visibility": "VISIBLE",
                      "FontConfiguration": {}
                    }
                  },
                  "Type": "MULTI_SELECT"
                }
              }
            }
          }
        }
      ],
      "ScopeConfiguration": {
        "AllSheets": {}
      },
      "Status": "ENABLED",
      "CrossDataset": "ALL_DATASETS"
    },
    {
      "FilterGroupId": "fg-org-dept",
      "Filters": [
        {
          "CategoryFilter": {
            "FilterId": "f-org-dept",
            "Column": {
              "DataSetIdentifier": "chat",
              "ColumnName": "org_department"
            },
            "Configuration": {
              "FilterListConfiguration": {
                "MatchOperator": "CONTAINS",
                "SelectAllOptions": "FILTER_ALL_VALUES",
                "NullOption": "ALL_VALUES"
              }
            },
            "DefaultFilterControlConfiguration": {
              "Title": "Department",
              "ControlOptions": {
                "DefaultDropdownOptions": {
                  "DisplayOptions": {
                    "SelectAllOptions": {
                      "Visibility": "VISIBLE"
                    },
                    "TitleOptions": {
                      "Visibility": "VISIBLE",
                      "FontConfiguration": {}
                    }
                  },
                  "Type": "MULTI_SELECT"
                }
              }
            }
          }
        }
      ],
      "ScopeConfiguration": {
        "AllSheets": {}
      },
      "Status": "ENABLED",
      "CrossDataset": "ALL_DATASETS"
    },
    {
      "FilterGroupId": "fg-org-cc",
      "Filters": [
        {
          "CategoryFilter": {
            "FilterId": "f-org-cc",
            "Column": {
              "DataSetIdentifier": "chat",
              "ColumnName": "org_cost_center"
            },
            "Configuration": {
              "FilterListConfiguration": {
                "MatchOperator": "CONTAINS",
                "SelectAllOptions": "FILTER_ALL_VALUES",
                "NullOption": "ALL_VALUES"
              }
            },
            "DefaultFilterControlConfiguration": {
              "Title": "Cost center",
              "ControlOptions": {
                "DefaultDropdownOptions": {
                  "DisplayOptions": {
                    "SelectAllOptions": {
                      "Visibility": "VISIBLE"
                    },
                    "TitleOptions": {
                      "Visibility": "VISIBLE",
                      "FontConfiguration": {}
                    }
                  },
                  "Type": "MULTI_SELECT"
                }
              }
            }
          }
        }
      ],
      "ScopeConfiguration": {
        "AllSheets": {}
      },
      "Status": "ENABLED",
      "CrossDataset": "ALL_DATASETS"
    },
    {
      "FilterGroupId": "fg-org-user",
      "Filters": [
        {
          "CategoryFilter": {
            "FilterId": "f-org-user",
            "Column": {
              "DataSetIdentifier": "chat",
              "ColumnName": "org_user_name"
            },
            "Configuration": {
              "FilterListConfiguration": {
                "MatchOperator": "CONTAINS",
                "SelectAllOptions": "FILTER_ALL_VALUES",
                "NullOption": "ALL_VALUES"
              }
            },
            "DefaultFilterControlConfiguration": {
              "Title": "User",
              "ControlOptions": {
                "DefaultDropdownOptions": {
                  "DisplayOptions": {
                    "SelectAllOptions": {
                      "Visibility": "VISIBLE"
                    },
                    "TitleOptions": {
                      "Visibility": "VISIBLE",
                      "FontConfiguration": {}
                    }
                  },
                  "Type": "MULTI_SELECT"
                }
              }
            }
          }
        }
      ],
      "ScopeConfiguration": {
        "AllSheets": {}
      },
      "Status": "ENABLED",
      "CrossDataset": "ALL_DATASETS"
    }
  ],
  "Sheets": [
    {
      "SheetId": "s1",
      "InsertRows": {
        "RowIndex": 6,
        "RowSpan": 3
      },
      "FilterControls": [
        {
          "CrossSheet": {
            "FilterControlId": "s1-c-org-div",
            "SourceFilterId": "f-org-div"
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s1-c-org-dept",
            "SourceFilterId": "f-org-dept",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s1-c-org-div",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_division"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s1-c-org-cc",
            "SourceFilterId": "f-org-cc",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s1-c-org-dept",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_department"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s1-c-org-user",
            "SourceFilterId": "f-org-user",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s1-c-org-cc",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_cost_center"
                  }
                }
              ]
            }
          }
        }
      ],
      "Visuals": [
        {
          "PivotTableVisual": {
            "VisualId": "s1-v5",
            "Title": {
              "Visibility": "VISIBLE",
              "FormatText": {
                "RichText": "<visual-title>\n  <b>Quick usage by department</b>\n</visual-title>"
              }
            },
            "Subtitle": {
              "Visibility": "VISIBLE",
              "FormatText": {
                "PlainText": "Expand a division to see departments and cost centers"
              }
            },
            "ChartConfiguration": {
              "FieldWells": {
                "PivotTableAggregatedFieldWells": {
                  "Rows": [
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d20",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Division"
                        }
                      }
                    },
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d21",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Department"
                        }
                      }
                    },
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d22",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Cost center"
                        }
                      }
                    }
                  ],
                  "Columns": [],
                  "Values": [
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m23",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Active users"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m24",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Messages"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m25",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Conversations"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m26",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Success %"
                        }
                      }
                    }
                  ]
                }
              },
              "SortConfiguration": {
                "FieldSortOptions": [
                  {
                    "FieldId": "d20",
                    "SortBy": {
                      "Field": {
                        "FieldId": "m23",
                        "Direction": "DESC"
                      }
                    }
                  }
                ]
              },
              "TableOptions": {
                "ColumnHeaderStyle": {
                  "FontConfiguration": {
                    "FontColor": "#FFFFFF",
                    "FontWeight": {
                      "Name": "BOLD"
                    }
                  },
                  "BackgroundColor": "#2E4A62"
                },
                "RowHeaderStyle": {
                  "FontConfiguration": {
                    "FontWeight": {
                      "Name": "BOLD"
                    }
                  }
                },
                "CellStyle": {},
                "RowFieldNamesStyle": {
                  "FontConfiguration": {
                    "FontColor": "#FFFFFF",
                    "FontWeight": {
                      "Name": "BOLD"
                    }
                  },
                  "BackgroundColor": "#2E4A62"
                },
                "RowsLayout": "HIERARCHY"
              },
              "TotalOptions": {
                "RowSubtotalOptions": {
                  "TotalsVisibility": "VISIBLE",
                  "FieldLevel": "ALL",
                  "TotalCellStyle": {},
                  "ValueCellStyle": {},
                  "MetricHeaderCellStyle": {},
                  "StyleTargets": [
                    {
                      "CellType": "VALUE"
                    },
                    {
                      "CellType": "TOTAL"
                    },
                    {
                      "CellType": "METRIC_HEADER"
                    }
                  ]
                },
                "ColumnTotalOptions": {
                  "TotalsVisibility": "VISIBLE",
                  "Placement": "END"
                }
              },
              "FieldOptions": {
                "SelectedFieldOptions": [
                  {
                    "FieldId": "d20",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "d21",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "d22",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "m23",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "m24",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "m25",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "m26",
                    "Visibility": "VISIBLE"
                  }
                ]
              }
            },
            "Actions": []
          }
        }
      ],
      "Elements": [
        {
          "ElementId": "s1-c-org-div",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 0,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s1-c-org-dept",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 9,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s1-c-org-cc",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 18,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s1-c-org-user",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 27,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s1-v5",
          "ElementType": "VISUAL",
          "ColumnIndex": 0,
          "ColumnSpan": 36,
          "RowIndex": 40,
          "RowSpan": 14,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s1-v6",
          "ElementType": "VISUAL",
          "ColumnIndex": 0,
          "ColumnSpan": 36,
          "RowIndex": 54,
          "RowSpan": 12,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        }
      ]
    },
    {
      "SheetId": "s2",
      "InsertRows": {
        "RowIndex": 6,
        "RowSpan": 3
      },
      "FilterControls": [
        {
          "CrossSheet": {
            "FilterControlId": "s2-c-org-div",
            "SourceFilterId": "f-org-div"
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s2-c-org-dept",
            "SourceFilterId": "f-org-dept",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s2-c-org-div",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_division"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s2-c-org-cc",
            "SourceFilterId": "f-org-cc",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s2-c-org-dept",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_department"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s2-c-org-user",
            "SourceFilterId": "f-org-user",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s2-c-org-cc",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_cost_center"
                  }
                }
              ]
            }
          }
        }
      ],
      "Visuals": [],
      "Elements": [
        {
          "ElementId": "s2-c-org-div",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 0,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s2-c-org-dept",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 9,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s2-c-org-cc",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 18,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s2-c-org-user",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 27,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        }
      ]
    },
    {
      "SheetId": "s3",
      "InsertRows": {
        "RowIndex": 6,
        "RowSpan": 3
      },
      "FilterControls": [
        {
          "CrossSheet": {
            "FilterControlId": "s3-c-org-div",
            "SourceFilterId": "f-org-div"
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s3-c-org-dept",
            "SourceFilterId": "f-org-dept",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s3-c-org-div",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_division"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s3-c-org-cc",
            "SourceFilterId": "f-org-cc",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s3-c-org-dept",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_department"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s3-c-org-user",
            "SourceFilterId": "f-org-user",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s3-c-org-cc",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_cost_center"
                  }
                }
              ]
            }
          }
        }
      ],
      "Visuals": [
        {
          "TableVisual": {
            "VisualId": "s3-v5",
            "Title": {
              "Visibility": "VISIBLE",
              "FormatText": {
                "RichText": "<visual-title>\n  <b>Top users by agent hours</b>\n</visual-title>"
              }
            },
            "Subtitle": {
              "Visibility": "VISIBLE",
              "FormatText": {
                "PlainText": "Red rows with a flag: extra hours at or above the review threshold"
              }
            },
            "ChartConfiguration": {
              "FieldWells": {
                "TableAggregatedFieldWells": {
                  "GroupBy": [
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d69",
                        "Column": {
                          "DataSetIdentifier": "agent_hours",
                          "ColumnName": "User"
                        }
                      }
                    },
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d70",
                        "Column": {
                          "DataSetIdentifier": "agent_hours",
                          "ColumnName": "Department"
                        }
                      }
                    },
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d71",
                        "Column": {
                          "DataSetIdentifier": "agent_hours",
                          "ColumnName": "Cost center"
                        }
                      }
                    }
                  ],
                  "Values": [
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m72",
                        "Column": {
                          "DataSetIdentifier": "agent_hours",
                          "ColumnName": "Agent hours"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m73",
                        "Column": {
                          "DataSetIdentifier": "agent_hours",
                          "ColumnName": "Included hours"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "s3-extra",
                        "Column": {
                          "DataSetIdentifier": "agent_hours",
                          "ColumnName": "Extra hours"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m74",
                        "Column": {
                          "DataSetIdentifier": "agent_hours",
                          "ColumnName": "Extra %"
                        }
                      }
                    }
                  ]
                }
              },
              "SortConfiguration": {
                "RowSort": [
                  {
                    "FieldSort": {
                      "FieldId": "m72",
                      "Direction": "DESC"
                    }
                  }
                ]
              },
              "TableOptions": {
                "HeaderStyle": {
                  "FontConfiguration": {
                    "FontColor": "#FFFFFF",
                    "FontWeight": {
                      "Name": "BOLD"
                    }
                  },
                  "BackgroundColor": "#2E4A62"
                }
              },
              "FieldOptions": {}
            },
            "ConditionalFormatting": {
              "ConditionalFormattingOptions": [
                {
                  "Row": {
                    "BackgroundColor": {
                      "Solid": {
                        "Expression": "{Extra hours} >= 3.0",
                        "Color": "#FBE4E6"
                      }
                    }
                  }
                },
                {
                  "Row": {
                    "TextColor": {
                      "Solid": {
                        "Expression": "{Extra hours} >= 3.0",
                        "Color": "#B3261E"
                      }
                    }
                  }
                },
                {
                  "Cell": {
                    "FieldId": "s3-extra",
                    "TextFormat": {
                      "Icon": {
                        "CustomCondition": {
                          "Expression": "{Extra hours} >= 3.0",
                          "IconOptions": {
                            "Icon": "FLAG"
                          },
                          "Color": "#B3261E"
                        }
                      }
                    }
                  }
                }
              ]
            },
            "Actions": [
              {
                "CustomActionId": "s3-a2",
                "Name": "Show this user only",
                "Status": "ENABLED",
                "Trigger": "DATA_POINT_CLICK",
                "ActionOperations": [
                  {
                    "FilterOperation": {
                      "SelectedFieldsConfiguration": {
                        "SelectedFieldOptions": "ALL_FIELDS"
                      },
                      "TargetVisualsConfiguration": {
                        "SameSheetTargetVisualConfiguration": {
                          "TargetVisualOptions": "ALL_VISUALS"
                        }
                      }
                    }
                  }
                ]
              }
            ]
          }
        }
      ],
      "Elements": [
        {
          "ElementId": "s3-c-org-div",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 0,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s3-c-org-dept",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 9,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s3-c-org-cc",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 18,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s3-c-org-user",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 27,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        }
      ]
    },
    {
      "SheetId": "s4",
      "InsertRows": {
        "RowIndex": 6,
        "RowSpan": 3
      },
      "FilterControls": [
        {
          "CrossSheet": {
            "FilterControlId": "s4-c-org-div",
            "SourceFilterId": "f-org-div"
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s4-c-org-dept",
            "SourceFilterId": "f-org-dept",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s4-c-org-div",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_division"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s4-c-org-cc",
            "SourceFilterId": "f-org-cc",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s4-c-org-dept",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_department"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s4-c-org-user",
            "SourceFilterId": "f-org-user",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s4-c-org-cc",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_cost_center"
                  }
                }
              ]
            }
          }
        }
      ],
      "Visuals": [
        {
          "TableVisual": {
            "VisualId": "s4-v4",
            "Title": {
              "Visibility": "VISIBLE",
              "FormatText": {
                "RichText": "<visual-title>\n  <b>Feedback by department</b>\n</visual-title>"
              }
            },
            "Subtitle": {
              "Visibility": "HIDDEN"
            },
            "ChartConfiguration": {
              "FieldWells": {
                "TableAggregatedFieldWells": {
                  "GroupBy": [
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d88",
                        "Column": {
                          "DataSetIdentifier": "chat_feedback",
                          "ColumnName": "Department"
                        }
                      }
                    }
                  ],
                  "Values": [
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m89",
                        "Column": {
                          "DataSetIdentifier": "chat_feedback",
                          "ColumnName": "Feedback given"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m90",
                        "Column": {
                          "DataSetIdentifier": "chat_feedback",
                          "ColumnName": "Useful"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m91",
                        "Column": {
                          "DataSetIdentifier": "chat_feedback",
                          "ColumnName": "Not Useful"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m92",
                        "Column": {
                          "DataSetIdentifier": "chat_feedback",
                          "ColumnName": "Useful %"
                        }
                      }
                    }
                  ]
                }
              },
              "SortConfiguration": {
                "RowSort": [
                  {
                    "FieldSort": {
                      "FieldId": "m89",
                      "Direction": "DESC"
                    }
                  }
                ]
              },
              "TableOptions": {
                "HeaderStyle": {
                  "FontConfiguration": {
                    "FontColor": "#FFFFFF",
                    "FontWeight": {
                      "Name": "BOLD"
                    }
                  },
                  "BackgroundColor": "#2E4A62"
                }
              }
            },
            "Actions": []
          }
        }
      ],
      "Elements": [
        {
          "ElementId": "s4-c-org-div",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 0,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s4-c-org-dept",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 9,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s4-c-org-cc",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 18,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s4-c-org-user",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 27,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s4-v4",
          "ElementType": "VISUAL",
          "ColumnIndex": 0,
          "ColumnSpan": 36,
          "RowIndex": 28,
          "RowSpan": 14,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        }
      ]
    },
    {
      "SheetId": "s5",
      "InsertRows": {
        "RowIndex": 6,
        "RowSpan": 3
      },
      "FilterControls": [
        {
          "CrossSheet": {
            "FilterControlId": "s5-c-org-div",
            "SourceFilterId": "f-org-div"
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s5-c-org-dept",
            "SourceFilterId": "f-org-dept",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s5-c-org-div",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_division"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s5-c-org-cc",
            "SourceFilterId": "f-org-cc",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s5-c-org-dept",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_department"
                  }
                }
              ]
            }
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s5-c-org-user",
            "SourceFilterId": "f-org-user",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s5-c-org-cc",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_cost_center"
                  }
                }
              ]
            }
          }
        }
      ],
      "Visuals": [
        {
          "BarChartVisual": {
            "VisualId": "s5-v3",
            "Title": {
              "Visibility": "VISIBLE",
              "FormatText": {
                "RichText": "<visual-title>\n  <b>Message scope by department</b>\n</visual-title>"
              }
            },
            "Subtitle": {
              "Visibility": "HIDDEN"
            },
            "ChartConfiguration": {
              "FieldWells": {
                "BarChartAggregatedFieldWells": {
                  "Category": [
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d103",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Department"
                        }
                      }
                    }
                  ],
                  "Values": [
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m104",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Messages"
                        }
                      }
                    }
                  ],
                  "Colors": [
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d105",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Message scope"
                        }
                      }
                    }
                  ]
                }
              },
              "SortConfiguration": {
                "CategorySort": [
                  {
                    "FieldSort": {
                      "FieldId": "m104",
                      "Direction": "DESC"
                    }
                  }
                ]
              },
              "Orientation": "HORIZONTAL",
              "BarsArrangement": "STACKED_PERCENT",
              "CategoryLabelOptions": {
                "Visibility": "HIDDEN"
              },
              "ValueLabelOptions": {
                "Visibility": "HIDDEN"
              },
              "Legend": {
                "Visibility": "VISIBLE",
                "Title": {
                  "Visibility": "HIDDEN"
                },
                "Position": "BOTTOM"
              },
              "DataLabels": {
                "Visibility": "HIDDEN",
                "Overlap": "DISABLE_OVERLAP"
              }
            },
            "Actions": [],
            "ColumnHierarchies": []
          }
        }
      ],
      "Elements": [
        {
          "ElementId": "s5-c-org-div",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 0,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s5-c-org-dept",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 9,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s5-c-org-cc",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 18,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s5-c-org-user",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 27,
          "ColumnSpan": 9,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s5-v3",
          "ElementType": "VISUAL",
          "ColumnIndex": 0,
          "ColumnSpan": 18,
          "RowIndex": 28,
          "RowSpan": 14,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s5-v4",
          "ElementType": "VISUAL",
          "ColumnIndex": 18,
          "ColumnSpan": 18,
          "RowIndex": 28,
          "RowSpan": 14,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        }
      ]
    },
    {
      "SheetId": "s7",
      "InsertRows": {
        "RowIndex": 6,
        "RowSpan": 3
      },
      "FilterControls": [
        {
          "CrossSheet": {
            "FilterControlId": "s7-c-org-div",
            "SourceFilterId": "f-org-div"
          }
        },
        {
          "CrossSheet": {
            "FilterControlId": "s7-c-org-dept",
            "SourceFilterId": "f-org-dept",
            "CascadingControlConfiguration": {
              "SourceControls": [
                {
                  "SourceSheetControlId": "s7-c-org-div",
                  "ColumnToMatch": {
                    "DataSetIdentifier": "chat",
                    "ColumnName": "org_division"
                  }
                }
              ]
            }
          }
        }
      ],
      "Visuals": [
        {
          "InsightVisual": {
            "VisualId": "s7-v1",
            "Title": {
              "Visibility": "VISIBLE",
              "FormatText": {
                "RichText": "<visual-title>\n  <b>Top departments this month</b>\n</visual-title>"
              }
            },
            "Subtitle": {
              "Visibility": "HIDDEN"
            },
            "InsightConfiguration": {
              "Computations": [
                {
                  "TopBottomRanked": {
                    "ComputationId": "s7-topd",
                    "Name": "TopDept",
                    "Category": {
                      "CategoricalDimensionField": {
                        "FieldId": "d151",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Department"
                        }
                      }
                    },
                    "Value": {
                      "NumericalMeasureField": {
                        "FieldId": "m152",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Messages"
                        }
                      }
                    },
                    "ResultSize": 3,
                    "Type": "TOP"
                  }
                }
              ],
              "CustomNarrative": {
                "Narrative": "<narrative>\n  Departments sending the most chat messages:\n  <br/>\n  <for level=\"block\" listExpression=\"TopDept.items\">\n    <ul>\n      <li>\n        <inline color=\"Accent\">\n          <b>\n            <expression>TopDept.items[index].categoryValue.formattedValue</expression>\n          </b>\n        </inline>\n        \u00a0with\u00a0\n        <expression>TopDept.items[index].metricValue.formattedValue</expression>\n      </li>\n    </ul>\n  </for>\n</narrative>"
              }
            },
            "Actions": [],
            "DataSetIdentifier": "chat"
          }
        },
        {
          "PivotTableVisual": {
            "VisualId": "s7-v3",
            "Title": {
              "Visibility": "VISIBLE",
              "FormatText": {
                "RichText": "<visual-title>\n  <b>Quick usage by department</b>\n</visual-title>"
              }
            },
            "Subtitle": {
              "Visibility": "HIDDEN"
            },
            "ChartConfiguration": {
              "FieldWells": {
                "PivotTableAggregatedFieldWells": {
                  "Rows": [
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d155",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Division"
                        }
                      }
                    },
                    {
                      "CategoricalDimensionField": {
                        "FieldId": "d156",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Department"
                        }
                      }
                    }
                  ],
                  "Columns": [],
                  "Values": [
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m157",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Active users"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m158",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Messages"
                        }
                      }
                    },
                    {
                      "NumericalMeasureField": {
                        "FieldId": "m159",
                        "Column": {
                          "DataSetIdentifier": "chat",
                          "ColumnName": "Success %"
                        }
                      }
                    }
                  ]
                }
              },
              "SortConfiguration": {
                "FieldSortOptions": [
                  {
                    "FieldId": "d155",
                    "SortBy": {
                      "Field": {
                        "FieldId": "m157",
                        "Direction": "DESC"
                      }
                    }
                  }
                ]
              },
              "TableOptions": {
                "ColumnHeaderStyle": {
                  "FontConfiguration": {
                    "FontColor": "#FFFFFF",
                    "FontWeight": {
                      "Name": "BOLD"
                    }
                  },
                  "BackgroundColor": "#2E4A62"
                },
                "RowHeaderStyle": {
                  "FontConfiguration": {
                    "FontWeight": {
                      "Name": "BOLD"
                    }
                  }
                },
                "CellStyle": {},
                "RowFieldNamesStyle": {
                  "FontConfiguration": {
                    "FontColor": "#FFFFFF",
                    "FontWeight": {
                      "Name": "BOLD"
                    }
                  },
                  "BackgroundColor": "#2E4A62"
                },
                "RowsLayout": "HIERARCHY"
              },
              "TotalOptions": {
                "RowSubtotalOptions": {
                  "TotalsVisibility": "VISIBLE",
                  "FieldLevel": "ALL",
                  "TotalCellStyle": {},
                  "ValueCellStyle": {},
                  "MetricHeaderCellStyle": {},
                  "StyleTargets": [
                    {
                      "CellType": "VALUE"
                    },
                    {
                      "CellType": "TOTAL"
                    },
                    {
                      "CellType": "METRIC_HEADER"
                    }
                  ]
                },
                "ColumnTotalOptions": {
                  "TotalsVisibility": "VISIBLE",
                  "Placement": "END"
                }
              },
              "FieldOptions": {
                "SelectedFieldOptions": [
                  {
                    "FieldId": "d155",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "d156",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "m157",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "m158",
                    "Visibility": "VISIBLE"
                  },
                  {
                    "FieldId": "m159",
                    "Visibility": "VISIBLE"
                  }
                ]
              }
            },
            "Actions": []
          }
        }
      ],
      "Elements": [
        {
          "ElementId": "s7-c-org-div",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 0,
          "ColumnSpan": 18,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s7-c-org-dept",
          "ElementType": "FILTER_CONTROL",
          "ColumnIndex": 18,
          "ColumnSpan": 18,
          "RowIndex": 6,
          "RowSpan": 3,
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s7-v1",
          "ElementType": "VISUAL",
          "ColumnIndex": 0,
          "ColumnSpan": 18,
          "RowIndex": 16,
          "RowSpan": 8,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s7-v2",
          "ElementType": "VISUAL",
          "ColumnIndex": 18,
          "ColumnSpan": 18,
          "RowIndex": 16,
          "RowSpan": 8,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s7-v3",
          "ElementType": "VISUAL",
          "ColumnIndex": 0,
          "ColumnSpan": 18,
          "RowIndex": 24,
          "RowSpan": 14,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        },
        {
          "ElementId": "s7-v4",
          "ElementType": "VISUAL",
          "ColumnIndex": 18,
          "ColumnSpan": 18,
          "RowIndex": 24,
          "RowSpan": 14,
          "BorderRadius": "8px",
          "BackgroundStyle": {
            "Visibility": "VISIBLE",
            "Color": "#FFFFFF"
          },
          "BorderStyle": {
            "Visibility": "HIDDEN"
          }
        }
      ]
    }
  ]
}
"""


def verify_org_prerequisites(setup, log):
    """quick_usage_org.py: check (read-only) only what the org part uses, not every setup step.

    It needs the Quick pre-checks and at least one usage dataset to join. The bucket was checked
    when the settings were worked out. Setup gaps the org part doesn't use, such as a missing
    log expiry rule, don't stop it.
    """
    print("\nChecking what the org part needs (read-only):")
    setup.run_precheck()
    print("  Pre-checks: passed")
    state = setup.check_datasets()
    log.write(
        event="prerequisite_check",
        step="Org",
        step_name="Usage datasets",
        done=state.done,
        unknown=state.unknown,
        details=state.lines,
    )
    for line in state.lines:
        print(f"  {line}")
    if all(d in state.todo for d in setup.selected):
        raise Abort(
            "None of the usage datasets exist yet, so there is nothing to join the org data to. "
            "Run quick_usage_datasets_setup.py first, then run this again."
        )
    if state.todo:
        print("  The org part skips the usage datasets that don't exist.")


def run_org_dataset(setup, log, args):
    """The org dataset, joined datasets and org dashboard. Returns False if the customer stopped at the prompt."""
    name = "Org dataset"
    print(f"\n=== {name} ===")
    for line in setup.plan_org():
        print(f"  {line}")
    print("\n  Reading IAM Identity Center users (read-only)...", flush=True)
    identity_store_id = setup.org_identity_store_id()
    rows = org_rows_for_identity_center_users(
        setup.org_identity_center_users(identity_store_id),
        setup.quick_user_arn_prefix(),
    )
    for line in setup.org_column_fill_lines(rows):
        print(f"  {line}")
    log.write(
        event="org_users_read", identity_store_id=identity_store_id, users=len(rows)
    )
    if args.dry_run:
        return True
    if not confirm(log, "Org", name, "LOAD ORG DATASET"):
        return False
    setup.run_org(rows)
    log.write(event="step_done", step="Org", step_name=name)
    run_org_dashboard(setup, log)
    return True


def run_org_dashboard(setup, log):
    """The org dashboard, after the org-joined datasets. Asks only to update a previous one."""
    name = "Org dashboard"
    print(f"\n=== {name} ===")
    problem = setup.org_dashboard_problem()
    if problem:
        print(f"  Skipped. {problem}")
        log.write(event="org_dashboard_skipped", reason=problem)
        return
    outdated = setup.check_org_dashboard()
    if outdated:
        for line in setup.plan_org_dashboard_update(outdated):
            print(f"  {line}")
        if (
            confirm(log, "Org", name, "UPDATE ORG DASHBOARD", allow_skip=True)
            is not True
        ):
            print("  Kept. Your current org analysis and dashboard stay as they are.")
            log.write(event="org_dashboard_update_skipped")
            outdated = {}
    setup.run_org_dashboard(outdated)
    log.write(event="step_done", step="Org", step_name=name)


class Setup:
    def __init__(self, args, session, identity, log):
        self.args = args
        self.region = args.region
        self.account = identity["Account"]
        self.bucket = args.bucket
        self.log = log
        self.session = session
        self.s3 = session.client("s3", region_name=self.region, config=BOTO_CONFIG)
        self.logs = session.client("logs", region_name=self.region, config=BOTO_CONFIG)
        self.qs = session.client(
            "quicksight", region_name=self.region, config=BOTO_CONFIG
        )
        # botocore's standard retries treat LimitExceededException (no SPICE capacity) as a
        # throttle and retry it for ~75s. Org dataset create/update use this client to fail at once.
        self.qs_single_try = session.client(
            "quicksight",
            region_name=self.region,
            config=BOTO_CONFIG.merge(
                Config(retries={"mode": "standard", "max_attempts": 1})
            ),
        )
        self.iam = session.client("iam", config=BOTO_CONFIG)
        self.glue = session.client("glue", region_name=self.region, config=BOTO_CONFIG)
        self.athena = session.client(
            "athena", region_name=self.region, config=BOTO_CONFIG
        )
        self.partition = identity["Arn"].split(":")[1]
        self.resource_arn = (
            f"arn:{self.partition}:quicksight:{self.region}:"
            f"{self.account}:account/{self.account}"
        )
        self.selected = args.datasets

    def source_name(self, name):
        return f"quick-usage-{name}"

    def bucket_exists(self):
        try:
            self.s3.head_bucket(Bucket=self.bucket, ExpectedBucketOwner=self.account)
            return True
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("404", "NoSuchBucket"):
                return False
            if code in ("403", "AccessDenied"):
                raise Abort(
                    f"Bucket {self.bucket} exists but isn't owned by account {self.account}, "
                    "or you can't access it.\n  Bucket names are global. Re-run with "
                    "--bucket <another name>."
                ) from e
            raise

    def get_or_none(self, fn, key, **kwargs):
        """Describe/get call that returns None instead of raising when the resource is missing."""
        try:
            return fn(**kwargs)[key]
        except ClientError as e:
            if e.response["Error"]["Code"] in (
                "ResourceNotFoundException",
                "NoSuchKey",
                "404",
            ):
                return None
            raise

    # ---------- owner of the Quick assets (checked before any step runs) ----------
    def admin_users(self):
        """Quick users in the default namespace with an admin role, as (arn, name, role).

        ListUsers doesn't filter by role, so this reads every user in the namespace, 100 per call.
        In accounts with thousands of users that takes a while, so it prints progress.
        """
        print("  Looking for Quick admins to own the datasets. To find them,")
        print(
            "  the script reads every Quick user in the account (100 per call). In accounts"
        )
        print(
            "  with many users this takes a minute or more. To skip it next time, pass"
        )
        print("  --quick-admin-arn with the owner's Quick user ARN.", flush=True)
        found, seen = [], 0
        try:
            for page in self.qs.get_paginator("list_users").paginate(
                AwsAccountId=self.account,
                Namespace="default",
                PaginationConfig={"PageSize": 100},
            ):
                for u in page["UserList"]:
                    if u.get("Role", "").startswith("ADMIN") and u.get("Active", True):
                        found.append((u["Arn"], u["UserName"], u["Role"]))
                seen += len(page["UserList"])
                progress(
                    f"    {seen} Quick users checked, {len(found)} admins found so far..."
                )
        except ClientError as e:
            self.raise_quick_error(e)
        progress(
            f"    Done: {seen} Quick users checked, {len(found)} admins found.",
            done=True,
        )
        self.log.write(event="admin_scan", users_checked=seen, admins_found=len(found))
        return found

    def quick_call(self, fn, **kwargs):
        """Call a Quick user API; turn known setup problems into a clear stop."""
        try:
            return fn(AwsAccountId=self.account, **kwargs)
        except ClientError as e:
            self.raise_quick_error(e)

    def raise_quick_error(self, e):
        """Stop with a clear message for errors a re-run with other settings fixes; else re-raise."""
        message = e.response["Error"].get("Message", "")
        if "identity region" in message.lower():
            raise Abort(
                f"Quick can't manage users from {self.region}. Your Quick account was "
                f"created in another region (its 'identity region').\n  Re-run with "
                f"--region <that region>. Quick says: {message}"
            )
        if e.response["Error"]["Code"] in ("AccessDeniedException", "AccessDenied"):
            raise Abort(
                f"Your AWS identity isn't allowed to call quicksight:{e.operation_name}, which "
                "the script needs to find the Quick admin who will own the datasets.\n  Re-run "
                f"with an identity that has Quick permissions. Quick says: {message}"
            )
        raise e

    def describe_quick_user(self, namespace, user_name):
        """The Quick user, or None if there is none by that name.

        A user that doesn't exist can come back as ResourceNotFoundException or
        InvalidParameterValueException (for example an IAM-style 'Role/Session' name in an
        Active Directory account). Both mean no such user, as does a name botocore rejects
        before calling Quick, such as an empty one.
        """
        if not namespace or not user_name:
            return None
        try:
            return self.quick_call(
                self.qs.describe_user, Namespace=namespace, UserName=user_name
            )["User"]
        except ClientError as e:
            if e.response["Error"]["Code"] in (
                "ResourceNotFoundException",
                "InvalidParameterValueException",
            ):
                return None
            raise
        except ParamValidationError:
            return None

    def caller_quick_user_name(self, caller_arn):
        """The Quick user name an IAM caller signs in as: 'Role/Session' or 'UserName'."""
        parts = caller_arn.split(":", 5)
        if len(parts) < 6:
            return None
        kind, _, rest = parts[5].partition("/")
        if kind == "assumed-role":
            return rest  # RoleName/SessionName
        if kind == "user":
            return rest.rsplit("/", 1)[-1]  # IAM user name, without its path
        return None

    def parse_quick_user(self, text):
        """(namespace, user name) from a Quick user ARN or a bare user name."""
        marker = ":user/"
        if marker in text:
            namespace, _, user_name = text.split(marker, 1)[1].partition("/")
            return namespace, user_name
        return "default", text

    def resolve_admin(self, caller_arn):
        """Pick the Quick user who will own the datasets. Returns (arn, role, source)."""
        arn = self.args.quick_admin_arn
        if arn:
            marker = ":user/"
            if marker not in arn:
                raise Abort(
                    f"--quick-admin-arn does not look like a Quick user ARN: {arn}"
                )
            namespace, _, user_name = arn.split(marker, 1)[1].partition("/")
            print(
                f"  Checking the Quick user in --quick-admin-arn ({user_name})...",
                flush=True,
            )
            user = self.describe_quick_user(namespace, user_name)
            if not user:
                raise Abort(
                    f"No Quick user matches --quick-admin-arn {arn} in this account."
                )
            if not user.get("Active", True):
                raise Abort(
                    f"The Quick user in --quick-admin-arn ({user_name}) isn't active yet, so it "
                    "can't own the datasets.\n  Have them sign in to Quick once, or pass another "
                    "admin."
                )
            role = user.get("Role", "")
            if not role.startswith("ADMIN"):
                raise Abort(
                    f"The Quick user in --quick-admin-arn ({user_name}) has role {role or 'unknown'}, "
                    "not ADMIN.\n  Only a Quick admin can own the datasets. Pass an admin's ARN."
                )
            # Quick's own ARN for the user in this account, not the text that was passed in.
            return user.get("Arn") or arn, role, "from --quick-admin-arn"

        # 1. The Quick user you are signed in as, if it is an active admin.
        found = self.caller_admin(caller_arn)
        if found:
            return found

        # 2. Instead of scanning every Quick user, ask who should own the datasets.
        return self.ask_admin()

    def caller_admin(self, caller_arn):
        """(arn, role, source) if the caller is an active Quick admin, else None."""
        try:
            name = self.caller_quick_user_name(caller_arn)
            user = self.describe_quick_user("default", name) if name else None
            if (
                user
                and user.get("Role", "").startswith("ADMIN")
                and user.get("Active", True)
            ):
                return user["Arn"], user["Role"], "that's you"
        except Exception as e:  # noqa: BLE001 - a failed lookup never stops the run
            self.log.write(event="caller_admin_check_failed", error=repr(e))
        return None

    def ask_admin(self):
        """Ask for the dataset owner by Quick user name or ARN; SEARCH lists every admin."""
        print("\nWhich Quick admin should own the datasets?")
        print(
            "  Type the admin's Quick user name or Quick user ARN. You can find user names"
        )
        print(
            "  in Quick under Manage account > Manage users. In Identity Center accounts the"
        )
        print("  user name is usually the person's email.")
        print(
            f"  Or type {SEARCH_WORD} to have the script find the admins for you. This reads"
        )
        print(
            "  every Quick user in the account, so it can take several minutes depending on"
        )
        print("  how many Quick users you have.")
        print(f"  (Type '{STOP_WORD}' to stop.)")
        while True:
            try:
                answer = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                raise Abort(
                    "Stopped while choosing the dataset owner. To skip this question, re-run "
                    "with --quick-admin-arn."
                ) from None
            if not answer:
                continue
            if answer.lower() == STOP_WORD:
                raise Abort("Stopped while choosing the dataset owner.")
            if answer.upper() == SEARCH_WORD:
                return self.search_admin()
            namespace, user_name = self.parse_quick_user(answer)
            user = self.describe_quick_user(namespace, user_name)
            if not user:
                print(
                    f"  No Quick user named '{user_name}' in this account. Try again, or "
                    f"type {SEARCH_WORD}."
                )
                continue
            role = user.get("Role", "")
            if not role.startswith("ADMIN"):
                print(
                    f"  {user_name} is a Quick {role or 'user'}, not an admin. Type an "
                    f"admin, or {SEARCH_WORD}."
                )
                continue
            if not user.get("Active", True):
                print(
                    f"  {user_name} hasn't signed in to Quick yet, so it can't own the "
                    f"datasets. Type another admin, or {SEARCH_WORD}."
                )
                continue
            self.log.write(event="admin_typed", arn=user["Arn"], typed=answer)
            return user["Arn"], role, "typed by you"

    def search_admin(self):
        """List every admin: the only one is used, otherwise the customer picks one."""
        admins = self.admin_users()
        if not admins:
            raise Abort(
                f"This Quick account has no admin users, and step {DATASETS_STEP} needs one to own the "
                "datasets.\n  Add an admin in Quick (Manage account > Manage users), "
                "then re-run."
            )
        if len(admins) == 1:
            a, _, role = admins[0]
            return a, role, "the only Quick admin in this account"
        return (*self.choose_admin(admins), "chosen by you")

    def choose_admin(self, admins):
        print(
            "\nThis Quick account has several admins. Which one should own the datasets?"
        )
        for i, (_arn, user_name, role) in enumerate(admins, start=1):
            print(f"  {i}. {user_name} ({role})")
        print(f"  (Type a number, or '{STOP_WORD}' to stop.)")
        while True:
            try:
                answer = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                raise Abort("Stopped while choosing the dataset owner.") from None
            if answer.lower() == STOP_WORD:
                raise Abort("Stopped while choosing the dataset owner.")
            if answer.isdigit() and 1 <= int(answer) <= len(admins):
                a, _, role = admins[int(answer) - 1]
                self.log.write(event="admin_chosen", arn=a, typed=answer)
                return a, role
            print(f"  Type a number from 1 to {len(admins)}.")

    def resolve_bucket(self):
        """Bucket name: --bucket, else the one an earlier run delivers to, else our naming rule."""
        if self.args.bucket:
            return self.args.bucket, "from --bucket"
        for name in self.selected:
            try:
                dest = self.logs.get_delivery_destination(name=self.source_name(name))
            except ClientError as e:
                if e.response["Error"]["Code"] == "ResourceNotFoundException":
                    continue
                raise
            target = dest["deliveryDestination"]["deliveryDestinationConfiguration"][
                "destinationResourceArn"
            ]
            if target.startswith("arn:") and ":s3:::" in target:
                return (
                    target.split(":s3:::", 1)[1].split("/", 1)[0],
                    "used by your earlier setup",
                )
        return (
            f"quick-usage-{self.account}-{region_code(self.region)}",
            "default name",
        )

    def bucket_state(self):
        try:
            return (
                "exists in this account" if self.bucket_exists() else "will be created"
            )
        except ClientError as e:
            return f"can't check: {e.response['Error']['Code']}"

    def run_precheck(self, state=None):
        print("  Checking the Quick subscription...", flush=True)
        sub = self.qs.describe_account_subscription(AwsAccountId=self.account)
        status = sub["AccountInfo"].get("AccountSubscriptionStatus")
        edition = sub["AccountInfo"].get("Edition")
        auth_type = sub["AccountInfo"].get("AuthenticationType")
        print(
            f"  Quick subscription status: {status}, edition: {edition}, sign-in: {auth_type}"
        )
        if status != "ACCOUNT_CREATED":
            raise Abort(f"Quick subscription is {status}; it must be ACCOUNT_CREATED.")
        if edition and "ENTERPRISE" not in edition and "PROFESSIONAL" not in edition:
            print(
                f"  WARNING: AWS documents vended logs for Enterprise or Professional; this is {edition}."
            )
        print(f"  Bucket exists: {self.bucket_exists()}")
        print("  Checking which log types Quick offers here...", flush=True)
        self.check_log_types_offered()
        print("  Checking for existing delivery sources...", flush=True)
        existing = self.existing_sources()
        for log_type, name in existing.items():
            print(f"  Existing delivery source for {log_type}: {name}")
        conflicts = {
            lt: n
            for lt, n in existing.items()
            if lt in self.selected_log_types()
            and n != self.source_name(self.dataset_of(lt))
        }
        if conflicts:
            raise Abort(
                "Delivery sources already exist under other names (only one is allowed "
                f"per log type): {conflicts}. Remove them or skip those datasets."
            )

    def selected_log_types(self):
        return {DATASETS[d][0] for d in self.selected}

    def check_log_types_offered(self):
        """Warn early if CloudWatch Logs doesn't list a usage log type for Quick in this region.

        A warning, not a stop: the list may not show every supported log type yet. If a
        log type really isn't available, step 3 stops with CloudWatch Logs' own error.
        """
        try:
            offered = {
                t.get("logType")
                for page in self.logs.get_paginator(
                    "describe_configuration_templates"
                ).paginate(service="quicksight", deliveryDestinationTypes=["S3"])
                for t in page.get("configurationTemplates", [])
            }
        except ClientError as e:
            print(
                "  Couldn't list the log types Quick offers here "
                f"({e.response['Error']['Code']}); step 3 will tell if one is missing."
            )
            self.log.write(
                event="log_types_unchecked", error=e.response["Error"]["Code"]
            )
            return
        missing = sorted(self.selected_log_types() - offered)
        self.log.write(
            event="log_types_offered",
            offered=sorted(t for t in offered if t),
            missing=missing,
        )
        if missing:
            print(
                f"  WARNING: CloudWatch Logs doesn't list {', '.join(missing)} for Quick in "
                f"{self.region}.\n           The usage log types may not be available in this "
                "region yet. If so, step 3 stops\n           before anything is delivered."
            )
        else:
            print(
                f"  Usage log types offered in {self.region}: {', '.join(sorted(self.selected_log_types()))}"
            )

    def dataset_of(self, log_type):
        return next(d for d, (lt, _) in DATASETS.items() if lt == log_type)

    def existing_sources(self):
        found = {}
        for page in self.logs.get_paginator("describe_delivery_sources").paginate():
            for src in page["deliverySources"]:
                if self.resource_arn in src.get("resourceArns", []):
                    found[src["logType"]] = src["name"]
        return found

    # With default access Deny, a data source needs its own read access to the bucket.
    # Step 6 therefore creates each data source with S3Parameters.RoleArn set to a role that
    # reads only this bucket's usage logs. With a RoleArn, Quick reads S3 through that role and
    # ignores the account-wide role and IAM policy assignments for that data source, so creation
    # works under Deny whoever runs the script.
    # The owner's IAM policy assignment is kept so the owner can use this bucket in the Quick
    # console under Deny.
    def data_source_role_name(self):
        name = f"QuickUsageDataSourceRead-{self.bucket}"
        if len(name) <= 64:  # IAM role name limit
            return name
        digest = hashlib.sha256(self.bucket.encode()).hexdigest()[:16]
        return f"QuickUsageDataSourceRead-{digest}"

    def data_source_role_arn(self):
        return f"arn:{self.partition}:iam::{self.account}:role/{self.data_source_role_name()}"

    def data_source_role_trust(self):
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "quicksight.amazonaws.com"},
                    "Action": ["sts:AssumeRole", "sts:TagSession"],
                    "Condition": {"StringEquals": {"aws:SourceAccount": self.account}},
                }
            ],
        }

    def is_our_data_source_role(self, role):
        """Trusted only by Quick: the trust policy names quicksight.amazonaws.com and nothing else."""
        doc = role.get("AssumeRolePolicyDocument") or {}
        if isinstance(doc, str):
            doc = json.loads(urllib.parse.unquote(doc))
        principals = set()
        for st in doc.get("Statement", []):
            if st.get("Effect") != "Allow":
                continue
            for kind, value in (st.get("Principal") or {}).items():
                for v in value if isinstance(value, list) else [value]:
                    principals.add((kind, v))
        return principals == {("Service", "quicksight.amazonaws.com")}

    def dataset_state(self, d):
        """Parts of d's Quick setup that are missing. Stops on a conflicting or broken asset."""
        ds_id = f"quick-usage-{d}"
        missing = []
        src = self.get_or_none(
            self.qs.describe_data_source,
            "DataSource",
            AwsAccountId=self.account,
            DataSourceId=ds_id,
        )
        if src is None:
            missing.append("data source")
        else:
            status = src.get("Status", "")
            if status.endswith("IN_PROGRESS"):
                raise Abort(
                    f"Quick data source {ds_id} is still being created or updated ({status}). "
                    "Wait a minute, then re-run."
                )
            if status.endswith("FAILED"):
                raise Abort(
                    f"Quick data source {ds_id} is in state {status}. Delete it, then re-run:\n"
                    f"    aws quicksight delete-data-source --aws-account-id {self.account} "
                    f"--data-source-id {ds_id} --region {self.region}"
                )
            params = src.get("DataSourceParameters", {})
            if "AthenaParameters" not in params:
                # An S3 manifest data source. UpdateDataSource doesn't change the data source
                # type, so the customer deletes it and its dataset; the Athena pair then reads the
                # same logs already in the bucket.
                raise Abort(
                    f"Quick data source {ds_id} is an S3 (manifest) data source. This script "
                    "reads the logs through Athena instead, and an existing data source "
                    "keeps its type.\n  Delete the data source and its dataset, then re-run (the new Athena "
                    "dataset reads the same logs already in your bucket):\n"
                    f"    aws quicksight delete-data-set --aws-account-id {self.account} "
                    f"--data-set-id {ds_id} --region {self.region}\n"
                    f"    aws quicksight delete-data-source --aws-account-id {self.account} "
                    f"--data-source-id {ds_id} --region {self.region}",
                    resume_step=DATASETS_STEP,
                )
            athena = params["AthenaParameters"]
            role_arn = athena.get("RoleArn")
            if (
                athena.get("WorkGroup") != ATHENA_WORKGROUP
                or role_arn != self.data_source_role_arn()
            ):
                raise Abort(
                    f"Quick data source {ds_id} already exists but uses workgroup "
                    f"{athena.get('WorkGroup')} / role {role_arn},\n  not {ATHENA_WORKGROUP} / "
                    f"{self.data_source_role_arn()}. The script won't change it. Delete the data "
                    "source and its dataset, then re-run."
                )
        data_set = self.get_or_none(
            self.qs.describe_data_set,
            "DataSet",
            AwsAccountId=self.account,
            DataSetId=ds_id,
        )
        if data_set is None:
            missing.append("dataset")
        else:
            expected = {f if isinstance(f, str) else f[0] for f in DATASETS[d][1]}
            have = {c["Name"] for c in data_set.get("OutputColumns", [])}
            if have and not expected <= have:
                raise Abort(
                    f"Quick dataset {ds_id} already exists but doesn't have the columns this "
                    f"script creates\n  (missing: {', '.join(sorted(expected - have))}"
                    "). The script won't change it.\n"
                    "  Delete it (and the starter analysis/dashboard if you have them), then re-run:\n"
                    f"    aws quicksight delete-data-set --aws-account-id {self.account} "
                    f"--data-set-id {ds_id} --region {self.region}"
                )
        return missing, src

    def wait_for_data_source(self, ds_id, timeout=120, failure_hint=None):
        """Data source creation is asynchronous; wait until it settles before using it."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.qs.describe_data_source(
                AwsAccountId=self.account, DataSourceId=ds_id
            )["DataSource"].get("Status", "")
            if not status.endswith("IN_PROGRESS"):
                if status.endswith("FAILED"):
                    raise Abort(
                        f"Quick couldn't create data source {ds_id} ({status}). "
                        + (
                            failure_hint
                            or (
                                f"Check that IAM role {self.data_source_role_name()} can assume and "
                                f"run Athena over s3://{self.bucket}/{PREFIX_ROOT}/, then re-run."
                            )
                        )
                    )
                return
            if time.monotonic() > deadline:
                raise Abort(
                    f"Data source {ds_id} is still {status} after {timeout}s. Re-run later."
                )
            time.sleep(3)

    def check_datasets(self):
        todo, lines = {}, []
        for d in self.selected:
            missing, _ = self.dataset_state(d)
            if missing:
                todo[d] = missing
                lines.append(
                    f"quick-usage-{d}: not set up yet (missing: {', '.join(missing)})"
                )
            else:
                lines.append(f"quick-usage-{d}: Athena data source and dataset exist")
        return StepState(not todo, lines, todo)

    def quick_link(self, kind, asset_id):
        return link(
            f"https://{self.region}.quicksight.aws.amazon.com/sn/{kind}/{asset_id}"
        )

    def asset_state(self, kind, asset_id):
        """None if missing, else the status. Stops on a failed or still-running asset."""
        if kind == "analysis":
            asset = self.get_or_none(
                self.qs.describe_analysis,
                "Analysis",
                AwsAccountId=self.account,
                AnalysisId=asset_id,
            )
            status = asset and asset.get("Status", "")
            delete = f"delete-analysis --analysis-id {asset_id} --force-delete-without-recovery"
        else:
            asset = self.get_or_none(
                self.qs.describe_dashboard,
                "Dashboard",
                AwsAccountId=self.account,
                DashboardId=asset_id,
            )
            status = asset and asset.get("Version", {}).get("Status", "")
            delete = f"delete-dashboard --dashboard-id {asset_id}"
        if asset is None:
            return None
        if status == "DELETED":
            raise Abort(
                f"Quick {kind} {asset_id} was deleted and is in Quick's recovery window, so its ID "
                "can't be reused yet. Restore it in Quick, or delete it for good, then re-run:\n"
                f"    aws quicksight {delete} --aws-account-id {self.account} "
                f"--region {self.region}"
            )
        if status.endswith("IN_PROGRESS"):
            raise Abort(
                f"Quick {kind} {asset_id} is still being created or updated ({status}). "
                "Wait a minute, then re-run."
            )
        if status.endswith("FAILED"):
            raise Abort(
                f"Quick {kind} {asset_id} is in state {status}. Delete it, then re-run:\n"
                f"    aws quicksight {delete} --aws-account-id {self.account} "
                f"--region {self.region}"
            )
        return status

    def asset_arn(self, kind, asset_id):
        return f"arn:{self.partition}:quicksight:{self.region}:{self.account}:{kind}/{asset_id}"

    def asset_version(self, kind, asset_id, version_tag=DASHBOARD_VERSION_TAG):
        """Dashboard version an existing asset was built from, or None if unreadable."""
        try:
            tags = self.qs.list_tags_for_resource(
                ResourceArn=self.asset_arn(kind, asset_id)
            )
        except ClientError as e:
            self.log.write(
                event="dashboard_version_unreadable",
                kind=kind,
                asset_id=asset_id,
                error=e.response["Error"]["Code"],
            )
            return None
        for tag in tags.get("Tags") or []:
            if tag.get("Key") == version_tag:
                try:
                    return int(tag.get("Value"))
                except (TypeError, ValueError):
                    return 1
        return 1  # built before the script tagged versions

    def version_tags(self, spec=STARTER_DASHBOARD):
        return [{"Key": spec.version_tag, "Value": str(spec.version)}]

    def check_assets(self, spec, todo, lines):
        """Add spec's missing or outdated assets to todo and lines. Returns {kind: old version}."""
        outdated = {}
        for kind, asset_id in spec.assets:
            if self.asset_state(kind, asset_id) is None:
                todo[kind] = [asset_id]
                lines.append(f"{kind} {asset_id}: not created yet")
                continue
            version = self.asset_version(kind, asset_id, spec.version_tag)
            if version is None:
                lines.append(
                    f"{kind} {asset_id}: already exists, kept as is (couldn't read its version)"
                )
            elif version < spec.version:
                outdated[kind] = version
                todo.setdefault("update", []).append(asset_id)
                lines.append(
                    f"{kind} {asset_id}: version {version}, "
                    f"newer version {spec.version} available"
                )
            else:
                lines.append(
                    f"{kind} {asset_id}: already exists (version {version}), kept as is"
                )
        return outdated

    def wait_for_asset(
        self, kind, asset_id, version_number=None, resume_at_dashboard_step=True
    ):
        """Creation and updates are asynchronous. Wait, and turn a failed definition into a
        clear stop. version_number waits for that dashboard version (after an update).
        resume_at_dashboard_step=False resumes at the step that was running instead."""
        resume_step = DASHBOARD_STEP if resume_at_dashboard_step else None
        deadline = time.monotonic() + CREATE_TIMEOUT_SECONDS
        while True:
            if kind == "analysis":
                asset = self.qs.describe_analysis(
                    AwsAccountId=self.account, AnalysisId=asset_id
                )["Analysis"]
            else:
                extra = {"VersionNumber": version_number} if version_number else {}
                asset = self.qs.describe_dashboard(
                    AwsAccountId=self.account, DashboardId=asset_id, **extra
                )["Dashboard"]["Version"]
            status = asset.get("Status", "")
            if not status.endswith("IN_PROGRESS"):
                break
            if time.monotonic() > deadline:
                raise Abort(
                    f"Quick {kind} {asset_id} is still {status} after "
                    f"{CREATE_TIMEOUT_SECONDS}s. Re-run later.",
                    resume_step=resume_step,
                )
            time.sleep(3)
        errors = [
            {
                "type": e.get("Type"),
                "message": e.get("Message"),
                "paths": [v.get("Path") for v in e.get("ViolatedEntities") or []],
            }
            for e in asset.get("Errors") or []
        ]
        if errors:
            self.log.write(
                event="asset_errors", kind=kind, asset_id=asset_id, errors=errors
            )
        if status.endswith("FAILED"):
            details = "\n".join(
                f"    - {e['type']}: {e['message']}"
                + "".join(f"\n        at {p}" for p in e["paths"])
                for e in errors
            )
            raise Abort(
                f"Quick couldn't create {kind} {asset_id} ({status}):\n{details or '    (no details)'}"
                "\n  This is a problem in the script's dashboard definition, not in your account."
                "\n  Your datasets are unaffected. Skip this step with --no-dashboard, and report"
                " the errors above.",
                resume_step=resume_step,
            )
        for e in errors:
            print(f"  WARNING ({kind}): {e['type']}: {e['message']}")

    def ensure_assets(self, spec, definition, outdated, resume_at_dashboard_step=True):
        """Create spec's analysis and dashboard, or update the ones in outdated ({kind: old
        version}). An asset that exists and isn't outdated is kept."""
        # No ThemeArn: the dashboard carries its own styling (per-tile background/border and
        # per-visual chart colours baked into DASHBOARD_DEFINITION_JSON); anything not covered by
        # the definition uses the account's default Quick theme.
        common = {
            "AwsAccountId": self.account,
            "Name": spec.name,
            "Definition": definition,
            "ValidationStrategy": {"Mode": "STRICT"},
        }
        for kind, asset_id in spec.assets:
            # Re-check just before creating, so a retry only creates what's still missing.
            if self.asset_state(kind, asset_id) is None:
                self.create_asset(spec, kind, common, resume_at_dashboard_step)
            elif kind in outdated:
                self.update_asset(
                    spec, kind, common, outdated[kind], resume_at_dashboard_step
                )
            else:
                print(f"  {kind} {asset_id} already exists, kept")
        print(
            f"  Open the dashboard: {self.quick_link('dashboards', spec.dashboard_id)}"
        )
        print(
            f"  Edit it in the analysis: {self.quick_link('analyses', spec.analysis_id)}"
        )

    def create_asset(self, spec, kind, common, resume_at_dashboard_step):
        asset_id = dict(spec.assets)[kind]
        create, id_key, actions = {
            "analysis": (self.qs.create_analysis, "AnalysisId", ANALYSIS_ACTIONS),
            "dashboard": (self.qs.create_dashboard, "DashboardId", DASHBOARD_ACTIONS),
        }[kind]
        create(
            **common,
            **{id_key: asset_id},
            Permissions=[{"Principal": self.args.quick_admin_arn, "Actions": actions}],
            Tags=self.version_tags(spec),
        )
        self.wait_for_asset(
            kind, asset_id, resume_at_dashboard_step=resume_at_dashboard_step
        )
        print(f"  {kind} {asset_id} created")
        self.log.write(event="resource", **{kind: asset_id})

    def update_asset(self, spec, kind, common, from_version, resume_at_dashboard_step):
        """Replace the definition in place. A dashboard update is a new version, so publish it."""
        asset_id = dict(spec.assets)[kind]
        published, extra = "", {}
        if kind == "analysis":
            self.qs.update_analysis(**common, AnalysisId=asset_id)
            self.wait_for_asset(
                kind, asset_id, resume_at_dashboard_step=resume_at_dashboard_step
            )
        else:
            version_number = self.update_and_publish_dashboard(
                asset_id, common, resume_at_dashboard_step
            )
            published = f" (Quick dashboard version {version_number}, published)"
            extra = {"quick_version_number": version_number}
        self.qs.tag_resource(
            ResourceArn=self.asset_arn(kind, asset_id), Tags=self.version_tags(spec)
        )
        print(f"  {kind} {asset_id} updated to version {spec.version}{published}")
        self.log.write(
            event="dashboard_updated",
            kind=kind,
            asset_id=asset_id,
            from_version=from_version,
            to_version=spec.version,
            **extra,
        )

    def update_and_publish_dashboard(
        self, dashboard_id, common, resume_at_dashboard_step
    ):
        """Returns the new Quick dashboard version number."""
        response = self.qs.update_dashboard(**common, DashboardId=dashboard_id)
        version_number = int(response["VersionArn"].rsplit("/", 1)[1])
        self.wait_for_asset(
            "dashboard", dashboard_id, version_number, resume_at_dashboard_step
        )
        # An update creates a new dashboard version; viewers see it once it's published.
        self.qs.update_dashboard_published_version(
            AwsAccountId=self.account,
            DashboardId=dashboard_id,
            VersionNumber=version_number,
        )
        return version_number

    # ---------- destroy: undo the setup steps ----------
    # Each part has find_<key>_to_remove (read-only), plan_remove_<key> and remove_<key>.
    # Only things the script creates are removed, by their fixed names. Something with one of
    # those names that points somewhere the script wouldn't have pointed it is kept.
    def delete_if_present(self, fn, **kwargs):
        """Delete call that treats 'already gone' as success. Returns True if it deleted."""
        try:
            fn(**kwargs)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in (
                "ResourceNotFoundException",
                "NoSuchKey",
                "404",
            ):
                return False
            raise

    def find_assets_to_remove(self, spec, nothing_line):
        todo, lines = {}, []
        if self.get_or_none(
            self.qs.describe_dashboard,
            "Dashboard",
            AwsAccountId=self.account,
            DashboardId=spec.dashboard_id,
        ):
            todo["dashboard"] = [spec.dashboard_id]
            lines.append(f"dashboard {spec.dashboard_id}")
        analysis = self.get_or_none(
            self.qs.describe_analysis,
            "Analysis",
            AwsAccountId=self.account,
            AnalysisId=spec.analysis_id,
        )
        if analysis is not None:
            todo["analysis"] = [spec.analysis_id]
            note = (
                " (deleted, still in Quick's recovery window)"
                if analysis.get("Status") == "DELETED"
                else ""
            )
            lines.append(f"analysis {spec.analysis_id}{note}")
        if not todo:
            lines.append(nothing_line)
        return StepState(not todo, lines, todo)

    def remove_assets(self, spec, state):
        if "dashboard" in state.todo:
            self.delete_if_present(
                self.qs.delete_dashboard,
                AwsAccountId=self.account,
                DashboardId=spec.dashboard_id,
            )
            print(f"  dashboard {spec.dashboard_id} deleted")
            self.log.write(event="resource_deleted", dashboard=spec.dashboard_id)
        if "analysis" in state.todo:
            self.delete_if_present(
                self.qs.delete_analysis,
                AwsAccountId=self.account,
                AnalysisId=spec.analysis_id,
                ForceDeleteWithoutRecovery=True,
            )
            print(f"  analysis {spec.analysis_id} deleted")
            self.log.write(event="resource_deleted", analysis=spec.analysis_id)

    # ---------- optional org dataset of Identity Center users ----------
    def org_role_name(self):
        name = f"QuickOrgDataSourceRead-{self.bucket}"
        if len(name) <= 64:  # IAM role name limit
            return name
        digest = hashlib.sha256(self.bucket.encode()).hexdigest()[:16]
        return f"QuickOrgDataSourceRead-{digest}"

    def org_role_arn(self):
        return f"arn:{self.partition}:iam::{self.account}:role/{self.org_role_name()}"

    def org_role_policy_document(self):
        """Reads only the org prefix: the CSV and its manifest. Nothing under PREFIX_ROOT."""
        bucket_arn = f"arn:{self.partition}:s3:::{self.bucket}"
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ReadOrgDatasetFiles",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"{bucket_arn}/{ORG_PREFIX}/*"],
                },
                {
                    "Sid": "ListOrgDatasetPrefix",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": [bucket_arn],
                    "Condition": {"StringLike": {"s3:prefix": [f"{ORG_PREFIX}/*"]}},
                },
                {
                    "Sid": "LocateBucket",
                    "Effect": "Allow",
                    "Action": ["s3:GetBucketLocation"],
                    "Resource": [bucket_arn],
                },
                # Quick's S3 checks list bucket names; this shows names only, no contents.
                {
                    "Sid": "ListBucketNames",
                    "Effect": "Allow",
                    "Action": ["s3:ListAllMyBuckets"],
                    "Resource": "*",
                },
            ],
        }

    def org_role(self):
        try:
            return self.iam.get_role(RoleName=self.org_role_name())["Role"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchEntity":
                return None
            raise

    def org_identity_center_region(self):
        return self.args.org_identity_center_region or self.region

    def org_identity_store_id(self):
        """The identity store behind this Quick account: --org-identity-store-id, else looked up."""
        if self.args.org_identity_store_id:
            return self.args.org_identity_store_id
        account_info = self.qs.describe_account_subscription(AwsAccountId=self.account)[
            "AccountInfo"
        ]
        instance_arn = account_info.get("IAMIdentityCenterInstanceArn")
        if not instance_arn:
            raise Abort(
                "This script reads users from IAM Identity Center, but this Quick account doesn't sign in "
                f"through Identity Center (sign-in: {account_info.get('AuthenticationType')})."
            )
        region = self.org_identity_center_region()
        sso_admin = self.session.client(
            "sso-admin", region_name=region, config=BOTO_CONFIG
        )
        for page in sso_admin.get_paginator("list_instances").paginate():
            for instance in page["Instances"]:
                if instance["InstanceArn"] == instance_arn:
                    return instance["IdentityStoreId"]
        raise Abort(
            f"Couldn't find Quick's Identity Center instance ({instance_arn}) from this account in "
            f"{region}.\n  It may be an organization instance in another account, or in another "
            "region. Find its identity store ID (d-xxxxxxxxxx) in the IAM Identity Center console "
            "under Settings, then re-run this script with --org-identity-store-id <id> and, if needed, "
            "--org-identity-center-region <region>."
        )

    def org_identity_center_users(self, identity_store_id):
        """Yields Identity Center users a page at a time, printing progress every 10 pages."""
        region = self.org_identity_center_region()
        identitystore = self.session.client(
            "identitystore", region_name=region, config=BOTO_CONFIG
        )
        pages = identitystore.get_paginator("list_users").paginate(
            IdentityStoreId=identity_store_id,
            Extensions=[IDENTITY_STORE_ENTERPRISE_EXTENSION],
        )
        count = 0
        for number, page in enumerate(pages, start=1):
            count += len(page["Users"])
            if number % 10 == 0:
                print(f"  ...{count} users read so far", flush=True)
            yield page["Users"]

    def plan_org(self):
        return [
            ORG_NO_REFRESH_NOTE,
            "",
            "Builds a SPICE dataset with one row per IAM Identity Center user:",
            "  names, email, phone, job fields (title, department, cost center, manager, ...),",
            "  address and preferences. Attributes that Identity Center doesn't return aren't",
            "  included.",
            "",
            "This is personal data (PII). To keep access to it small:",
            "  - Your identity reads Identity Center users once, now. Nothing reads them again",
            "    unless you re-run this script. No role is given standing access to Identity Center.",
            f"  - The CSV goes to s3://{self.bucket}/{ORG_CSV_KEY} (encrypted), outside",
            f"    {PREFIX_ROOT}/, so the usage datasets' role and policies can't read it.",
            f"  - Quick reads it through IAM role {self.org_role_name()}, which only Quick can",
            f"    use and which reads only {ORG_PREFIX}/.",
            "  - The CSV is deleted as soon as SPICE has loaded it. Only the manifest stays.",
            f"  - Only the dataset owner gets dataset {ORG_DATA_SET_ID}. Share it with care:",
            "    everyone it's shared with sees every column for every user.",
            "",
            "Then, for each usage dataset, a dataset quick-usage-<dataset>-with-org: every usage",
            "record LEFT-joined to its user's job fields (prefixed org_): user name, Quick user",
            "ARN, title, user type, cost center, organization, division, department and manager's",
            "user name. No phone or address. User names are often email addresses. Records with",
            "no Identity Center user keep empty org_ columns. These are also owner-only. Each",
            "refreshes daily, an hour after its usage dataset's own refresh, in the same time zone",
            "(none if the usage dataset has no refresh schedule).",
            "",
            "Every column of these datasets gets a short description, which Quick's",
            "natural-language features use. Re-running replaces their definitions, descriptions",
            "included.",
            "",
            *self.plan_org_dashboard(),
            "The org fields don't refresh on their own: the joined datasets' daily refresh reloads",
            "usage records, but the org fields stay as of this run. To refresh the org data, re-run",
            "this script with the same command. (Refresh now in Quick won't work for the org",
            "dataset itself: the CSV is gone.)",
        ]

    def plan_org_dashboard(self):
        if self.args.no_dashboard:
            return ["No org dashboard: --no-dashboard skips it.", ""]
        return [
            f"Then an analysis {ORG_ANALYSIS_ID} and a dashboard {ORG_DASHBOARD_ID}: the starter",
            "dashboard plus usage by department, division and cost center, and org filters on",
            "every sheet. They read the org-joined chat, chat_feedback, agent_hours and index_usage datasets.",
            "Only the dataset owner gets them. Anyone you share the org dashboard with sees each",
            "user's name, department, division and cost center. It shows no phone or address.",
            "Re-running keeps an existing org dashboard, and asks before updating a previous one.",
            "",
        ]

    def org_column_fill_lines(self, rows):
        lines = [
            f"{len(rows)} Identity Center users. Filled in per column (no values shown):"
        ]
        lines += [
            f"  {c}: {sum(1 for row in rows if row[c])}/{len(rows)}"
            for c in ORG_COLUMNS
        ]
        return lines

    def require_bucket_blocks_public_access(self):
        try:
            block = self.s3.get_public_access_block(Bucket=self.bucket)[
                "PublicAccessBlockConfiguration"
            ]
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchPublicAccessBlockConfiguration":
                raise
            block = {}
        settings = (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
        if not all(block.get(setting) for setting in settings):
            raise Abort(
                f"Bucket {self.bucket} doesn't block all public access, so this script won't write PII "
                "to it.\n  Turn on Block all public access for the bucket, then re-run."
            )

    def ensure_org_role(self):
        role = self.org_role()
        if role is not None and not self.is_our_data_source_role(role):
            raise Abort(
                f"IAM role {self.org_role_name()} already exists but can be assumed by something "
                "other than Quick.\n  The script won't change it. Delete or rename it, then re-run."
            )
        if role is None:
            self.iam.create_role(
                RoleName=self.org_role_name(),
                AssumeRolePolicyDocument=json.dumps(self.data_source_role_trust()),
                Description="Quick reads the org dataset CSV through this role. Created by "
                "quick_usage_org.py.",
            )
        self.iam.put_role_policy(
            RoleName=self.org_role_name(),
            PolicyName=ORG_ROLE_POLICY_NAME,
            PolicyDocument=json.dumps(self.org_role_policy_document()),
        )
        verb = "created" if role is None else "kept; read policy re-applied"
        print(
            f"  IAM role {self.org_role_name()} {verb} (only Quick can use it; reads only {ORG_PREFIX}/)"
        )
        self.log.write(event="resource", org_iam_role=self.org_role_arn())

    def upload_org_files(self, rows):
        encrypted = {
            "ServerSideEncryption": "AES256",
            "ExpectedBucketOwner": self.account,
        }
        # An unnamed temp file (removed when closed, even on a crash), uploaded in parts, so the
        # CSV isn't held in memory as text and again as bytes.
        with tempfile.TemporaryFile("w+", encoding="utf-8", newline="") as csv_file:
            write_org_csv(rows, csv_file)
            csv_file.seek(0)
            self.s3.upload_fileobj(
                csv_file.buffer,
                self.bucket,
                ORG_CSV_KEY,
                ExtraArgs={"ContentType": "text/csv", **encrypted},
            )
        self.s3.put_object(
            Bucket=self.bucket,
            Key=ORG_MANIFEST_KEY,
            Body=json.dumps(org_manifest(self.bucket)).encode(),
            ContentType="application/json",
            **encrypted,
        )
        print(f"  Uploaded s3://{self.bucket}/{ORG_CSV_KEY} and its manifest")

    def org_data_source_parameters(self):
        manifest = {"Bucket": self.bucket, "Key": ORG_MANIFEST_KEY}
        return {
            "S3Parameters": {
                "ManifestFileLocation": manifest,
                "RoleArn": self.org_role_arn(),
            }
        }

    def ensure_org_data_source(self, attempts=4, delay=15):
        """Create or repoint the org data source, retrying while the new role propagates."""
        existing = self.get_or_none(
            self.qs.describe_data_source,
            "DataSource",
            AwsAccountId=self.account,
            DataSourceId=ORG_DATA_SET_ID,
        )
        common = {
            "AwsAccountId": self.account,
            "DataSourceId": ORG_DATA_SET_ID,
            "Name": ORG_DATA_SET_NAME,
            "DataSourceParameters": self.org_data_source_parameters(),
        }

        def create_or_update():
            if existing:
                return self.qs.update_data_source(**common)
            permissions = [
                {"Principal": self.args.quick_admin_arn, "Actions": DATA_SOURCE_ACTIONS}
            ]
            return self.qs.create_data_source(
                Type="S3", Permissions=permissions, **common
            )

        def role_not_ready(error):
            return (
                error["Code"] == "AccessDeniedException"
                and "assume" in error.get("Message", "").lower()
            )

        def on_retry(attempt, error):
            print(
                f"  Quick can't use the new role yet (IAM takes a few seconds); checking again in {delay}s..."
            )

        arn = retry_on_client_error(
            create_or_update, role_not_ready, attempts, delay, on_retry=on_retry
        )["Arn"]
        print(f"  Data source {ORG_DATA_SET_ID} {'updated' if existing else 'created'}")
        self.wait_for_data_source(
            ORG_DATA_SET_ID,
            failure_hint=f"Check that IAM role {self.org_role_name()} can read "
            f"s3://{self.bucket}/{ORG_PREFIX}/, then re-run this script.",
        )
        return arn

    def ensure_org_data_set(self, data_source_arn):
        exists = self.get_or_none(
            self.qs.describe_data_set,
            "DataSet",
            AwsAccountId=self.account,
            DataSetId=ORG_DATA_SET_ID,
        )
        upload_settings = {
            "Format": "CSV",
            "ContainsHeader": True,
            "Delimiter": ",",
            "TextQualifier": "DOUBLE_QUOTE",
            "StartFromRow": 1,
        }
        common = {
            "AwsAccountId": self.account,
            "DataSetId": ORG_DATA_SET_ID,
            "Name": ORG_DATA_SET_NAME,
            "ImportMode": "SPICE",
            "PhysicalTableMap": {
                "users": {
                    "S3Source": {
                        "DataSourceArn": data_source_arn,
                        "UploadSettings": upload_settings,
                        "InputColumns": [
                            {"Name": c, "Type": "STRING"} for c in ORG_COLUMNS
                        ],
                    }
                }
            },
            "LogicalTableMap": {
                "l": {
                    "Alias": ORG_DATA_SET_ID,
                    "Source": {"PhysicalTableId": "users"},
                    "DataTransforms": column_description_transforms(
                        ORG_COLUMN_DESCRIPTIONS
                    ),
                }
            },
        }

        def create_or_update():
            if exists:
                return self.qs_single_try.update_data_set(**common)
            permissions = [
                {"Principal": self.args.quick_admin_arn, "Actions": DATASET_ACTIONS}
            ]
            return self.qs_single_try.create_data_set(Permissions=permissions, **common)

        def throttled(error):
            return error["Code"] in ("ThrottlingException", "TooManyRequestsException")

        try:
            retry_on_client_error(create_or_update, throttled, attempts=5, delay=5)
        except ClientError as exc:
            error = exc.response["Error"]
            if (
                error["Code"] == "LimitExceededException"
                and "spice" in error.get("Message", "").lower()
            ):
                raise org_spice_capacity_abort(self.account) from exc
            raise
        print(f"  Dataset {ORG_DATA_SET_ID} {'updated' if exists else 'created'}")
        self.log.write(
            event="resource", org_data_set=ORG_DATA_SET_ID, created=not exists
        )

    def latest_org_ingestion_since(self, started, data_set_id=ORG_DATA_SET_ID):
        pages = self.qs.get_paginator("list_ingestions").paginate(
            AwsAccountId=self.account, DataSetId=data_set_id
        )
        recent = [
            i
            for page in pages
            for i in page["Ingestions"]
            if i["CreatedTime"] >= started
        ]
        return max(recent, key=lambda i: i["CreatedTime"], default=None)

    def wait_for_org_ingestion(self, started, data_set_id=ORG_DATA_SET_ID):
        """Wait for the SPICE load the create/update started; start one if none shows up."""
        start_wait_until = time.monotonic() + ORG_INGESTION_START_WAIT_SECONDS
        deadline = time.monotonic() + CREATE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            ingestion = self.latest_org_ingestion_since(started, data_set_id)
            if ingestion is None and time.monotonic() > start_wait_until:
                self.qs.create_ingestion(
                    AwsAccountId=self.account,
                    DataSetId=data_set_id,
                    IngestionId=str(uuid.uuid4()),
                )
                start_wait_until = deadline
            status = ingestion["IngestionStatus"] if ingestion else "QUEUED"
            if status == "COMPLETED":
                rows = ingestion.get("RowInfo", {})
                print(
                    f"  {data_set_id}: SPICE load complete: {rows.get('RowsIngested')} rows, "
                    f"{rows.get('RowsDropped')} dropped"
                )
                self.log.write(
                    event="org_ingestion_complete", data_set=data_set_id, rows=rows
                )
                return
            if status in ("FAILED", "CANCELLED"):
                if (
                    ingestion.get("ErrorInfo", {}).get("Type")
                    == "ACCOUNT_CAPACITY_LIMIT_EXCEEDED"
                ):
                    raise org_spice_capacity_abort(self.account)
                raise Abort(
                    f"The SPICE load of {data_set_id} {status}: {ingestion.get('ErrorInfo')}"
                )
            time.sleep(5)
        raise Abort(
            f"The SPICE load of {data_set_id} didn't finish in {CREATE_TIMEOUT_SECONDS}s. Check it in Quick."
        )

    def delete_org_csv(self):
        self.delete_if_present(
            self.s3.delete_object,
            Bucket=self.bucket,
            Key=ORG_CSV_KEY,
            ExpectedBucketOwner=self.account,
        )
        print(f"  Deleted s3://{self.bucket}/{ORG_CSV_KEY} (SPICE keeps its own copy)")
        self.log.write(event="org_csv_deleted", key=ORG_CSV_KEY)

    def run_org(self, rows):
        if not self.args.quick_admin_arn:
            raise Abort(
                "The org part needs the dataset owner; re-run with --quick-admin-arn."
            )
        self.require_bucket_blocks_public_access()
        self.ensure_org_role()
        started = datetime.datetime.now(datetime.timezone.utc)  # noqa: UP017 - CloudShell runs Python 3.9
        try:
            self.upload_org_files(rows)
            data_source_arn = self.ensure_org_data_source()
            self.ensure_org_data_set(data_source_arn)
            self.wait_for_org_ingestion(started)
        finally:
            self.delete_org_csv()  # never leave the PII file behind, whatever happened
        self.run_org_joined_data_sets()

    def data_set_arn(self, data_set_id):
        return f"arn:{self.partition}:quicksight:{self.region}:{self.account}:dataset/{data_set_id}"

    def org_joined_data_set_id(self, d):
        return f"quick-usage-{d}-with-org"

    def quick_user_arn_prefix(self):
        """arn:...:quicksight:<identity region>:<account>:user/default/, from the owner's ARN.

        Quick user ARNs carry the identity region, which can differ from --region, and usage
        records name Identity Center users in the default namespace.
        """
        if not self.args.quick_admin_arn:
            raise Abort(
                "The org part needs the dataset owner; re-run with --quick-admin-arn."
            )
        return self.args.quick_admin_arn.split(":user/", 1)[0] + ":user/default/"

    def usage_output_columns(self, d):
        """Column names of quick-usage-<d> as Quick has them, so the joined dataset keeps them all."""
        data_set = self.qs.describe_data_set(
            AwsAccountId=self.account, DataSetId=f"quick-usage-{d}"
        )
        return [c["Name"] for c in data_set["DataSet"]["OutputColumns"]]

    def org_joined_logical_table_map(self, d):
        """Usage dataset LEFT JOIN org dataset on user_arn. Org columns get an org_ prefix.

        Every org column is renamed (so none clashes with a usage column during the join), then
        the joined table keeps every usage column and only ORG_JOINED_COLUMNS from the org side.
        The trim is a ProjectOperation on the joined table: the join's two sides only rename,
        cast or tag, as the Quick console requires.
        """
        kept_columns = self.usage_output_columns(d) + [
            f"{ORG_JOINED_COLUMN_PREFIX}{c}" for c in ORG_JOINED_COLUMNS
        ]
        renamed_org_columns = [
            {
                "RenameColumnOperation": {
                    "ColumnName": c,
                    "NewColumnName": f"{ORG_JOINED_COLUMN_PREFIX}{c}",
                }
            }
            for c in ORG_COLUMNS
        ]
        descriptions = {
            **{c: t for c, t in COLUMN_DESCRIPTIONS[d].items() if c in kept_columns},
            **{
                f"{ORG_JOINED_COLUMN_PREFIX}{c}": ORG_COLUMN_DESCRIPTIONS[c]
                for c in ORG_JOINED_COLUMNS
            },
        }
        return {
            "usage": {
                "Alias": f"quick-usage-{d}",
                "Source": {"DataSetArn": self.data_set_arn(f"quick-usage-{d}")},
            },
            "org": {
                "Alias": ORG_DATA_SET_ID,
                "Source": {"DataSetArn": self.data_set_arn(ORG_DATA_SET_ID)},
                "DataTransforms": renamed_org_columns,
            },
            "joined": {
                "Alias": self.org_joined_data_set_id(d),
                "Source": {
                    "JoinInstruction": {
                        "LeftOperand": "usage",
                        "RightOperand": "org",
                        "Type": "LEFT",
                        "OnClause": f"{{user_arn}} = {{{ORG_JOINED_COLUMN_PREFIX}{ORG_JOIN_KEY_COLUMN}}}",
                        "RightJoinKeyProperties": {"UniqueKey": True},
                    }
                },
                "DataTransforms": [
                    {"ProjectOperation": {"ProjectedColumns": kept_columns}},
                    *column_description_transforms(descriptions),
                ],
            },
        }

    def usage_data_sets_to_join(self):
        """Selected usage datasets that exist. One that doesn't is skipped, not an error."""
        present = []
        for d in self.selected:
            if self.get_or_none(
                self.qs.describe_data_set,
                "DataSet",
                AwsAccountId=self.account,
                DataSetId=f"quick-usage-{d}",
            ):
                present.append(d)
            else:
                print(
                    f"  quick-usage-{d} doesn't exist, so it gets no org-joined dataset"
                )
        return present

    def ensure_org_joined_data_set(self, d):
        data_set_id = self.org_joined_data_set_id(d)
        exists = self.get_or_none(
            self.qs.describe_data_set,
            "DataSet",
            AwsAccountId=self.account,
            DataSetId=data_set_id,
        )
        common = {
            "AwsAccountId": self.account,
            "DataSetId": data_set_id,
            "Name": f"Quick usage - {d} with organization",
            "ImportMode": "SPICE",
            "PhysicalTableMap": {},
            "LogicalTableMap": self.org_joined_logical_table_map(d),
        }
        if exists:
            self.qs.update_data_set(**common)
        else:
            permissions = [
                {"Principal": self.args.quick_admin_arn, "Actions": DATASET_ACTIONS}
            ]
            self.qs.create_data_set(Permissions=permissions, **common)
        print(
            f"  Dataset {data_set_id} {'updated' if exists else 'created'} (quick-usage-{d} + org columns)"
        )
        self.log.write(
            event="resource", org_joined_data_set=data_set_id, created=not exists
        )

    def usage_refresh_schedule(self, d):
        """ScheduleFrequency of quick-usage-<d>'s refresh (the script's own one first), or None."""
        schedules = self.qs.list_refresh_schedules(
            AwsAccountId=self.account, DataSetId=f"quick-usage-{d}"
        )["RefreshSchedules"]
        schedules.sort(key=lambda s: s["ScheduleId"] != REFRESH_SCHEDULE_ID)
        for schedule in schedules:
            frequency = schedule.get("ScheduleFrequency", {})
            if frequency.get("TimeOfTheDay"):
                return frequency
        return None

    def ensure_org_joined_refresh(self, d):
        """Daily full refresh an hour after the usage dataset's own refresh, in its time zone.

        A usage dataset with no refresh schedule gives its joined dataset none either.
        """
        data_set_id = self.org_joined_data_set_id(d)
        usage = self.usage_refresh_schedule(d)
        if usage is None:
            print(
                f"  {data_set_id}: no daily refresh, because quick-usage-{d} has no refresh schedule"
            )
            self.log.write(
                event="refresh_skipped",
                data_set=data_set_id,
                reason="usage dataset has no schedule",
            )
            return
        time_of_day = (
            datetime.datetime.strptime(usage["TimeOfTheDay"], "%H:%M")
            + ORG_JOINED_REFRESH_DELAY
        ).strftime("%H:%M")
        timezone = usage.get("Timezone", DEFAULT_TIMEZONE)
        schedule = {
            "ScheduleId": REFRESH_SCHEDULE_ID,
            "ScheduleFrequency": {
                "Interval": "DAILY",
                "TimeOfTheDay": time_of_day,
                "Timezone": timezone,
            },
            "RefreshType": "FULL_REFRESH",
        }
        try:
            self.qs.create_refresh_schedule(
                AwsAccountId=self.account, DataSetId=data_set_id, Schedule=schedule
            )
            outcome = "created"
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceExistsException":
                raise
            self.qs.update_refresh_schedule(
                AwsAccountId=self.account, DataSetId=data_set_id, Schedule=schedule
            )
            outcome = "updated"
        print(
            f"  {data_set_id}: daily full refresh at {time_of_day} ({timezone}), {outcome}"
        )
        self.log.write(
            event="refresh_schedule",
            data_set=data_set_id,
            outcome=outcome,
            time=time_of_day,
            timezone=timezone,
        )

    def run_org_joined_data_sets(self):
        started = datetime.datetime.now(datetime.timezone.utc)  # noqa: UP017 - CloudShell runs Python 3.9
        joined = self.usage_data_sets_to_join()
        for d in joined:
            self.ensure_org_joined_data_set(d)
        for d in joined:
            self.wait_for_org_ingestion(started, self.org_joined_data_set_id(d))
            self.ensure_org_joined_refresh(d)

    # ---------- org dashboard over the org-joined datasets ----------
    def org_dashboard_problem(self):
        """Why the org dashboard can't be built in this run, or None."""
        if self.args.no_dashboard:
            return "--no-dashboard skips the org dashboard too."
        missing = [
            self.org_joined_data_set_id(d)
            for d in DASHBOARD_DATASETS
            if self.get_or_none(
                self.qs.describe_data_set,
                "DataSet",
                AwsAccountId=self.account,
                DataSetId=self.org_joined_data_set_id(d),
            )
            is None
        ]
        if missing:
            return (
                "It reads the org-joined chat, chat_feedback, agent_hours and index_usage datasets, and these "
                f"don't exist: {', '.join(missing)}. Set up the usage datasets, then re-run this script."
            )
        return None

    def check_org_dashboard(self):
        """{kind: old version} for org assets built from a previous definition."""
        return self.check_assets(ORG_DASHBOARD, {}, [])

    def plan_org_dashboard_update(self, outdated):
        current = ", ".join(f"{kind} version {v}" for kind, v in outdated.items())
        return [
            f"A newer org dashboard is available: version {ORG_DASHBOARD_VERSION} (yours: {current}).",
            f"Updating replaces the definition of {ORG_ANALYSIS_ID} and {ORG_DASHBOARD_ID} in place.",
            "Their IDs, links, owner and sharing stay the same.",
            "Any changes you made to the org analysis in Quick are overwritten. To keep them,",
            "use Save as in Quick before you confirm, or type SKIP to keep your version.",
        ]

    def run_org_dashboard(self, outdated):
        arns = {
            d: self.data_set_arn(self.org_joined_data_set_id(d))
            for d in DASHBOARD_DATASETS
        }
        self.ensure_assets(
            ORG_DASHBOARD,
            org_dashboard_definition(arns),
            outdated,
            resume_at_dashboard_step=False,
        )

    # undoes the org dataset
    def find_org_to_remove(self):
        todo, lines = {}, []
        joined = [
            self.org_joined_data_set_id(d)
            for d in DATASETS
            if self.get_or_none(
                self.qs.describe_data_set,
                "DataSet",
                AwsAccountId=self.account,
                DataSetId=self.org_joined_data_set_id(d),
            )
        ]
        if joined:
            todo["joined_data_sets"] = joined
            lines.append(f"org-joined datasets: {', '.join(joined)}")
        for kind, describe, key, id_name in (
            ("data_set", self.qs.describe_data_set, "DataSet", "DataSetId"),
            ("data_source", self.qs.describe_data_source, "DataSource", "DataSourceId"),
        ):
            if self.get_or_none(
                describe, key, AwsAccountId=self.account, **{id_name: ORG_DATA_SET_ID}
            ):
                todo[kind] = [ORG_DATA_SET_ID]
                lines.append(f"Org {kind.replace('_', ' ')} {ORG_DATA_SET_ID}")
        role = self.org_role()
        if role is not None and self.is_our_data_source_role(role):
            todo["role"] = [self.org_role_name()]
            lines.append(f"IAM role {self.org_role_name()}")
        if self.bucket_exists():
            # Only the script's own two files: anything else under the prefix isn't ours.
            keys = [
                o["Key"]
                for page in self.s3.get_paginator("list_objects_v2").paginate(
                    Bucket=self.bucket, Prefix=f"{ORG_PREFIX}/"
                )
                for o in page.get("Contents", [])
                if o["Key"] in (ORG_CSV_KEY, ORG_MANIFEST_KEY)
            ]
            if keys:
                todo["files"] = keys
                lines.append(f"{len(keys)} file(s) in {ORG_PREFIX}/")
        if not todo:
            lines.append("no org dataset from this script")
        return StepState(not todo, lines, todo)

    def plan_remove_org(self, state):
        return [
            f"Delete the org dataset {ORG_DATA_SET_ID}, the usage datasets joined to it "
            "(quick-usage-<dataset>-with-org), its data source, IAM role "
            f"{self.org_role_name()} and its files {ORG_CSV_KEY} and {ORG_MANIFEST_KEY}.",
            "Anyone you shared these datasets with loses them, and analyses built on them break.",
        ]

    def remove_org(self, state):
        for data_set_id in state.todo.get("joined_data_sets", []):
            self.delete_if_present(
                self.qs.delete_data_set,
                AwsAccountId=self.account,
                DataSetId=data_set_id,
            )
        if "data_set" in state.todo:
            self.delete_if_present(
                self.qs.delete_data_set,
                AwsAccountId=self.account,
                DataSetId=ORG_DATA_SET_ID,
            )
        if "data_source" in state.todo:
            self.delete_if_present(
                self.qs.delete_data_source,
                AwsAccountId=self.account,
                DataSourceId=ORG_DATA_SET_ID,
            )
        if "role" in state.todo:
            for fn, kwargs in (
                (
                    self.iam.delete_role_policy,
                    {
                        "RoleName": self.org_role_name(),
                        "PolicyName": ORG_ROLE_POLICY_NAME,
                    },
                ),
                (self.iam.delete_role, {"RoleName": self.org_role_name()}),
            ):
                try:
                    fn(**kwargs)
                except ClientError as e:
                    if e.response["Error"]["Code"] != "NoSuchEntity":
                        raise
        for key in state.todo.get("files", []):
            self.delete_if_present(self.s3.delete_object, Bucket=self.bucket, Key=key)
        print("  org dataset, data source, role and files removed")
        self.log.write(event="resource_deleted", org=sorted(state.todo))

    # undoes the org dashboard from this script
    def find_org_dashboard_to_remove(self):
        return self.find_assets_to_remove(
            ORG_DASHBOARD, "no org dashboard or analysis from this script"
        )

    def plan_remove_org_dashboard(self, state):
        return [
            "Delete the org dashboard and analysis. Your changes to them are lost, and",
            "anyone you shared the org dashboard with loses it. The analysis is deleted without",
            "Quick's recovery window, so a later run can create it again. To keep a copy,",
            "use Save as in Quick before you confirm.",
        ]

    def remove_org_dashboard(self, state):
        self.remove_assets(ORG_DASHBOARD, state)


ORG_SCRIPT_VERSION = "0.1.0"
# Destroy removes only what this script creates, in this order: (name, key, what it undoes).
TEARDOWN = [
    ("Delete the org dashboard and analysis", "org_dashboard", "the org dashboard"),
    (
        "Delete the org dataset, its joined datasets, role and files",
        "org",
        "the org dataset",
    ),
]
# If you keep one of these, the parts that depend on it are kept too, so what remains keeps working.
TEARDOWN_NEEDS = {"org": ["org_dashboard"]}


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument(
        "--region",
        help="AWS region (default: your session's region, e.g. CloudShell's region)",
    )
    p.add_argument(
        "--bucket",
        help="the usage analytics bucket (default: quick-usage-<account>-<region code>)",
    )
    p.add_argument(
        "--quick-admin-arn",
        help="Quick user ARN that will own the org datasets and dashboard (default: you, if you "
        "are a Quick admin)",
    )
    p.add_argument("--no-dashboard", action="store_true", help="skip the org dashboard")
    p.add_argument(
        "--destroy",
        action="store_true",
        help="remove what this script created (asks before each removal)",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="print the plan, change nothing"
    )
    p.add_argument("--profile", help="AWS CLI profile, if you run outside CloudShell")
    p.add_argument(
        "--org-identity-store-id",
        help="identity store ID (d-xxxxxxxxxx), if the script can't find it",
    )
    p.add_argument(
        "--org-identity-center-region",
        help="IAM Identity Center's region (default: --region)",
    )
    args = p.parse_args()
    # Settings the shared setup code reads. This script always covers every usage dataset and
    # never creates the bucket, so these keep their defaults.
    args.datasets = list(DATASETS)
    args.retention_days = None
    return args


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    region, region_source = resolve_region(args, session)
    if not region:
        print(
            "Stopped: no AWS region found. Re-run with --region <region>, for example "
            "--region us-east-1."
        )
        return 1
    args.region = region
    identity = session.client("sts").get_caller_identity()
    run_id = str(uuid.uuid4())
    log_path = f"quick-usage-org-{run_id}.jsonl"
    log = ConfirmationLog(
        log_path,
        {
            "run_id": run_id,
            "script": "quick_usage_org",
            "script_version": ORG_SCRIPT_VERSION,
            "account_id": identity["Account"],
            "caller_arn": identity["Arn"],
            "region": args.region,
        },
    )
    log.write(event="run_start", dry_run=args.dry_run, args=vars(args))
    setup = Setup(args, session, identity, log)

    profile, source = describe_profile(args)
    profile_region = boto3.Session(profile_name=args.profile).region_name
    print(f"Run {run_id}")
    print(f"AWS profile: {profile} ({source})")
    if profile_region and profile_region != args.region:
        print(
            f"Note: this profile's default region is {profile_region}; the script uses "
            f"{args.region} for every call."
        )
    log.write(
        event="identity",
        profile=profile,
        profile_source=source,
        profile_region=profile_region,
    )

    print(
        "\nWorking out the settings (read-only; nothing is changed yet)...", flush=True
    )
    try:
        setup.bucket, bucket_source = setup.resolve_bucket()
        args.bucket = setup.bucket
        if not setup.bucket_exists():
            raise Abort(
                f"Bucket {setup.bucket} doesn't exist. Set up Quick usage analytics with "
                "quick_usage_datasets_setup.py first (or pass the --bucket it used)."
            )
        if args.destroy:
            admin_arn = admin_role = admin_source = None
            owner = "not needed for destroy"
        else:
            admin_arn, admin_role, admin_source = setup.resolve_admin(identity["Arn"])
            args.quick_admin_arn = admin_arn
            owner = (
                f"{admin_arn.split(':user/', 1)[1].partition('/')[2]} "
                f"({admin_role}, {admin_source})"
            )
        bucket_state = setup.bucket_state()
    except Abort as e:
        print(f"\nStopped: {e}\nNothing was changed.")
        log.write(event="run_aborted", reason=str(e))
        log.close()
        print(f"Confirmation log kept locally at {log_path}")
        return 1
    log.write(
        event="inputs_resolved",
        region=args.region,
        region_source=region_source,
        bucket=setup.bucket,
        bucket_source=bucket_source,
        bucket_state=bucket_state,
        quick_admin_arn=admin_arn,
        quick_admin_role=admin_role,
        quick_admin_source=admin_source,
    )
    caller = identity["Arn"].split(":", 5)[5]
    if args.destroy:
        print("\nWe'll look for what this script created, with these settings:")
    else:
        print("\nWe'll add the org dataset and dashboard with these settings:")
    print(f"  AWS account:   {identity['Account']}  (signed in as {caller})")
    print(f"  Region:        {args.region}  ({region_source})")
    print(f"  Bucket:        {setup.bucket}  ({bucket_source}; {bucket_state})")
    print(f"  Dataset owner: {owner}")
    if not args.destroy:
        print(f"  Org dataset:   {ORG_DATA_SET_ID} (personal data)")
        if not args.no_dashboard:
            print(f"  Org dashboard: {ORG_DASHBOARD_ID}")
    print(f"  Confirmation log: {log_path}")
    print(
        "\nTo change any of these, type 'stop' and re-run with --region, --bucket or "
        "--quick-admin-arn."
    )
    if not args.dry_run and not args.destroy:
        print(
            f"\nTo remove what this script created instead, type {DESTROY_WORD}. Nothing is"
            "\ndeleted yet: you'll see what it finds and confirm each removal."
        )
        ok = ask_until(
            log,
            0,
            "Confirm settings",
            "If all of these settings are right, confirm by typing the account ID.",
            identity["Account"],
            alternatives={DESTROY_WORD: "destroy"},
        )
        if ok == "destroy":
            args.destroy = True
        elif not ok:
            print(
                "Stopped. Nothing was changed. To use a different account, re-run with "
                "--profile <name>; to change a setting, use the flags above."
            )
            log.close()
            return 1
    if args.destroy:
        try:
            code = run_destroy(setup, log, args)
        except Abort as e:
            print(f"\nStopped: {e}")
            log.write(event="run_aborted", mode="destroy", reason=str(e))
            print(f"To continue, run:\n    {destroy_command(args)}")
            code = 1
        except KeyboardInterrupt:
            print(
                "\n\nInterrupted. Part of the current destroy step may already be done."
            )
            log.write(event="run_interrupted", mode="destroy")
            print(f"To continue, run:\n    {destroy_command(args)}")
            code = 130
        except Exception as e:
            print(f"\nDestroy failed: {describe_error(e)}")
            log.write(
                event="run_failed",
                mode="destroy",
                error=repr(e),
                trace=traceback.format_exc(),
            )
            print(f"To continue, run:\n    {destroy_command(args)}")
            code = 2
        log.close()
        upload_log(setup, log_path, args)
        return code
    code = 0
    try:
        verify_org_prerequisites(setup, log)
        if not run_org_dataset(setup, log, args):
            print("\nStopped at the org dataset. Nothing in it was changed.")
            log.write(event="run_stopped", step="Org")
            print_rerun(log, args)
            code = 1
        else:
            log.write(event="run_complete")
            if not args.dry_run:
                print(f"\nDone. {ORG_NO_REFRESH_NOTE}")
    except Abort as e:
        print(f"\nStopped: {e}")
        log.write(event="run_aborted", step="Org", reason=str(e))
        print_rerun(log, args)
        code = 1
    except KeyboardInterrupt:
        print("\n\nInterrupted during the org dataset. Part of it may already be done.")
        log.write(event="run_interrupted", step="Org")
        print_rerun(log, args)
        code = 130
    except Exception as e:
        print(f"\nThe org dataset failed: {describe_error(e)}")
        log.write(
            event="run_failed", step="Org", error=repr(e), trace=traceback.format_exc()
        )
        print_rerun(log, args)
        code = 2
    log.close()
    upload_log(setup, log_path, args)
    return code


def run_destroy(setup, log, args):
    """Undo what this script created. Returns the exit code."""
    account = setup.account
    print("\n=== Destroy: remove what this script created ===")
    print(
        "Only things this script creates are removed, under their fixed names. Nothing has"
    )
    print("been deleted yet. This is what the script finds in this account:")
    found = {}
    for number, (name, key, undoes) in enumerate(TEARDOWN, start=1):
        state = getattr(setup, f"find_{key}_to_remove")()
        found[key] = state
        status = "nothing to remove" if state.done else "to remove"
        print(f"\n  {number}. {name} (undoes {undoes}): {status}")
        for line in state.lines:
            print(f"       {line}")
    log.write(
        event="destroy_inventory",
        found={
            k: {"done": s.done, "unknown": s.unknown, "details": s.lines}
            for k, s in found.items()
        },
    )
    if all(s.done for s in found.values()):
        print(
            "\nNothing from this script is left in this account. Nothing was changed."
        )
        log.write(event="destroy_complete", kept=[])
        return 0
    if args.dry_run:
        for number, (name, key, _) in enumerate(TEARDOWN, start=1):
            if not found[key].done:
                print(f"\n=== Destroy {number}/{len(TEARDOWN)}: {name} (dry run) ===")
                for line in getattr(setup, f"plan_remove_{key}")(found[key]):
                    print(f"  {line}")
        return 0
    print(
        "\nEach removal asks for its own confirmation, and you can type SKIP to keep it."
    )
    ok = ask_until(
        log,
        0,
        "Confirm destroy",
        f"To start removing these from account {account}, confirm by typing the account ID.",
        account,
    )
    if not ok:
        print("Stopped. Nothing was deleted.")
        return 1
    kept = []
    for number, (name, key, _) in enumerate(TEARDOWN, start=1):
        step = f"D{number}"
        print(f"\n=== Destroy {number}/{len(TEARDOWN)}: {name} ===")
        state = getattr(
            setup, f"find_{key}_to_remove"
        )()  # re-read just before changing anything
        for line in state.lines:
            print(f"  {line}")
        if state.done:
            print("  Nothing to remove; moving on.")
            log.write(event="destroy_step_already_done", step=step, step_name=name)
            continue
        blocked = [k for k in TEARDOWN_NEEDS.get(key, []) if k in kept]
        if blocked:
            what = ", ".join(n for n, k, _ in TEARDOWN if k in blocked)
            print(f"  Kept: you chose to keep '{what}', which needs this.")
            kept.append(key)
            log.write(
                event="destroy_step_kept",
                step=step,
                step_name=name,
                reason="dependency",
                needs=blocked,
            )
            continue
        for line in getattr(setup, f"plan_remove_{key}")(state):
            print(f"  {line}")
        answer = ask_until(
            log,
            step,
            name,
            f"Ready to run destroy step {number} ({name})? Confirm by typing what it does.",
            ORG_REMOVAL_PHRASES[key],
            allow_skip=True,
        )
        if answer == "skip":
            print("  Kept.")
            kept.append(key)
            log.write(
                event="destroy_step_kept",
                step=step,
                step_name=name,
                reason="typed SKIP",
            )
            continue
        if not answer:
            print(
                f"\nStopped at destroy step {number}. Nothing in this step was deleted."
            )
            log.write(event="destroy_stopped", step=step)
            print(f"To continue, run:\n    {destroy_command(args)}")
            print("Anything already removed is detected and skipped.")
            return 1
        getattr(setup, f"remove_{key}")(state)
        after = getattr(setup, f"find_{key}_to_remove")()
        if not after.done:
            raise Abort(
                f"Destroy step {number} ran, but the check afterwards still finds:\n"
                + "\n".join(f"    {line}" for line in after.lines)
            )
        log.write(event="destroy_step_done", step=step, step_name=name)
    log.write(event="destroy_complete", kept=kept)
    if kept:
        print(
            "\nDone. You chose to keep: "
            + ", ".join(n for n, k, _ in TEARDOWN if k in kept)
            + "."
        )
        print(f"To remove them later, run:\n    {destroy_command(args)}")
    else:
        print("\nDone. Everything this script created has been removed.")
        print("To add the org view again, run the script without --destroy.")
    return 0


ORG_REMOVAL_PHRASES = {
    "org_dashboard": "DELETE ORG DASHBOARD",
    "org": "DELETE ORG DATASET",
}


def destroy_command(args):
    parts = ["python3", sys.argv[0], "--destroy"]
    if args.profile:
        parts += ["--profile", args.profile]
    parts += ["--region", args.region]
    if args.bucket:
        parts += ["--bucket", args.bucket]
    return " ".join(shlex.quote(p) for p in parts)


def rerun_command(args):
    """The command to run again with every setting this run resolved. Finished parts are kept."""
    parts = ["python3", sys.argv[0]]
    if args.profile:
        parts += ["--profile", args.profile]
    parts += ["--region", args.region]
    if args.bucket:
        parts += ["--bucket", args.bucket]
    if args.quick_admin_arn:
        parts += ["--quick-admin-arn", args.quick_admin_arn]
    if args.no_dashboard:
        parts.append("--no-dashboard")
    if args.org_identity_store_id:
        parts += ["--org-identity-store-id", args.org_identity_store_id]
    if args.org_identity_center_region:
        parts += ["--org-identity-center-region", args.org_identity_center_region]
    return " ".join(shlex.quote(p) for p in parts)


def print_rerun(log, args):
    command = rerun_command(args)
    print("\nTo continue, fix the problem above, then run:")
    print(f"    {command}")
    print(
        "Finished parts are detected and kept, so this is safe to run more than once."
    )
    log.write(event="resume_hint", step="Org", command=command)


if __name__ == "__main__":
    sys.exit(main())
