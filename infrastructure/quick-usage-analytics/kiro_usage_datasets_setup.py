#!/usr/bin/env python3
"""Set up Kiro user activity datasets (and a Quick + Kiro dashboard) in your own AWS account.

Companion to quick_usage_datasets_setup.py. Kiro writes daily per-user activity reports (CSV) to
an S3 bucket you own; this script connects them to Amazon Quick through Athena, the same way the
Quick usage script does for Quick's own usage logs, and joins them with the Quick usage logs on
user_id.

Runs as separate steps. Before each step that changes anything, the script prints exactly what it
will do and waits for you to type the confirmation phrase. Every prompt, answer and outcome is
written to a confirmation log (JSON Lines) that is also uploaded to your bucket.

Usage (for example in AWS CloudShell, signed in as a Quick admin):
  python3 kiro_usage_datasets_setup.py
The script asks whether Kiro already writes its reports to S3 (paste the S3 URI from the Kiro
console) or whether it should create a new bucket. To skip the question:
  --reports-s3-uri s3://my-bucket/kiro-reports/   the S3 URI set in Kiro (existing setup), or the
                                                  bucket and prefix to create
  --region us-east-1        default: your session's region (must be your Kiro profile's region)
  --quick-admin-arn arn:aws:quicksight:us-east-1:111122223333:user/default/Admin
                            default: you, if you are a Quick admin
  --refresh-time 03:00 --timezone UTC   time of the optional daily refresh
  --no-refresh       skip the optional daily refresh step
  --no-dashboard     skip the optional starter dashboard step
  --dry-run          print every step's plan, change nothing
  --datasets user_report,model_messages   only set up some datasets (default: all)
  --from-step N      start at step N. The script first checks (read-only) that steps
                     1..N-1 are complete, and stops if one isn't.
  --destroy          remove what this script created, in reverse order (you can also
                     type DESTROY instead of the account ID at the first prompt)

Safe to re-run: before each step, the script checks whether that step is already complete.
If it is, nothing is changed and the step is logged as already done. If a run stops or
fails, the script prints the exact command to continue from the step that didn't finish.

Destroy: undoes the steps in reverse order. It lists what it finds first, asks for the account
ID, then asks again before each removal (type SKIP to keep something). It only removes things
this script creates, under their fixed names. It deletes the bucket only if the script created it
and nothing else is in it, and it never deletes Kiro reports from a bucket it didn't create.
"""

import argparse
import copy
import datetime
import fnmatch
import hashlib
import json
import os
import shlex
import sys
import time
import traceback
import urllib.parse
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ParamValidationError

SCRIPT_VERSION = "0.1.0"
# Standard retry mode backs off on throttling and transient errors; explicit timeouts keep a dead
# connection from hanging forever (same settings as quick_usage_datasets_setup.py).
BOTO_CONFIG = Config(
    retries={"mode": "standard", "max_attempts": 10},
    connect_timeout=10,
    read_timeout=60,
)
# Kiro writes each day's reports at 02:00 UTC, so the optional daily refresh runs an hour later.
DEFAULT_REFRESH_TIME = "03:00"
DEFAULT_TIMEZONE = "UTC"
# Every dataset carries a real DATETIME column parsed from the report's Date, named like the
# Quick usage datasets' look-back column so incremental refresh is set up the same way.
EVENT_DATETIME_COLUMN = "event_datetime"
# The script's own files in the reports bucket (Athena results, confirmation logs). Kiro's
# reports stay under the prefix set in the Kiro console.
PREFIX_ROOT = "kiro-usage-analytics"
LOG_PREFIX = f"{PREFIX_ROOT}/_setup-confirmation-logs"
ATHENA_RESULTS_PREFIX = f"{PREFIX_ROOT}/_athena-results"
# Athena query results (copies of report rows, with emails) in a bucket this script created
# expire after this many days, as in quick_usage_datasets_setup.py. Kiro reports never expire.
ATHENA_RESULTS_RETENTION_DAYS = 365
ATHENA_RESULTS_RULE_ID = (
    f"expire-kiro-usage-athena-results-after-{ATHENA_RESULTS_RETENTION_DAYS}-days"
)
# Kiro writes reports under a prefix, so a new bucket gets this one.
DEFAULT_REPORTS_PREFIX = "kiro-user-activity-reports"
NEW_WORD = "NEW"

# ---- Kiro user activity reports ----
# Kiro (https://kiro.dev/docs/enterprise/monitor-and-track/user-activity/) writes CSV files to
#   s3://<bucket>/<prefix>/AWSLogs/<account>/KiroLogs/<report>/<region>/<yyyy>/<mm>/<dd>/00/...
# user_report: one file per client type (KIRO_IDE, KIRO_CLI, PLUGIN), one row per user and day.
#   Besides the fixed columns it has one <model>_messages column per model used that day, so
#   the columns differ from file to file.
# by_user_analytic: the legacy report (CLI and plugin usage), one row per user and day.
# The service principal that writes them, and the source ARN it writes as.
KIRO_PRINCIPAL = "q.amazonaws.com"
BUCKET_POLICY_SID = "KiroUserActivityReportsWrite"
# Bucket-policy statement that refuses any request not made over TLS.
TLS_ONLY_SID = "DenyInsecureTransport"
# One Glue table per report type: (folder under KiroLogs/, first header cell, Date format).
REPORTS = {
    "user_report": ("user_report", "Date", "%Y-%m-%d"),
    "by_user_analytic": ("by_user_analytic", "UserId", "%m-%d-%Y"),
}
USER_REPORT_FIELDS = [
    "UserId",
    "Client_Type",
    ("Chat_Conversations", "INTEGER"),
    ("Credits_Used", "DECIMAL"),
    ("Overage_Cap", "DECIMAL"),
    ("Overage_Credits_Used", "DECIMAL"),
    "Overage_Enabled",
    "ProfileId",
    "Subscription_Tier",
    ("Total_Messages", "INTEGER"),
    "New_User",
    "User_Email",
    ("Usage_Limit", "DECIMAL"),
]
BY_USER_ANALYTIC_METRICS = [
    "Chat_AICodeLines",
    "Chat_MessagesInteracted",
    "Chat_MessagesSent",
    "CodeFix_AcceptanceEventCount",
    "CodeFix_AcceptedLines",
    "CodeFix_GeneratedLines",
    "CodeFix_GenerationEventCount",
    "CodeReview_FailedEventCount",
    "CodeReview_FindingsCount",
    "CodeReview_SucceededEventCount",
    "Dev_AcceptanceEventCount",
    "Dev_AcceptedLines",
    "Dev_GeneratedLines",
    "Dev_GenerationEventCount",
    "DocGeneration_AcceptedFileUpdates",
    "DocGeneration_AcceptedFilesCreations",
    "DocGeneration_AcceptedLineAdditions",
    "DocGeneration_AcceptedLineUpdates",
    "DocGeneration_EventCount",
    "DocGeneration_RejectedFileCreations",
    "DocGeneration_RejectedFileUpdates",
    "DocGeneration_RejectedLineAdditions",
    "DocGeneration_RejectedLineUpdates",
    "InlineChat_AcceptanceEventCount",
    "InlineChat_AcceptedLineAdditions",
    "InlineChat_AcceptedLineDeletions",
    "InlineChat_DismissalEventCount",
    "InlineChat_DismissedLineAdditions",
    "InlineChat_DismissedLineDeletions",
    "InlineChat_RejectedLineAdditions",
    "InlineChat_RejectedLineDeletions",
    "InlineChat_RejectionEventCount",
    "InlineChat_TotalEventCount",
    "Inline_AICodeLines",
    "Inline_AcceptanceCount",
    "Inline_SuggestionsCount",
    "TestGeneration_AcceptedLines",
    "TestGeneration_AcceptedTests",
    "TestGeneration_EventCount",
    "TestGeneration_GeneratedLines",
    "TestGeneration_GeneratedTests",
    "Transformation_EventCount",
    "Transformation_LinesGenerated",
    "Transformation_LinesIngested",
]

# ---- Quick usage datasets (from quick_usage_datasets_setup.py) ----
# quick_kiro_by_user joins Kiro with these Glue tables, and the dashboard's Quick tiles read the
# Quick usage datasets. The join key is user_id, the IAM Identity Center principal id that both
# Kiro's UserId and Quick's user_id hold (see quick_kiro_sql).
QUICK_GLUE_DATABASE = "quick_usage_analytics"
QUICK_JOIN_TABLES = ("quick_usage_chat", "quick_usage_agent_hours")
QUICK_PREFIX_ROOT = "quick-usage-analytics"
QUICK_DASHBOARD_DATASETS = ("chat", "chat_feedback", "agent_hours", "index_usage")

# One Quick dataset per entry: (description, the Glue table it reads, its output columns as
# (name, Quick type)). The SQL for each is built by dataset_sql().
DATASETS = {
    "user_report": (
        "Kiro use per user, day and client: credits, overage, messages, tier.",
        "user_report",
        [("Date", "DATETIME"), "user_id"]
        + USER_REPORT_FIELDS
        + [(EVENT_DATETIME_COLUMN, "DATETIME")],
    ),
    "model_messages": (
        "Kiro messages per user, day, client and model (from the per-model report columns).",
        "user_report",
        [
            ("Date", "DATETIME"),
            "user_id",
            "UserId",
            "Client_Type",
            "Subscription_Tier",
            "User_Email",
            "model",
            ("messages", "INTEGER"),
            (EVENT_DATETIME_COLUMN, "DATETIME"),
        ],
    ),
    "by_user_analytic": (
        "Legacy Kiro report: chat, inline suggestions, code review and /dev, /doc, /test use.",
        "by_user_analytic",
        [("Date", "DATETIME"), "user_id", "UserId"]
        + [(m, "INTEGER") for m in BY_USER_ANALYTIC_METRICS]
        + [(EVENT_DATETIME_COLUMN, "DATETIME")],
    ),
    "quick_kiro_by_user": (
        "Quick and Kiro use per user and day, joined with the Quick usage logs on user_id.",
        "user_report",
        [
            ("day", "DATETIME"),
            "user_id",
            "user_email",
            "quick_user_arn",
            ("quick_messages", "INTEGER"),
            ("quick_agent_hours", "DECIMAL"),
            ("quick_included_hours", "DECIMAL"),
            ("quick_extra_hours", "DECIMAL"),
            ("kiro_credits", "DECIMAL"),
            ("kiro_included_credits", "DECIMAL"),
            ("kiro_overage_credits", "DECIMAL"),
            ("kiro_messages", "INTEGER"),
            ("uses_quick", "INTEGER"),
            ("uses_kiro", "INTEGER"),
        ],
    ),
}
# Datasets that read the Quick usage Glue tables as well as Kiro's.
JOIN_DATASETS = {"quick_kiro_by_user"}
# Columns of a quick_kiro_by_user with one row per user and month; the datasets step
# updates such a dataset in place to the daily SQL.
MONTHLY_JOIN_COLUMNS = {"month", "month_label", "user_name"}
# Marker in a dataset's missing list: it exists with the monthly layout.
DAILY_ROWS_MISSING = "daily rows"

# Text for each dataset column's Description in Quick, as in quick_usage_datasets_setup.py.
# Quick's natural-language features (Topics, chat over the dataset) read it to understand the
# column. Wording follows https://kiro.dev/docs/enterprise/monitor-and-track/user-activity/.
# Max 500 characters each.
_KIRO_DAY = "Day of the activity, in UTC."
_KIRO_DATETIME = "Day of the activity, as a UTC date and time."
_KIRO_USER = {
    "user_id": "IAM Identity Center user ID of the user (principal ID). Same as user_id in the "
    "Quick usage datasets, so use it to join them.",
    "UserId": "IAM Identity Center user ID of the user (principal ID). Same as user_id.",
    "Client_Type": "Kiro client that was used: KIRO_IDE, KIRO_CLI or PLUGIN.",
    "Subscription_Tier": "Kiro subscription tier of the user, for example PRO or POWER.",
    "User_Email": "Email address of the user.",
}
# by_user_analytic metric name -> what it counts: <Kiro feature>_<measure>.
_LEGACY_FEATURES = {
    "Chat": "chat",
    "CodeFix": "code review fixes",
    "CodeReview": "code review",
    "Dev": "the /dev command",
    "DocGeneration": "the /doc command",
    "InlineChat": "inline chat",
    "Inline": "inline suggestions",
    "TestGeneration": "the /test command",
    "Transformation": "the /transform command",
}
_LEGACY_MEASURES = {
    "AICodeLines": "Lines of code from {f} that the user accepted.",
    "MessagesInteracted": "Messages in {f} that the user interacted with.",
    "MessagesSent": "Messages the user sent in {f}.",
    "AcceptanceEventCount": "Times the user accepted code from {f}.",
    "AcceptedLines": "Lines of code the user accepted from {f}.",
    "GeneratedLines": "Lines of code generated by {f}.",
    "GenerationEventCount": "Times {f} generated code.",
    "FailedEventCount": "Runs of {f} that failed.",
    "FindingsCount": "Findings reported by {f}.",
    "SucceededEventCount": "Runs of {f} that succeeded.",
    "AcceptedFileUpdates": "File updates from {f} that the user accepted.",
    "AcceptedFilesCreations": "New files from {f} that the user accepted.",
    "AcceptedLineAdditions": "Added lines from {f} that the user accepted.",
    "AcceptedLineUpdates": "Changed lines from {f} that the user accepted.",
    "AcceptedLineDeletions": "Deleted lines from {f} that the user accepted.",
    "EventCount": "Times {f} was used.",
    "TotalEventCount": "Times {f} was used.",
    "RejectedFileCreations": "New files from {f} that the user rejected.",
    "RejectedFileUpdates": "File updates from {f} that the user rejected.",
    "RejectedLineAdditions": "Added lines from {f} that the user rejected.",
    "RejectedLineUpdates": "Changed lines from {f} that the user rejected.",
    "RejectedLineDeletions": "Deleted lines from {f} that the user rejected.",
    "RejectionEventCount": "Times the user rejected code from {f}.",
    "DismissalEventCount": "Times {f} showed code that the user neither accepted nor rejected.",
    "DismissedLineAdditions": "Added lines from {f} that the user neither accepted nor rejected.",
    "DismissedLineDeletions": "Deleted lines from {f} that the user neither accepted nor "
    "rejected.",
    "AcceptanceCount": "Times the user accepted {f}.",
    "SuggestionsCount": "Times {f} were shown to the user.",
    "AcceptedTests": "Tests from {f} that the user accepted.",
    "GeneratedTests": "Tests generated by {f}.",
    "LinesGenerated": "Lines of code generated by {f}.",
    "LinesIngested": "Lines of code read in by {f}.",
}


def legacy_metric_description(metric):
    """Description of one by_user_analytic metric, from its Kiro feature prefix and measure."""
    prefix, _, measure = metric.partition("_")
    return _LEGACY_MEASURES[measure].format(f=_LEGACY_FEATURES[prefix])


COLUMN_DESCRIPTIONS = {
    "user_report": {
        "Date": _KIRO_DAY + " One row per user, day and client.",
        **_KIRO_USER,
        "Chat_Conversations": "Number of chat conversations the user had that day.",
        "Credits_Used": "Credits the user used that day from the subscription.",
        "Overage_Cap": "Overage limit an admin set when overage is enabled. Otherwise the "
        "credits included in the plan.",
        "Overage_Credits_Used": "Overage credits the user used that day, when overage is "
        "enabled.",
        "Overage_Enabled": "true when overage is enabled for the user.",
        "ProfileId": "ARN of the Kiro profile the activity belongs to.",
        "Total_Messages": "Number of messages to and from Kiro that day, including prompts, "
        "tool calls and responses.",
        "New_User": "true on the day the user activated their subscription.",
        "Usage_Limit": "Credits included in the user's plan each month.",
        EVENT_DATETIME_COLUMN: _KIRO_DATETIME,
    },
    "model_messages": {
        "Date": _KIRO_DAY + " One row per user, day, client and model.",
        **_KIRO_USER,
        "model": "Model that processed the messages, in lowercase, for example auto.",
        "messages": "Number of messages the model processed for this user, day and client.",
        EVENT_DATETIME_COLUMN: _KIRO_DATETIME,
    },
    "by_user_analytic": {
        "Date": _KIRO_DAY
        + " Legacy Kiro report for CLI and plugin use, one row per user and "
        "day. All counts are for that day.",
        "user_id": _KIRO_USER["user_id"],
        "UserId": _KIRO_USER["UserId"],
        **{m: legacy_metric_description(m) for m in BY_USER_ANALYTIC_METRICS},
        EVENT_DATETIME_COLUMN: _KIRO_DATETIME,
    },
    "quick_kiro_by_user": {
        "day": "Day of the activity, in UTC.",
        "user_id": "IAM Identity Center user ID of the user (principal ID). Quick and Kiro use "
        "are matched on it.",
        "user_email": "Email address of the user from the Kiro reports, on every day of the "
        "user. Empty when the user has no Kiro use.",
        "quick_user_arn": "Quick user ARN of the user, on every day of the user. It includes "
        "the Quick user name. Empty when the user has no Quick use.",
        "quick_messages": "Number of Quick chat messages the user sent that day.",
        "quick_agent_hours": "Quick agent hours the user used that day.",
        "quick_included_hours": "Quick agent hours within the monthly subscription grant.",
        "quick_extra_hours": "Quick agent hours that are overage, billed on use.",
        "kiro_credits": "Kiro credits the user used that day.",
        "kiro_included_credits": "Kiro credits used within the plan, without overage.",
        "kiro_overage_credits": "Kiro overage credits the user used that day.",
        "kiro_messages": "Number of messages to and from Kiro that day.",
        "uses_quick": "1 when the user used Quick that day, otherwise 0.",
        "uses_kiro": "1 when the user used Kiro that day, otherwise 0.",
    },
}
# Marker in a dataset's missing list: it exists but has columns with no description yet.
COLUMN_DESCRIPTIONS_MISSING = "column descriptions"
IDENTITY_NOTE = [
    "The reports contain no prompts, code or chat text.",
    "They do identify users: each row has the user's email address and Identity Center user",
    "id, and says how much they used Kiro each day.",
]

# ---- Athena / Glue (step 4) ----
GLUE_DATABASE = "kiro_usage_analytics"
ATHENA_WORKGROUP = "kiro-usage-analytics"
WORKGROUP_TAG = {"Key": "ManagedBy", "Value": "kiro_usage_datasets_setup"}
# Each Glue table reads every line of the CSV files as one string column (the \x01 delimiter
# never occurs in the reports). The dataset SQL splits the lines itself and matches every value
# to its file's header line by name, because user_report's per-model columns vary by file.
GLUE_LINE_SERDE = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
GLUE_TEXT_INPUT = "org.apache.hadoop.mapred.TextInputFormat"
GLUE_IGNOREKEY_OUTPUT = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
ATHENA_CAST = {"INTEGER": "bigint", "DECIMAL": "double"}
DATA_SOURCE_ROLE_POLICY = "KiroUsageReportsRead"

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
# Step 7: creates an analysis and a dashboard.
ANALYSIS_ID = "kiro-usage-analysis"
DASHBOARD_ID = "kiro-usage-dashboard"
DASHBOARD_NAME = "Amazon Quick and Kiro usage"
# Bump whenever dashboard_definition() changes, so a re-run offers to update previous assets.
DASHBOARD_VERSION = 4
DASHBOARD_VERSION_TAG = "kiro-usage-dashboard-version"
# Kiro datasets the dashboard reads; it also reads the Quick usage datasets above.
DASHBOARD_DATASETS = ["user_report", "by_user_analytic", "quick_kiro_by_user"]
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
# Like the Quick usage starter dashboard, the styling is embedded in DASHBOARD_DEFINITION_JSON:
# per-tile background/border on every layout element, a per-visual chart colour, and fixed series
# colours for multi-series charts (ColumnConfigurations CustomColors, per-visual ColorMap). So no
# theme asset is created and no CreateTheme/DeleteTheme permissions are needed.
DASHBOARD_ASSETS = (("analysis", ANALYSIS_ID), ("dashboard", DASHBOARD_ID))
CREATE_TIMEOUT_SECONDS = 300
REFRESH_SCHEDULE_ID = "kiro-usage-daily"
ATHENA_SHARED = "athena resources"
DATA_SOURCE_ROLE = "data source role"
BUCKET_TAG = {"Key": "created-by", "Value": "kiro-usage-datasets-setup"}
DESTROY_WORD = "DESTROY"


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


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


def bold(text):
    """Bold in terminals; plain text otherwise (logs, pipes)."""
    if sys.stdout.isatty():
        return f"\033[1m{text}\033[0m"
    return text


def progress(text, done=False):
    """A status line that updates in place in a terminal, or a plain line otherwise."""
    if sys.stdout.isatty():
        print(f"\r\033[K{text}", end="\n" if done else "", flush=True)
    else:
        print(text, flush=True)


def region_code(region):
    parts = region.split("-")
    return parts[0] + "".join(DIRECTION_CODES.get(p, p) for p in parts[1:])


def parse_s3_uri(text):
    """(bucket, prefix without slashes) from s3://bucket/prefix/. Raises ValueError if invalid."""
    text = text.strip()
    if not text.startswith("s3://"):
        raise ValueError("it must start with s3://")
    bucket, _, prefix = text[len("s3://") :].partition("/")
    prefix = prefix.strip("/")
    if not bucket:
        raise ValueError("it has no bucket name")
    if not prefix:
        raise ValueError(
            "Kiro can't write to the bucket root, so it needs a prefix (folder)"
        )
    return bucket, prefix


def retry_on_client_error(fn, retryable, attempts, delay, on_retry=None, give_up=None):
    """Call fn(), retrying while it raises a ClientError that retryable(error) accepts."""
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


def policy_document(doc):
    """An IAM/S3 policy document as a dict, whether IAM returned it URL-encoded or not."""
    if isinstance(doc, str):
        doc = json.loads(urllib.parse.unquote(doc))
    return doc or {}


def as_list(value):
    return value if isinstance(value, list) else [value]


class Abort(Exception):
    """Stop the run with a message. resume_step overrides the step the resume command uses."""

    def __init__(self, message, resume_step=None):
        super().__init__(message)
        self.resume_step = resume_step


class StepState:
    """Result of a read-only completion check for one step (see quick_usage_datasets_setup)."""

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


class Setup:
    def __init__(self, args, session, identity, log):
        self.args = args
        self.region = args.region
        self.account = identity["Account"]
        self.bucket = None
        self.reports_prefix = None
        self.log = log
        self.s3 = session.client("s3", region_name=self.region, config=BOTO_CONFIG)
        self.qs = session.client(
            "quicksight", region_name=self.region, config=BOTO_CONFIG
        )
        self.iam = session.client("iam", config=BOTO_CONFIG)
        self.glue = session.client("glue", region_name=self.region, config=BOTO_CONFIG)
        self.athena = session.client(
            "athena", region_name=self.region, config=BOTO_CONFIG
        )
        self.partition = identity["Arn"].split(":")[1]
        self.selected = list(args.datasets)
        # Bucket of the Quick usage logs, if quick_usage_datasets_setup.py has run (precheck).
        self.quick_bucket = None

    # ---------- helpers ----------
    def dataset_id(self, d):
        return f"kiro-usage-{d}"

    def kiro_logs_prefix(self):
        """Where Kiro writes, for this account: <prefix>/AWSLogs/<account>/KiroLogs/."""
        return f"{self.reports_prefix}/AWSLogs/{self.account}/KiroLogs/"

    def reports_uri(self):
        return f"s3://{self.bucket}/{self.reports_prefix}/"

    def default_bucket(self):
        return f"kiro-usage-{self.account}-{region_code(self.region)}"

    def bucket_exists(self, bucket=None):
        bucket = bucket or self.bucket
        try:
            self.s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=self.account)
            return True
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("404", "NoSuchBucket"):
                return False
            if code in ("403", "AccessDenied"):
                raise Abort(
                    f"Bucket {bucket} exists but isn't owned by account {self.account}, or you "
                    "can't access it.\n  Kiro only writes to a bucket in the account where users "
                    "are subscribed. Re-run with --reports-s3-uri s3://<another name>/<prefix>/."
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

    def count_reports(self, sub="", limit=1000):
        """Number of objects Kiro wrote under KiroLogs/<sub> (up to limit), and the newest key."""
        if not self.bucket_exists():
            return 0, None
        count, newest = 0, None
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self.kiro_logs_prefix() + sub
        ):
            for obj in page.get("Contents", []):
                count += 1
                if newest is None or obj["LastModified"] > newest["LastModified"]:
                    newest = obj
            if count >= limit:
                break
        return count, newest

    # ---------- where the reports are (resolved before any step runs) ----------
    def resolve_reports(self):
        """Bucket and prefix of the Kiro reports, and where they came from."""
        if self.args.reports_s3_uri:
            try:
                bucket, prefix = parse_s3_uri(self.args.reports_s3_uri)
            except ValueError as e:
                raise Abort(
                    f"--reports-s3-uri {self.args.reports_s3_uri} isn't usable: {e}."
                ) from e
            return bucket, prefix, "from --reports-s3-uri"
        default = self.default_bucket()
        if self.bucket_exists(default) and self.bucket_has_our_tag(default):
            return (
                default,
                DEFAULT_REPORTS_PREFIX,
                "created by an earlier run of this script",
            )
        return self.ask_reports()

    def ask_reports(self):
        print(
            "\nDoes Kiro already send user activity reports to an S3 bucket in this account?"
        )
        print(
            "  If yes, paste the S3 URI shown in the Kiro console under Settings > Kiro user"
        )
        print(
            "  activity reports, for example s3://amzn-s3-demo-bucket/user-activity-reports/."
        )
        print(
            f"  The datasets then read the reports already there: {self.kiro_settings_link()}"
        )
        print(
            f"  If not, type {NEW_WORD}: the script creates a bucket for them, and a later step"
        )
        print("  shows you how to turn the reports on in the Kiro console.")
        print(f"  (Type '{STOP_WORD}' to stop.)")
        while True:
            try:
                answer = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                raise Abort("Stopped while choosing the reports bucket.") from None
            if not answer:
                continue
            if answer.lower() == STOP_WORD:
                raise Abort("Stopped while choosing the reports bucket.")
            if answer.upper() == NEW_WORD:
                self.log.write(event="reports_location_typed", typed=answer, mode="new")
                return (
                    self.default_bucket(),
                    DEFAULT_REPORTS_PREFIX,
                    "new bucket (you typed NEW)",
                )
            try:
                bucket, prefix = parse_s3_uri(answer)
            except ValueError as e:
                print(
                    f"  That isn't an S3 URI the script can use: {e}. Try again, or type "
                    f"{NEW_WORD}."
                )
                continue
            if not self.bucket_exists(bucket):
                print(
                    f"  There's no bucket named {bucket} in this account. Check the URI in the "
                    f"Kiro console, or type {NEW_WORD}."
                )
                continue
            self.log.write(
                event="reports_location_typed", typed=answer, mode="existing"
            )
            return bucket, prefix, "typed by you"

    def bucket_has_our_tag(self, bucket=None):
        try:
            tags = self.s3.get_bucket_tagging(Bucket=bucket or self.bucket)["TagSet"]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchTagSet", "NoSuchTagSetError"):
                return False
            raise
        return BUCKET_TAG in tags

    def bucket_state(self):
        try:
            return (
                "exists in this account" if self.bucket_exists() else "will be created"
            )
        except ClientError as e:
            return f"can't check: {e.response['Error']['Code']}"

    def kiro_settings_link(self):
        return link(
            f"https://{self.region}.console.aws.amazon.com/amazonq/developer/home"
            f"?region={self.region}#/kiro/settings"
        )

    # ---------- owner of the Quick assets (same rules as quick_usage_datasets_setup.py) ----------
    def admin_users(self):
        """Quick users in the default namespace with an admin role, as (arn, name, role)."""
        print("  Looking for Quick admins to own the datasets. To find them,")
        print(
            "  the script reads every Quick user in the account (100 per call). To skip it"
        )
        print(
            "  next time, pass --quick-admin-arn with the owner's Quick user ARN.",
            flush=True,
        )
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

    def raise_quick_error(self, e):
        """Stop with a clear message for errors a re-run with other settings fixes; else re-raise."""
        message = e.response["Error"].get("Message", "")
        if "identity region" in message.lower():
            raise Abort(
                f"Quick can't manage users from {self.region}. Your Quick account was created in "
                f"another region (its 'identity region').\n  Re-run with --region <that region>. "
                f"Quick says: {message}"
            )
        if e.response["Error"]["Code"] in ("AccessDeniedException", "AccessDenied"):
            raise Abort(
                f"Your AWS identity isn't allowed to call quicksight:{e.operation_name}, which "
                "the script needs to find the Quick admin who will own the datasets.\n  Re-run "
                f"with an identity that has Quick permissions. Quick says: {message}"
            )
        raise e

    def describe_quick_user(self, namespace, user_name):
        """The Quick user, or None if there is none by that name."""
        if not namespace or not user_name:
            return None
        try:
            return self.qs.describe_user(
                AwsAccountId=self.account, Namespace=namespace, UserName=user_name
            )["User"]
        except ClientError as e:
            if e.response["Error"]["Code"] in (
                "ResourceNotFoundException",
                "InvalidParameterValueException",
            ):
                return None
            self.raise_quick_error(e)
        except ParamValidationError:
            return None

    def caller_quick_user_name(self, caller_arn):
        """The Quick user name an IAM caller signs in as: 'Role/Session' or 'UserName'."""
        parts = caller_arn.split(":", 5)
        if len(parts) < 6:
            return None
        kind, _, rest = parts[5].partition("/")
        if kind == "assumed-role":
            return rest
        if kind == "user":
            return rest.rsplit("/", 1)[-1]
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
            if ":user/" not in arn:
                raise Abort(
                    f"--quick-admin-arn does not look like a Quick user ARN: {arn}"
                )
            namespace, user_name = self.parse_quick_user(arn)
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
        found = self.caller_admin(caller_arn)
        if found:
            return found
        return self.ask_admin()

    def caller_admin(self, caller_arn):
        """(arn, role, source) if the caller is an active Quick admin, else None. Never stops."""
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
            f"  Or type {SEARCH_WORD} to have the script find the admins for you (this reads"
        )
        print("  every Quick user in the account, so it can take several minutes).")
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
                f"This Quick account has no admin users, and step {DATASETS_STEP} needs one to "
                "own the datasets.\n  Add an admin in Quick (Manage account > Manage users), "
                "then re-run."
            )
        if len(admins) == 1:
            a, _, role = admins[0]
            return a, role, "the only Quick admin in this account"
        print(
            "\nThis Quick account has several admins. Which one should own the datasets?"
        )
        for i, (_, user_name, role) in enumerate(admins, start=1):
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
                return a, role, "chosen by you"
            print(f"  Type a number from 1 to {len(admins)}.")

    # ---------- precheck step: read-only checks ----------
    def plan_precheck(self):
        return [
            f"Read-only. Check the Quick subscription in {self.account} / {self.region}.",
            f"Check the reports location {self.reports_uri()} (bucket exists and is yours, or is",
            "free to create) and whether Kiro has written reports there yet.",
            "Check that the Quick usage setup (quick_usage_datasets_setup.py) is done: its Glue",
            "tables and quick-usage-* datasets, which the Quick + Kiro join and the dashboard read.",
            "The script stops here, changing nothing, if they're missing.",
        ]

    def quick_usage_tables(self):
        """{table: S3 location} of the Quick usage Glue tables the join reads (missing ones left out)."""
        found = {}
        for name in QUICK_JOIN_TABLES:
            try:
                table = self.glue.get_table(
                    DatabaseName=QUICK_GLUE_DATABASE, Name=name
                )["Table"]
            except ClientError as e:
                if e.response["Error"]["Code"] == "EntityNotFoundException":
                    continue
                raise
            found[name] = table.get("StorageDescriptor", {}).get("Location", "")
        return found

    def find_quick_bucket(self):
        """The Quick usage logs bucket, from its Glue tables, or None if they aren't all there."""
        tables = self.quick_usage_tables()
        if set(tables) != set(QUICK_JOIN_TABLES):
            return None
        buckets = {
            loc[len("s3://") :].split("/", 1)[0]
            for loc in tables.values()
            if loc.startswith("s3://")
        }
        return buckets.pop() if len(buckets) == 1 else None

    def missing_quick_datasets(self):
        return [
            f"quick-usage-{d}"
            for d in QUICK_DASHBOARD_DATASETS
            if self.get_or_none(
                self.qs.describe_data_set,
                "DataSet",
                AwsAccountId=self.account,
                DataSetId=f"quick-usage-{d}",
            )
            is None
        ]

    def run_precheck(self, state=None):
        print("  Checking the Quick subscription...", flush=True)
        sub = self.qs.describe_account_subscription(AwsAccountId=self.account)
        status = sub["AccountInfo"].get("AccountSubscriptionStatus")
        edition = sub["AccountInfo"].get("Edition")
        print(f"  Quick subscription status: {status}, edition: {edition}")
        if status != "ACCOUNT_CREATED":
            raise Abort(f"Quick subscription is {status}; it must be ACCOUNT_CREATED.")
        exists = self.bucket_exists()
        print(f"  Bucket {self.bucket} exists: {exists}")
        if exists:
            count, newest = self.count_reports()
            if count:
                print(
                    f"  Kiro reports found under {self.kiro_logs_prefix()}: {count}"
                    f"{'+' if count >= 1000 else ''} file(s), newest "
                    f"{newest['LastModified']:%Y-%m-%d}"
                )
            else:
                print(f"  No Kiro reports under {self.kiro_logs_prefix()} yet.")
        print(
            "  Checking for the Quick usage setup (quick_usage_datasets_setup.py)...",
            flush=True,
        )
        self.quick_bucket = self.find_quick_bucket()
        missing = [
            f"Glue table {QUICK_GLUE_DATABASE}.{t}"
            for t in QUICK_JOIN_TABLES
            if not self.quick_bucket
        ] + self.missing_quick_datasets()
        self.log.write(
            event="precheck",
            quick_bucket=self.quick_bucket,
            missing_quick_usage=missing,
        )
        if missing:
            raise Abort(
                "This script adds Kiro to the Quick usage datasets, and they aren't set up in "
                f"{self.account} / {self.region} yet (missing: {', '.join(missing)}).\n  Run "
                "quick_usage_datasets_setup.py first (README.md), then run this script "
                "again. Nothing was changed.",
                resume_step=PRECHECK_STEP,
            )
        print(
            f"  Quick usage setup found: Glue tables in {QUICK_GLUE_DATABASE} (logs in "
            f"s3://{self.quick_bucket}/) and the quick-usage-* datasets."
        )

    # ---------- bucket step ----------
    def bucket_policy(self):
        try:
            return policy_document(
                self.s3.get_bucket_policy(Bucket=self.bucket)["Policy"]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchBucketPolicy":
                return {}
            raise

    def kiro_write_statement(self):
        return {
            "Sid": BUCKET_POLICY_SID,
            "Effect": "Allow",
            "Principal": {"Service": KIRO_PRINCIPAL},
            "Action": "s3:PutObject",
            "Resource": f"arn:{self.partition}:s3:::{self.bucket}/{self.reports_prefix}/*",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {
                    "aws:SourceArn": f"arn:{self.partition}:codewhisperer:{self.region}:{self.account}:*"
                },
            },
        }

    def tls_only_statement(self):
        bucket_arn = f"arn:{self.partition}:s3:::{self.bucket}"
        return {
            "Sid": TLS_ONLY_SID,
            "Effect": "Deny",
            "Principal": "*",
            "Action": "s3:*",
            "Resource": [bucket_arn, f"{bucket_arn}/*"],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        }

    @staticmethod
    def has_tls_only(policy):
        """True if some Deny statement refuses requests made without TLS."""
        for st in as_list(policy.get("Statement", [])):
            secure = (
                (st.get("Condition") or {}).get("Bool", {}).get("aws:SecureTransport")
            )
            if st.get("Effect") == "Deny" and str(secure).lower() == "false":
                return True
        return False

    def lifecycle_rules(self):
        try:
            return self.s3.get_bucket_lifecycle_configuration(Bucket=self.bucket).get(
                "Rules", []
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchLifecycleConfiguration":
                raise
            return []

    def needs_results_expiry(self):
        """A bucket this script created should expire its Athena results; a customer's is left alone."""
        return self.bucket_has_our_tag() and not any(
            r.get("ID") == ATHENA_RESULTS_RULE_ID for r in self.lifecycle_rules()
        )

    def ensure_results_expiry(self):
        if not self.needs_results_expiry():
            return
        # put replaces the whole configuration, so keep every rule that isn't ours.
        rules = [
            r for r in self.lifecycle_rules() if r.get("ID") != ATHENA_RESULTS_RULE_ID
        ]
        rules.append(
            {
                "ID": ATHENA_RESULTS_RULE_ID,
                "Filter": {"Prefix": f"{ATHENA_RESULTS_PREFIX}/"},
                "Status": "Enabled",
                "Expiration": {"Days": ATHENA_RESULTS_RETENTION_DAYS},
            }
        )
        self.s3.put_bucket_lifecycle_configuration(
            Bucket=self.bucket, LifecycleConfiguration={"Rules": rules}
        )
        print(
            f"  Athena query results expire after {ATHENA_RESULTS_RETENTION_DAYS} days; Kiro reports are kept."
        )
        self.log.write(
            event="resource", bucket_lifecycle_days=ATHENA_RESULTS_RETENTION_DAYS
        )

    def needs_tls_only(self, policy):
        """Only a bucket this script created gets the TLS-only statement; a customer's is left alone."""
        return self.bucket_has_our_tag() and not self.has_tls_only(policy)

    def kiro_can_write(self, policy):
        """True if an Allow statement lets q.amazonaws.com put objects under the reports prefix."""
        probe = f"arn:{self.partition}:s3:::{self.bucket}/{self.kiro_logs_prefix()}probe.csv"
        for st in as_list(policy.get("Statement", [])):
            if st.get("Effect") != "Allow":
                continue
            services = as_list((st.get("Principal") or {}).get("Service", []))
            if KIRO_PRINCIPAL not in services:
                continue
            actions = as_list(st.get("Action", []))
            if not any(fnmatch.fnmatchcase("s3:PutObject", a) for a in actions):
                continue
            if any(
                fnmatch.fnmatchcase(probe, r) for r in as_list(st.get("Resource", []))
            ):
                return True
        return False

    def bucket_warnings(self):
        """Settings on an existing bucket that the script doesn't change but you should know about."""
        warnings = []
        location = self.s3.get_bucket_location(Bucket=self.bucket).get(
            "LocationConstraint"
        )
        location = location or "us-east-1"
        if location != self.region:
            warnings.append(
                f"The bucket is in {location}, not {self.region}. Kiro needs the bucket in the "
                "region of your Kiro profile; run the script in that region (--region)."
            )
        try:
            pab = self.s3.get_public_access_block(Bucket=self.bucket)[
                "PublicAccessBlockConfiguration"
            ]
            if not all(pab.values()):
                warnings.append(
                    "Block Public Access isn't fully on for this bucket. The reports identify "
                    "users; turn it on (S3 console > the bucket > Permissions)."
                )
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchPublicAccessBlockConfiguration":
                raise
            warnings.append(
                "This bucket has no Block Public Access settings. Turn them on."
            )
        try:
            rules = self.s3.get_bucket_encryption(Bucket=self.bucket)[
                "ServerSideEncryptionConfiguration"
            ]["Rules"]
            algorithm = rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
            if algorithm != "AES256":
                warnings.append(
                    f"Default encryption is {algorithm}. Kiro and the data source role need "
                    "access to that KMS key."
                )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                != "ServerSideEncryptionConfigurationNotFoundError"
            ):
                raise
        if not self.bucket_has_our_tag() and not self.has_tls_only(
            self.bucket_policy()
        ):
            warnings.append(
                "The bucket policy doesn't refuse requests made without TLS. Consider adding a "
                "Deny statement with the condition aws:SecureTransport = false."
            )
        return warnings

    def check_bucket(self):
        if not self.bucket_exists():
            return StepState(
                False,
                [f"Bucket s3://{self.bucket} doesn't exist yet."],
                {"bucket": ["create"]},
            )
        lines = [f"Bucket s3://{self.bucket} exists and is owned by this account."]
        lines += [f"WARNING: {w}" for w in self.bucket_warnings()]
        policy = self.bucket_policy()
        changes = {}
        if self.kiro_can_write(policy):
            lines.append(
                f"Its bucket policy already lets Kiro ({KIRO_PRINCIPAL}) write under "
                f"{self.reports_prefix}/."
            )
            if self.needs_tls_only(policy):
                lines.append(
                    "Its TLS-only bucket policy statement is missing and will be added."
                )
                changes["bucket policy"] = ["add TLS-only statement"]
        else:
            lines.append(
                f"Its bucket policy doesn't let Kiro ({KIRO_PRINCIPAL}) write under "
                f"{self.reports_prefix}/ yet."
            )
            changes["bucket policy"] = ["add statement"]
        # run_bucket always applies the expiration, so the plan must always show it.
        if self.needs_results_expiry():
            lines.append(
                f"Its {ATHENA_RESULTS_RETENTION_DAYS}-day Athena results expiration is "
                "missing and will be added."
            )
            changes["bucket lifecycle"] = ["add expiration"]
        return StepState(not changes, lines, changes or None)

    def plan_bucket(self):
        statement = json.dumps(self.kiro_write_statement(), indent=2).splitlines()
        lines = []
        if self.args.dry_run or not self.bucket_exists():
            lines += [
                f"Create bucket s3://{self.bucket} in {self.region}:",
                "  - block all public access, SSE-S3 encryption, bucket-owner-enforced ownership",
                "  - a bucket policy statement that refuses requests made without TLS",
                f"  - Athena query results expire after {ATHENA_RESULTS_RETENTION_DAYS} days; Kiro "
                "reports are never expired",
                f"  - Kiro writes its reports under {self.reports_prefix}/ (Kiro can't use the root)",
            ]
        else:
            lines.append(
                f"Use existing bucket s3://{self.bucket}. Its other settings are not changed."
            )
            if self.needs_tls_only(self.bucket_policy()):
                lines.append(
                    "  - a bucket policy statement that refuses requests made without TLS is added"
                )
        lines += [
            "Add this statement to the bucket policy (existing statements are kept). It is the",
            "policy the Kiro documentation asks for: only Kiro, for this account and region, may",
            f"write, and only under {self.reports_prefix}/:",
            *[f"  {line}" for line in statement],
            "NOTE: " + IDENTITY_NOTE[0],
            *[f"      {line}" for line in IDENTITY_NOTE[1:]],
            "      Restrict who can read the bucket.",
        ]
        return lines

    def run_bucket(self, state=None):
        if not self.bucket_exists():
            kwargs = {"Bucket": self.bucket, "ObjectOwnership": "BucketOwnerEnforced"}
            if self.region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {
                    "LocationConstraint": self.region
                }
            self.s3.create_bucket(**kwargs)
            self.s3.put_public_access_block(
                Bucket=self.bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            self.s3.put_bucket_encryption(
                Bucket=self.bucket,
                ServerSideEncryptionConfiguration={
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "AES256"
                            }
                        }
                    ]
                },
            )
            self.s3.put_bucket_tagging(
                Bucket=self.bucket, Tagging={"TagSet": [BUCKET_TAG]}
            )
            print(f"  Created s3://{self.bucket}")
            self.log.write(event="resource", bucket=self.bucket)
        else:
            print(f"  Using existing s3://{self.bucket}")
        self.ensure_results_expiry()
        policy = self.bucket_policy()
        statements = as_list(policy.get("Statement", []))
        added = []
        if self.kiro_can_write(policy):
            print("  The bucket policy already lets Kiro write.")
        else:
            statements = [st for st in statements if st.get("Sid") != BUCKET_POLICY_SID]
            statements.append(self.kiro_write_statement())
            added.append(BUCKET_POLICY_SID)
        if self.needs_tls_only(policy):
            statements.append(self.tls_only_statement())
            added.append(TLS_ONLY_SID)
        if not added:
            return
        policy = {"Version": policy.get("Version", "2012-10-17"), **policy}
        policy["Statement"] = statements
        self.s3.put_bucket_policy(Bucket=self.bucket, Policy=json.dumps(policy))
        if BUCKET_POLICY_SID in added:
            print(
                f"  Bucket policy: statement {BUCKET_POLICY_SID} added (Kiro may write under "
                f"{self.reports_prefix}/)"
            )
        if TLS_ONLY_SID in added:
            print(
                f"  Bucket policy: statement {TLS_ONLY_SID} added (requests without TLS are refused)"
            )
        for sid in added:
            self.log.write(
                event="resource", bucket_policy_statement=sid, bucket=self.bucket
            )

    # ---------- reporting step: manual, in the Kiro console ----------
    # This setting is managed in the Kiro console, so the customer turns it on in the
    # Kiro console. Reports in the bucket (or an earlier run's record) show it's done.
    def earlier_log_entries(self):
        """Every entry in the confirmation logs earlier runs uploaded to the bucket."""
        if not self.bucket_exists():
            return []
        entries = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=LOG_PREFIX + "/"
        ):
            for obj in page.get("Contents", []):
                body = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])[
                    "Body"
                ].read()
                for line in body.decode("utf-8", "replace").splitlines():
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        continue
        return entries

    def reporting_record(self):
        """Latest recorded change to Kiro reporting: ('turned_on' | 'already' | 'turned_off', when)."""
        found = [
            e
            for e in self.earlier_log_entries()
            if e.get("event") == "kiro_reporting"
            and e.get("outcome") in ("turned_on", "already", "turned_off")
            and e.get("reports_uri") == self.reports_uri()
        ]
        if not found:
            return None
        latest = max(found, key=lambda e: e.get("timestamp", ""))
        return latest["outcome"], latest.get("timestamp", "")

    def check_reporting(self):
        count, newest = self.count_reports()
        if count:
            return StepState(
                True,
                [
                    f"Kiro is writing reports to {self.reports_uri()}: {count}"
                    f"{'+' if count >= 1000 else ''} file(s) under {self.kiro_logs_prefix()},",
                    f"the newest from {newest['LastModified']:%Y-%m-%d %H:%M} UTC.",
                ],
            )
        record = self.reporting_record()
        if record and record[0] in ("turned_on", "already"):
            return StepState(
                True,
                [
                    f"An earlier run recorded on {record[1][:10]} that Kiro reports go to "
                    f"{self.reports_uri()};",
                    "no report has arrived yet. Kiro writes the first one at 02:00 UTC after you "
                    "turn them on.",
                ],
            )
        return StepState(
            False, [f"No Kiro reports under {self.kiro_logs_prefix()} yet."]
        )

    def plan_reporting(self):
        return [
            bold(
                "MANUAL STEP (you do this in the Kiro console; the script waits for you)."
            ),
            "This setting is managed in the Kiro console. You need the Kiro administrator permissions",
            "(https://kiro.dev/docs/enterprise/iam/).",
            f"  1. Open the Kiro console in {self.region} (the region of your Kiro profile):",
            "       " + self.kiro_settings_link(),
            "     or search for Kiro in the AWS console, then choose Settings.",
            "  2. Under 'Kiro user activity reports', choose Edit.",
            "  3. Turn on 'Collect granular metrics per user'.",
            "  4. Enter this S3 URI exactly:",
            "       " + bold(self.reports_uri()),
            "  5. Save.",
            "",
            "Kiro writes the first report at the next 02:00 UTC, then one per day. Earlier days",
            "aren't backfilled. The datasets created next stay empty until then.",
            "",
            "Then type one of these:",
            "  I TURNED ON KIRO REPORTS   after you saved the setting",
            "  IT WAS ALREADY ON          if the page already showed this S3 URI",
        ]

    def run_reporting(self, state=None):
        outcome = (
            "already"
            if getattr(self, "confirmed_as", True) == "already"
            else "turned_on"
        )
        self.log.write(
            event="kiro_reporting", outcome=outcome, reports_uri=self.reports_uri()
        )
        count, _ = self.count_reports()
        if count:
            print(f"  Kiro reports are arriving ({count} file(s) so far).")
        else:
            print(
                "  Recorded. No report is in the bucket yet; the first one arrives at 02:00 UTC."
            )
            print(
                "  The next steps work without it (the datasets refresh to 0 rows until then)."
            )

    # ---------- athena step: data source role, Glue database + tables, Athena workgroup ----------
    # The data sources set AthenaParameters.RoleArn to this role, so Quick runs their Athena
    # queries through it instead of the account-wide Quick service role. The service role
    # then needs no access to the Kiro reports, so other Quick authors can't reach them
    # through it.
    def data_source_role_name(self):
        name = f"KiroUsageDataSourceRead-{self.bucket}"
        if len(name) <= 64:  # IAM role name limit
            return name
        digest = hashlib.sha256(self.bucket.encode()).hexdigest()[:16]
        return f"KiroUsageDataSourceRead-{digest}"

    def data_source_role_arn(self):
        return f"arn:{self.partition}:iam::{self.account}:role/{self.data_source_role_name()}"

    def data_source_role_trust(self):
        # Quick's Athena connector assumes the role with session tags, so TagSession is needed.
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

    def data_source_role(self):
        try:
            return self.iam.get_role(RoleName=self.data_source_role_name())["Role"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchEntity":
                return None
            raise

    def is_our_data_source_role(self, role):
        """Trusted only by Quick: the trust policy names quicksight.amazonaws.com and nothing else."""
        doc = policy_document(role.get("AssumeRolePolicyDocument"))
        principals = set()
        for st in as_list(doc.get("Statement", [])):
            if st.get("Effect") != "Allow":
                continue
            for kind, value in (st.get("Principal") or {}).items():
                for v in as_list(value):
                    principals.add((kind, v))
        return principals == {("Service", "quicksight.amazonaws.com")}

    def data_source_role_policy_document(self):
        """Read the Kiro reports (and, for the join, the Quick usage logs), run Athena in our
        workgroup, read the Glue catalog, and write Athena results under PREFIX_ROOT."""
        p, region, account = self.partition, self.region, self.account
        bucket_arn = f"arn:{p}:s3:::{self.bucket}"
        read = [f"{bucket_arn}/{self.kiro_logs_prefix()}*"]
        buckets = [bucket_arn]
        databases = [GLUE_DATABASE]
        if self.quick_bucket and any(d in JOIN_DATASETS for d in self.selected):
            read.append(f"arn:{p}:s3:::{self.quick_bucket}/{QUICK_PREFIX_ROOT}/*")
            buckets.append(f"arn:{p}:s3:::{self.quick_bucket}")
            databases.append(QUICK_GLUE_DATABASE)
        glue = [f"arn:{p}:glue:{region}:{account}:catalog"]
        for db in databases:
            glue += [
                f"arn:{p}:glue:{region}:{account}:database/{db}",
                f"arn:{p}:glue:{region}:{account}:table/{db}/*",
            ]
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ReadUsageReports",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": read,
                },
                {
                    "Sid": "ListUsageReportBuckets",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": buckets,
                },
                {
                    "Sid": "WriteAthenaResults",
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:AbortMultipartUpload",
                        "s3:ListMultipartUploadParts",
                    ],
                    "Resource": [f"{bucket_arn}/{ATHENA_RESULTS_PREFIX}/*"],
                },
                {
                    "Sid": "RunAthena",
                    "Effect": "Allow",
                    "Action": [
                        "athena:StartQueryExecution",
                        "athena:StopQueryExecution",
                        "athena:GetQueryExecution",
                        "athena:GetQueryResults",
                        "athena:GetQueryResultsStream",
                        "athena:BatchGetQueryExecution",
                        "athena:GetWorkGroup",
                        "athena:GetDataCatalog",
                        "athena:GetTableMetadata",
                    ],
                    "Resource": [
                        self.workgroup_arn(),
                        f"arn:{p}:athena:{region}:{account}:datacatalog/AwsDataCatalog",
                    ],
                },
                {
                    "Sid": "ReadGlueCatalog",
                    "Effect": "Allow",
                    "Action": [
                        "glue:GetDatabase",
                        "glue:GetDatabases",
                        "glue:GetTable",
                        "glue:GetTables",
                        "glue:GetPartition",
                        "glue:GetPartitions",
                    ],
                    "Resource": glue,
                },
                # Needed when the Glue catalog is governed by Lake Formation; harmless otherwise.
                {
                    "Sid": "LakeFormationDataAccess",
                    "Effect": "Allow",
                    "Action": ["lakeformation:GetDataAccess"],
                    "Resource": "*",
                },
            ],
        }

    def data_source_role_state(self):
        """What's missing or outdated on the data source role. Stops on a role that isn't ours."""
        role = self.data_source_role()
        if role is None:
            return ["role"]
        if not self.is_our_data_source_role(role):
            raise Abort(
                f"IAM role {self.data_source_role_name()} exists but is trusted by more than "
                "quicksight.amazonaws.com, so it isn't this script's.\n  The script won't change "
                "it. Delete or rename it, then re-run.",
                resume_step=ATHENA_STEP,
            )
        missing = []
        actions = set()
        for st in as_list(
            policy_document(role.get("AssumeRolePolicyDocument")).get("Statement", [])
        ):
            if st.get("Effect") == "Allow":
                actions |= set(as_list(st.get("Action", [])))
        if not {"sts:AssumeRole", "sts:TagSession"} <= actions:
            missing.append("trust policy")
        try:
            doc = self.iam.get_role_policy(
                RoleName=self.data_source_role_name(),
                PolicyName=DATA_SOURCE_ROLE_POLICY,
            )["PolicyDocument"]
            if policy_document(doc) != self.data_source_role_policy_document():
                missing.append("role policy")
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise
            missing.append("role policy")
        return missing

    def ensure_data_source_role(self):
        name = self.data_source_role_name()
        missing = self.data_source_role_state()
        if "role" in missing:
            self.iam.create_role(
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(self.data_source_role_trust()),
                Description="Lets Quick read the Kiro user activity reports through Athena. "
                "Created by kiro_usage_datasets_setup.py.",
                Tags=[BUCKET_TAG],
            )
            print(f"  IAM role {name} created")
            self.log.write(event="resource", iam_role=name)
            missing = ["role policy"]
        if "trust policy" in missing:
            self.iam.update_assume_role_policy(
                RoleName=name, PolicyDocument=json.dumps(self.data_source_role_trust())
            )
            print(f"  IAM role {name}: trust policy updated")
        if "role policy" in missing:
            self.iam.put_role_policy(
                RoleName=name,
                PolicyName=DATA_SOURCE_ROLE_POLICY,
                PolicyDocument=json.dumps(self.data_source_role_policy_document()),
            )
            print(f"  IAM role {name}: policy {DATA_SOURCE_ROLE_POLICY} set")
            self.log.write(
                event="resource", iam_role_policy=DATA_SOURCE_ROLE_POLICY, role=name
            )

    def athena_results_location(self):
        return f"s3://{self.bucket}/{ATHENA_RESULTS_PREFIX}/"

    def workgroup_arn(self):
        return f"arn:{self.partition}:athena:{self.region}:{self.account}:workgroup/{ATHENA_WORKGROUP}"

    def glue_table_name(self, report):
        return f"kiro_{report}"

    def table_location(self, report):
        return f"s3://{self.bucket}/{self.kiro_logs_prefix()}{REPORTS[report][0]}/"

    def table_input(self, report):
        return {
            "Name": self.glue_table_name(report),
            "TableType": "EXTERNAL_TABLE",
            "Parameters": {"classification": "csv", "EXTERNAL": "TRUE"},
            "StorageDescriptor": {
                "Columns": [{"Name": "line", "Type": "string"}],
                "Location": self.table_location(report),
                "InputFormat": GLUE_TEXT_INPUT,
                "OutputFormat": GLUE_IGNOREKEY_OUTPUT,
                "SerdeInfo": {
                    "SerializationLibrary": GLUE_LINE_SERDE,
                    "Parameters": {
                        "field.delim": "\u0001",
                        "serialization.format": "\u0001",
                    },
                },
            },
        }

    def selected_reports(self):
        return sorted({DATASETS[d][1] for d in self.selected})

    def glue_table(self, report):
        try:
            return self.glue.get_table(
                DatabaseName=GLUE_DATABASE, Name=self.glue_table_name(report)
            )["Table"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityNotFoundException":
                return None
            raise

    def glue_table_missing(self, report):
        table = self.glue_table(report)
        if table is None:
            return True
        loc = table.get("StorageDescriptor", {}).get("Location", "").rstrip("/")
        return loc != self.table_location(report).rstrip("/")

    def glue_database_present(self):
        try:
            self.glue.get_database(Name=GLUE_DATABASE)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityNotFoundException":
                return False
            raise

    def workgroup_present(self):
        try:
            self.athena.get_work_group(WorkGroup=ATHENA_WORKGROUP)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in (
                "InvalidRequestException",
                "ResourceNotFoundException",
            ):
                return False
            raise

    def workgroup_is_ours(self):
        if not self.workgroup_present():
            return False
        tags = self.athena.list_tags_for_resource(ResourceARN=self.workgroup_arn()).get(
            "Tags", []
        )
        return any(
            t.get("Key") == WORKGROUP_TAG["Key"]
            and t.get("Value") == WORKGROUP_TAG["Value"]
            for t in tags
        )

    def ensure_glue_database(self):
        try:
            self.glue.create_database(
                DatabaseInput={
                    "Name": GLUE_DATABASE,
                    "Description": "Kiro user activity reports. Created by kiro_usage_datasets_setup.py.",
                }
            )
            print(f"  Glue database {GLUE_DATABASE} created")
            self.log.write(event="resource", glue_database=GLUE_DATABASE)
        except ClientError as e:
            if e.response["Error"]["Code"] != "AlreadyExistsException":
                raise

    def ensure_workgroup(self):
        loc = self.athena_results_location()
        if not self.workgroup_present():
            self.athena.create_work_group(
                Name=ATHENA_WORKGROUP,
                Configuration={
                    "ResultConfiguration": {"OutputLocation": loc},
                    "EnforceWorkGroupConfiguration": True,
                    "PublishCloudWatchMetricsEnabled": False,
                },
                Description="Kiro user activity reports. Created by kiro_usage_datasets_setup.py.",
                Tags=[WORKGROUP_TAG],
            )
            print(f"  Athena workgroup {ATHENA_WORKGROUP} created")
            self.log.write(event="resource", athena_workgroup=ATHENA_WORKGROUP)
            return
        if not self.workgroup_is_ours():
            raise Abort(
                f"An Athena workgroup named {ATHENA_WORKGROUP} already exists but this script "
                f"didn't create it (it isn't tagged {WORKGROUP_TAG['Key']}="
                f"{WORKGROUP_TAG['Value']}).\n  The script won't change it. Delete or rename it, "
                "then re-run.",
                resume_step=ATHENA_STEP,
            )
        wg = self.athena.get_work_group(WorkGroup=ATHENA_WORKGROUP)["WorkGroup"]
        current = (
            wg.get("Configuration", {})
            .get("ResultConfiguration", {})
            .get("OutputLocation")
        )
        if current != loc:
            self.athena.update_work_group(
                WorkGroup=ATHENA_WORKGROUP,
                ConfigurationUpdates={
                    "ResultConfigurationUpdates": {"OutputLocation": loc},
                    "EnforceWorkGroupConfiguration": True,
                },
            )
            print(
                f"  Athena workgroup {ATHENA_WORKGROUP} results location set to {loc}"
            )
            self.log.write(event="athena_workgroup_results_updated", location=loc)

    def workgroup_results_current(self):
        wg = self.athena.get_work_group(WorkGroup=ATHENA_WORKGROUP)["WorkGroup"]
        current = (
            wg.get("Configuration", {})
            .get("ResultConfiguration", {})
            .get("OutputLocation")
        )
        return current == self.athena_results_location()

    def plan_athena(self):
        lines = [
            "Set up the Athena and Glue resources the datasets read through. Athena reads the",
            f"reports in place under s3://{self.bucket}/{self.kiro_logs_prefix()}, so this works",
            "for reports already there and for new ones, with no limit on the number of files.",
            "This step creates (and --destroy removes):",
            f"  - IAM role {self.data_source_role_name()}: only Quick can use it. It reads the",
            f"    Kiro reports, runs Athena in {ATHENA_WORKGROUP} and reads the Glue catalog.",
        ]
        if self.quick_bucket and any(d in JOIN_DATASETS for d in self.selected):
            lines += [
                "    For the join on user_id it also reads the Quick usage logs in",
                f"    s3://{self.quick_bucket}/{QUICK_PREFIX_ROOT}/ and the {QUICK_GLUE_DATABASE} tables.",
            ]
        lines += [
            "    You need iam:CreateRole, iam:PutRolePolicy and iam:PassRole for this role.",
            f"  - Glue database {GLUE_DATABASE} with one external table per report type:",
            *[
                f"      {self.glue_table_name(r)}  ->  {self.table_location(r)}"
                for r in self.selected_reports()
            ],
            f"  - Athena workgroup {ATHENA_WORKGROUP} (query results under",
            f"    s3://{self.bucket}/{ATHENA_RESULTS_PREFIX}/).",
        ]
        return lines

    def check_athena(self):
        todo, lines = {}, []
        role_missing = self.data_source_role_state()
        if role_missing:
            todo[DATA_SOURCE_ROLE] = role_missing
            lines.append(
                f"data source role: not set up yet (missing: {', '.join(role_missing)})"
            )
        else:
            lines.append(f"data source role {self.data_source_role_name()} is in place")
        shared = []
        if not self.glue_database_present():
            shared.append("Glue database")
        if not self.workgroup_present():
            shared.append("Athena workgroup")
        elif self.workgroup_is_ours() and not self.workgroup_results_current():
            shared.append("Athena workgroup results location")
        if shared:
            todo[ATHENA_SHARED] = shared
            lines.append(f"Athena/Glue: not set up yet (missing: {', '.join(shared)})")
        else:
            lines.append(
                f"Athena/Glue: database {GLUE_DATABASE} and workgroup {ATHENA_WORKGROUP} exist"
            )
        for r in self.selected_reports():
            if self.glue_table_missing(r):
                todo[r] = ["Glue table"]
                lines.append(f"{self.glue_table_name(r)}: Glue table not set up yet")
            else:
                lines.append(f"{self.glue_table_name(r)}: Glue table exists")
        return StepState(not todo, lines, todo)

    def run_athena(self, state=None):
        self.ensure_data_source_role()
        self.ensure_glue_database()
        self.ensure_workgroup()
        for r in self.selected_reports():
            if not self.glue_table_missing(r):
                continue
            exists = self.glue_table(r) is not None
            fn = self.glue.update_table if exists else self.glue.create_table
            fn(DatabaseName=GLUE_DATABASE, TableInput=self.table_input(r))
            print(
                f"  Glue table {self.glue_table_name(r)} {'updated' if exists else 'created'} "
                f"({self.table_location(r)})"
            )
            self.log.write(
                event="resource",
                glue_table=self.glue_table_name(r),
                location=self.table_location(r),
                updated=exists,
            )

    # ---------- datasets step: Quick Athena data sources + SPICE datasets ----------
    def plan_datasets(self):
        lines = [
            "For each dataset, create a Quick Athena data source over the workgroup (through the",
            "role above) and a SPICE dataset that reads the Glue tables:",
        ]
        lines += [f"  - {self.dataset_id(d)}: {DATASETS[d][0]}" for d in self.selected]
        lines += [
            f"Owner of the data sources and datasets: {self.args.quick_admin_arn}",
            "Datasets with no reports yet refresh to 0 rows until the first report arrives.",
            "Each column gets a short description, which Quick's natural-language features use.",
            "Datasets that already exist only get the descriptions they lack (this starts one",
            "refresh); a dataset that was reshaped in Quick is left alone.",
        ]
        if any(d in JOIN_DATASETS for d in self.selected):
            lines += [
                "The Quick + Kiro dataset matches people on user_id, their IAM Identity Center",
                "principal id (Kiro's UserId). Quick users from another identity store don't match",
                "and appear as separate rows.",
                "A Quick + Kiro dataset with one row per user and month is",
                "updated in place to one row per user and day. Then update the starter dashboard",
                "too (dashboard step): until then its Quick + Kiro by user tab can't load.",
            ]
        return lines

    def dataset_state(self, d):
        """Parts of d's Quick setup that are missing. Stops on a conflicting or broken asset."""
        ds_id = self.dataset_id(d)
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
                    f"Quick data source {ds_id} is still being created ({status}). Wait a "
                    "minute, then re-run."
                )
            athena = src.get("DataSourceParameters", {}).get("AthenaParameters", {})
            if (
                athena.get("WorkGroup") != ATHENA_WORKGROUP
                or athena.get("RoleArn") != self.data_source_role_arn()
            ):
                raise Abort(
                    f"Quick data source {ds_id} already exists but doesn't use workgroup "
                    f"{ATHENA_WORKGROUP} with role {self.data_source_role_name()}. The script "
                    "won't change it. Delete the data source and its dataset, then re-run."
                )
            if status.endswith("FAILED"):
                # Ours, left failed by an earlier run (e.g. IAM not ready): run_datasets replaces it.
                missing.append("data source")
        data_set = self.get_or_none(
            self.qs.describe_data_set,
            "DataSet",
            AwsAccountId=self.account,
            DataSetId=ds_id,
        )
        if data_set is None:
            missing.append("dataset")
        else:
            expected = {f if isinstance(f, str) else f[0] for f in DATASETS[d][2]}
            have = {c["Name"] for c in data_set.get("OutputColumns", [])}
            if self.is_monthly_join(d, data_set):
                return missing + [DAILY_ROWS_MISSING], src
            if have and not expected <= have:
                raise Abort(
                    f"Quick dataset {ds_id} already exists but doesn't have the columns this "
                    f"version of the script creates (missing: {', '.join(sorted(expected - have))}).\n"
                    "  The script won't change it. Delete it (and the starter analysis/dashboard "
                    "if you have them), then re-run:\n"
                    f"    aws quicksight delete-data-set --aws-account-id {self.account} "
                    f"--data-set-id {ds_id} --region {self.region}"
                )
            if self.descriptions_to_add(d, data_set):
                missing.append(COLUMN_DESCRIPTIONS_MISSING)
        return missing, src

    def is_monthly_join(self, d, data_set):
        """True for a quick_kiro_by_user with one row per user and month.

        Only a dataset still in the shape the script made (one custom SQL table, at most one
        logical table, no data-prep format) is upgraded; anything reshaped in Quick is not.
        """
        if d not in JOIN_DATASETS or data_set.get("DataPrepConfiguration"):
            return False
        have = {c["Name"] for c in data_set.get("OutputColumns", [])}
        physical = list((data_set.get("PhysicalTableMap") or {}).values())
        return (
            MONTHLY_JOIN_COLUMNS <= have
            and len(physical) == 1
            and "CustomSql" in physical[0]
            and len(data_set.get("LogicalTableMap") or {}) <= 1
        )

    def upgrade_to_daily_rows(self, d):
        """Replace the monthly SQL of quick_kiro_by_user with the daily SQL, keeping its id,
        permissions and data source, so analyses and dashboards that use it keep working
        once the dashboard step updates them. Starts one SPICE refresh.
        """
        ds_id = self.dataset_id(d)
        data_set = self.qs.describe_data_set(
            AwsAccountId=self.account, DataSetId=ds_id
        )["DataSet"]
        accepted = self.qs.meta.service_model.operation_model(
            "UpdateDataSet"
        ).input_shape.members
        params = {
            k: v
            for k, v in data_set.items()
            if k in accepted and k != "DataSetId" and v not in (None, [], {})
        }
        # The previous column groups, folders and permissions name month columns that are gone.
        for key in ("ColumnGroups", "FieldFolders", "ColumnLevelPermissionRules"):
            params.pop(key, None)
        ((physical_id, table),) = data_set["PhysicalTableMap"].items()
        params["PhysicalTableMap"] = {
            physical_id: {
                "CustomSql": {
                    "DataSourceArn": table["CustomSql"]["DataSourceArn"],
                    "Name": ds_id,
                    "SqlQuery": dataset_sql(d, self.glue_table_name),
                    "Columns": sql_columns(DATASETS[d][2]),
                }
            }
        }
        # A fresh logical table: the previous transforms refer to the month columns.
        params["LogicalTableMap"] = logical_table_map(
            ds_id, physical_id, COLUMN_DESCRIPTIONS[d]
        )
        self.qs.update_data_set(AwsAccountId=self.account, DataSetId=ds_id, **params)
        print(f"  {d}: dataset {ds_id} updated to one row per user and day")
        self.log.write(event="dataset_upgraded_to_daily_rows", dataset=d)

    def descriptions_to_add(self, d, data_set):
        """Columns of an existing dataset with no description that the script can safely add.

        Empty for a dataset reshaped in Quick (joins, several logical tables, or the newer
        data-prep format): the script will not modify those. Descriptions already set are kept.
        """
        tables = data_set.get("LogicalTableMap") or {}
        if data_set.get("DataPrepConfiguration") or len(tables) > 1:
            return {}
        if not tables and len(data_set.get("PhysicalTableMap") or {}) != 1:
            return {}
        have = {
            c["Name"]: c.get("Description") for c in data_set.get("OutputColumns", [])
        }
        return {
            n: t for n, t in COLUMN_DESCRIPTIONS[d].items() if n in have and not have[n]
        }

    def add_column_descriptions(self, d):
        """Add the missing column descriptions to an existing dataset, keeping everything else.

        UpdateDataSet replaces the whole definition, so every field DescribeDataSet returned
        that UpdateDataSet accepts is sent back unchanged. It also starts one SPICE refresh.
        """
        ds_id = self.dataset_id(d)
        data_set = self.qs.describe_data_set(
            AwsAccountId=self.account, DataSetId=ds_id
        )["DataSet"]
        to_add = self.descriptions_to_add(d, data_set)
        if not to_add:
            return
        accepted = self.qs.meta.service_model.operation_model(
            "UpdateDataSet"
        ).input_shape.members
        # Empty collections are dropped: several UpdateDataSet lists (ColumnGroups,
        # DatasetParameters, ColumnLevelPermissionRules) must have at least one item.
        params = {
            k: v
            for k, v in data_set.items()
            if k in accepted and k != "DataSetId" and v not in (None, [], {})
        }
        # deepcopy, not a JSON round trip: the logical table can hold datetime values.
        tables = copy.deepcopy(data_set.get("LogicalTableMap") or {})
        if tables:
            (table,) = tables.values()
            table.setdefault("DataTransforms", []).extend(
                column_description_transforms(to_add)
            )
        else:
            (physical_id,) = data_set["PhysicalTableMap"]
            tables = logical_table_map(ds_id, physical_id, to_add)
        params["LogicalTableMap"] = tables
        self.qs.update_data_set(AwsAccountId=self.account, DataSetId=ds_id, **params)
        print(f"  {d}: added descriptions to {len(to_add)} columns of dataset {ds_id}")
        self.log.write(
            event="column_descriptions_added", dataset=d, columns=sorted(to_add)
        )

    def create_athena_data_source(self, ds_id, attempts=4, delay=15):
        """CreateDataSource (Athena) with the data source role, retrying while IAM propagates."""

        def create():
            return self.qs.create_data_source(
                AwsAccountId=self.account,
                DataSourceId=ds_id,
                Name=ds_id,
                Type="ATHENA",
                DataSourceParameters={
                    "AthenaParameters": {
                        "WorkGroup": ATHENA_WORKGROUP,
                        "RoleArn": self.data_source_role_arn(),
                    }
                },
                Permissions=[
                    {
                        "Principal": self.args.quick_admin_arn,
                        "Actions": DATA_SOURCE_ACTIONS,
                    }
                ],
            )

        def role_not_ready(error):
            return (
                error["Code"] == "AccessDeniedException"
                and "assume" in error.get("Message", "").lower()
            )

        def on_retry(attempt, error):
            self.log.write(
                event="assume_role_denied",
                data_source=ds_id,
                attempt=attempt,
                message=error.get("Message", ""),
            )
            print(
                f"  Quick can't use the new role yet (IAM takes a few seconds); checking again "
                f"in {delay}s ({attempt}/{attempts - 1})..."
            )

        def give_up(exc):
            raise Abort(
                f"Quick can't assume IAM role {self.data_source_role_name()} to run Athena, so it "
                "won't create the data source.\n  Check that the role trusts "
                f"quicksight.amazonaws.com with aws:SourceAccount {self.account}, and that no SCP "
                "denies it. Re-run from the Athena step, which repairs the role.",
                resume_step=ATHENA_STEP,
            )

        return retry_on_client_error(
            create, role_not_ready, attempts, delay, on_retry=on_retry, give_up=give_up
        )

    def wait_for_data_source(self, ds_id, timeout=120):
        """Wait until creation settles. Returns the failed DataSource, or None on success."""
        deadline = time.monotonic() + timeout
        while True:
            src = self.qs.describe_data_source(
                AwsAccountId=self.account, DataSourceId=ds_id
            )["DataSource"]
            status = src.get("Status", "")
            if not status.endswith("IN_PROGRESS"):
                return src if status.endswith("FAILED") else None
            if time.monotonic() > deadline:
                raise Abort(
                    f"Data source {ds_id} is still {status} after {timeout}s. Re-run later."
                )
            time.sleep(3)

    def create_data_source_and_wait(self, ds_id, attempts=4, delay=15):
        """Create the data source and wait for it. Creation is asynchronous, so right after step 4
        creates the role, a new IAM role can take a short time to become usable and Quick can
        report "Failed to assume your role". Then delete the failed one and try again."""
        for attempt in range(1, attempts + 1):
            src = self.create_athena_data_source(ds_id)
            failed = self.wait_for_data_source(ds_id)
            if failed is None:
                return src
            message = (failed.get("ErrorInfo") or {}).get("Message", "")
            self.log.write(
                event="data_source_failed",
                data_source=ds_id,
                attempt=attempt,
                message=message,
            )
            self.delete_if_present(
                self.qs.delete_data_source,
                AwsAccountId=self.account,
                DataSourceId=ds_id,
            )
            if "assume" not in message.lower() or attempt == attempts:
                raise Abort(
                    f"Quick couldn't create data source {ds_id}: {message or 'no details'}\n  Check that "
                    f"IAM role {self.data_source_role_name()} trusts quicksight.amazonaws.com and can run "
                    f"Athena in {ATHENA_WORKGROUP}, then re-run.",
                    resume_step=DATASETS_STEP,
                )
            print(
                f"  Quick can't use the new role yet (IAM takes a few seconds); trying again in "
                f"{delay}s ({attempt}/{attempts - 1})..."
            )
            time.sleep(delay)

    def check_datasets(self):
        todo, lines = {}, []
        for d in self.selected:
            missing, _ = self.dataset_state(d)
            if DAILY_ROWS_MISSING in missing:
                todo[d] = missing
                lines.append(
                    f"{self.dataset_id(d)}: has one row per user and month;"
                    " will be updated to one row per user and day"
                )
            elif missing == [COLUMN_DESCRIPTIONS_MISSING]:
                todo[d] = missing
                lines.append(
                    f"{self.dataset_id(d)}: dataset exists, column descriptions not added yet"
                )
            elif missing:
                todo[d] = missing
                lines.append(
                    f"{self.dataset_id(d)}: not set up yet (missing: {', '.join(missing)})"
                )
            else:
                lines.append(
                    f"{self.dataset_id(d)}: Athena data source and dataset exist"
                )
        return StepState(not todo, lines, todo)

    def run_datasets(self, state=None):
        if not self.args.quick_admin_arn:
            raise Abort("--quick-admin-arn is required for this step.")
        for d in [x for x in (state.todo if state else self.selected) if x in DATASETS]:
            missing, src = self.dataset_state(d)
            ds_id = self.dataset_id(d)
            if "data source" in missing:
                if src is not None:  # a failed one from an earlier run
                    self.delete_if_present(
                        self.qs.delete_data_source,
                        AwsAccountId=self.account,
                        DataSourceId=ds_id,
                    )
                    self.log.write(
                        event="failed_data_source_removed", data_source=ds_id
                    )
                src = self.create_data_source_and_wait(ds_id)
                print(f"  {d}: Athena data source {ds_id} created")
            if "dataset" not in missing:
                if DAILY_ROWS_MISSING in missing:
                    self.upgrade_to_daily_rows(d)
                    continue
                if COLUMN_DESCRIPTIONS_MISSING in missing:
                    self.add_column_descriptions(d)
                    continue
                print(f"  {d}: dataset {ds_id} already exists, kept")
                continue
            self.qs.create_data_set(
                AwsAccountId=self.account,
                DataSetId=ds_id,
                Name=f"Kiro usage - {d}",
                ImportMode="SPICE",
                PhysicalTableMap={
                    "t": {
                        "CustomSql": {
                            "DataSourceArn": src["Arn"],
                            "Name": ds_id,
                            "SqlQuery": dataset_sql(d, self.glue_table_name),
                            "Columns": sql_columns(DATASETS[d][2]),
                        }
                    }
                },
                LogicalTableMap=logical_table_map(ds_id, "t", COLUMN_DESCRIPTIONS[d]),
                Permissions=[
                    {"Principal": self.args.quick_admin_arn, "Actions": DATASET_ACTIONS}
                ],
            )
            print(f"  {d}: dataset {ds_id} created")
            self.log.write(
                event="resource", dataset=d, data_source=src["Arn"], data_set=ds_id
            )

    # ---------- confirmation phrase per step ----------
    def confirmation_phrase(self, key):
        """What the customer types to approve a step. It says what the step does."""
        return {
            "bucket": "LET KIRO WRITE TO MY BUCKET"
            if self.bucket_exists()
            else "CREATE KIRO BUCKET",
            "reporting": "I TURNED ON KIRO REPORTS",
            "athena": "CREATE ATHENA RESOURCES",
            "datasets": "CREATE DATASETS",
            "refresh": "ENABLE DAILY REFRESH",
            "dashboard": "UPDATE DASHBOARD"
            if getattr(self, "dashboard_outdated", {})
            else "CREATE DASHBOARD",
        }[key]

    def confirmation_alternatives(self, key):
        """Other phrases a step accepts besides its confirmation phrase: {phrase: answer}."""
        if key == "reporting":
            return {"IT WAS ALREADY ON": "already"}
        return {}

    # ---------- refresh step: optional daily refresh ----------
    def plan_refresh(self):
        return [
            "OPTIONAL. We recommend refreshing the datasets once a day so they show new reports.",
            "Without a schedule, they only update when someone clicks Refresh now in Quick.",
            "",
            f"Schedule: a full refresh every day at {self.args.refresh_time} ({self.args.timezone}),",
            f"for: {', '.join(self.dataset_id(d) for d in self.selected)}",
            "Kiro writes its reports at 02:00 UTC, so the default runs an hour after that.",
            "(Change it with --refresh-time HH:MM and --timezone.)",
        ]

    def check_refresh(self):
        todo, lines = {}, []
        for d in self.selected:
            ds_id = self.dataset_id(d)
            try:
                schedules = self.qs.list_refresh_schedules(
                    AwsAccountId=self.account, DataSetId=ds_id
                )["RefreshSchedules"]
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
                todo[d] = ["dataset not created yet"]
                lines.append(f"{ds_id}: dataset doesn't exist yet")
                continue
            if schedules:
                # Keep whatever is there, including a customer's own incremental schedule.
                lines.append(
                    f"{ds_id}: already has a refresh schedule "
                    f"({', '.join(s['ScheduleId'] for s in schedules)})"
                )
            else:
                todo[d] = ["schedule"]
                lines.append(f"{ds_id}: no refresh schedule")
        return StepState(not todo, lines, todo)

    def run_refresh(self, state=None):
        for d in state.todo if state else self.selected:
            ds_id = self.dataset_id(d)
            schedule = {
                "ScheduleId": REFRESH_SCHEDULE_ID,
                "ScheduleFrequency": {
                    "Interval": "DAILY",
                    "TimeOfTheDay": self.args.refresh_time,
                    "Timezone": self.args.timezone,
                },
                "RefreshType": "FULL_REFRESH",
            }
            try:
                self.qs.create_refresh_schedule(
                    AwsAccountId=self.account, DataSetId=ds_id, Schedule=schedule
                )
                print(
                    f"  {d}: daily full refresh scheduled at {self.args.refresh_time}"
                )
                outcome = "created"
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceExistsException":
                    raise
                self.qs.update_refresh_schedule(
                    AwsAccountId=self.account, DataSetId=ds_id, Schedule=schedule
                )
                print(f"  {d}: refresh schedule updated to daily full refresh")
                outcome = "updated"
            self.log.write(
                event="refresh_schedule",
                dataset=ds_id,
                outcome=outcome,
                time=self.args.refresh_time,
                timezone=self.args.timezone,
            )

    def skip_refresh(self, reason):
        print("  Skipped. The datasets won't update on their own. To refresh them:")
        print("    - in Quick: Datasets > the dataset > Refresh now, or")
        print(
            "    - add a schedule later: Datasets > the dataset > Refresh > Add schedule."
        )
        self.log.write(event="refresh_skipped", reason=reason)

    # ---------- dashboard step: optional starter dashboard ----------
    def dataset_arns(self):
        base = f"arn:{self.partition}:quicksight:{self.region}:{self.account}:dataset/"
        arns = {d: base + self.dataset_id(d) for d in DASHBOARD_DATASETS}
        arns.update({d: base + f"quick-usage-{d}" for d in QUICK_DASHBOARD_DATASETS})
        return arns

    def quick_link(self, kind, asset_id):
        return link(
            f"https://{self.region}.quicksight.aws.amazon.com/sn/{kind}/{asset_id}"
        )

    def dashboard_scope_problem(self):
        """Why the starter dashboard can't be built for this run, or None."""
        missing = [d for d in DASHBOARD_DATASETS if d not in self.selected]
        if missing:
            return (
                "The starter dashboard reads the user_report, by_user_analytic and "
                f"quick_kiro_by_user datasets, but this run doesn't set up {', '.join(missing)}."
            )
        return None

    def plan_dashboard(self):
        problem = self.dashboard_scope_problem()
        if problem:
            return [problem, "This step will be skipped."]
        sheets = "  Sheets: Overview, Kiro, Quick + Kiro by user, Monthly report."
        outdated = getattr(self, "dashboard_outdated", {})
        if outdated:
            current = ", ".join(f"{kind} version {v}" for kind, v in outdated.items())
            return [
                f"OPTIONAL. A newer starter dashboard is available: version {DASHBOARD_VERSION} "
                f"(yours: {current}).",
                sheets,
                f"Updating replaces the definition of {ANALYSIS_ID} and {DASHBOARD_ID} in place;",
                "IDs, links, owner and sharing stay the same. Changes you made to the analysis are",
                "overwritten: use Save as in Quick first, or type SKIP to keep your version.",
            ]
        return [
            f"OPTIONAL. Create a starter analysis and dashboard, '{DASHBOARD_NAME}', over the Kiro",
            "datasets and the Quick usage datasets (quick-usage-chat, quick-usage-chat_feedback,",
            "quick-usage-agent_hours, quick-usage-index_usage):",
            f"  - analysis  {ANALYSIS_ID}  (open it to change or add visuals)",
            f"  - dashboard {DASHBOARD_ID}  (the read-only view you can share)",
            sheets,
            f"Owner of both: {self.args.quick_admin_arn}. Nobody else can see them until you share.",
            "",
            "What a viewer sees: per-user Kiro and Quick use (email addresses and Quick user",
            "names), credits, messages and agent hours, for every user.",
            "Visuals stay empty until the datasets have data and have been refreshed.",
        ]

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
                f"    aws quicksight {delete} --aws-account-id {self.account} --region {self.region}"
            )
        if status.endswith("IN_PROGRESS"):
            raise Abort(
                f"Quick {kind} {asset_id} is still being created or updated ({status}). "
                "Wait a minute, then re-run."
            )
        if status.endswith("FAILED"):
            raise Abort(
                f"Quick {kind} {asset_id} is in state {status}. Delete it, then re-run:\n"
                f"    aws quicksight {delete} --aws-account-id {self.account} --region {self.region}"
            )
        return status

    def asset_arn(self, kind, asset_id):
        return f"arn:{self.partition}:quicksight:{self.region}:{self.account}:{kind}/{asset_id}"

    def asset_version(self, kind, asset_id):
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
            if tag.get("Key") == DASHBOARD_VERSION_TAG:
                try:
                    return int(tag.get("Value"))
                except (TypeError, ValueError):
                    return 1
        return 1

    def version_tags(self):
        return [{"Key": DASHBOARD_VERSION_TAG, "Value": str(DASHBOARD_VERSION)}]

    def check_dashboard(self):
        todo, lines = {}, []
        missing = [
            self.dataset_id(d)
            for d in DASHBOARD_DATASETS
            if self.get_or_none(
                self.qs.describe_data_set,
                "DataSet",
                AwsAccountId=self.account,
                DataSetId=self.dataset_id(d),
            )
            is None
        ] + self.missing_quick_datasets()
        if missing:
            todo["datasets"] = missing
            lines.append(f"datasets not created yet: {', '.join(missing)}")
        self.dashboard_outdated = {}
        for kind, asset_id in DASHBOARD_ASSETS:
            if self.asset_state(kind, asset_id) is None:
                todo[kind] = [asset_id]
                lines.append(f"{kind} {asset_id}: not created yet")
                continue
            version = self.asset_version(kind, asset_id)
            if version is not None and version < DASHBOARD_VERSION:
                self.dashboard_outdated[kind] = version
                todo.setdefault("update", []).append(asset_id)
                lines.append(
                    f"{kind} {asset_id}: version {version}, newer version "
                    f"{DASHBOARD_VERSION} available"
                )
            else:
                lines.append(f"{kind} {asset_id}: already exists, kept as is")
        return StepState(not todo, lines, todo)

    def wait_for_asset(self, kind, asset_id, version_number=None):
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
                    resume_step=DASHBOARD_STEP,
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
                resume_step=DASHBOARD_STEP,
            )
        for e in errors:
            print(f"  WARNING ({kind}): {e['type']}: {e['message']}")

    def run_dashboard(self, state=None):
        if not self.args.quick_admin_arn:
            raise Abort("--quick-admin-arn is required for this step.")
        missing = (state.todo if state else self.check_dashboard().todo).get("datasets")
        if missing:
            raise Abort(
                f"The starter dashboard needs datasets that don't exist yet: {', '.join(missing)}. "
                f"Run step {DATASETS_STEP} (and quick_usage_datasets_setup.py for quick-usage-*) "
                "first.",
                resume_step=DATASETS_STEP,
            )
        outdated = getattr(self, "dashboard_outdated", {})
        common = {
            "AwsAccountId": self.account,
            "Name": DASHBOARD_NAME,
            "Definition": dashboard_definition(self.dataset_arns()),
            "ValidationStrategy": {"Mode": "STRICT"},
        }
        if self.asset_state("analysis", ANALYSIS_ID) is None:
            self.qs.create_analysis(
                **common,
                AnalysisId=ANALYSIS_ID,
                Permissions=[
                    {
                        "Principal": self.args.quick_admin_arn,
                        "Actions": ANALYSIS_ACTIONS,
                    }
                ],
                Tags=self.version_tags(),
            )
            self.wait_for_asset("analysis", ANALYSIS_ID)
            print(f"  analysis {ANALYSIS_ID} created")
            self.log.write(event="resource", analysis=ANALYSIS_ID)
        elif "analysis" in outdated:
            self.qs.update_analysis(**common, AnalysisId=ANALYSIS_ID)
            self.wait_for_asset("analysis", ANALYSIS_ID)
            self.qs.tag_resource(
                ResourceArn=self.asset_arn("analysis", ANALYSIS_ID),
                Tags=self.version_tags(),
            )
            print(f"  analysis {ANALYSIS_ID} updated to version {DASHBOARD_VERSION}")
            self.log.write(
                event="dashboard_updated",
                kind="analysis",
                asset_id=ANALYSIS_ID,
                from_version=outdated["analysis"],
                to_version=DASHBOARD_VERSION,
            )
        else:
            print(f"  analysis {ANALYSIS_ID} already exists, kept")
        if self.asset_state("dashboard", DASHBOARD_ID) is None:
            self.qs.create_dashboard(
                **common,
                DashboardId=DASHBOARD_ID,
                Permissions=[
                    {
                        "Principal": self.args.quick_admin_arn,
                        "Actions": DASHBOARD_ACTIONS,
                    }
                ],
                Tags=self.version_tags(),
            )
            self.wait_for_asset("dashboard", DASHBOARD_ID)
            print(f"  dashboard {DASHBOARD_ID} created")
            self.log.write(event="resource", dashboard=DASHBOARD_ID)
        elif "dashboard" in outdated:
            response = self.qs.update_dashboard(**common, DashboardId=DASHBOARD_ID)
            version_number = int(response["VersionArn"].rsplit("/", 1)[1])
            self.wait_for_asset("dashboard", DASHBOARD_ID, version_number)
            self.qs.update_dashboard_published_version(
                AwsAccountId=self.account,
                DashboardId=DASHBOARD_ID,
                VersionNumber=version_number,
            )
            self.qs.tag_resource(
                ResourceArn=self.asset_arn("dashboard", DASHBOARD_ID),
                Tags=self.version_tags(),
            )
            print(
                f"  dashboard {DASHBOARD_ID} updated to version {DASHBOARD_VERSION} (published)"
            )
            self.log.write(
                event="dashboard_updated",
                kind="dashboard",
                asset_id=DASHBOARD_ID,
                from_version=outdated["dashboard"],
                to_version=DASHBOARD_VERSION,
                quick_version_number=version_number,
            )
        else:
            print(f"  dashboard {DASHBOARD_ID} already exists, kept")
        print(f"  Open the dashboard: {self.quick_link('dashboards', DASHBOARD_ID)}")
        print(f"  Edit it in the analysis: {self.quick_link('analyses', ANALYSIS_ID)}")

    def skip_dashboard(self, reason):
        if getattr(self, "dashboard_outdated", {}):
            print(
                "  Skipped. Your current analysis and dashboard are kept as they are. Re-run"
            )
            print(f"  with --from-step {DASHBOARD_STEP} to update them later.")
            self.log.write(event="dashboard_update_skipped", reason=reason)
            return
        print(
            "  Skipped. No analysis or dashboard was created. You can build your own in Quick"
        )
        print(
            f"  from the kiro-usage-* datasets, or re-run with --from-step {DASHBOARD_STEP}."
        )
        self.log.write(event="dashboard_skipped", reason=reason)

    # ---------- verify step ----------
    def plan_verify(self):
        return ["Read-only. Show what has landed in the bucket and what was set up."]

    def run_verify(self, state=None):
        for r in REPORTS:
            count, newest = self.count_reports(REPORTS[r][0] + "/")
            when = f", newest {newest['LastModified']:%Y-%m-%d}" if newest else ""
            print(f"  {r} reports: {count}{'+' if count >= 1000 else ''} file(s){when}")
        for d in self.selected:
            print(
                f"  dataset {self.dataset_id(d)}: {self.quick_link('data-sets', self.dataset_id(d))}"
            )
        if self.get_or_none(
            self.qs.describe_dashboard,
            "Dashboard",
            AwsAccountId=self.account,
            DashboardId=DASHBOARD_ID,
        ):
            print(f"  starter dashboard: {self.quick_link('dashboards', DASHBOARD_ID)}")

    # ---------- destroy: undo the setup steps ----------
    # Each part has find_<key>_to_remove (read-only), plan_remove_<key> and remove_<key>.
    def delete_if_present(self, fn, **kwargs):
        try:
            fn(**kwargs)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in (
                "ResourceNotFoundException",
                "NoSuchKey",
                "404",
                "EntityNotFoundException",
                "NoSuchEntity",
            ):
                return False
            raise

    def removal_phrase(self, key, state):
        if key == "bucket":
            return (
                "DELETE BUCKET AND KIRO REPORTS"
                if "bucket" in state.todo
                else "DELETE ATHENA RESULTS FROM BUCKET"
            )
        return {
            "dashboard": "DELETE DASHBOARD",
            "refresh": "DELETE REFRESH SCHEDULES",
            "datasets": "DELETE DATASETS",
            "athena": "DELETE ATHENA RESOURCES",
            "role": "DELETE DATA SOURCE ROLE",
            "reporting": "I TURNED OFF KIRO REPORTS",
        }[key]

    def find_dashboard_to_remove(self):
        todo, lines = {}, []
        if self.get_or_none(
            self.qs.describe_dashboard,
            "Dashboard",
            AwsAccountId=self.account,
            DashboardId=DASHBOARD_ID,
        ):
            todo["dashboard"] = [DASHBOARD_ID]
            lines.append(f"dashboard {DASHBOARD_ID}")
        if (
            self.get_or_none(
                self.qs.describe_analysis,
                "Analysis",
                AwsAccountId=self.account,
                AnalysisId=ANALYSIS_ID,
            )
            is not None
        ):
            todo["analysis"] = [ANALYSIS_ID]
            lines.append(f"analysis {ANALYSIS_ID}")
        if not todo:
            lines.append("no starter dashboard or analysis")
        return StepState(not todo, lines, todo)

    def plan_remove_dashboard(self, state):
        return [
            "Delete the starter dashboard and analysis. Your changes to them are lost, and anyone",
            "you shared the dashboard with loses it. To keep a copy, use Save as in Quick first.",
        ]

    def remove_dashboard(self, state):
        if "dashboard" in state.todo:
            self.delete_if_present(
                self.qs.delete_dashboard,
                AwsAccountId=self.account,
                DashboardId=DASHBOARD_ID,
            )
            print(f"  dashboard {DASHBOARD_ID} deleted")
            self.log.write(event="resource_deleted", dashboard=DASHBOARD_ID)
        if "analysis" in state.todo:
            self.delete_if_present(
                self.qs.delete_analysis,
                AwsAccountId=self.account,
                AnalysisId=ANALYSIS_ID,
                ForceDeleteWithoutRecovery=True,
            )
            print(f"  analysis {ANALYSIS_ID} deleted")
            self.log.write(event="resource_deleted", analysis=ANALYSIS_ID)

    def find_refresh_to_remove(self):
        todo, lines = {}, []
        for d in self.selected:
            ds_id = self.dataset_id(d)
            try:
                schedules = self.qs.list_refresh_schedules(
                    AwsAccountId=self.account, DataSetId=ds_id
                )["RefreshSchedules"]
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
                continue
            if REFRESH_SCHEDULE_ID in [s["ScheduleId"] for s in schedules]:
                todo[d] = [REFRESH_SCHEDULE_ID]
                lines.append(f"{ds_id}: daily refresh schedule {REFRESH_SCHEDULE_ID}")
        if not todo:
            lines.append("no daily refresh schedules from this script")
        return StepState(not todo, lines, todo)

    def plan_remove_refresh(self, state):
        return [
            f"Delete the daily refresh schedule ({REFRESH_SCHEDULE_ID}) on: "
            + ", ".join(self.dataset_id(d) for d in state.todo),
            "The datasets stop updating on their own. Nothing else changes.",
        ]

    def remove_refresh(self, state):
        for d in state.todo:
            self.delete_if_present(
                self.qs.delete_refresh_schedule,
                AwsAccountId=self.account,
                DataSetId=self.dataset_id(d),
                ScheduleId=REFRESH_SCHEDULE_ID,
            )
            print(f"  {d}: daily refresh schedule deleted")
            self.log.write(
                event="resource_deleted",
                refresh_schedule=REFRESH_SCHEDULE_ID,
                dataset=d,
            )

    def find_datasets_to_remove(self):
        todo, lines = {}, []
        for d in self.selected:
            ds_id = self.dataset_id(d)
            parts = []
            if self.get_or_none(
                self.qs.describe_data_set,
                "DataSet",
                AwsAccountId=self.account,
                DataSetId=ds_id,
            ):
                parts.append("dataset")
            src = self.get_or_none(
                self.qs.describe_data_source,
                "DataSource",
                AwsAccountId=self.account,
                DataSourceId=ds_id,
            )
            if src is not None:
                athena = src.get("DataSourceParameters", {}).get("AthenaParameters", {})
                if (
                    athena.get("WorkGroup") == ATHENA_WORKGROUP
                    and athena.get("RoleArn") == self.data_source_role_arn()
                ):
                    parts.append("data source")
                else:
                    lines.append(
                        f"{ds_id}: a data source with this id exists but isn't this script's. Kept."
                    )
            if parts:
                todo[d] = parts
                lines.append(f"{ds_id}: {', '.join(parts)}")
        if not todo:
            lines.append("no datasets or data sources from this script")
        return StepState(not todo, lines, todo)

    def plan_remove_datasets(self, state):
        return [
            "Delete these Quick datasets (with their SPICE data) and their Athena data sources:",
            *[
                f"  {self.dataset_id(d)}: {', '.join(parts)}"
                for d, parts in state.todo.items()
            ],
            "Any analysis or dashboard you built yourself on these datasets stops working.",
            "The Kiro reports in the bucket aren't touched in this step.",
        ]

    def remove_datasets(self, state):
        for d, parts in state.todo.items():
            ds_id = self.dataset_id(d)
            if "dataset" in parts:
                self.delete_if_present(
                    self.qs.delete_data_set, AwsAccountId=self.account, DataSetId=ds_id
                )
                print(f"  {d}: dataset {ds_id} deleted")
            if "data source" in parts:
                self.delete_if_present(
                    self.qs.delete_data_source,
                    AwsAccountId=self.account,
                    DataSourceId=ds_id,
                )
                print(f"  {d}: data source {ds_id} deleted")
            self.log.write(event="resource_deleted", dataset=d, parts=parts)

    def glue_database_only_ours(self):
        names, token = [], None
        while True:
            kw = {"DatabaseName": GLUE_DATABASE}
            if token:
                kw["NextToken"] = token
            resp = self.glue.get_tables(**kw)
            names += [t["Name"] for t in resp.get("TableList", [])]
            token = resp.get("NextToken")
            if not token:
                break
        return all(n in {self.glue_table_name(r) for r in REPORTS} for n in names)

    def find_athena_to_remove(self):
        todo, lines = {}, []
        for r in REPORTS:
            if self.glue_database_present() and self.glue_table(r) is not None:
                todo[r] = ["Glue table"]
                lines.append(f"Glue table {GLUE_DATABASE}.{self.glue_table_name(r)}")
        shared = []
        if self.workgroup_is_ours():
            shared.append("workgroup")
        elif self.workgroup_present():
            lines.append(
                f"Athena workgroup {ATHENA_WORKGROUP} isn't tagged as this script's; kept."
            )
        if self.glue_database_present() and self.glue_database_only_ours():
            shared.append("Glue database")
        if shared:
            todo[ATHENA_SHARED] = shared
            lines.append(f"shared: {', '.join(shared)}")
        if not todo:
            lines.append("no Glue tables, database or workgroup from this script")
        return StepState(not todo, lines, todo)

    def plan_remove_athena(self, state):
        lines = ["Delete these Athena/Glue resources:"]
        lines += [
            f"  Glue table {self.glue_table_name(r)}"
            for r in state.todo
            if r in REPORTS
        ]
        lines += [f"  {s}" for s in state.todo.get(ATHENA_SHARED, [])]
        lines.append("The Kiro reports in the bucket aren't touched in this step.")
        return lines

    def remove_athena(self, state):
        for r in [r for r in state.todo if r in REPORTS]:
            self.delete_if_present(
                self.glue.delete_table,
                DatabaseName=GLUE_DATABASE,
                Name=self.glue_table_name(r),
            )
            print(f"  Glue table {self.glue_table_name(r)} deleted")
            self.log.write(event="resource_deleted", glue_table=self.glue_table_name(r))
        if self.workgroup_is_ours():
            self.delete_if_present(
                self.athena.delete_work_group,
                WorkGroup=ATHENA_WORKGROUP,
                RecursiveDeleteOption=True,
            )
            print(f"  Athena workgroup {ATHENA_WORKGROUP} deleted")
            self.log.write(event="resource_deleted", athena_workgroup=ATHENA_WORKGROUP)
        if self.glue_database_present() and self.glue_database_only_ours():
            self.delete_if_present(self.glue.delete_database, Name=GLUE_DATABASE)
            print(f"  Glue database {GLUE_DATABASE} deleted")
            self.log.write(event="resource_deleted", glue_database=GLUE_DATABASE)

    def find_role_to_remove(self):
        role = self.data_source_role()
        if role is None:
            return StepState(True, ["no data source role from this script"])
        if not self.is_our_data_source_role(role):
            return StepState(
                True,
                [f"IAM role {self.data_source_role_name()} isn't this script's. Kept."],
            )
        return StepState(
            False,
            [f"IAM role {self.data_source_role_name()}"],
            {"role": [self.data_source_role_name()]},
        )

    def plan_remove_role(self, state):
        return [
            f"Delete IAM role {self.data_source_role_name()} and its inline policy. Nothing else uses it."
        ]

    def remove_role(self, state):
        name = self.data_source_role_name()
        for policy in self.iam.list_role_policies(RoleName=name).get("PolicyNames", []):
            self.iam.delete_role_policy(RoleName=name, PolicyName=policy)
        self.delete_if_present(self.iam.delete_role, RoleName=name)
        print(f"  IAM role {name} deleted")
        self.log.write(event="resource_deleted", iam_role=name)

    def find_reporting_to_remove(self):
        """Offer to turn Kiro reporting off only if this script's setup had the customer turn it on."""
        record = self.reporting_record()
        if record and record[0] == "turned_on":
            return StepState(
                False,
                [
                    f"Kiro reports go to {self.reports_uri()} (you turned them on "
                    f"during setup on {record[1][:10]})"
                ],
                {"reporting": ["on"]},
            )
        return StepState(
            True, ["Kiro reporting wasn't turned on by this script; left as is"]
        )

    def plan_remove_reporting(self, state):
        return [
            bold("MANUAL STEP (in the Kiro console)."),
            f"  1. Open {self.kiro_settings_link()}",
            "  2. Under 'Kiro user activity reports', choose Edit.",
            "  3. Turn off 'Collect granular metrics per user', then Save.",
            "Do this before the bucket is deleted, or Kiro keeps trying to write to it.",
        ]

    def remove_reporting(self, state):
        self.log.write(
            event="kiro_reporting", outcome="turned_off", reports_uri=self.reports_uri()
        )
        print("  Recorded: Kiro user activity reports turned off.")

    def other_files_in_bucket(self):
        """True if the bucket holds anything outside the reports prefix and the script's folder."""
        resp = self.s3.list_object_versions(
            Bucket=self.bucket, Delimiter="/", MaxKeys=1000
        )
        ours = {PREFIX_ROOT + "/", self.reports_prefix.split("/")[0] + "/"}
        prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
        return bool(
            resp.get("Versions")
            or resp.get("DeleteMarkers")
            or [p for p in prefixes if p not in ours]
            or resp.get("IsTruncated")
        )

    def object_versions(self, prefix, skip_prefix=None):
        found = []
        for page in self.s3.get_paginator("list_object_versions").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            for v in page.get("Versions", []) + page.get("DeleteMarkers", []):
                if skip_prefix and v["Key"].startswith(skip_prefix):
                    continue
                found.append({"Key": v["Key"], "VersionId": v["VersionId"]})
        return found

    def find_bucket_to_remove(self):
        if not self.bucket_exists():
            return StepState(True, [f"bucket s3://{self.bucket} doesn't exist"])
        ours = self.bucket_has_our_tag() or any(
            e.get("event") == "resource"
            and e.get("bucket") == self.bucket
            and "bucket_policy_statement" not in e
            for e in self.earlier_log_entries()
        )
        whole = ours and not self.other_files_in_bucket()
        todo, lines = {}, []
        if whole:
            files = self.object_versions(PREFIX_ROOT + "/") + self.object_versions(
                self.reports_prefix + "/"
            )
            todo["bucket"] = [self.bucket]
            lines.append(
                f"bucket s3://{self.bucket}: created by this script, nothing else in it"
            )
        else:
            # Only the script's own files (Athena results); confirmation logs and reports stay.
            files = self.object_versions(
                PREFIX_ROOT + "/", skip_prefix=LOG_PREFIX + "/"
            )
            lines.append(
                f"bucket s3://{self.bucket}: "
                + (
                    "created by this script, but it has other files too. Kept."
                    if ours
                    else "not created by this script. Kept, with its Kiro reports."
                )
            )
        if files:
            todo["files"] = files
        lines.append(f"{len(files)} file(s) to delete")
        return StepState(not todo, lines, todo)

    def plan_remove_bucket(self, state):
        n = len(state.todo.get("files", []))
        if "bucket" in state.todo:
            return [
                f"Delete all {n} file(s) in s3://{self.bucket}, including every Kiro report, then",
                "the bucket itself. This can't be undone: Kiro doesn't write earlier days again.",
                f"This run's confirmation log stays local: {getattr(self.log, 'path', '')}",
            ]
        return [
            f"Delete {n} file(s) under s3://{self.bucket}/{PREFIX_ROOT}/ (Athena query results).",
            "The bucket, the Kiro reports, its bucket policy and the confirmation logs are kept.",
        ]

    def remove_bucket(self, state):
        files = state.todo.get("files", [])
        for i in range(0, len(files), 1000):
            resp = self.s3.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": files[i : i + 1000], "Quiet": True},
                ExpectedBucketOwner=self.account,
            )
            if resp.get("Errors"):
                first = resp["Errors"][0]
                raise Abort(
                    f"S3 couldn't delete {len(resp['Errors'])} file(s), for example "
                    f"{first.get('Key')}: {first.get('Code')} {first.get('Message')}"
                )
        print(f"  {len(files)} file(s) deleted")
        self.log.write(event="files_deleted", bucket=self.bucket, count=len(files))
        if "bucket" in state.todo:
            self.s3.delete_bucket(Bucket=self.bucket, ExpectedBucketOwner=self.account)
            print(f"  bucket s3://{self.bucket} deleted")
            self.log.write(event="resource_deleted", bucket=self.bucket)


# ---------- dataset SQL ----------
# Splits a CSV line on commas outside double quotes, then strips the quotes from each value.
CSV_SPLIT = (
    "transform(regexp_split(regexp_replace(replace(line, U&'\\FEFF', ''), '\\r$', ''), "
    '\',(?=(?:[^"]*"[^"]*")*[^"]*$)\'), c -> trim(BOTH \'"\' FROM c))'
)


def report_records_cte(table, header_first):
    """CTEs ending in rec(m): every data line as a map from its file's header names to values.

    Each report file starts with its own header line, so values are matched to column names per
    file ("$path"), never by position. Columns a file doesn't have read as NULL (element_at).
    """
    return (
        "raw AS (\n"  # noqa: S608 - names are constants defined in this script, never user input
        f'  SELECT "$path" AS path, {CSV_SPLIT} AS cells\n'
        f'  FROM "{GLUE_DATABASE}"."{table}"\n'
        "  WHERE trim(line) <> ''\n"
        "), hdr AS (\n"
        f"  SELECT path, cells AS names FROM raw WHERE cells[1] = '{header_first}'\n"
        "), rec AS (\n"
        "  SELECT map_from_entries(filter(zip_with(h.names, r.cells,\n"
        "           (k, v) -> IF(k IS NULL, NULL, ROW(k, v))), e -> e IS NOT NULL)) AS m\n"
        "  FROM raw r JOIN hdr h ON r.path = h.path\n"
        f"  WHERE r.cells[1] <> '{header_first}'\n"
        ")"
    )


def value(name, quick_type="STRING"):
    """SQL for one report value, cast to its Quick type."""
    expr = f"element_at(m, '{name}')"
    if quick_type in ATHENA_CAST:
        return f"TRY_CAST({expr} AS {ATHENA_CAST[quick_type]})"
    return expr


def report_date(report):
    return f"CAST(TRY(date_parse(element_at(m, 'Date'), '{REPORTS[report][2]}')) AS timestamp)"


def select_fields(fields):
    out = []
    for f in fields:
        name, quick_type = (f, "STRING") if isinstance(f, str) else f
        out.append(f'{value(name, quick_type)} AS "{name}"')
    return out


def quick_event_time(column="event_timestamp"):
    """Quick usage logs' epoch event_timestamp (ms or s) as a timestamp, like the Quick datasets."""
    ts = f'TRY_CAST("{column}" AS bigint)'
    return f"CAST(from_unixtime(CASE WHEN {ts} > 100000000000 THEN {ts} / 1000 ELSE {ts} END) AS timestamp)"


def dataset_sql(d, table_name):
    """The Athena SQL behind dataset d. table_name maps a report key to its Glue table name."""
    report = DATASETS[d][1]
    header = REPORTS[report][1]
    with_rec = "WITH " + report_records_cte(table_name(report), header)
    date = report_date(report)
    if d == "user_report":
        cols = [f'{date} AS "Date"', "element_at(m, 'UserId') AS \"user_id\""]
        cols += select_fields(USER_REPORT_FIELDS) + [
            f'{date} AS "{EVENT_DATETIME_COLUMN}"'
        ]
        return f"{with_rec}\nSELECT " + ",\n       ".join(cols) + "\nFROM rec"
    if d == "by_user_analytic":
        cols = [
            f'{date} AS "Date"',
            "element_at(m, 'UserId') AS \"user_id\"",
            "element_at(m, 'UserId') AS \"UserId\"",
        ]
        cols += select_fields([(m, "INTEGER") for m in BY_USER_ANALYTIC_METRICS])
        cols.append(f'{date} AS "{EVENT_DATETIME_COLUMN}"')
        return f"{with_rec}\nSELECT " + ",\n       ".join(cols) + "\nFROM rec"
    if d == "model_messages":
        # One row per <model>_messages column (Total_Messages is the sum, not a model).
        cols = [
            f'{date} AS "Date"',
            "element_at(m, 'UserId') AS \"user_id\"",
            *select_fields(
                ["UserId", "Client_Type", "Subscription_Tier", "User_Email"]
            ),
            "regexp_replace(k, '_messages$', '') AS \"model\"",
            'TRY_CAST(v AS bigint) AS "messages"',
            f'{date} AS "{EVENT_DATETIME_COLUMN}"',
        ]
        return (
            f"{with_rec}\nSELECT " + ",\n       ".join(cols) + "\nFROM rec\n"
            "CROSS JOIN UNNEST(map_filter(m, (k, v) -> lower(k) LIKE '%\\_messages' ESCAPE '\\'\n"
            "                             AND lower(k) <> 'total_messages')) AS t(k, v)"
        )
    if d == "quick_kiro_by_user":
        return quick_kiro_sql(with_rec, date)
    raise ValueError(d)


def quick_kiro_sql(with_rec, date):
    """Quick and Kiro use per user and day, full outer joined on (user_id, day).

    user_id is the IAM Identity Center user (principal) id, which Quick's usage logs and Kiro's
    reports both carry. It is the only join key: accounts whose users are provisioned by SCIM
    from an identity provider share that principal id across Quick and Kiro, so both services
    record the same id for a user.
    user_email and quick_user_arn are the user's across all days, not just that day, so the
    dashboard's User label is the same on every row of a user.
    Both sides are aggregated per user and day in Athena first, so SPICE only holds one row
    per user and active day however large the Quick usage logs are. The dashboard sums the days
    of the selected range.
    """
    chat = f'"{QUICK_GLUE_DATABASE}"."quick_usage_chat"'
    hours = f'"{QUICK_GLUE_DATABASE}"."quick_usage_agent_hours"'
    return f"""{with_rec},
kiro AS (
  SELECT date_trunc('day', {date}) AS day, element_at(m, 'UserId') AS user_id,
         max(element_at(m, 'User_Email')) AS user_email,
         sum({value("Credits_Used", "DECIMAL")}) AS kiro_credits,
         sum({value("Overage_Credits_Used", "DECIMAL")}) AS kiro_overage_credits,
         sum({value("Total_Messages", "INTEGER")}) AS kiro_messages
  FROM rec
  WHERE coalesce(element_at(m, 'UserId'), '') <> ''
  GROUP BY 1, 2
), quick_events AS (
  SELECT {quick_event_time()} AS ts, user_id, user_arn,
         TRY_CAST(usage_hours AS double) AS hours, usage_group, 0 AS is_message
  FROM {hours}
  UNION ALL
  SELECT {quick_event_time()} AS ts, user_id, user_arn,
         CAST(NULL AS double) AS hours, CAST(NULL AS varchar) AS usage_group, 1 AS is_message
  FROM {chat}
), quick AS (
  SELECT date_trunc('day', ts) AS day, user_id, max(user_arn) AS user_arn,
         sum(is_message) AS messages,
         sum(coalesce(hours, 0)) AS agent_hours,
         sum(IF(usage_group = 'Extra', hours, 0)) AS extra_hours
  FROM quick_events
  WHERE coalesce(user_id, '') <> ''
  GROUP BY 1, 2
)
SELECT coalesce(k.day, q.day) AS "day",
       coalesce(k.user_id, q.user_id) AS "user_id",
       max(k.user_email) OVER (PARTITION BY coalesce(k.user_id, q.user_id)) AS "user_email",
       max(q.user_arn) OVER (PARTITION BY coalesce(k.user_id, q.user_id)) AS "quick_user_arn",
       coalesce(q.messages, 0) AS "quick_messages",
       coalesce(q.agent_hours, 0) AS "quick_agent_hours",
       coalesce(q.agent_hours, 0) - coalesce(q.extra_hours, 0) AS "quick_included_hours",
       coalesce(q.extra_hours, 0) AS "quick_extra_hours",
       coalesce(k.kiro_credits, 0) AS "kiro_credits",
       coalesce(k.kiro_credits, 0) - coalesce(k.kiro_overage_credits, 0) AS "kiro_included_credits",
       coalesce(k.kiro_overage_credits, 0) AS "kiro_overage_credits",
       coalesce(k.kiro_messages, 0) AS "kiro_messages",
       IF(q.user_id IS NULL, 0, 1) AS "uses_quick",
       IF(k.user_id IS NULL, 0, 1) AS "uses_kiro"
FROM kiro k FULL OUTER JOIN quick q ON k.user_id = q.user_id AND k.day = q.day"""  # noqa: S608 - names are constants defined in this script, never user input


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


def logical_table_map(alias, physical_table_id, descriptions):
    """The dataset's single logical table over its physical table, with column descriptions."""
    return {
        "l": {
            "Alias": alias,
            "Source": {"PhysicalTableId": physical_table_id},
            "DataTransforms": column_description_transforms(descriptions),
        }
    }


def sql_columns(fields):
    return [
        {
            "Name": f if isinstance(f, str) else f[0],
            "Type": "STRING" if isinstance(f, str) else f[1],
        }
        for f in fields
    ]


def dashboard_definition(dataset_arns):
    """Definition of the starter analysis and dashboard (sheets: Overview, Kiro, Quick + Kiro by
    user, Monthly report). dataset_arns maps each DataSetIdentifier (the Kiro dataset keys and the
    Quick usage dataset keys chat, chat_feedback, agent_hours, index_usage) to its dataset ARN.
    """
    definition = json.loads(DASHBOARD_DEFINITION_JSON)
    for declaration in definition["DataSetIdentifierDeclarations"]:
        declaration["DataSetArn"] = dataset_arns[declaration["Identifier"]]
    return definition


# Exported from a Quick analysis (describe-analysis-definition), with each DataSetArn left as a
# ${DATASET_ARN:<dataset>} placeholder that dashboard_definition fills in.
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
   "Identifier": "user_report",
   "DataSetArn": "${DATASET_ARN:user_report}"
  },
  {
   "Identifier": "by_user_analytic",
   "DataSetArn": "${DATASET_ARN:by_user_analytic}"
  },
  {
   "Identifier": "quick_kiro_by_user",
   "DataSetArn": "${DATASET_ARN:quick_kiro_by_user}"
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
      "VisualId": "s1-k2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active Kiro users</b>\n</visual-title>"
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
            "DataSetIdentifier": "user_report",
            "ColumnName": "Active Kiro users"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": [
         {
          "CategoricalDimensionField": {
           "FieldId": "s1-k2-period",
           "Column": {
            "DataSetIdentifier": "user_report",
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
           "FieldId": "s1-k2-period",
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
      "VisualId": "s1-v2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active Kiro users</b>\n</visual-title>"
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
            "FieldId": "t12",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t12"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m13",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Active Kiro users"
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
           "FieldId": "t12",
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
         "HierarchyId": "t12"
        }
       }
      ]
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
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick and Kiro usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\"> | Overview</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">Who uses Quick and Kiro, how much, and how usage is trending.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s1-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"20px\"><b>Quick + Kiro</b></inline>\n  </block>\n</text-box>"
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
         "ElementId": "s1-k3",
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
         "ElementId": "s1-k4",
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
         "ElementId": "s1-k5",
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
         "ElementId": "s1-k6",
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
         "ElementId": "s1-v1",
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
         "ElementId": "s1-k2",
         "ElementType": "VISUAL",
         "ColumnIndex": 6,
         "ColumnSpan": 6,
         "RowIndex": 6,
         "RowSpan": 7,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s1-v2",
         "ElementType": "VISUAL",
         "ColumnIndex": 18,
         "ColumnSpan": 18,
         "RowIndex": 13,
         "RowSpan": 12,
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
   "SheetId": "s6",
   "Name": "Kiro",
   "ParameterControls": [
    {
     "DateTimePicker": {
      "ParameterControlId": "s6-c-pStart",
      "Title": "From",
      "SourceParameterName": "pStart",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {
         "FontSize": {
          "Relative": "MEDIUM"
         }
        }
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "DateTimePicker": {
      "ParameterControlId": "s6-c-pEnd",
      "Title": "To",
      "SourceParameterName": "pEnd",
      "DisplayOptions": {
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {
         "FontSize": {
          "Relative": "MEDIUM"
         }
        }
       },
       "DateTimeFormat": "MMM D, YYYY"
      }
     }
    },
    {
     "Dropdown": {
      "ParameterControlId": "s6-c-gran",
      "Title": "Time scale",
      "SourceParameterName": "pGranularity",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       },
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       }
      },
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
      "ParameterControlId": "s6-c-topn",
      "Title": "Top N",
      "SourceParameterName": "pTopN",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       },
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       }
      },
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
      "FilterControlId": "s6-c-client",
      "Title": "Client",
      "SourceFilterId": "f-s6-client",
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
    },
    {
     "Dropdown": {
      "FilterControlId": "s6-c-tier",
      "Title": "Tier",
      "SourceFilterId": "f-s6-tier",
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
   ],
   "Visuals": [
    {
     "KPIVisual": {
      "VisualId": "s6-k1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active Kiro users</b>\n</visual-title>"
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
            "DataSetIdentifier": "user_report",
            "ColumnName": "Active Kiro users"
           }
          }
         }
        ],
        "TargetValues": [],
        "TrendGroups": [
         {
          "CategoricalDimensionField": {
           "FieldId": "s6-k1-period",
           "Column": {
            "DataSetIdentifier": "user_report",
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
           "FieldId": "s6-k1-period",
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
      "VisualId": "s6-k2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Credits used</b>\n</visual-title>"
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
           "FieldId": "m112",
           "Column": {
            "DataSetIdentifier": "user_report",
            "ColumnName": "Credits used"
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
      "VisualId": "s6-k3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Overage credits</b>\n</visual-title>"
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
           "FieldId": "m113",
           "Column": {
            "DataSetIdentifier": "user_report",
            "ColumnName": "Overage credits"
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
      "VisualId": "s6-k4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Users in overage</b>\n</visual-title>"
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
           "FieldId": "m114",
           "Column": {
            "DataSetIdentifier": "user_report",
            "ColumnName": "Users in overage"
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
      "VisualId": "s6-k5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Inline suggestion acceptance</b>\n</visual-title>"
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
           "FieldId": "m115",
           "Column": {
            "DataSetIdentifier": "by_user_analytic",
            "ColumnName": "Inline acceptance %"
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
      "VisualId": "s6-v1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active Kiro users by client</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "People use several clients, so lines are not added up"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "LineChartAggregatedFieldWells": {
         "Category": [
          {
           "DateDimensionField": {
            "FieldId": "t116",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Period"
            },
            "DateGranularity": "DAY",
            "HierarchyId": "t116"
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m117",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Active Kiro users"
            }
           }
          }
         ],
         "Colors": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d118",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Client"
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
           "FieldId": "t116",
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
         "HierarchyId": "t116"
        }
       }
      ]
     }
    },
    {
     "BarChartVisual": {
      "VisualId": "s6-v2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Credits by tier</b>\n</visual-title>"
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
            "FieldId": "d119",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Tier"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m120",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Overage credits"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m121",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Included credits"
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
           "FieldId": "m120",
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
       "VisualPalette": {
        "ColorMap": [
         {
          "Element": {
           "FieldId": "m120",
           "FieldValue": "Overage credits"
          },
          "Color": "#2E6DB4"
         },
         {
          "Element": {
           "FieldId": "m121",
           "FieldValue": "Included credits"
          },
          "Color": "#4F9DA6"
         }
        ]
       }
      },
      "Actions": [
       {
        "CustomActionId": "s6-a1",
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
     "BarChartVisual": {
      "VisualId": "s6-v3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Average monthly credit use by tier</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Credits used / (tier limit \u00d7 user-months). Above 100% means overage"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "BarChartAggregatedFieldWells": {
         "Category": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d122",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Tier"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m123",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Avg monthly credit use %"
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
           "FieldId": "m123",
           "Direction": "DESC"
          }
         }
        ]
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
      "Actions": [],
      "ColumnHierarchies": []
     }
    },
    {
     "InsightVisual": {
      "VisualId": "s6-v4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Kiro credits forecast</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "HIDDEN"
      },
      "InsightConfiguration": {
       "Computations": [
        {
         "Forecast": {
          "ComputationId": "s6-fc",
          "Name": "Forecast",
          "Time": {
           "DateDimensionField": {
            "FieldId": "t124",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Date"
            },
            "DateGranularity": "MONTH"
           }
          },
          "Value": {
           "NumericalMeasureField": {
            "FieldId": "m125",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Credits used"
            }
           }
          },
          "PeriodsForward": 2,
          "PeriodsBackward": 0,
          "PredictionInterval": 90,
          "Seasonality": "AUTOMATIC"
         }
        }
       ]
      },
      "Actions": [],
      "DataSetIdentifier": "user_report"
     }
    },
    {
     "TableVisual": {
      "VisualId": "s6-v5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Top Kiro users by credits</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "Red rows with a flag: a month at or above 80% of the tier credit limit"
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "TableAggregatedFieldWells": {
         "GroupBy": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d126",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "User"
            }
           }
          },
          {
           "CategoricalDimensionField": {
            "FieldId": "d128",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Tier"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m129",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Credits used"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m130",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Overage credits"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "s6-peak",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Peak monthly credit use %"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m131",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Kiro messages"
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
           "FieldId": "m129",
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
            "Expression": "{Peak monthly credit use %} >= 0.8",
            "Color": "#FBE4E6"
           }
          }
         }
        },
        {
         "Row": {
          "TextColor": {
           "Solid": {
            "Expression": "{Peak monthly credit use %} >= 0.8",
            "Color": "#B3261E"
           }
          }
         }
        },
        {
         "Cell": {
          "FieldId": "s6-peak",
          "TextFormat": {
           "Icon": {
            "CustomCondition": {
             "Expression": "{Peak monthly credit use %} >= 0.8",
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
        "CustomActionId": "s6-a2",
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
     "SheetTextBoxId": "s6-title",
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick and Kiro usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\"> | Kiro</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">Kiro adoption by client, credit use against tier limits, and code acceptance.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s6-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"20px\"><b>Quick + Kiro</b></inline>\n  </block>\n</text-box>"
    }
   ],
   "Layouts": [
    {
     "Configuration": {
      "GridLayout": {
       "Elements": [
        {
         "ElementId": "s6-logo",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
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
         "ElementId": "s6-title",
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
         "ElementId": "s6-c-pStart",
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
         "ElementId": "s6-c-pEnd",
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
         "ElementId": "s6-c-gran",
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
         "ElementId": "s6-c-client",
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
         "ElementId": "s6-c-tier",
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
         "ElementId": "s6-c-topn",
         "ElementType": "PARAMETER_CONTROL",
         "ColumnIndex": 22,
         "ColumnSpan": 14,
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
         "ElementId": "s6-k1",
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
         "ElementId": "s6-k2",
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
         "ElementId": "s6-k3",
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
         "ElementId": "s6-k4",
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
         "ElementId": "s6-k5",
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
         "ElementId": "s6-v1",
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
         "ElementId": "s6-v2",
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
         "ElementId": "s6-v3",
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
         "ElementId": "s6-v4",
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
         "ElementId": "s6-v5",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 37,
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
   "SheetId": "s8",
   "Name": "Quick + Kiro by user",
   "ParameterControls": [
    {
     "DateTimePicker": {
      "ParameterControlId": "s8-c-pStart",
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
      "ParameterControlId": "s8-c-pEnd",
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
      "ParameterControlId": "s8-c-topn",
      "Title": "Top N",
      "SourceParameterName": "pTopN",
      "DisplayOptions": {
       "SelectAllOptions": {
        "Visibility": "VISIBLE"
       },
       "TitleOptions": {
        "Visibility": "VISIBLE",
        "FontConfiguration": {}
       }
      },
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
   "FilterControls": [],
   "Visuals": [
    {
     "KPIVisual": {
      "VisualId": "s8-k1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Quick users</b>\n</visual-title>"
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
           "FieldId": "m132",
           "Column": {
            "DataSetIdentifier": "quick_kiro_by_user",
            "ColumnName": "Quick users"
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
      "VisualId": "s8-k2",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Kiro users</b>\n</visual-title>"
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
           "FieldId": "m133",
           "Column": {
            "DataSetIdentifier": "quick_kiro_by_user",
            "ColumnName": "Kiro users"
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
      "VisualId": "s8-k3",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Users on both</b>\n</visual-title>"
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
           "FieldId": "m134",
           "Column": {
            "DataSetIdentifier": "quick_kiro_by_user",
            "ColumnName": "Users on both"
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
      "VisualId": "s8-k4",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Quick agent hours</b>\n</visual-title>"
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
           "FieldId": "m135",
           "Column": {
            "DataSetIdentifier": "quick_kiro_by_user",
            "ColumnName": "Quick agent hours"
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
      "VisualId": "s8-k5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Quick extra hours</b>\n</visual-title>"
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
           "FieldId": "m136",
           "Column": {
            "DataSetIdentifier": "quick_kiro_by_user",
            "ColumnName": "Quick extra hours"
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
      "VisualId": "s8-k6",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Kiro overage credits</b>\n</visual-title>"
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
           "FieldId": "m137",
           "Column": {
            "DataSetIdentifier": "quick_kiro_by_user",
            "ColumnName": "Kiro overage credits"
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
     "TableVisual": {
      "VisualId": "s8-v1",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Quick + Kiro usage by user</b>\n</visual-title>"
       }
      },
      "Subtitle": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "PlainText": "One row per user for the selected dates. Quick hours (included/extra) and Kiro credits (included/overage) side by side."
       }
      },
      "ChartConfiguration": {
       "FieldWells": {
        "TableAggregatedFieldWells": {
         "GroupBy": [
          {
           "CategoricalDimensionField": {
            "FieldId": "d138",
            "Column": {
             "DataSetIdentifier": "quick_kiro_by_user",
             "ColumnName": "User"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "s8-qh",
            "Column": {
             "DataSetIdentifier": "quick_kiro_by_user",
             "ColumnName": "Quick agent hours"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m140",
            "Column": {
             "DataSetIdentifier": "quick_kiro_by_user",
             "ColumnName": "Quick included hours"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m141",
            "Column": {
             "DataSetIdentifier": "quick_kiro_by_user",
             "ColumnName": "Quick extra hours"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m142",
            "Column": {
             "DataSetIdentifier": "quick_kiro_by_user",
             "ColumnName": "Kiro credits"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m143",
            "Column": {
             "DataSetIdentifier": "quick_kiro_by_user",
             "ColumnName": "Kiro included credits"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m144",
            "Column": {
             "DataSetIdentifier": "quick_kiro_by_user",
             "ColumnName": "Kiro overage credits"
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
           "FieldId": "s8-qh",
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
     "SheetTextBoxId": "s8-title",
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick and Kiro usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\"> | Quick + Kiro by user</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">Quick agent hours and Kiro credits per user for the selected dates, included vs extra/overage.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s8-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"20px\"><b>Quick + Kiro</b></inline>\n  </block>\n</text-box>"
    }
   ],
   "Layouts": [
    {
     "Configuration": {
      "GridLayout": {
       "Elements": [
        {
         "ElementId": "s8-logo",
         "ElementType": "TEXT_BOX",
         "ColumnIndex": 0,
         "ColumnSpan": 4,
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
         "ElementId": "s8-title",
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
         "ElementId": "s8-c-pStart",
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
         "ElementId": "s8-c-pEnd",
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
         "ElementId": "s8-c-topn",
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
         "ElementId": "s8-k1",
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
         "ElementId": "s8-k2",
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
         "ElementId": "s8-k3",
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
         "ElementId": "s8-k4",
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
         "ElementId": "s8-k5",
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
         "ElementId": "s8-k6",
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
         "ElementId": "s8-v1",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 13,
         "RowSpan": 21,
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
     "KPIVisual": {
      "VisualId": "s7-k6",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Active Kiro users</b>\n</visual-title>"
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
           "FieldId": "m150",
           "Column": {
            "DataSetIdentifier": "user_report",
            "ColumnName": "Active Kiro users"
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
    },
    {
     "BarChartVisual": {
      "VisualId": "s7-v5",
      "Title": {
       "Visibility": "VISIBLE",
       "FormatText": {
        "RichText": "<visual-title>\n  <b>Kiro credits by client</b>\n</visual-title>"
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
            "FieldId": "d163",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Client"
            }
           }
          }
         ],
         "Values": [
          {
           "NumericalMeasureField": {
            "FieldId": "m164",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Overage credits"
            }
           }
          },
          {
           "NumericalMeasureField": {
            "FieldId": "m165",
            "Column": {
             "DataSetIdentifier": "user_report",
             "ColumnName": "Included credits"
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
           "FieldId": "m164",
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
       "VisualPalette": {
        "ColorMap": [
         {
          "Element": {
           "FieldId": "m164",
           "FieldValue": "Overage credits"
          },
          "Color": "#2E6DB4"
         },
         {
          "Element": {
           "FieldId": "m165",
           "FieldValue": "Included credits"
          },
          "Color": "#4F9DA6"
         }
        ]
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
     "Content": "<text-box>\n  <block align=\"left\">\n    <inline color=\"#1f2933\" font-size=\"26px\">\n      <b>Amazon Quick and Kiro usage</b>\n    </inline>\n    <inline color=\"#2e6db4\" font-size=\"26px\"> | Monthly report</inline>\n  </block>\n  <br/>\n  <block align=\"left\">\n    <inline color=\"#5f6b7a\" font-size=\"14px\">One-page monthly summary for admins. Choose a month; print or export as PDF.</inline>\n  </block>\n</text-box>"
    },
    {
     "SheetTextBoxId": "s7-logo",
     "Content": "<text-box>\n  <block align=\"center\">\n    <inline color=\"#ff9900\" font-size=\"13px\">Amazon</inline>\n  </block>\n  <block align=\"center\">\n    <inline color=\"#232F3E\" font-size=\"20px\"><b>Quick + Kiro</b></inline>\n  </block>\n</text-box>"
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
         "ElementId": "s7-k2",
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
         "ElementId": "s7-k3",
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
         "ElementId": "s7-k4",
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
         "ElementId": "s7-k5",
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
        },
        {
         "ElementId": "s7-k6",
         "ElementType": "VISUAL",
         "ColumnIndex": 30,
         "ColumnSpan": 6,
         "RowIndex": 6,
         "RowSpan": 7,
         "BackgroundStyle": {
          "Visibility": "VISIBLE",
          "Color": "#FFFFFF"
         },
         "BorderStyle": {
          "Visibility": "HIDDEN"
         }
        },
        {
         "ElementId": "s7-v5",
         "ElementType": "VISUAL",
         "ColumnIndex": 0,
         "ColumnSpan": 36,
         "RowIndex": 27,
         "RowSpan": 12,
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
   "DataSetIdentifier": "by_user_analytic",
   "Name": "AI code lines",
   "Expression": "sum({Inline_AICodeLines}) + sum({Chat_AICodeLines})"
  },
  {
   "DataSetIdentifier": "by_user_analytic",
   "Name": "Inline acceptance %",
   "Expression": "sum({Inline_AcceptanceCount}) / sum({Inline_SuggestionsCount})"
  },
  {
   "DataSetIdentifier": "by_user_analytic",
   "Name": "Kiro active days",
   "Expression": "count({Chat_MessagesSent})"
  },
  {
   "DataSetIdentifier": "by_user_analytic",
   "Name": "Month",
   "Expression": "truncDate(\"MM\", Date)"
  },
  {
   "DataSetIdentifier": "by_user_analytic",
   "Name": "Period",
   "Expression": "ifelse(${pGranularity} = \"Day\", truncDate(\"DD\", Date), ${pGranularity} = \"Month\", truncDate(\"MM\", Date), truncDate(\"WK\", Date))"
  },
  {
   "DataSetIdentifier": "by_user_analytic",
   "Name": "User",
   "Expression": "{UserId}"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "30-day window",
   "Expression": "ifelse(dateDiff(Date, maxOver(Date, [], PRE_AGG), \"DD\") < 30, \"1 Last 30 days\", dateDiff(Date, maxOver(Date, [], PRE_AGG), \"DD\") < 60, \"0 Prior 30 days\", NULL)"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Active Kiro users",
   "Expression": "distinct_count(UserId)"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Avg monthly credit use %",
   "Expression": "sum({Credits_Used}) / (max({Usage_Limit}) * distinct_count({User-month key}))"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Client",
   "Expression": "ifelse({Client_Type} = \"KIRO_IDE\", \"Kiro IDE\", {Client_Type} = \"KIRO_CLI\", \"Kiro CLI\", {Client_Type} = \"KIRO_WEB\", \"Kiro Web\", {Client_Type} = \"PLUGIN\", \"Plugin\", {Client_Type})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Credits used",
   "Expression": "sum({Credits_Used})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Included credits",
   "Expression": "sum({Credits_Used}) - sum({Overage_Credits_Used})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Kiro conversations",
   "Expression": "sum({Chat_Conversations})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Kiro messages",
   "Expression": "sum({Total_Messages})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Month",
   "Expression": "truncDate(\"MM\", Date)"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Monthly credit use (row)",
   "Expression": "sum({Credits_Used}, [UserId, Month]) / max({Usage_Limit}, [UserId, Month])"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Overage credits",
   "Expression": "sum({Overage_Credits_Used})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Peak monthly credit use %",
   "Expression": "max({Monthly credit use (row)})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Period",
   "Expression": "ifelse(${pGranularity} = \"Day\", truncDate(\"DD\", Date), ${pGranularity} = \"Month\", truncDate(\"MM\", Date), truncDate(\"WK\", Date))"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Tier",
   "Expression": "ifelse({Subscription_Tier} = \"FREE\", \"Free\", {Subscription_Tier} = \"PRO\", \"Pro\", {Subscription_Tier} = \"PRO_PLUS\", \"Pro+\", {Subscription_Tier} = \"PRO_MAX\", \"Pro Max\", {Subscription_Tier} = \"POWER\", \"Power\", {Subscription_Tier})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "User",
   "Expression": "{User_Email}"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "User-month key",
   "Expression": "concat(UserId, \"-\", toString(extract(\"YYYY\", Date)), \"-\", toString(extract(\"MM\", Date)))"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "User-months",
   "Expression": "distinct_count({User-month key})"
  },
  {
   "DataSetIdentifier": "user_report",
   "Name": "Users in overage",
   "Expression": "distinct_countIf(UserId, {Overage_Credits_Used} > 0)"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Kiro credits",
   "Expression": "sum({kiro_credits})"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Kiro included credits",
   "Expression": "sum({kiro_included_credits})"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Kiro messages",
   "Expression": "sum({kiro_messages})"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Kiro overage credits",
   "Expression": "sum({kiro_overage_credits})"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Kiro users",
   "Expression": "distinct_countIf({user_id}, {uses_kiro} = 1)"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Quick agent hours",
   "Expression": "sum({quick_agent_hours})"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Quick extra hours",
   "Expression": "sum({quick_extra_hours})"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Quick included hours",
   "Expression": "sum({quick_included_hours})"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Quick users",
   "Expression": "distinct_countIf({user_id}, {uses_quick} = 1)"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "User",
   "Expression": "ifelse(isNotNull({quick_user_arn}), split({quick_user_arn}, '/', 3), isNotNull({user_email}), {user_email}, {user_id})"
  },
  {
   "DataSetIdentifier": "quick_kiro_by_user",
   "Name": "Users on both",
   "Expression": "distinct_countIf({user_id}, maxOver({uses_quick}, [{user_id}], PRE_AGG) = 1 AND maxOver({uses_kiro}, [{user_id}], PRE_AGG) = 1)"
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
       "SheetId": "s6",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s8",
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
       "SheetId": "s6",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s8",
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
       "SheetId": "s6",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s8",
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
   "FilterGroupId": "fg-date-kr",
   "Filters": [
    {
     "TimeRangeFilter": {
      "FilterId": "f-date-kr",
      "Column": {
       "DataSetIdentifier": "user_report",
       "ColumnName": "Date"
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
       "SheetId": "s6",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s8",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-month-kr",
   "Filters": [
    {
     "TimeEqualityFilter": {
      "FilterId": "f-month-kr",
      "Column": {
       "DataSetIdentifier": "user_report",
       "ColumnName": "Date"
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
   "FilterGroupId": "fg-date-ka",
   "Filters": [
    {
     "TimeRangeFilter": {
      "FilterId": "f-date-ka",
      "Column": {
       "DataSetIdentifier": "by_user_analytic",
       "ColumnName": "Date"
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
       "SheetId": "s6",
       "Scope": "ALL_VISUALS"
      },
      {
       "SheetId": "s8",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-month-ka",
   "Filters": [
    {
     "TimeEqualityFilter": {
      "FilterId": "f-month-ka",
      "Column": {
       "DataSetIdentifier": "by_user_analytic",
       "ColumnName": "Date"
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
   "FilterGroupId": "fg-s6-client",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s6-client",
      "Column": {
       "DataSetIdentifier": "user_report",
       "ColumnName": "Client"
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
       "SheetId": "s6",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s6-tier",
   "Filters": [
    {
     "CategoryFilter": {
      "FilterId": "f-s6-tier",
      "Column": {
       "DataSetIdentifier": "user_report",
       "ColumnName": "Tier"
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
       "SheetId": "s6",
       "Scope": "ALL_VISUALS"
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s6-v5-topn",
   "Filters": [
    {
     "TopBottomFilter": {
      "FilterId": "f-s6-v5-topn",
      "Column": {
       "DataSetIdentifier": "user_report",
       "ColumnName": "User_Email"
      },
      "AggregationSortConfigurations": [
       {
        "Column": {
         "DataSetIdentifier": "user_report",
         "ColumnName": "Credits_Used"
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
       "SheetId": "s6",
       "Scope": "SELECTED_VISUALS",
       "VisualIds": [
        "s6-v5"
       ]
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s8-v1-topn",
   "Filters": [
    {
     "TopBottomFilter": {
      "FilterId": "f-s8-v1-topn",
      "Column": {
       "DataSetIdentifier": "quick_kiro_by_user",
       "ColumnName": "User"
      },
      "AggregationSortConfigurations": [
       {
        "Column": {
         "DataSetIdentifier": "quick_kiro_by_user",
         "ColumnName": "quick_agent_hours"
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
       "SheetId": "s8",
       "Scope": "SELECTED_VISUALS",
       "VisualIds": [
        "s8-v1"
       ]
      }
     ]
    }
   },
   "Status": "ENABLED",
   "CrossDataset": "SINGLE_DATASET"
  },
  {
   "FilterGroupId": "fg-s8-date",
   "Filters": [
    {
     "TimeRangeFilter": {
      "FilterId": "f-s8-date",
      "Column": {
       "DataSetIdentifier": "quick_kiro_by_user",
       "ColumnName": "day"
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
       "SheetId": "s8",
       "Scope": "ALL_VISUALS"
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
    "DataSetIdentifier": "by_user_analytic",
    "ColumnName": "AI code lines"
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
    "DataSetIdentifier": "by_user_analytic",
    "ColumnName": "Inline acceptance %"
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
    "DataSetIdentifier": "by_user_analytic",
    "ColumnName": "Kiro active days"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Active Kiro users"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Avg monthly credit use %"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Credits used"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Included credits"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Kiro conversations"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Kiro messages"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Overage credits"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Peak monthly credit use %"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "User-months"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Users in overage"
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
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Kiro credits"
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
        "DecimalPlaces": 2
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Kiro included credits"
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
        "DecimalPlaces": 2
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Kiro messages"
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
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Kiro overage credits"
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
        "DecimalPlaces": 2
       },
       "NumberScale": "NONE"
      }
     }
    }
   }
  },
  {
   "Column": {
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Kiro users"
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
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Quick agent hours"
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
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Quick extra hours"
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
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Quick included hours"
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
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Quick users"
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
    "DataSetIdentifier": "quick_kiro_by_user",
    "ColumnName": "Users on both"
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
    "DataSetIdentifier": "user_report",
    "ColumnName": "Client"
   },
   "ColorsConfiguration": {
    "CustomColors": [
     {
      "FieldValue": "Kiro CLI",
      "Color": "#2E6DB4"
     },
     {
      "FieldValue": "Kiro IDE",
      "Color": "#4F9DA6"
     },
     {
      "FieldValue": "Kiro Web",
      "Color": "#F2A541"
     },
     {
      "FieldValue": "Plugin",
      "Color": "#6C8EBF"
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
    ("Create or prepare the Kiro reports bucket", "bucket", True),
    ("Turn on Kiro user activity reports (manual, Kiro console)", "reporting", True),
    ("Create the Athena and Glue resources", "athena", True),
    ("Create Quick data sources and datasets", "datasets", True),
    ("Daily refresh (optional)", "refresh", True),
    ("Starter dashboard (optional)", "dashboard", True),
    ("Verify", "verify", False),
]
OPTIONAL_STEPS = {"refresh", "dashboard"}
# Steps the customer does in a console. The script can't check their result right away.
MANUAL_STEPS = {"reporting"}
STEP = {key: number for number, (_, key, _) in enumerate(STEPS, start=1)}
PRECHECK_STEP = STEP["precheck"]
BUCKET_STEP = STEP["bucket"]
REPORTING_STEP = STEP["reporting"]
ATHENA_STEP = STEP["athena"]
DATASETS_STEP = STEP["datasets"]
REFRESH_STEP = STEP["refresh"]
DASHBOARD_STEP = STEP["dashboard"]
VERIFY_STEP = STEP["verify"]
# Steps with a read-only "is it already done?" check.
CHECKED_STEPS = {"bucket", "reporting", "athena", "datasets", "refresh", "dashboard"}

STOP_WORD = "stop"
SEARCH_WORD = "SEARCH"
SKIP_WORD = "SKIP"


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
            return (
                f"You typed {len(digits)} digits, but the account ID has {len(expected)}. "
                "Check for a missing or extra digit."
            )
        return "The digits don't match the account ID shown."
    if t.upper() == expected:
        return f"Almost there. It needs CAPITAL letters: {expected}"
    if " ".join(t.split()).upper() == expected:
        return f"Check the spacing: one space between each word, like this: {expected}"
    return (
        f"That doesn't match. Type it exactly as shown, in CAPITAL letters: {expected}"
    )


def ask_until(
    log, step, step_name, prompt, expected, allow_skip=False, alternatives=None
):
    """Ask until the exact phrase is typed.

    Returns True (confirmed), False (stopped), "skip" (only when allow_skip is set), or the value
    of an alternatives entry ({typed phrase: value}) when that phrase is typed.
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

        def record(outcome, typed, attempt=attempt):
            log.write(
                event="confirmation",
                step=step,
                step_name=step_name,
                prompt=expected,
                typed=typed,
                attempt=attempt,
                outcome=outcome,
            )

        try:
            answer = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            record("stopped_interrupt", None)
            return False
        typed = answer.strip()
        if typed == expected:
            record("confirmed", answer)
            return True
        if typed in alternatives:
            record(f"chose_{alternatives[typed]}", answer)
            return alternatives[typed]
        if allow_skip and typed.upper() == SKIP_WORD:
            record("skipped", answer)
            return "skip"
        if typed.lower() == STOP_WORD:
            record("stopped", answer)
            return False
        record("mismatch", answer)
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


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument(
        "--region",
        help="AWS region of your Kiro profile and Quick account (default: your "
        "session's region, e.g. CloudShell's region)",
    )
    p.add_argument(
        "--reports-s3-uri",
        help="S3 URI Kiro writes its user activity reports to, e.g. "
        "s3://my-bucket/kiro-reports/ (default: ask)",
    )
    p.add_argument(
        "--quick-admin-arn",
        help="Quick user ARN that will own the datasets (default: you, "
        "if you are a Quick admin)",
    )
    p.add_argument(
        "--datasets",
        default=",".join(DATASETS),
        help=f"comma-separated subset of: {', '.join(DATASETS)}",
    )
    p.add_argument(
        "--refresh-time",
        default=DEFAULT_REFRESH_TIME,
        help=f"time of day (HH:MM) for the optional daily refresh (default {DEFAULT_REFRESH_TIME})",
    )
    p.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="timezone for the daily refresh (default UTC)",
    )
    p.add_argument(
        "--no-refresh", action="store_true", help="skip the optional daily refresh step"
    )
    p.add_argument(
        "--no-dashboard",
        action="store_true",
        help="skip the optional starter dashboard step",
    )
    p.add_argument(
        "--from-step",
        type=int,
        default=1,
        help=f"start at this step (1-{len(STEPS)}); earlier steps are checked, not re-run",
    )
    p.add_argument(
        "--destroy",
        action="store_true",
        help="remove what this script created, in reverse order",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--profile")
    args = p.parse_args(argv)
    args.datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    unknown = set(args.datasets) - set(DATASETS)
    if unknown:
        p.error(f"unknown datasets: {sorted(unknown)}")
    try:
        datetime.datetime.strptime(args.refresh_time, "%H:%M")
    except ValueError:
        p.error(
            f"--refresh-time must be HH:MM, for example 03:00 (got {args.refresh_time})"
        )
    if args.destroy and args.from_step != 1:
        p.error("--destroy can't be combined with --from-step")
    if args.destroy and set(args.datasets) != set(DATASETS):
        p.error("--destroy always covers every dataset; leave out --datasets")
    if not 1 <= args.from_step <= len(STEPS):
        p.error(
            f"--from-step must be between 1 and {len(STEPS)} (got {args.from_step})"
        )
    if args.reports_s3_uri:
        try:
            parse_s3_uri(args.reports_s3_uri)
        except ValueError as e:
            p.error(f"--reports-s3-uri isn't usable: {e}")
    return args


def resolve_region(args, session):
    if args.region:
        return args.region, "from --region"
    region = session.region_name
    if not region:
        return None, None
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        if os.environ.get(var) == region:
            return region, f"from {var}, which CloudShell sets to the console's region"
    return region, "from your AWS profile"


def describe_profile(args):
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


def common_flags(args):
    parts = []
    if args.profile:
        parts += ["--profile", args.profile]
    parts += ["--region", args.region]
    if args.reports_s3_uri:
        parts += ["--reports-s3-uri", args.reports_s3_uri]
    return parts


def destroy_command(args):
    return " ".join(
        shlex.quote(p)
        for p in ["python3", sys.argv[0], "--destroy"] + common_flags(args)
    )


def resume_command(args, step):
    """The exact command to continue from a step, with every setting this run resolved."""
    parts = ["python3", sys.argv[0], "--from-step", str(step)] + common_flags(args)
    if args.quick_admin_arn and step <= DASHBOARD_STEP:
        parts += ["--quick-admin-arn", args.quick_admin_arn]
    if set(args.datasets) != set(DATASETS):
        parts += ["--datasets", ",".join(args.datasets)]
    if args.refresh_time != DEFAULT_REFRESH_TIME:
        parts += ["--refresh-time", args.refresh_time]
    if args.timezone != DEFAULT_TIMEZONE:
        parts += ["--timezone", args.timezone]
    if args.no_refresh:
        parts.append("--no-refresh")
    if args.no_dashboard:
        parts.append("--no-dashboard")
    return " ".join(shlex.quote(p) for p in parts)


def print_resume(log, args, step):
    command = resume_command(args, step)
    print("\nTo continue from where it stopped, fix the problem above, then run:")
    print(f"    {command}")
    print(
        "Finished steps (and finished parts of a step) are detected and skipped, so this is"
    )
    print("safe to run more than once.")
    log.write(event="resume_hint", step=step, command=command)


def verify_earlier_steps(setup, log, from_step):
    """Before starting at from_step, check (read-only) that every earlier step is complete."""
    print(
        f"\nStarting at step {from_step}. Checking that steps 1-{from_step - 1} are complete:"
    )
    for number, (name, key, _) in enumerate(STEPS, start=1):
        if number >= from_step:
            break
        if key == "precheck":
            setup.run_precheck()
            print(f"  Step {number} ({name}): passed")
            continue
        if key not in CHECKED_STEPS or key in OPTIONAL_STEPS:
            continue
        state = getattr(setup, f"check_{key}")()
        log.write(
            event="prerequisite_check",
            step=number,
            step_name=name,
            done=state.done,
            unknown=state.unknown,
            details=state.lines,
        )
        if not state.done:
            raise Abort(
                f"You asked to start at step {from_step}, but step {number} ({name}) isn't complete:\n"
                + "\n".join(f"    {line}" for line in state.lines),
                resume_step=number,
            )
        print(f"  Step {number} ({name}): done")


# Destroy undoes the setup steps in reverse: (name, key, setup step it undoes).
TEARDOWN = [
    ("Delete the starter dashboard and analysis", "dashboard", DASHBOARD_STEP),
    ("Delete the daily refresh schedules", "refresh", REFRESH_STEP),
    ("Delete the Quick datasets and data sources", "datasets", DATASETS_STEP),
    ("Delete the Athena and Glue resources", "athena", ATHENA_STEP),
    ("Delete the data source role", "role", ATHENA_STEP),
    ("Turn off Kiro user activity reports (manual)", "reporting", REPORTING_STEP),
    ("Delete the bucket, or the Athena results in it", "bucket", BUCKET_STEP),
]
# If you keep one of these, the parts that depend on it are kept too, so what remains keeps working.
TEARDOWN_NEEDS = {
    "datasets": ["dashboard"],
    "athena": ["datasets"],
    "role": ["datasets"],
    "bucket": ["datasets", "athena", "reporting"],
}


def run_destroy(setup, log, args):
    """Undo what the setup steps created. Returns the exit code."""
    setup.selected = list(DATASETS)
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
        print(
            f"\n  {number}. {name} (undoes step {undoes}): "
            f"{'nothing to remove' if state.done else 'to remove'}"
        )
        for line in state.lines:
            print(f"       {line}")
    log.write(
        event="destroy_inventory",
        found={k: {"done": s.done, "details": s.lines} for k, s in found.items()},
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
    if not ask_until(
        log,
        0,
        "Confirm destroy",
        f"To start removing these from account {account}, confirm by typing the account ID.",
        account,
    ):
        print("Stopped. Nothing was deleted.")
        return 1
    kept = []
    for number, (name, key, _) in enumerate(TEARDOWN, start=1):
        step = f"D{number}"
        print(f"\n=== Destroy {number}/{len(TEARDOWN)}: {name} ===")
        state = getattr(setup, f"find_{key}_to_remove")()
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
            setup.removal_phrase(key, state),
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
            return 1
        getattr(setup, f"remove_{key}")(state)
        if key not in MANUAL_STEPS:
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
    return 0


def upload_log(setup, log_path, args):
    if args.dry_run or not setup.bucket:
        print(f"Confirmation log kept locally at {log_path}")
        return
    key = f"{LOG_PREFIX}/{log_path}"
    try:
        if setup.bucket_exists():
            setup.s3.upload_file(log_path, setup.bucket, key)
            print(f"Confirmation log uploaded to s3://{setup.bucket}/{key}")
            return
    except (ClientError, Abort) as e:
        print(f"Could not upload confirmation log: {e}")
    print(f"Confirmation log kept locally at {log_path}")


def run_steps(setup, log, args):
    """Run the setup steps from args.from_step. Returns the exit code."""
    current = args.from_step
    try:
        if args.from_step > 1:
            verify_earlier_steps(setup, log, args.from_step)
        for number, (name, key, changes) in enumerate(STEPS, start=1):
            if number < args.from_step:
                continue
            current = number
            print(f"\n=== Step {number}/{len(STEPS)}: {name} ===")
            state = None
            if key == "dashboard" and setup.dashboard_scope_problem():
                print(f"  {setup.dashboard_scope_problem()}")
                setup.skip_dashboard("not all datasets selected")
                log.write(event="step_skipped", step=number, step_name=name)
                continue
            optional = key in OPTIONAL_STEPS
            if optional and getattr(args, f"no_{key}", False):
                getattr(setup, f"skip_{key}")(f"--no-{key}")
                log.write(event="step_skipped", step=number, step_name=name)
                continue
            if key in CHECKED_STEPS:
                state = getattr(setup, f"check_{key}")()
                for line in state.lines:
                    print(f"  {line}")
                if state.done:
                    print(
                        f"  Step {number} is already complete. Nothing changed; moving on."
                    )
                    log.write(
                        event="step_already_done",
                        step=number,
                        step_name=name,
                        details=state.lines,
                    )
                    continue
            for line in getattr(setup, f"plan_{key}")():
                print(f"  {line}")
            if args.dry_run and changes:
                continue
            if changes:
                answer = confirm(
                    log,
                    number,
                    name,
                    setup.confirmation_phrase(key),
                    allow_skip=optional,
                    alternatives=setup.confirmation_alternatives(key),
                )
                if answer == "skip":
                    getattr(setup, f"skip_{key}")("typed SKIP")
                    log.write(event="step_skipped", step=number, step_name=name)
                    continue
                if not answer:
                    print(
                        f"\nStopped at step {number}. Nothing in step {number} was changed."
                    )
                    log.write(event="run_stopped", step=number)
                    print_resume(log, args, number)
                    return 1
                setup.confirmed_as = answer
            getattr(setup, f"run_{key}")(state)
            if key in CHECKED_STEPS and key not in MANUAL_STEPS:
                after = getattr(setup, f"check_{key}")()
                if not after.done and not after.unknown:
                    raise Abort(
                        f"Step {number} ran, but the check afterwards says it isn't complete:\n"
                        + "\n".join(f"    {line}" for line in after.lines),
                        resume_step=number,
                    )
            log.write(event="step_done", step=number, step_name=name)
        log.write(event="run_complete")
        if not args.dry_run:
            print(
                "\nAll steps are complete. Re-running the script is safe: finished steps"
            )
            print("are detected and skipped.")
        return 0
    except Abort as e:
        print(f"\nStopped: {e}")
        log.write(event="run_aborted", step=current, reason=str(e))
        print_resume(log, args, e.resume_step or current)
        return 1
    except KeyboardInterrupt:
        print(
            f"\n\nInterrupted during step {current}. Part of that step may already be done."
        )
        log.write(event="run_interrupted", step=current)
        print_resume(log, args, current)
        return 130
    except Exception as e:
        print(f"\nStep {current} failed: {describe_error(e)}")
        log.write(
            event="run_failed",
            step=current,
            error=repr(e),
            trace=traceback.format_exc(),
        )
        print_resume(log, args, current)
        return 2


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    region, region_source = resolve_region(args, session)
    if not region:
        print(
            "Stopped: no AWS region found. Re-run with --region <region>, for example --region us-east-1."
        )
        return 1
    args.region = region
    identity = session.client("sts").get_caller_identity()
    run_id = str(uuid.uuid4())
    log_path = f"kiro-usage-setup-{run_id}.jsonl"
    log = ConfirmationLog(
        log_path,
        {
            "run_id": run_id,
            "script_version": SCRIPT_VERSION,
            "account_id": identity["Account"],
            "caller_arn": identity["Arn"],
            "region": args.region,
        },
    )
    log.write(event="run_start", dry_run=args.dry_run, args=vars(args))
    setup = Setup(args, session, identity, log)
    profile, source = describe_profile(args)
    print(f"Run {run_id}")
    print(f"AWS profile: {profile} ({source})")

    print(
        "\nWorking out the settings (read-only; nothing is changed yet)...", flush=True
    )
    try:
        setup.bucket, setup.reports_prefix, reports_source = setup.resolve_reports()
        args.reports_s3_uri = setup.reports_uri()
        if args.from_step <= DASHBOARD_STEP and not args.destroy:
            admin_arn, admin_role, admin_source = setup.resolve_admin(identity["Arn"])
            args.quick_admin_arn = admin_arn
            owner = f"{admin_arn.split(':user/', 1)[1].partition('/')[2]} ({admin_role}, {admin_source})"
        else:
            admin_arn = None
            owner = (
                "not needed for destroy"
                if args.destroy
                else f"not needed (starting at step {args.from_step})"
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
        reports_uri=setup.reports_uri(),
        reports_source=reports_source,
        bucket_state=bucket_state,
        quick_admin_arn=admin_arn,
    )
    caller = identity["Arn"].split(":", 5)[5]
    if args.destroy:
        print("\nWe'll look for what this script created, with these settings:")
    else:
        print("\nWe'll set up Kiro user activity datasets with these settings:")
    print(f"  AWS account:   {identity['Account']}  (signed in as {caller})")
    print(
        f"  Region:        {args.region}  ({region_source}). Must be your Kiro profile's region."
    )
    print(
        f"  Kiro reports:  {setup.reports_uri()}  ({reports_source}; bucket {bucket_state})"
    )
    print(f"  Dataset owner: {owner}")
    print(f"  Datasets:      {', '.join(args.datasets)}")
    print(f"  Confirmation log: {log_path}")
    print(
        "\nTo change any of these, type 'stop' and re-run with --region, --reports-s3-uri or "
        "--quick-admin-arn."
    )
    if not args.dry_run and not args.destroy:
        print(
            f"\nTo remove everything this script created instead, type {DESTROY_WORD}. Nothing is"
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
            print("Stopped. Nothing was changed.")
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
    else:
        code = run_steps(setup, log, args)
    log.close()
    upload_log(setup, log_path, args)
    return code


if __name__ == "__main__":
    sys.exit(main())
