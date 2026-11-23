#!/usr/bin/env python3
"""Set up Amazon Quick usage analytics datasets in your own AWS account.

Runs as separate steps. Before each step that changes anything, the script prints exactly
what it will do and waits for you to type the confirmation phrase. Every prompt, answer and
outcome is written to a confirmation log (JSON Lines) that is also uploaded to your bucket.

Usage (for example in AWS CloudShell, signed in as a Quick admin):
  python3 quick_usage_datasets_setup.py
The script works out the region, bucket name and dataset owner, shows them, and asks you
to confirm. To override any of them:
  --region us-east-1                     default: your session's region
  --bucket my-quick-usage-data           default: quick-usage-<account>-<region code>
  --quick-admin-arn arn:aws:quicksight:us-east-1:111122223333:user/default/Admin
                                         default: you, if you are a Quick admin
  --refresh-time 02:00 --timezone UTC   time of the optional daily refresh
  --no-deny-default  skip the optional (recommended) default-access step without asking
  --no-refresh       skip the optional daily refresh step
  --no-dashboard     skip the optional starter dashboard step
  --dry-run          print every step's plan, change nothing
  --datasets chat,agent_hours   only set up some datasets (default: all)
  --from-step N      start at step N. The script first checks (read-only) that steps
                     1..N-1 are complete, and stops if one isn't.
  --destroy          remove what this script created, in reverse order (you can also
                     type DESTROY instead of the account ID at the first prompt)

Safe to re-run: before each step, the script checks whether that step is already complete.
If it is, nothing is changed and the step is logged as already done. If a run stops or
fails, the script prints the exact command to continue from the step that didn't finish.

Destroy: undoes the steps in reverse order. It lists what it finds first, asks for the
account ID, then asks again before each removal (type SKIP to keep something). It only
removes things this script creates, under their fixed names, and only deletes the bucket
if the script created it and nothing else is in it. Safe to re-run.
"""
import argparse
import copy
import datetime
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

SCRIPT_VERSION = "0.9.0"
# Standard retry mode backs off on throttling and transient errors. 10 attempts absorb
# throttling on large accounts; explicit timeouts (botocore's 60s read default, 10s connect)
# keep a dead connection from hanging forever. Progress lines show which call is running.
# Errors worth waiting out while polling for eventually consistent changes.
TRANSIENT_ERROR_CODES = {"ThrottlingException", "InternalFailureException", "ServiceUnavailableException"}
BOTO_CONFIG = Config(
    retries={"mode": "standard", "max_attempts": 10}, connect_timeout=10, read_timeout=60
)
DEFAULT_REFRESH_TIME = "02:00"
DEFAULT_TIMEZONE = "UTC"
# The datasets' SQL derives a real DATETIME column, event_datetime, from the epoch
# event_timestamp (milliseconds for chat/feedback, seconds for agent hours, so the SQL picks the
# unit per row). It is a generally useful typed timestamp, and it is the natural look-back column
# for a customer who later turns on incremental refresh. Named distinctly from the dashboard's
# own event_time calculated field so the two don't collide.
EVENT_DATETIME_COLUMN = "event_datetime"
PREFIX_ROOT = "quick-usage-analytics"
LOG_PREFIX = f"{PREFIX_ROOT}/_setup-confirmation-logs"
# Usage logs and Athena query results grow without bound, so a bucket this script creates gets
# lifecycle rules that expire them. The retention is chosen at setup (the script asks, defaulting
# to the value below). The rules are scoped to each dataset's log prefix (PREFIX_ROOT/<dataset>/)
# and the Athena results prefix (PREFIX_ROOT/_athena-results/), so the small bookkeeping under
# PREFIX_ROOT/_manifests/ and PREFIX_ROOT/_setup-confirmation-logs/ is never expired.
DEFAULT_RETENTION_DAYS = 365
# Prefix of the lifecycle rule IDs this script owns. Rules without it belong to the customer and
# are preserved when the script re-applies its own rules (put replaces the whole configuration).
OUR_RULE_ID_PREFIX = "expire-quick-usage-"

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
# What the logs do contain that identifies people, for the warnings in the delivery,
# grant and dashboard steps.
IDENTITY_NOTE = [
    "The logs contain no chat text, answers, files, feedback comments or document names.",
    "They do identify users: user_arn includes the Quick user name (often an email or",
    "login), and every record says who used Quick, when, and how much.",
]


def _common_descriptions(camel, event="the event happened", unit="milliseconds"):
    return {
        "resource_arn": "ARN of your Quick account.",
        "event_timestamp": f"When {event}, as Unix epoch time in {unit}. "
        "For dates and times, use event_datetime.",
        ("logType" if camel else "log_type"): "Log type of the record. "
        "It is the same on every row of this dataset.",
        ("accountId" if camel else "account_id"): "AWS account ID of your Quick account.",
        EVENT_DATETIME_COLUMN: f"When {event}, as a UTC date and time.",
    }


_USER_ID = "Stable Quick ID of the user (principal ID)."
_USER_TYPE = "Quick user type of the user, for example ADMIN_PRO."
_NAMESPACE = "Quick namespace of the user."

# Text for each dataset column's Description in Quick. Quick's natural-language features
# (Topics, chat over the dataset) read it to understand the column. Wording follows the
# Quick CloudWatch Logs field docs. Max 500 characters each.
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
        **_common_descriptions(camel=True, event="the usage was reported", unit="seconds"),
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
# Marker in a dataset's missing list: it exists but has columns with no description yet.
COLUMN_DESCRIPTIONS_MISSING = "column descriptions"
OUTPUT_FORMAT = "json"

# ---- Athena / Glue (step 6) ----
# The datasets read the logs through Athena, which scans each log prefix with no file-count
# limit. It reads the SAME bucket and prefixes the logs already land in
# (quick-usage-analytics/<dataset>/), so it works for buckets that already hold data and for
# brand-new ones alike. One Glue database holds an external table per dataset; the table's
# location is that dataset's log prefix and it has no partitions, so Athena reads every object
# under the prefix whatever key layout the delivery used. Quick
# reaches Athena through the script's Quick-only IAM role (step 6 grants it
# Athena, the Glue catalog and the query-results prefix); Athena then reads S3 as that
# role, so Quick's own account role still needs no access to the bucket.
GLUE_DATABASE = "quick_usage_analytics"
ATHENA_WORKGROUP = "quick-usage-analytics"
ATHENA_RESULTS_PREFIX = f"{PREFIX_ROOT}/_athena-results"
# Tag put on the workgroup so a re-run can tell its own workgroup (safe to repoint) from a
# customer workgroup that happens to share the name (left alone — the script aborts instead).
WORKGROUP_TAG = {"Key": "ManagedBy", "Value": "quick_usage_datasets_setup"}
# Athena reads gzipped newline-delimited JSON. The OpenX SerDe is lenient (skips bad lines) and
# lowercases JSON keys, so the Glue columns are the lowercased field names; the dataset's custom
# SQL aliases them back to the exact field names (logType, accountId) of the
# usage logs.
GLUE_JSON_SERDE = "org.openx.data.jsonserde.JsonSerDe"
GLUE_TEXT_INPUT = "org.apache.hadoop.mapred.TextInputFormat"
GLUE_IGNOREKEY_OUTPUT = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
# QuickSight column type (second tuple element in DATASETS) -> the Athena type the SQL casts to.
ATHENA_CAST = {"INTEGER": "bigint", "DECIMAL": "double"}

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
DASHBOARD_ASSETS = (("analysis", ANALYSIS_ID), ("dashboard", DASHBOARD_ID))
CREATE_TIMEOUT_SECONDS = 300
# Table page sizes Quick supports.
TABLE_PAGE_SIZES = (100, 500, 1000, 10000)
REFRESH_SCHEDULE_ID = "quick-usage-daily"
# check_datasets' todo key for the owner's IAM policy assignment (not a dataset).
OWNER_ACCESS = "owner access"
# Key used in a step's todo for the account-wide Glue database + Athena workgroup.
ATHENA_SHARED = "athena resources"
# Step 2 tags the buckets it creates, so destroy knows it may delete them.
BUCKET_TAG = {"Key": "created-by", "Value": "quick-usage-datasets-setup"}
# Bucket-policy statement that refuses any request not made over TLS.
TLS_ONLY_SID = "DenyInsecureTransport"
DESTROY_WORD = "DESTROY"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


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


def bold(text):
    """Bold in terminals; plain text otherwise (logs, pipes)."""
    if sys.stdout.isatty():
        return f"\033[1m{text}\033[0m"
    return text


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


class Setup:
    def __init__(self, args, session, identity, log):
        self.args = args
        self.region = args.region
        self.account = identity["Account"]
        self.bucket = args.bucket
        self.log = log
        self.s3 = session.client("s3", region_name=self.region, config=BOTO_CONFIG)
        self.logs = session.client("logs", region_name=self.region, config=BOTO_CONFIG)
        self.qs = session.client("quicksight", region_name=self.region, config=BOTO_CONFIG)
        self.iam = session.client("iam", config=BOTO_CONFIG)
        self.glue = session.client("glue", region_name=self.region, config=BOTO_CONFIG)
        self.athena = session.client("athena", region_name=self.region, config=BOTO_CONFIG)
        self.partition = identity["Arn"].split(":")[1]
        self.resource_arn = (
            f"arn:{self.partition}:quicksight:{self.region}:"
            f"{self.account}:account/{self.account}"
        )
        self.selected = args.datasets

    # ---------- helpers ----------
    def prefix(self, name):
        return f"{PREFIX_ROOT}/{name}"

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
                )
            raise

    def get_or_none(self, fn, key, **kwargs):
        """Describe/get call that returns None instead of raising when the resource is missing."""
        try:
            return fn(**kwargs)[key]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ResourceNotFoundException", "NoSuchKey", "404"):
                return None
            raise

    def manifest_key(self, d):
        return f"{PREFIX_ROOT}/_manifests/{d}.json"

    def manifest(self, d):
        return {
            "fileLocations": [{"URIPrefixes": [f"s3://{self.bucket}/{self.prefix(d)}/"]}],
            "globalUploadSettings": {"format": "JSON"},
        }

    def manifest_current(self, d):
        """True if our manifest for d exists in the bucket with the expected content."""
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=self.manifest_key(d))["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404", "NoSuchBucket"):
                return False
            raise
        try:
            return json.loads(body) == self.manifest(d)
        except ValueError:
            return False

    # ---------- owner of the Quick assets (checked before any step runs) ----------
    def admin_users(self):
        """Quick users in the default namespace with an admin role, as (arn, name, role).

        ListUsers doesn't filter by role, so this reads every user in the namespace, 100 per call.
        In accounts with thousands of users that takes a while, so it prints progress.
        """
        print("  Looking for Quick admins to own the datasets. To find them,")
        print("  the script reads every Quick user in the account (100 per call). In accounts")
        print("  with many users this takes a minute or more. To skip it next time, pass")
        print("  --quick-admin-arn with the owner's Quick user ARN.", flush=True)
        found, seen = [], 0
        try:
            for page in self.qs.get_paginator("list_users").paginate(
                AwsAccountId=self.account, Namespace="default", PaginationConfig={"PageSize": 100}
            ):
                for u in page["UserList"]:
                    if u.get("Role", "").startswith("ADMIN") and u.get("Active", True):
                        found.append((u["Arn"], u["UserName"], u["Role"]))
                seen += len(page["UserList"])
                progress(f"    {seen} Quick users checked, {len(found)} admins found so far...")
        except ClientError as e:
            self.raise_quick_error(e)
        progress(f"    Done: {seen} Quick users checked, {len(found)} admins found.", done=True)
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
            return self.quick_call(self.qs.describe_user, Namespace=namespace, UserName=user_name)[
                "User"
            ]
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
                raise Abort(f"--quick-admin-arn does not look like a Quick user ARN: {arn}")
            namespace, _, user_name = arn.split(marker, 1)[1].partition("/")
            print(f"  Checking the Quick user in --quick-admin-arn ({user_name})...", flush=True)
            user = self.describe_quick_user(namespace, user_name)
            if not user:
                raise Abort(f"No Quick user matches --quick-admin-arn {arn} in this account.")
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
            if user and user.get("Role", "").startswith("ADMIN") and user.get("Active", True):
                return user["Arn"], user["Role"], "that's you"
        except Exception as e:  # noqa: BLE001 - a failed lookup never stops the run
            self.log.write(event="caller_admin_check_failed", error=repr(e))
        return None

    def ask_admin(self):
        """Ask for the dataset owner by Quick user name or ARN; SEARCH lists every admin."""
        print("\nWhich Quick admin should own the datasets?")
        print("  Type the admin's Quick user name or Quick user ARN. You can find user names")
        print("  in Quick under Manage account > Manage users. In Identity Center accounts the")
        print("  user name is usually the person's email.")
        print(f"  Or type {SEARCH_WORD} to have the script find the admins for you. This reads")
        print("  every Quick user in the account, so it can take several minutes depending on")
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
                )
            if not answer:
                continue
            if answer.lower() == STOP_WORD:
                raise Abort("Stopped while choosing the dataset owner.")
            if answer.upper() == SEARCH_WORD:
                return self.search_admin()
            namespace, user_name = self.parse_quick_user(answer)
            user = self.describe_quick_user(namespace, user_name)
            if not user:
                print(f"  No Quick user named '{user_name}' in this account. Try again, or "
                      f"type {SEARCH_WORD}.")
                continue
            role = user.get("Role", "")
            if not role.startswith("ADMIN"):
                print(f"  {user_name} is a Quick {role or 'user'}, not an admin. Type an "
                      f"admin, or {SEARCH_WORD}.")
                continue
            if not user.get("Active", True):
                print(f"  {user_name} hasn't signed in to Quick yet, so it can't own the "
                      f"datasets. Type another admin, or {SEARCH_WORD}.")
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
        print("\nThis Quick account has several admins. Which one should own the datasets?")
        for i, (a, user_name, role) in enumerate(admins, start=1):
            print(f"  {i}. {user_name} ({role})")
        print(f"  (Type a number, or '{STOP_WORD}' to stop.)")
        while True:
            try:
                answer = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                raise Abort("Stopped while choosing the dataset owner.")
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
            return "exists in this account" if self.bucket_exists() else "will be created"
        except ClientError as e:
            return f"can't check: {e.response['Error']['Code']}"

    # ---------- precheck step: read-only checks ----------
    def plan_precheck(self):
        return [
            f"Read-only. Check Quick subscription in {self.account} / {self.region}.",
            f"Check bucket s3://{self.bucket} (exists and owned by you, or free to create).",
            f"Check for existing delivery sources for {self.resource_arn}.",
            "Later steps need permission to create S3 buckets, CloudWatch Logs deliveries, "
            "Athena and Glue resources, IAM policies, and Quick resources.",
        ]

    def run_precheck(self, state=None):
        print("  Checking the Quick subscription...", flush=True)
        sub = self.qs.describe_account_subscription(AwsAccountId=self.account)
        status = sub["AccountInfo"].get("AccountSubscriptionStatus")
        edition = sub["AccountInfo"].get("Edition")
        auth_type = sub["AccountInfo"].get("AuthenticationType")
        print(f"  Quick subscription status: {status}, edition: {edition}, sign-in: {auth_type}")
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
            if lt in self.selected_log_types() and n != self.source_name(self.dataset_of(lt))
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
                for page in self.logs.get_paginator("describe_configuration_templates").paginate(
                    service="quicksight", deliveryDestinationTypes=["S3"]
                )
                for t in page.get("configurationTemplates", [])
            }
        except ClientError as e:
            print(
                "  Couldn't list the log types Quick offers here "
                f"({e.response['Error']['Code']}); step 3 will tell if one is missing."
            )
            self.log.write(event="log_types_unchecked", error=e.response["Error"]["Code"])
            return
        missing = sorted(self.selected_log_types() - offered)
        self.log.write(event="log_types_offered", offered=sorted(t for t in offered if t), missing=missing)
        if missing:
            print(
                f"  WARNING: CloudWatch Logs doesn't list {', '.join(missing)} for Quick in "
                f"{self.region}.\n           The usage log types may not be available in this "
                "region yet. If so, step 3 stops\n           before anything is delivered."
            )
        else:
            print(f"  Usage log types offered in {self.region}: {', '.join(sorted(self.selected_log_types()))}")

    def dataset_of(self, log_type):
        return next(d for d, (lt, _) in DATASETS.items() if lt == log_type)

    def existing_sources(self):
        found = {}
        for page in self.logs.get_paginator("describe_delivery_sources").paginate():
            for src in page["deliverySources"]:
                if self.resource_arn in src.get("resourceArns", []):
                    found[src["logType"]] = src["name"]
        return found

    # ---------- bucket step ----------
    def check_bucket(self):
        if not self.bucket_exists():
            return StepState(False, [f"Bucket s3://{self.bucket} doesn't exist yet."])
        lines = [f"Bucket s3://{self.bucket} exists and is owned by this account."]
        lines += [f"WARNING: {w}" for w in self.bucket_warnings()]
        # A bucket this script created should carry its expiry lifecycle. If it's ours but the
        # lifecycle is missing (e.g. an earlier run created the bucket but failed before setting
        # it), flag the step so run_bucket re-applies it. A customer's own bucket is left alone.
        if self.bucket_is_ours() and not self.our_lifecycle_present():
            lines.append(
                f"Its {self.retention_days()}-day usage-log and Athena-results expiration is "
                "missing and will be applied."
            )
            return StepState(False, lines)
        if self.bucket_is_ours() and not self.has_tls_only(self.bucket_policy_document()):
            lines.append("Its TLS-only bucket policy statement is missing and will be added.")
            return StepState(False, lines)
        return StepState(True, lines)

    def bucket_is_ours(self):
        """True if the bucket carries the tag this script sets when it creates the bucket."""
        try:
            return BUCKET_TAG in self.s3.get_bucket_tagging(Bucket=self.bucket)["TagSet"]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchTagSet", "NoSuchTagSetError"):
                return False
            raise

    def bucket_policy_document(self):
        """The bucket's policy as a dict, or {} when it has none."""
        try:
            return json.loads(self.s3.get_bucket_policy(Bucket=self.bucket)["Policy"])
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchBucketPolicy":
                raise
            return {}

    def readable_bucket_policy(self):
        """The bucket policy, or None if you may not read it. Used only for a warning."""
        try:
            return self.bucket_policy_document()
        except ClientError as e:
            if e.response["Error"]["Code"] != "AccessDenied":
                raise
            return None

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
        statements = policy.get("Statement") or []
        for st in statements if isinstance(statements, list) else [statements]:
            secure = (st.get("Condition") or {}).get("Bool", {}).get("aws:SecureTransport")
            if st.get("Effect") == "Deny" and str(secure).lower() == "false":
                return True
        return False

    def ensure_tls_only(self):
        """Add the TLS-only statement to the bucket policy, keeping every other statement."""
        policy = self.bucket_policy_document()
        if self.has_tls_only(policy):
            return
        statements = policy.get("Statement", [])
        statements = statements if isinstance(statements, list) else [statements]
        policy = {"Version": policy.get("Version", "2012-10-17"), **policy}
        policy["Statement"] = statements + [self.tls_only_statement()]
        self.s3.put_bucket_policy(Bucket=self.bucket, Policy=json.dumps(policy))
        print(f"  Bucket policy: statement {TLS_ONLY_SID} added (requests without TLS are refused)")
        self.log.write(event="resource", bucket_policy_statement=TLS_ONLY_SID, bucket=self.bucket)

    def our_lifecycle_present(self):
        """True if every prefix we expire is already covered by an enabled expiration rule with
        the retention we want. Comparing the day count too means a changed --retention-days on an
        owned bucket is detected and re-applied, not silently ignored."""
        try:
            rules = self.s3.get_bucket_lifecycle_configuration(Bucket=self.bucket).get("Rules", [])
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
                return False
            raise
        covered = {
            (r.get("Filter", {}).get("Prefix"), r.get("Expiration", {}).get("Days"))
            for r in rules
            if r.get("Status") == "Enabled" and "Expiration" in r
        }
        want = {
            (rule["Filter"]["Prefix"], rule["Expiration"]["Days"])
            for rule in self.bucket_lifecycle()["Rules"]
        }
        return want <= covered

    def existing_retention_days(self):
        """The retention currently on this bucket's expiry rules, or None if it has none.

        Used so a plain re-run (no --retention-days) on a bucket we own keeps its current
        retention instead of resetting it to the default.
        """
        try:
            rules = self.s3.get_bucket_lifecycle_configuration(Bucket=self.bucket).get("Rules", [])
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
                return None
            raise
        want_prefixes = {f"{self.prefix(d)}/" for d in DATASETS} | {f"{ATHENA_RESULTS_PREFIX}/"}
        days = [
            r["Expiration"]["Days"]
            for r in rules
            if r.get("Status") == "Enabled"
            and r.get("Filter", {}).get("Prefix") in want_prefixes
            and "Days" in r.get("Expiration", {})
        ]
        return max(days) if days else None

    def bucket_warnings(self):
        """Settings on an existing bucket that the script doesn't change but you should know about."""
        warnings = []
        location = self.s3.get_bucket_location(Bucket=self.bucket).get("LocationConstraint")
        location = location or "us-east-1"
        if location != self.region:
            warnings.append(
                f"The bucket is in {location}, not {self.region}. Cross-region delivery "
                "hasn't been tested with this script."
            )
        try:
            pab = self.s3.get_public_access_block(Bucket=self.bucket)[
                "PublicAccessBlockConfiguration"
            ]
            if not all(pab.values()):
                warnings.append(
                    "Block Public Access isn't fully on for this bucket. The logs identify "
                    "users; turn it on (S3 console > the bucket > Permissions)."
                )
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchPublicAccessBlockConfiguration":
                raise
            warnings.append("This bucket has no Block Public Access settings. Turn them on.")
        try:
            rules = self.s3.get_bucket_encryption(Bucket=self.bucket)[
                "ServerSideEncryptionConfiguration"
            ]["Rules"]
            algorithm = rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
            if algorithm != "AES256":
                warnings.append(
                    f"Default encryption is {algorithm}. Quick and CloudWatch Logs need access "
                    "to that KMS key; this script hasn't been tested with KMS buckets."
                )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ServerSideEncryptionConfigurationNotFoundError":
                raise
        if not self.bucket_is_ours():
            policy = self.readable_bucket_policy()
            if policy is None:
                warnings.append(
                    "You can't read this bucket's policy, so the TLS-only check was skipped."
                )
            elif not self.has_tls_only(policy):
                warnings.append(
                    "The bucket policy doesn't refuse requests made without TLS. Consider adding a "
                    "Deny statement with the condition aws:SecureTransport = false."
                )
        return warnings

    def plan_bucket(self):
        lines = []
        if not self.args.dry_run and self.bucket_exists():
            if self.bucket_is_ours() and not self.our_lifecycle_present():
                lines.append(
                    f"Use existing bucket s3://{self.bucket}, and add the {self.retention_days()}-"
                    "day expiration on the usage logs and Athena results (manifests and setup logs "
                    "are kept). Nothing else is changed."
                )
            elif self.bucket_is_ours() and not self.has_tls_only(self.bucket_policy_document()):
                lines.append(
                    f"Use existing bucket s3://{self.bucket}, and add a bucket policy statement "
                    "that refuses requests made without TLS. Nothing else is changed."
                )
            else:
                lines.append(f"Use existing bucket s3://{self.bucket}. Settings are not changed.")
        else:
            lines += [
                f"Create bucket s3://{self.bucket} in {self.region} (if it does not exist)",
                "  - block all public access, SSE-S3 encryption, bucket-owner-enforced ownership",
                "  - a bucket policy statement that refuses requests made without TLS",
                f"  - a {self.retention_days()}-day expiration on the usage logs "
                f"({PREFIX_ROOT}/<dataset>/) and Athena query results; manifests and setup logs "
                "are kept",
            ]
        lines += [
            "In step 3, CloudWatch Logs adds the bucket policy statement that lets",
            "delivery.logs.amazonaws.com write your logs (AWS documents this behavior).",
        ]
        return lines

    def retention_days(self):
        """Days to keep usage logs and Athena results before deletion (chosen at setup)."""
        return self.args.retention_days or DEFAULT_RETENTION_DAYS

    def bucket_lifecycle(self):
        """Lifecycle rules that expire the bulk data the script writes after retention_days().

        Covers each dataset's usage-log prefix (PREFIX_ROOT/<dataset>/) and the Athena query
        results (PREFIX_ROOT/_athena-results/). Scoping to these prefixes leaves the small
        bookkeeping alone: the manifests (PREFIX_ROOT/_manifests/) and the setup confirmation logs
        (PREFIX_ROOT/_setup-confirmation-logs/) are never expired. Rules cover every log type, not
        just the datasets this run selected, so the policy is complete whatever gets set up.
        """
        days = self.retention_days()
        targets = [(f"quick-usage-{d}-logs", f"{self.prefix(d)}/") for d in DATASETS]
        targets.append(("quick-usage-athena-results", f"{ATHENA_RESULTS_PREFIX}/"))
        return {
            "Rules": [
                {
                    "ID": f"expire-{name}-after-{days}-days",
                    "Filter": {"Prefix": prefix},
                    "Status": "Enabled",
                    "Expiration": {"Days": days},
                }
                for name, prefix in targets
            ]
        }

    def merged_lifecycle(self):
        """Our expiry rules combined with any lifecycle rules the customer added themselves.

        put_bucket_lifecycle_configuration replaces the bucket's entire configuration, so to avoid
        dropping a customer's own rules (storage-class transitions, other expirations, ...) keep
        every existing rule that isn't ours (by the OUR_RULE_ID_PREFIX id) and add ours back.
        """
        try:
            existing = self.s3.get_bucket_lifecycle_configuration(Bucket=self.bucket).get(
                "Rules", []
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchLifecycleConfiguration":
                raise
            existing = []
        foreign = [r for r in existing if not r.get("ID", "").startswith(OUR_RULE_ID_PREFIX)]
        return {"Rules": foreign + self.bucket_lifecycle()["Rules"]}

    def run_bucket(self, state=None):
        if not self.bucket_exists():
            kwargs = {"Bucket": self.bucket, "ObjectOwnership": "BucketOwnerEnforced"}
            if self.region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
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
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                },
            )
            self.s3.put_bucket_tagging(Bucket=self.bucket, Tagging={"TagSet": [BUCKET_TAG]})
            print(f"  Created s3://{self.bucket}")
            self.log.write(event="resource", bucket=self.bucket)
        else:
            print(f"  Using existing s3://{self.bucket}")
        # Apply the expiry lifecycle to a bucket this script owns (an idempotent re-put is safe,
        # and this also heals a bucket created by an earlier run that failed before setting it). A
        # customer's own pre-existing bucket (without our tag) is left untouched.
        if self.bucket_is_ours():
            self.s3.put_bucket_lifecycle_configuration(
                Bucket=self.bucket, LifecycleConfiguration=self.merged_lifecycle()
            )
            print(
                f"  Usage logs and Athena query results expire after {self.retention_days()} days; "
                "manifests and setup logs are kept."
            )
            self.log.write(event="resource", bucket_lifecycle_days=self.retention_days())
            self.ensure_tls_only()

    # ---------- deliveries step ----------
    def expected_destination(self, d):
        return f"arn:{self.partition}:s3:::{self.bucket}/{self.prefix(d)}"

    def find_delivery(self, source_name, destination_arn):
        for page in self.logs.get_paginator("describe_deliveries").paginate():
            for dl in page["deliveries"]:
                if (
                    dl["deliverySourceName"] == source_name
                    and dl["deliveryDestinationArn"] == destination_arn
                ):
                    return dl
        return None

    def delivery_state(self, d):
        """Parts of d's delivery that are missing. Stops if an existing part points elsewhere."""
        name, log_type = self.source_name(d), DATASETS[d][0]
        missing = []
        src = self.get_or_none(self.logs.get_delivery_source, "deliverySource", name=name)
        if src is None:
            missing.append("source")
        elif src.get("logType") != log_type or self.resource_arn not in src.get(
            "resourceArns", []
        ):
            raise Abort(
                f"Delivery source {name} already exists, but for {src.get('logType')} on "
                f"{src.get('resourceArns')}, not {log_type} on {self.resource_arn}.\n  The script "
                "won't change it. Delete it (aws logs delete-delivery-source --name "
                f"{name}) or leave {d} out with --datasets."
            )
        dest = self.get_or_none(
            self.logs.get_delivery_destination, "deliveryDestination", name=name
        )
        if dest is None:
            missing.append("destination")
        else:
            target = dest["deliveryDestinationConfiguration"]["destinationResourceArn"].rstrip("/")
            if target != self.expected_destination(d):
                raise Abort(
                    f"Delivery destination {name} already sends {d} logs to {target},\n  not to "
                    f"{self.expected_destination(d)}. The script won't move an existing "
                    "delivery.\n  Re-run with --bucket set to the bucket in use, or remove the "
                    "existing delivery first."
                )
        if src is None or dest is None or not self.find_delivery(name, dest["arn"]):
            missing.append("delivery")
        return missing

    def check_deliveries(self):
        todo, lines = {}, []
        for d in self.selected:
            missing = self.delivery_state(d)
            if missing:
                todo[d] = missing
                lines.append(f"{d}: not set up yet (missing: {', '.join(missing)})")
            else:
                lines.append(f"{d}: delivery to s3://{self.bucket}/{self.prefix(d)}/ already set up")
        return StepState(not todo, lines, todo)

    def plan_deliveries(self):
        lines = [
            "Start streaming usage logs to your bucket. Data is delivered from this point on;",
            "there is NO backfill of earlier usage.",
        ]
        for d in self.selected:
            lt = DATASETS[d][0]
            lines += [
                f"- {d}: delivery source {self.source_name(d)} ({lt}, {self.resource_arn})",
                f"    -> destination s3://{self.bucket}/{self.prefix(d)}/ ({OUTPUT_FORMAT})",
            ]
        lines += ["NOTE: " + IDENTITY_NOTE[0]] + [f"      {line}" for line in IDENTITY_NOTE[1:]]
        lines += ["      Restrict who can read the bucket."]
        lines += [
            "CloudWatch Logs adds a write statement for delivery.logs.amazonaws.com to the bucket",
            "policy the first time a delivery is created (existing statements are kept).",
        ]
        return lines

    def run_deliveries(self, state=None):
        for d in state.todo if state else self.selected:
            lt = DATASETS[d][0]
            name = self.source_name(d)
            self.logs.put_delivery_source(name=name, resourceArn=self.resource_arn, logType=lt)
            dest = self.logs.put_delivery_destination(
                name=name,
                outputFormat=OUTPUT_FORMAT,
                deliveryDestinationConfiguration={
                    "destinationResourceArn": f"arn:{self.partition}:s3:::{self.bucket}/{self.prefix(d)}"
                },
            )
            dest_arn = dest["deliveryDestination"]["arn"]
            try:
                delivery = self.logs.create_delivery(
                    deliverySourceName=name, deliveryDestinationArn=dest_arn
                )
                print(f"  {d}: delivery {delivery['delivery']['id']} created")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConflictException":
                    raise
                print(f"  {d}: delivery already exists, kept")
            self.log.write(
                event="resource",
                dataset=d,
                delivery_source=name,
                delivery_destination=dest_arn,
            )

    # ---------- deny_default step (step 4): optional, manual: deny default access ----------
    # This setting is changed in the Quick console, so the
    # confirmation logs are the only record of what they did. Destroy uses that record.
    def default_access_link(self):
        return link(
            f"https://{self.region}.quicksight.aws.amazon.com/sn/console/account/"
            f"{self.account}/default-resource-access"
        )

    def earlier_log_entries(self):
        """Every entry in the confirmation logs earlier runs uploaded to the bucket."""
        if not self.bucket_exists():
            return []
        entries = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=LOG_PREFIX + "/"
        ):
            for obj in page.get("Contents", []):
                body = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
                for line in body.decode("utf-8", "replace").splitlines():
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        continue
        return entries

    def deny_default_record(self):
        """Latest recorded change to default access: ('denied' | 'already' | 'restored', when).

        'denied': step 4 had the customer switch it to Deny. 'already': it was Deny before.
        'restored': destroy had the customer switch it back to Allow. None: nothing recorded.
        """
        found = [
            e
            for e in self.earlier_log_entries()
            if e.get("event") == "deny_default" and e.get("outcome") in ("denied", "already", "restored")
        ]
        if not found:
            return None
        latest = max(found, key=lambda e: e.get("timestamp", ""))
        return latest["outcome"], latest.get("timestamp", "")

    def plan_deny_default(self):
        return [
            bold("RECOMMENDED BY US, BUT NOT REQUIRED. Setup works without it."),
            "",
            "Why: in the next step you let Quick read the log bucket. By default, every Quick",
            "author can then use every bucket Quick can read, so every author could read these",
            "usage logs. With default access set to Deny, authors only reach the AWS data you",
            "assign to them.",
            "",
            bold("WHAT THIS CHANGES. It applies to your WHOLE Quick account, not just this bucket:"),
            bold("  - Every Quick user and group without an IAM policy assignment loses access to ALL"),
            bold("    AWS data through Quick (Amazon S3, Athena, Redshift, RDS), including data they"),
            bold("    use today. They can't create new data sources on those services."),
            bold("  - That includes admins and you. The script keeps the dataset owner able to read"),
            bold("    THIS bucket's usage logs (see below), but nothing else."),
            bold("  - To give someone access back, assign them an IAM policy in Quick:"),
            bold("    Manage account > Security & permissions > IAM policy assignments."),
            "  Check with your Quick authors before you change it. You can switch it back to Allow",
            "  at any time on the same page, or by running this script with --destroy.",
            "",
            f"Step {DATASETS_STEP} creates the data sources with one shared, read-only IAM role for this",
            "bucket's usage logs, so the datasets work under Deny. It also gives the dataset owner a",
            "read-only IAM policy assignment for the bucket, for use in the Quick console.",
            "",
            "MANUAL STEP (you do this in the Quick console; the script waits for you):",
            "  Sign in to Quick as an admin and open the 'Default resource access' page, either way:",
            "    a. Direct link (click it, or copy it into your browser):",
            "         " + self.default_access_link(),
            "    b. Or from the menu: your user name (top right) > Manage account >",
            "       Default resource access.",
            "  1. Choose the option that denies access to all AWS data and resources.",
            "  2. Save.",
            "",
            "Then type one of these:",
            "  DENY DEFAULT ACCESS        after you saved Deny",
            "  IT WAS ALREADY DENIED      if the page already showed Deny (destroy then leaves it)",
            "  SKIP AND ACCEPT EXPOSURE   to keep the current setting: every Quick author will be",
            "                             able to read the usage logs after the next step",
        ]

    def check_deny_default(self):
        record = self.deny_default_record()
        if not (record and record[0] in ("denied", "already")):
            return StepState(False, ["No earlier run recorded default access as Deny."])
        how = "you set it during setup" if record[0] == "denied" else "it was already Deny"
        lines = [
            f"An earlier run recorded on {record[1][:10]} that default access is Deny ({how}).",
            "This setting is managed in the Quick console, so the script relies on that record.",
            f"To check it yourself: {self.default_access_link()}",
        ]
        return StepState(True, lines)

    def run_deny_default(self, state=None):
        outcome = "already" if getattr(self, "confirmed_as", True) == "already" else "denied"
        earlier = self.deny_default_record()
        if outcome == "already" and earlier and earlier[0] == "denied":
            # An earlier setup run had the customer switch it; keep that, so destroy offers to undo it.
            outcome = "denied"
            print("  Default access is still Deny, as an earlier setup run set it.")
        elif outcome == "denied":
            print("  Recorded: you set default access to Deny.")
        else:
            print("  Recorded: default access was already Deny. Destroy won't change it.")
        self.log.write(event="deny_default", outcome=outcome)
        print("  Quick users without an IAM policy assignment now can't reach AWS data through Quick.")
        print(f"  Step {DATASETS_STEP} sets up read access to this bucket's logs for the datasets and their owner.")

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

    def data_source_role(self):
        """The role as IAM returns it, or None."""
        try:
            return self.iam.get_role(RoleName=self.data_source_role_name())["Role"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchEntity":
                return None
            raise

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

    def data_source_role_trust_current(self, role):
        """True if the role's trust allows both sts:AssumeRole and sts:TagSession.

        Quick's Athena connector assumes the role WITH session tags, so the trust must allow
        sts:TagSession. A role that allows only sts:AssumeRole gets TagSession added, or Athena
        can't assume the role.
        """
        doc = role.get("AssumeRolePolicyDocument") or {}
        if isinstance(doc, str):
            doc = json.loads(urllib.parse.unquote(doc))
        actions = set()
        for st in doc.get("Statement", []):
            if st.get("Effect") != "Allow":
                continue
            a = st.get("Action", [])
            actions |= set(a if isinstance(a, list) else [a])
        return {"sts:AssumeRole", "sts:TagSession"} <= actions

    def data_source_role_policy_current(self):
        """True if the role's inline policy already grants Athena + Glue, not just S3 read.

        A QuickUsageLogsRead policy can grant S3 read only. Checking the name alone would miss
        that, so the policy would never be widened to Athena/Glue and the Athena data source
        would fail. Check the content.
        """
        try:
            doc = self.iam.get_role_policy(
                RoleName=self.data_source_role_name(), PolicyName="QuickUsageLogsRead"
            )["PolicyDocument"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchEntity":
                return False
            raise
        if isinstance(doc, str):
            doc = json.loads(urllib.parse.unquote(doc))
        sids = {st.get("Sid") for st in doc.get("Statement", [])}
        return {"RunAthena", "ReadGlueCatalog"} <= sids

    def owner_policy_name(self):
        return f"QuickUsageLogsRead-{self.bucket}"

    def owner_policy_arn(self):
        return f"arn:{self.partition}:iam::{self.account}:policy/{self.owner_policy_name()}"

    def owner_assignment_name(self):
        return f"quick-usage-logs-read-{self.bucket}"

    def owner_identity(self):
        """(namespace, user name) of the dataset owner, from its Quick user ARN."""
        namespace, _, user = self.args.quick_admin_arn.split(":user/", 1)[1].partition("/")
        return namespace, user

    def owner_policy_document(self):
        bucket_arn = f"arn:{self.partition}:s3:::{self.bucket}"
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ReadQuickUsageLogs",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"{bucket_arn}/{PREFIX_ROOT}/*"],
                },
                {
                    "Sid": "ListQuickUsageLogBucket",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": [bucket_arn],
                },
                # Quick's S3 checks list bucket names; this shows names only, no contents.
                {"Sid": "ListBucketNames", "Effect": "Allow", "Action": ["s3:ListAllMyBuckets"], "Resource": "*"},
            ],
        }

    def data_source_role_policy_document(self):
        """The Quick-only role's inline policy. Wider than the owner's policy: besides reading the
        logs it runs Athena, reads the Glue catalog, and writes the Athena query-results prefix.
        Quick assumes this role for the Athena data sources and Athena reads S3 as this role, so
        it is the only identity that touches the bucket (Quick's own account role needs nothing).
        """
        bucket_arn = f"arn:{self.partition}:s3:::{self.bucket}"
        region, account = self.region, self.account
        wg = f"arn:{self.partition}:athena:{region}:{account}:workgroup/{ATHENA_WORKGROUP}"
        catalog = f"arn:{self.partition}:athena:{region}:{account}:datacatalog/AwsDataCatalog"
        glue_cat = f"arn:{self.partition}:glue:{region}:{account}:catalog"
        glue_db = f"arn:{self.partition}:glue:{region}:{account}:database/{GLUE_DATABASE}"
        glue_tables = f"arn:{self.partition}:glue:{region}:{account}:table/{GLUE_DATABASE}/*"
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ReadQuickUsageLogs",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"{bucket_arn}/{PREFIX_ROOT}/*"],
                },
                {
                    "Sid": "ListQuickUsageLogBucket",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": [bucket_arn],
                },
                {"Sid": "ListBucketNames", "Effect": "Allow", "Action": ["s3:ListAllMyBuckets"], "Resource": "*"},
                {
                    "Sid": "WriteAthenaResults",
                    "Effect": "Allow",
                    "Action": [
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
                    "Resource": [wg, catalog],
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
                    "Resource": [glue_cat, glue_db, glue_tables],
                },
                # When the Glue catalog is governed by Lake Formation (IAMAllowedPrincipals
                # removed from the defaults), Athena needs lakeformation:GetDataAccess for the
                # role to read the data; harmless when Lake Formation isn't in use.
                {
                    "Sid": "LakeFormationDataAccess",
                    "Effect": "Allow",
                    "Action": ["lakeformation:GetDataAccess"],
                    "Resource": "*",
                },
            ],
        }

    def owner_policy_exists(self):
        try:
            self.iam.get_policy(PolicyArn=self.owner_policy_arn())
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchEntity":
                return False
            raise

    def owner_assignment(self, namespace="default"):
        return self.get_or_none(
            self.qs.describe_iam_policy_assignment,
            "IAMPolicyAssignment",
            AwsAccountId=self.account,
            AssignmentName=self.owner_assignment_name(),
            Namespace=namespace,
        )

    def owner_access_state(self):
        """Missing parts of the bucket access step 6 sets up. Stops on a resource that differs."""
        missing = [] if self.owner_policy_exists() else ["IAM policy"]
        role = self.data_source_role()
        if role is None:
            missing.append("data source role")
        elif not self.is_our_data_source_role(role):
            self.log.write(event="data_source_role_mismatch", role=self.data_source_role_name())
            raise Abort(
                f"IAM role {self.data_source_role_name()} already exists but can be assumed by "
                "something other than Quick.\n  The script won't change it. Delete or rename it, "
                "or re-run with another --bucket."
            )
        else:
            if not self.data_source_role_trust_current(role):
                missing.append("data source role trust")  # role without sts:TagSession: add it
            if not self.data_source_role_policy_current():
                missing.append("data source role policy")  # add/refresh Athena+Glue grants
        namespace, user = self.owner_identity()
        assignment = self.owner_assignment(namespace)
        if assignment is None:
            missing.append("IAM policy assignment")
        elif not self.is_owner_assignment(assignment, user):
            self.log.write(event="owner_assignment_mismatch", found=assignment)
            raise Abort(
                f"Quick IAM policy assignment {self.owner_assignment_name()} already exists but "
                "isn't the one this script creates. Found:\n"
                f"    policy: {assignment.get('PolicyArn')}\n"
                f"    status: {assignment.get('AssignmentStatus')}\n"
                f"    identities: {assignment.get('Identities')}\n"
                f"  Expected policy {self.owner_policy_arn()}, ENABLED, for user {user}.\n"
                "  The script won't change it. Fix it in Quick, or delete it and re-run:\n"
                f"    aws quicksight delete-iam-policy-assignment --aws-account-id {self.account} "
                f"--assignment-name {self.owner_assignment_name()} --namespace {namespace} "
                f"--region {self.region}"
            )
        return missing

    def is_owner_assignment(self, assignment, user):
        """Our policy, enabled, for the owner. Quick may return identity keys in another case
        or list users by ARN, so compare loosely."""
        if assignment.get("PolicyArn") != self.owner_policy_arn():
            return False
        if (assignment.get("AssignmentStatus") or "").upper() != "ENABLED":
            return False
        users = [
            v.lower()
            for k, values in (assignment.get("Identities") or {}).items()
            if k.lower() == "user"
            for v in values
        ]
        want = user.lower()
        return any(u == want or u.endswith("/" + want) for u in users)

    def grant_owner_access(self):
        missing = self.owner_access_state()
        namespace, user = self.owner_identity()
        if "IAM policy" in missing:
            self.iam.create_policy(
                PolicyName=self.owner_policy_name(),
                PolicyDocument=json.dumps(self.owner_policy_document()),
                Description="Lets the Quick usage dataset owner read the usage logs while "
                "Quick default resource access is Deny. Created by quick_usage_datasets_setup.py.",
            )
            print(f"  IAM policy {self.owner_policy_name()} created (read-only, this bucket's usage logs)")
            self.log.write(event="resource", iam_policy=self.owner_policy_arn())
        if "data source role" in missing:
            self.iam.create_role(
                RoleName=self.data_source_role_name(),
                AssumeRolePolicyDocument=json.dumps(self.data_source_role_trust()),
                Description="Quick reads the usage logs through this role for the quick-usage-* "
                "data sources. Created by quick_usage_datasets_setup.py.",
            )
        if "data source role" in missing or "data source role policy" in missing:
            self.iam.put_role_policy(
                RoleName=self.data_source_role_name(),
                PolicyName="QuickUsageLogsRead",
                PolicyDocument=json.dumps(self.data_source_role_policy_document()),
            )
            verb = "created" if "data source role" in missing else "given its read policy again"
            print(
                f"  IAM role {self.data_source_role_name()} {verb} (only Quick can use it; reads "
                "this bucket's usage logs and runs Athena over them)"
            )
            self.log.write(event="resource", iam_role=self.data_source_role_arn())
        if "data source role trust" in missing:  # add sts:TagSession so Athena can assume the role
            self.iam.update_assume_role_policy(
                RoleName=self.data_source_role_name(),
                PolicyDocument=json.dumps(self.data_source_role_trust()),
            )
            print(
                f"  IAM role {self.data_source_role_name()} trust updated so Quick can assume it "
                "for Athena (added sts:TagSession)"
            )
            self.log.write(event="data_source_role_trust_updated", role=self.data_source_role_name())
        if "IAM policy assignment" in missing:
            self.qs.create_iam_policy_assignment(
                AwsAccountId=self.account,
                AssignmentName=self.owner_assignment_name(),
                AssignmentStatus="ENABLED",
                PolicyArn=self.owner_policy_arn(),
                Identities={"User": [user]},
                Namespace=namespace,
            )
            print(f"  Quick IAM policy assignment {self.owner_assignment_name()} created for {user}")
            self.log.write(event="resource", iam_policy_assignment=self.owner_assignment_name(), user=user)
            # CreateIAMPolicyAssignment is eventually consistent: DescribeIAMPolicyAssignment can
            # still return ResourceNotFound for a short window right after. Wait for the read-back
            # so this step's own check_athena verification sees it instead of racing the create
            # and stopping the run with "step ran but isn't complete".
            self.wait_for_owner_assignment(namespace)
        if not missing:
            print(
                f"  Already in place: IAM role {self.data_source_role_name()} and the owner's IAM "
                f"policy assignment {self.owner_assignment_name()}."
            )

    def wait_for_owner_assignment(self, namespace, present=True, timeout=60, delay=3):
        """Wait until DescribeIAMPolicyAssignment shows the owner's assignment (present=True) or
        no longer shows it (present=False), after creating or deleting it.

        Creating and deleting an IAM policy assignment are eventually consistent: for a few
        seconds Describe can still miss a new assignment or still return a deleted one. Polling
        lets the step's own check right afterwards see the change instead of racing it. The wait
        is bounded: a transient error while reading (a throttle, say) counts as "not yet", any
        other error is raised at once, and if Quick still shows the old state at the timeout,
        the step's check decides, and the run stops with its usual re-run guidance.
        """
        deadline = time.monotonic() + timeout
        while not self.owner_assignment_is(namespace, present):
            if time.monotonic() > deadline:
                action, state = ("create", "doesn't list it yet") if present else ("delete", "still lists it")
                print(
                    f"  The {action} request for IAM policy assignment {self.owner_assignment_name()} "
                    f"succeeded, but after {timeout} seconds Quick {state}. If this step stops, "
                    "re-run the script: it checks again."
                )
                return
            time.sleep(delay)

    def owner_assignment_is(self, namespace, present):
        """Whether Describe shows the owner's assignment as present (or absent).

        A transient error (still throttled after boto3's own retries, or a Quick-side failure)
        counts as "not yet", so the wait carries on. Any other error, such as AccessDenied, is
        raised at once: waiting wouldn't fix it.
        """
        try:
            return (self.owner_assignment(namespace) is not None) == present
        except ClientError as e:
            if e.response["Error"]["Code"] in TRANSIENT_ERROR_CODES:
                return False
            raise

    def skip_quick_access(self, reason):
        print("  Not needed. The data sources read the bucket through IAM role")
        print(f"  {self.data_source_role_name()} (step {DATASETS_STEP}), so Quick's own role needs no")
        print("  access to it. If an earlier run had you tick this bucket under Quick's")
        print("  'AWS resources > Amazon S3', you can untick it; nothing here needs it.")
        self.log.write(event="quick_access_skipped", reason=reason)

    def skip_deny_default(self, reason):
        print("  Skipped. Default access isn't changed, so after the next step every Quick author")
        print("  in this account can read the usage logs.")
        self.log.write(event="deny_default_skipped", reason=reason)

    def confirmation_alternatives(self, key):
        """Other phrases a step accepts besides its confirmation phrase: {phrase: answer}."""
        if key == "deny_default":
            return {"IT WAS ALREADY DENIED": "already", "SKIP AND ACCEPT EXPOSURE": "skip"}
        return {}

    # ---------- quick_access step (step 5): manual Quick console grant ----------
    # Policies Quick itself attaches to its role: AWSQuickSight*Policy under /service-role/ in the
    # account (S3, RDS, Redshift, IAM, ...) and AWS managed ones such as AWSQuicksightAthenaAccess.
    # The console saves the S3 grant only when the role has just these policies attached.
    @staticmethod
    def is_quick_managed_policy(policy_arn):
        resource = policy_arn.split(":", 5)[5]  # policy/<path>/<name>
        return resource.startswith("policy/service-role/") and resource.rsplit("/", 1)[
            -1
        ].lower().startswith("awsquicksight")

    def quick_role_own_policies(self):
        """Policies on Quick's S3 role that Quick didn't create.

        Returns (role_name, [policy names]); the list is None if the check itself failed.
        """
        role_arn = self.quick_s3_role()
        if not role_arn:
            return None, None
        role_name = role_arn.rsplit("/", 1)[-1]
        try:
            own = [
                p["PolicyName"]
                for page in self.iam.get_paginator("list_attached_role_policies").paginate(
                    RoleName=role_name
                )
                for p in page["AttachedPolicies"]
                if not self.is_quick_managed_policy(p["PolicyArn"])
            ]
            own += [
                f"{name} (inline)"
                for page in self.iam.get_paginator("list_role_policies").paginate(
                    RoleName=role_name
                )
                for name in page["PolicyNames"]
            ]
        except ClientError as e:
            self.log.write(event="quick_role_policies", outcome="unchecked",
                           error=e.response["Error"]["Code"])
            return role_name, None
        self.log.write(event="quick_role_policies", role=role_name, own_policies=own)
        return role_name, own

    def quick_access_statements(self):
        """The 3 read statements the console grant adds, for customers who add them by hand."""
        bucket_arn = f"arn:{self.partition}:s3:::{self.bucket}"
        return [
            {"Effect": "Allow", "Action": "s3:ListAllMyBuckets",
             "Resource": f"arn:{self.partition}:s3:::*"},
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": bucket_arn},
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"],
             "Resource": f"{bucket_arn}/*"},
        ]

    def plan_quick_access(self):
        role_name, own = self.quick_role_own_policies()
        role_label = role_name or "aws-quicksight-service-role-v0"
        if own is None:
            found = [
                f"  The script couldn't check the policies on {role_label}. Try A first; if",
                "  Save fails with the message above, use B.",
            ]
        elif own:
            found = [
                f"  {bold('The script found policies on ' + role_label + ' that Quick did not create:')}",
                *[f"    - {name}" for name in own],
                f"  So the console will refuse to save. {bold('Use B.')}",
            ]
        else:
            found = [f"  The script checked {role_label}: it has only Quick's own policies. Use A."]
        bucket_arn = f"arn:{self.partition}:s3:::{self.bucket}"
        items = [json.dumps(st) for st in self.quick_access_statements()]
        statements = [item + ("," if i < len(items) - 1 else "") for i, item in enumerate(items)]
        return [
            "MANUAL STEP (you do this yourself; the script waits for you, then checks the result):",
            "",
            "  Quick reads S3 through its IAM role, " + role_label + ". This step gives that role",
            "  read access to the bucket. Which way you do it depends on that role:",
            "  the Quick console only saves the grant if the role has no policies that you or your",
            "  admins attached yourselves. If it has any, Save fails with:",
            "    'Quick has detected unknown policies attached to following roles, please detach",
            "     them and retry'",
            *found,
            "",
            "  A. Quick's role has only Quick's own policies (most accounts): grant it in the console.",
            "",
            "  Before you start: sign in to Quick as an admin user (Admin or Admin Pro role).",
            "  Other users can't open this page.",
            "",
            "  Open the 'AWS resources' page, either way:",
            "    a. Direct link (click it, or copy it into your browser):",
            "         "
            + link(
                f"https://{self.region}.quicksight.aws.amazon.com/sn/console/account/"
                f"{self.account}/resources"
            ),
            "    b. Or from the menu: in Quick, choose your user name (top right) >",
            "       Manage account > AWS resources.",
            "",
            "  Bucket to grant (copy this):",
            f"      {self.bucket}",
            "",
            "  On the 'AWS resources' page:",
            "  1. Leave 'IAM Role' on 'Use Quick-managed role (default)'.",
            "  2. Tick 'Amazon S3' and click 'Select S3 buckets' under it.",
            "     Use 'Amazon S3', NOT 'Amazon S3 Tables'. The right dialog is titled",
            "     'Select Amazon S3 buckets'; if the dialog asks for a 'Table bucket ARN', go back.",
            "  3. Tick the bucket above. If it is not listed, open the 'S3 buckets you can access",
            "     across AWS' tab and paste the bucket name above into 'Use a different bucket'.",
            "  4. Leave 'Write permission for Athena Workgroup' unticked (Quick only needs to read).",
            "  5. Click Finish, then Save at the bottom of the page. Don't change any other service.",
            "",
            "  B. You attached your own policies to Quick's role: don't detach them (other Quick",
            "     data sources may depend on them). Add these 3 statements to one of YOUR policies",
            f"     on {role_label} instead (IAM console > Roles > {role_label} >",
            "     your policy > Edit), then save the policy:",
            "       1. s3:ListAllMyBuckets on " + f"arn:{self.partition}:s3:::*",
            f"       2. s3:ListBucket on {bucket_arn}",
            f"       3. s3:GetObject and s3:GetObjectVersion on {bucket_arn}/*",
            "     As JSON, to paste into that policy's \"Statement\" list (add a comma before them",
            "     if the list already has statements):",
            *[f"       {line}" for line in statements],
            "     Don't add write actions; Quick only needs to read.",
            "",
            f"This lets Quick read the bucket. Without it, step {DATASETS_STEP} fails.",
            "",
            "WHO CAN THEN READ THE LOGS: the grant is for Quick as a whole, not for you. Every",
            "Quick author in this account can create a data source on this bucket and read all",
            f"usage records, unless default access is Deny (step {DENY_DEFAULT_STEP}) and they "
            "have no IAM policy",
            "assignment for it. " + IDENTITY_NOTE[0],
            IDENTITY_NOTE[1],
            IDENTITY_NOTE[2],
            "Type the confirmation phrase only after you have saved the change.",
        ]

    def quick_s3_role(self):
        """The IAM role Quick uses to read S3. Some accounts have a dedicated S3 consumers role."""
        for name in (
            "aws-quicksight-s3-consumers-role-v0",
            "aws-quicksight-service-role-v0",
        ):
            try:
                return self.iam.get_role(RoleName=name)["Role"]["Arn"]
            except ClientError as e:
                if e.response["Error"]["Code"] != "NoSuchEntity":
                    raise
        return None

    def check_quick_access(self):
        """Step-level check: is the console grant already in place?"""
        try:
            ok, _, _ = self.simulate_quick_access()
        except ClientError as e:
            return StepState(
                False,
                [f"Can't check the grant automatically ({e.response['Error']['Code']})."],
                unknown=True,
            )
        if ok:
            return StepState(True, ["Quick can already read the bucket (IAM policy simulator)."])
        return StepState(False, ["Quick can't read the bucket yet."])

    def simulate_quick_access(self):
        """Simulate Quick's role against the bucket. Returns (ok, lines to print, details to log)."""
        role = self.quick_s3_role()
        if not role:
            return (
                False,
                ["  No Quick service role found. Is the Quick subscription in this account?"],
                {},
            )
        sample = f"arn:{self.partition}:s3:::{self.bucket}/{PREFIX_ROOT}/chat/sample.json"
        checks = [
            ("s3:ListBucket", f"arn:{self.partition}:s3:::{self.bucket}", True),
            ("s3:GetObject", sample, True),
            ("s3:PutObject", sample, False),
            ("s3:DeleteObject", sample, False),
        ]
        results, lines, ok = (
            {},
            [f"  Checking what Quick's role ({role.split('/')[-1]}) can do:"],
            True,
        )
        for action, resource, want in checks:
            decision = self.iam.simulate_principal_policy(
                PolicySourceArn=role, ActionNames=[action], ResourceArns=[resource]
            )["EvaluationResults"][0]["EvalDecision"]
            allowed = decision == "allowed"
            results[action] = decision
            if want and not allowed:
                ok = False
                lines.append(f"    {action}: NOT allowed. Quick can't read the bucket yet.")
            elif not want and allowed:
                lines.append(
                    f"    {action}: allowed. Quick has WRITE access; untick 'Write permission"
                    " for Athena Workgroup' unless you need it."
                )
            else:
                lines.append(
                    f"    {action}: {'allowed' if allowed else 'not allowed'} (as expected)"
                )
        return ok, lines, {"quick_role": role, "simulation": results}

    def run_quick_access(self, state=None):
        while True:
            try:
                ok, lines, details = self.simulate_quick_access()
            except ClientError as e:
                print(
                    f"  Could not verify the grant automatically ({e.response['Error']['Code']})."
                )
                print(
                    f"  Continuing; if step {DATASETS_STEP} fails with an access error, repeat "
                    f"step {QUICK_ACCESS_STEP}."
                )
                self.log.write(
                    event="verify_quick_access",
                    outcome="unverified",
                    error=e.response["Error"]["Code"],
                )
                return
            for line in lines:
                print(line)
            self.log.write(
                event="verify_quick_access",
                outcome="ok" if ok else "missing",
                **details,
            )
            if ok:
                print("  Verified: Quick can read the bucket.")
                return
            print(
                f"\n  The grant isn't in place yet. Check that s3://{self.bucket} is ticked under"
            )
            print("  'Amazon S3' (not 'Amazon S3 Tables') and that you clicked Finish and Save.")
            print("  If you used B (your own policy on Quick's role), check the 3 statements are in")
            print("  that policy, the policy is attached to the role, and you saved it.")
            try:
                again = input(f"  Press Enter to check again, or type '{STOP_WORD}' to stop: ")
            except (EOFError, KeyboardInterrupt):
                again = STOP_WORD
            if again.strip().lower() == STOP_WORD:
                raise Abort(f"Stopped at step {QUICK_ACCESS_STEP}: Quick can't read the bucket yet.")

    # ---------- athena step: Glue database + tables, Athena workgroup, access role ----------
    def plan_athena(self):
        return [
            "Set up the Athena and Glue resources the datasets read through. Athena reads the",
            f"logs in place under s3://{self.bucket}/{PREFIX_ROOT}/<dataset>/ — the same prefixes",
            "delivery writes to — so this works whether the bucket already holds months of logs",
            "or is brand new, and there is no limit on the number of log files. This step creates",
            "(and --destroy removes):",
            f"  - IAM role {self.data_source_role_name()}: only Quick can use it. It reads",
            f"    s3://{self.bucket}/{PREFIX_ROOT}/, runs Athena, and reads the Glue catalog.",
            "    You need iam:CreateRole, iam:PutRolePolicy and iam:PassRole for this role, plus",
            "    athena:CreateWorkGroup and glue:CreateDatabase/CreateTable for the resources below.",
            f"  - IAM policy {self.owner_policy_name()} and Quick IAM policy assignment",
            f"    {self.owner_assignment_name()} that give the dataset owner read access to this",
            "    bucket, so the owner can use it in the Quick console under Deny. Under Allow it",
            "    changes nobody's access.",
            f"  - Glue database {GLUE_DATABASE} with one external table per dataset (no",
            "    partitions, so Athena reads the logs whatever their layout).",
            f"  - Athena workgroup {ATHENA_WORKGROUP} (query results under",
            f"    s3://{self.bucket}/{ATHENA_RESULTS_PREFIX}/).",
            f"Owner of the Quick resources: {self.args.quick_admin_arn}",
            "Tables for: " + ", ".join(f"quick-usage-{d}" for d in self.selected),
        ]

    # ---------- datasets step: Quick Athena data sources + SPICE datasets ----------
    def plan_datasets(self):
        lines = [
            "For each dataset, create a Quick Athena data source over the workgroup and a SPICE",
            "dataset that reads its Glue table (created in the previous step).",
            f"Owner of the data sources and datasets: {self.args.quick_admin_arn}",
            "All datasets are created now, even if no logs have arrived yet; those simply refresh",
            "to 0 rows until the first log files land.",
            "Each column gets a short description, which Quick's natural-language features use.",
            "Datasets that already exist only get the descriptions they lack (this starts one",
            "refresh); a dataset that was reshaped in Quick is left alone.",
        ]
        lines += [f"- quick-usage-{d}" for d in self.selected]
        return lines

    def has_data(self, d):
        if not self.bucket_exists():
            return False
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix(d) + "/", MaxKeys=1)
        return resp.get("KeyCount", 0) > 0

    # ---------- Athena / Glue resources that back the datasets ----------
    def table_location(self, d):
        """Where this dataset's logs already land; the Glue table reads everything under it."""
        return f"s3://{self.bucket}/{self.prefix(d)}/"

    def athena_results_location(self):
        return f"s3://{self.bucket}/{ATHENA_RESULTS_PREFIX}/"

    def workgroup_arn(self):
        return f"arn:{self.partition}:athena:{self.region}:{self.account}:workgroup/{ATHENA_WORKGROUP}"

    def table_input(self, d):
        return {
            "Name": glue_table_name(d),
            "TableType": "EXTERNAL_TABLE",
            "Parameters": {"classification": "json", "EXTERNAL": "TRUE"},
            "StorageDescriptor": {
                "Columns": glue_columns(DATASETS[d][1]),
                "Location": self.table_location(d),
                "InputFormat": GLUE_TEXT_INPUT,
                "OutputFormat": GLUE_IGNOREKEY_OUTPUT,
                "SerdeInfo": {
                    "SerializationLibrary": GLUE_JSON_SERDE,
                    "Parameters": {"ignore.malformed.json": "true", "case.insensitive": "true"},
                },
            },
        }

    def ensure_glue_database(self):
        try:
            self.glue.create_database(
                DatabaseInput={
                    "Name": GLUE_DATABASE,
                    "Description": "Amazon Quick usage analytics external tables. "
                    "Created by quick_usage_datasets_setup.py.",
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
            try:
                self.athena.create_work_group(
                    Name=ATHENA_WORKGROUP,
                    Configuration={
                        "ResultConfiguration": {"OutputLocation": loc},
                        "EnforceWorkGroupConfiguration": True,
                        "PublishCloudWatchMetricsEnabled": False,
                    },
                    Description="Amazon Quick usage analytics. Created by "
                    "quick_usage_datasets_setup.py.",
                    Tags=[WORKGROUP_TAG],
                )
                print(f"  Athena workgroup {ATHENA_WORKGROUP} created")
                self.log.write(event="resource", athena_workgroup=ATHENA_WORKGROUP)
                return
            except ClientError as e:
                # Only a race (created between the check above and now) is tolerated; any other
                # error is real. Existence is decided by get_work_group, not the message text.
                if e.response["Error"]["Code"] != "InvalidRequestException":
                    raise
                # InvalidRequestException is also raised for genuine config errors (e.g. a bad
                # results location). If the workgroup still isn't there, it wasn't a race, so
                # re-raise the real error rather than masking it in the tag check below.
                if not self.workgroup_present():
                    raise
        # The workgroup already exists. Only touch it if this script created it (our tag); a
        # customer workgroup that happens to share the name is left alone.
        tags = self.athena.list_tags_for_resource(ResourceARN=self.workgroup_arn()).get("Tags", [])
        if not any(t.get("Key") == WORKGROUP_TAG["Key"] and t.get("Value") == WORKGROUP_TAG["Value"] for t in tags):
            raise Abort(
                f"An Athena workgroup named {ATHENA_WORKGROUP} already exists but this script "
                "didn't create it (it isn't tagged ManagedBy=quick_usage_datasets_setup).\n  The "
                "script won't change it. Delete or rename it, then re-run.",
                resume_step=ATHENA_STEP,
            )
        # Our workgroup: its results location is enforced, so if an earlier run (e.g. against
        # another --bucket) pointed it elsewhere, Athena would write results where this bucket's
        # role can't. Repoint it at this bucket.
        wg = self.athena.get_work_group(WorkGroup=ATHENA_WORKGROUP)["WorkGroup"]
        current = wg.get("Configuration", {}).get("ResultConfiguration", {}).get("OutputLocation")
        if current != loc:
            self.athena.update_work_group(
                WorkGroup=ATHENA_WORKGROUP,
                ConfigurationUpdates={
                    "ResultConfigurationUpdates": {"OutputLocation": loc},
                    "EnforceWorkGroupConfiguration": True,
                },
            )
            print(f"  Athena workgroup {ATHENA_WORKGROUP} results location set to {loc}")
            self.log.write(event="athena_workgroup_results_updated", location=loc)

    def glue_table_for(self, d):
        try:
            return self.glue.get_table(DatabaseName=GLUE_DATABASE, Name=glue_table_name(d))["Table"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityNotFoundException":
                return None
            raise

    def ensure_glue_table(self, d, exists):
        """Create the external table, or point an existing one at the right location/columns."""
        if exists:
            self.glue.update_table(DatabaseName=GLUE_DATABASE, TableInput=self.table_input(d))
            print(f"  {d}: Glue table {glue_table_name(d)} updated")
        else:
            self.glue.create_table(DatabaseName=GLUE_DATABASE, TableInput=self.table_input(d))
            print(f"  {d}: Glue table {glue_table_name(d)} created ({self.table_location(d)})")
        self.log.write(
            event="resource",
            glue_table=glue_table_name(d),
            location=self.table_location(d),
            updated=exists,
        )

    def glue_table_missing(self, d):
        """True if d's Glue external table is absent or points at the wrong location."""
        table = self.glue_table_for(d)
        if table is None:
            return True
        loc = table.get("StorageDescriptor", {}).get("Location", "").rstrip("/")
        return loc != self.table_location(d).rstrip("/")

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
            if athena.get("WorkGroup") != ATHENA_WORKGROUP or role_arn != self.data_source_role_arn():
                raise Abort(
                    f"Quick data source {ds_id} already exists but uses workgroup "
                    f"{athena.get('WorkGroup')} / role {role_arn},\n  not {ATHENA_WORKGROUP} / "
                    f"{self.data_source_role_arn()}. The script won't change it. Delete the data "
                    "source and its dataset, then re-run."
                )
        data_set = self.get_or_none(
            self.qs.describe_data_set, "DataSet", AwsAccountId=self.account, DataSetId=ds_id
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
            if self.descriptions_to_add(d, data_set):
                missing.append(COLUMN_DESCRIPTIONS_MISSING)
        return missing, src

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
        have = {c["Name"]: c.get("Description") for c in data_set.get("OutputColumns", [])}
        return {n: t for n, t in COLUMN_DESCRIPTIONS[d].items() if n in have and not have[n]}

    def add_column_descriptions(self, d):
        """Add the missing column descriptions to an existing dataset, keeping everything else.

        UpdateDataSet replaces the whole definition, so every field DescribeDataSet returned
        that UpdateDataSet accepts is sent back unchanged. It also starts one SPICE refresh.
        """
        ds_id = f"quick-usage-{d}"
        data_set = self.qs.describe_data_set(AwsAccountId=self.account, DataSetId=ds_id)["DataSet"]
        to_add = self.descriptions_to_add(d, data_set)
        if not to_add:
            return
        accepted = self.qs.meta.service_model.operation_model("UpdateDataSet").input_shape.members
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
            table.setdefault("DataTransforms", []).extend(column_description_transforms(to_add))
        else:
            (physical_id,) = data_set["PhysicalTableMap"]
            tables = logical_table_map(glue_table_name(d), physical_id, to_add)
        params["LogicalTableMap"] = tables
        self.qs.update_data_set(AwsAccountId=self.account, DataSetId=ds_id, **params)
        print(f"  {d}: added descriptions to {len(to_add)} columns of dataset {ds_id}")
        self.log.write(event="column_descriptions_added", dataset=d, columns=sorted(to_add))

    def athena_parameters(self):
        return {
            "AthenaParameters": {
                "WorkGroup": ATHENA_WORKGROUP,
                "RoleArn": self.data_source_role_arn(),
            }
        }

    def create_athena_data_source(self, ds_id, attempts=4, delay=15):
        """CreateDataSource (Athena) with the data source role, retrying while it propagates.

        Quick checks it can assume AthenaParameters.RoleArn before it creates the data source.
        Right after this step creates the role, IAM can take a few seconds.
        """

        def create():
            return self.qs.create_data_source(
                AwsAccountId=self.account,
                DataSourceId=ds_id,
                Name=ds_id,
                Type="ATHENA",
                DataSourceParameters=self.athena_parameters(),
                Permissions=[
                    {"Principal": self.args.quick_admin_arn, "Actions": DATA_SOURCE_ACTIONS}
                ],
            )

        # A new IAM role can take a short time to become usable, so Quick can briefly report
        # "Failed to assume your role" right after the role is created.
        def role_not_ready(error):
            return error["Code"] == "AccessDeniedException" and "assume" in error.get(
                "Message", ""
            ).lower()

        def on_retry(attempt, error):
            self.log.write(
                event="assume_role_denied",
                data_source=ds_id,
                attempt=attempt,
                message=error.get("Message", ""),
            )
            print(
                f"  Quick can't use the new role yet (IAM takes a few seconds); checking "
                f"again in {delay}s ({attempt}/{attempts - 1})..."
            )

        def give_up(exc):
            self.log.write(
                event="assume_role_denied",
                data_source=ds_id,
                attempt=attempts,
                message=exc.response["Error"].get("Message", ""),
            )
            raise Abort(
                f"Quick can't assume IAM role {self.data_source_role_name()} to run "
                "Athena, so it won't create the data source.\n"
                "  Check:\n"
                f"  - the role exists and trusts quicksight.amazonaws.com with "
                f"aws:SourceAccount {self.account}. The athena step creates it.\n"
                f"  - its inline policy QuickUsageLogsRead allows athena:StartQueryExecution "
                f"on workgroup\n    {ATHENA_WORKGROUP} and s3:GetObject on "
                f"s3://{self.bucket}/{PREFIX_ROOT}/*.\n"
                "  - no bucket policy or SCP denies the role.\n"
                "  Re-run from the athena step, which creates/repairs the role.",
                resume_step=ATHENA_STEP,
            )

        return retry_on_client_error(
            create, role_not_ready, attempts, delay, on_retry=on_retry, give_up=give_up
        )

    def wait_for_data_source(self, ds_id, timeout=120):
        """Data source creation is asynchronous; wait until it settles before using it."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.qs.describe_data_source(AwsAccountId=self.account, DataSourceId=ds_id)[
                "DataSource"
            ].get("Status", "")
            if not status.endswith("IN_PROGRESS"):
                if status.endswith("FAILED"):
                    raise Abort(
                        f"Quick couldn't create data source {ds_id} ({status}). Check that IAM "
                        f"role {self.data_source_role_name()} can assume and run Athena over "
                        f"s3://{self.bucket}/{PREFIX_ROOT}/, then re-run."
                    )
                return
            if time.monotonic() > deadline:
                raise Abort(f"Data source {ds_id} is still {status} after {timeout}s. Re-run later.")
            time.sleep(3)

    def check_athena(self):
        todo, lines = {}, []
        if self.args.quick_admin_arn:  # only known when the owner was resolved for this run
            owner_missing = self.owner_access_state()
            if owner_missing:
                todo[OWNER_ACCESS] = owner_missing
                lines.append(f"dataset owner's access: not set up yet (missing: {', '.join(owner_missing)})")
            else:
                lines.append(f"dataset owner's access: IAM role and policy assignment in place")
        shared = []
        if not self.glue_database_present():
            shared.append("Glue database")
        if not self.workgroup_present():
            shared.append("Athena workgroup")
        if shared:
            todo[ATHENA_SHARED] = shared
            lines.append(f"shared Athena/Glue: not set up yet (missing: {', '.join(shared)})")
        else:
            lines.append(
                f"shared Athena/Glue: database {GLUE_DATABASE} and workgroup {ATHENA_WORKGROUP} exist"
            )
        for d in self.selected:
            if self.glue_table_missing(d):
                todo[d] = ["Glue table"]
                lines.append(f"quick-usage-{d}: Glue table not set up yet")
            else:
                lines.append(f"quick-usage-{d}: Glue table {glue_table_name(d)} exists")
        return StepState(not todo, lines, todo)

    def run_athena(self, state=None):
        if not self.args.quick_admin_arn:
            raise Abort("--quick-admin-arn is required for this step.")
        # Access plumbing and the account-wide resources, all idempotent, so re-runs are safe.
        self.grant_owner_access()
        self.ensure_glue_database()
        self.ensure_workgroup()
        for d in [x for x in (state.todo if state else self.selected) if x in DATASETS]:
            if self.glue_table_missing(d):
                self.ensure_glue_table(d, exists=self.glue_table_for(d) is not None)

    def check_datasets(self):
        todo, lines = {}, []
        for d in self.selected:
            missing, _ = self.dataset_state(d)
            if missing == [COLUMN_DESCRIPTIONS_MISSING]:
                todo[d] = missing
                lines.append(f"quick-usage-{d}: dataset exists, column descriptions not added yet")
            elif missing:
                todo[d] = missing
                lines.append(f"quick-usage-{d}: not set up yet (missing: {', '.join(missing)})")
            else:
                lines.append(f"quick-usage-{d}: Athena data source and dataset exist")
        return StepState(not todo, lines, todo)

    def run_datasets(self, state=None):
        if not self.args.quick_admin_arn:
            raise Abort("--quick-admin-arn is required for this step.")
        for d in [x for x in (state.todo if state else self.selected) if x in DATASETS]:
            # Re-read just before changing anything, so a retry only does what's still missing.
            missing, src = self.dataset_state(d)
            ds_id = f"quick-usage-{d}"
            if src is None:
                src = self.create_athena_data_source(ds_id)
                print(f"  {d}: Athena data source {ds_id} created")
                self.wait_for_data_source(ds_id)
            else:
                print(f"  {d}: data source {ds_id} already exists, kept")
            if "dataset" not in missing:
                if COLUMN_DESCRIPTIONS_MISSING in missing:
                    self.add_column_descriptions(d)
                    continue
                print(f"  {d}: dataset {ds_id} already exists, kept")
                self.log.write(event="resource_kept", dataset=d, data_set=ds_id)
                continue
            has_data = self.has_data(d)
            self.qs.create_data_set(
                AwsAccountId=self.account,
                DataSetId=ds_id,
                Name=f"Quick usage - {d}",
                ImportMode="SPICE",
                PhysicalTableMap={
                    "t": {
                        "CustomSql": {
                            "DataSourceArn": src["Arn"],
                            "Name": glue_table_name(d),
                            "SqlQuery": athena_custom_sql(
                                GLUE_DATABASE, glue_table_name(d), DATASETS[d][1]
                            ),
                            "Columns": athena_sql_columns(DATASETS[d][1]),
                        }
                    }
                },
                LogicalTableMap=logical_table_map(glue_table_name(d), "t", COLUMN_DESCRIPTIONS[d]),
                Permissions=[{"Principal": self.args.quick_admin_arn, "Actions": DATASET_ACTIONS}],
            )
            print(f"  {d}: dataset {ds_id} created")
            self.log.write(
                event="resource",
                dataset=d,
                data_source=src["Arn"],
                data_set=ds_id,
                data_present=has_data,
            )
            if not has_data:
                print(f"  NOTE: no {d} logs have arrived yet, so this dataset's first refresh")
                print("        reads 0 rows (Athena simply finds no files; this is not an error).")
                print("        Once you've used Quick, refresh it (Datasets > the dataset >")
                print("        Refresh now).")
                self.log.write(event="dataset_created_without_data", dataset=d)

    # ---------- confirmation phrase per step ----------
    def confirmation_phrase(self, key):
        """What the customer types to approve a step. It says what the step does."""
        return {
            "deliveries": "SEND USAGE LOGS TO MY BUCKET",
            "deny_default": "DENY DEFAULT ACCESS",
            "bucket": "CREATE BUCKET",
            "quick_access": "I GAVE QUICK READ ACCESS TO MY BUCKET",
            "athena": "CREATE ATHENA RESOURCES",
            "datasets": "CREATE DATASETS",
            "refresh": "ENABLE DAILY REFRESH",
            "dashboard": (
                "UPDATE DASHBOARD" if getattr(self, "dashboard_outdated", {}) else "CREATE DASHBOARD"
            ),
        }[key]

    # ---------- refresh step: optional daily refresh ----------
    def plan_refresh(self):
        return [
            "OPTIONAL. We recommend refreshing the datasets once a day so they show new usage.",
            "Without a schedule, the datasets only update when someone clicks Refresh now in Quick.",
            "",
            f"Schedule: a full refresh every day at {self.args.refresh_time} ({self.args.timezone}),",
            f"for: {', '.join(f'quick-usage-{d}' for d in self.selected)}",
            "",
            "A full refresh reloads each dataset from all of its logs, so SPICE always matches the",
            "bucket. (Change the time with --refresh-time HH:MM and --timezone, e.g.",
            "America/New_York.)",
            "",
            "Each dataset also has an event_datetime column (a real timestamp parsed from the raw",
            "logs). If you later want faster refreshes, you can switch a dataset to incremental",
            "refresh yourself in Quick, using event_datetime as the look-back column; see",
            "README.md.",
        ]

    def check_refresh(self):
        todo, lines = {}, []
        for d in self.selected:
            ds_id = f"quick-usage-{d}"
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
                # Leave an existing schedule alone. This deliberately preserves whatever it is
                # now, including an incremental schedule a customer set up themselves (see
                # README). run_refresh runs for a dataset only when it has no schedule,
                # or when the customer forces this step with --from-step.
                ids = ", ".join(s["ScheduleId"] for s in schedules)
                lines.append(f"{ds_id}: already has a refresh schedule ({ids})")
            else:
                todo[d] = ["schedule"]
                lines.append(f"{ds_id}: no refresh schedule")
        return StepState(not todo, lines, todo)

    def run_refresh(self, state=None):
        for d in state.todo if state else self.selected:
            ds_id = f"quick-usage-{d}"
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
                print(f"  {d}: daily full refresh scheduled at {self.args.refresh_time}")
                outcome = "created"
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceExistsException":
                    raise
                # Reached only when the customer forces this step with --from-step (a normal run
                # skips a dataset that already has a schedule; see check_refresh). Reset it to the
                # daily full refresh this script manages.
                self.qs.update_refresh_schedule(
                    AwsAccountId=self.account, DataSetId=ds_id, Schedule=schedule
                )
                print(f"  {d}: refresh schedule updated to daily full refresh")
                outcome = "updated"
            self.log.write(
                event="refresh_schedule",
                dataset=ds_id,
                outcome=outcome,
                refresh_type="full",
                time=self.args.refresh_time,
                timezone=self.args.timezone,
            )

    def skip_refresh(self, reason):
        print("  Skipped. The datasets won't update on their own. To refresh them:")
        print("    - in Quick: Datasets > the dataset > Refresh now, or")
        print("    - add a schedule later: Datasets > the dataset > Refresh > Add schedule.")
        self.log.write(event="refresh_skipped", reason=reason)

    # ---------- dashboard step: optional starter dashboard ----------
    def dataset_arn(self, d):
        return f"arn:{self.partition}:quicksight:{self.region}:{self.account}:dataset/quick-usage-{d}"

    def quick_link(self, kind, asset_id):
        return link(f"https://{self.region}.quicksight.aws.amazon.com/sn/{kind}/{asset_id}")

    def dashboard_scope_problem(self):
        """Why the starter dashboard can't be built for this run, or None."""
        missing = [d for d in DASHBOARD_DATASETS if d not in self.selected]
        if missing:
            return (
                "The starter dashboard reads the chat, chat_feedback, agent_hours and index_usage datasets, "
                "but this run sets up only "
                f"{', '.join(self.selected)} (missing: {', '.join(missing)})."
            )
        return None

    def plan_dashboard(self):
        problem = self.dashboard_scope_problem()
        if problem:
            return [problem, "This step will be skipped."]
        outdated = getattr(self, "dashboard_outdated", {})
        if outdated:
            current = ", ".join(f"{kind} version {v}" for kind, v in outdated.items())
            return [
                f"OPTIONAL. A newer starter dashboard is available: version {DASHBOARD_VERSION}"
                f" (yours: {current}).",
                "  Sheets: Overview, Chat adoption & performance, Agent hours, Feedback,",
                "  Agents & resources, Monthly report.",
                "",
                f"Updating replaces the definition of {ANALYSIS_ID} and {DASHBOARD_ID} in place.",
                "Their IDs, links, owner and sharing stay the same.",
                "",
                "Any changes you made to the analysis in Quick are overwritten. To keep them,",
                "use Save as in Quick before you confirm, or type SKIP to keep your version.",
                "The dashboard's previous version stays in its version history in Quick.",
            ]
        return [
            "OPTIONAL. Create a starter analysis and dashboard over the chat, chat_feedback,",
            "agent_hours and index_usage datasets:",
            f"  - analysis  {ANALYSIS_ID}  (open it to change or add visuals)",
            f"  - dashboard {DASHBOARD_ID}  (the read-only view you can share)",
            "  Sheets: Overview, Chat adoption & performance, Agent hours, Feedback,",
            "  Agents & resources, Monthly report.",
            f"Owner of both: {self.args.quick_admin_arn}. Nobody else can see them until you share.",
            "",
            "What a viewer sees: per-user usage (user ARNs, which include the Quick user name),",
            "counts, agent hours and feedback ratings. The logs have no chat text or feedback",
            "comments. Anyone you share the dashboard with sees this for every user.",
            "",
            "Visuals stay empty until the datasets have data and have been refreshed.",
            "Re-running the script keeps an existing analysis and dashboard. If a later version of",
            "the script has a newer starter dashboard, it asks before updating them.",
            "Creating them is free; viewing follows your Quick pricing.",
        ]

    def asset_state(self, kind, asset_id):
        """None if missing, else the status. Stops on a failed or still-running asset."""
        if kind == "analysis":
            asset = self.get_or_none(
                self.qs.describe_analysis, "Analysis", AwsAccountId=self.account, AnalysisId=asset_id
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

    def asset_version(self, kind, asset_id):
        """Starter dashboard version an existing asset was built from, or None if unreadable."""
        try:
            tags = self.qs.list_tags_for_resource(ResourceArn=self.asset_arn(kind, asset_id))
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
        return 1  # built before the script tagged versions

    def version_tags(self):
        return [{"Key": DASHBOARD_VERSION_TAG, "Value": str(DASHBOARD_VERSION)}]

    def check_dashboard(self):
        missing_datasets = [
            d
            for d in DASHBOARD_DATASETS
            if self.get_or_none(
                self.qs.describe_data_set,
                "DataSet",
                AwsAccountId=self.account,
                DataSetId=f"quick-usage-{d}",
            )
            is None
        ]
        todo, lines = {}, []
        if missing_datasets:
            todo["datasets"] = [f"quick-usage-{d}" for d in missing_datasets]
            lines.append(
                f"datasets not created yet (step {DATASETS_STEP}): {', '.join(todo['datasets'])}"
            )
        self.dashboard_outdated = {}
        for kind, asset_id in DASHBOARD_ASSETS:
            if self.asset_state(kind, asset_id) is None:
                todo[kind] = [asset_id]
                lines.append(f"{kind} {asset_id}: not created yet")
                continue
            version = self.asset_version(kind, asset_id)
            if version is None:
                lines.append(
                    f"{kind} {asset_id}: already exists, kept as is (couldn't read its version)"
                )
            elif version < DASHBOARD_VERSION:
                self.dashboard_outdated[kind] = version
                todo.setdefault("update", []).append(asset_id)
                lines.append(
                    f"{kind} {asset_id}: version {version}, "
                    f"newer version {DASHBOARD_VERSION} available"
                )
            else:
                lines.append(f"{kind} {asset_id}: already exists (version {version}), kept as is")
        return StepState(not todo, lines, todo)

    def wait_for_asset(self, kind, asset_id, version_number=None):
        """Creation and updates are asynchronous. Wait, and turn a failed definition into a
        clear stop. version_number waits for that dashboard version (after an update)."""
        deadline = time.monotonic() + CREATE_TIMEOUT_SECONDS
        while True:
            if kind == "analysis":
                asset = self.qs.describe_analysis(AwsAccountId=self.account, AnalysisId=asset_id)[
                    "Analysis"
                ]
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
            self.log.write(event="asset_errors", kind=kind, asset_id=asset_id, errors=errors)
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
                f"The starter dashboard needs datasets that don't exist yet: {', '.join(missing)}."
                f" Run step {DATASETS_STEP} first.",
                resume_step=DATASETS_STEP,
            )
        outdated = getattr(self, "dashboard_outdated", {})
        definition = dashboard_definition({d: self.dataset_arn(d) for d in DASHBOARD_DATASETS})
        # No ThemeArn: the dashboard carries its own styling (per-tile background/border and
        # per-visual chart colours baked into DASHBOARD_DEFINITION_JSON); anything not covered by
        # the definition uses the account's default Quick theme.
        common = {
            "AwsAccountId": self.account,
            "Name": DASHBOARD_NAME,
            "Definition": definition,
            "ValidationStrategy": {"Mode": "STRICT"},
        }
        # Re-check just before creating, so a retry only creates what's still missing.
        if self.asset_state("analysis", ANALYSIS_ID) is None:
            self.qs.create_analysis(
                **common,
                AnalysisId=ANALYSIS_ID,
                Permissions=[{"Principal": self.args.quick_admin_arn, "Actions": ANALYSIS_ACTIONS}],
                Tags=self.version_tags(),
            )
            self.wait_for_asset("analysis", ANALYSIS_ID)
            print(f"  analysis {ANALYSIS_ID} created")
            self.log.write(event="resource", analysis=ANALYSIS_ID)
        elif "analysis" in outdated:
            self.qs.update_analysis(**common, AnalysisId=ANALYSIS_ID)
            self.wait_for_asset("analysis", ANALYSIS_ID)
            self.qs.tag_resource(
                ResourceArn=self.asset_arn("analysis", ANALYSIS_ID), Tags=self.version_tags()
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
                Permissions=[{"Principal": self.args.quick_admin_arn, "Actions": DASHBOARD_ACTIONS}],
                Tags=self.version_tags(),
            )
            self.wait_for_asset("dashboard", DASHBOARD_ID)
            print(f"  dashboard {DASHBOARD_ID} created")
            self.log.write(event="resource", dashboard=DASHBOARD_ID)
        elif "dashboard" in outdated:
            response = self.qs.update_dashboard(**common, DashboardId=DASHBOARD_ID)
            version_number = int(response["VersionArn"].rsplit("/", 1)[1])
            self.wait_for_asset("dashboard", DASHBOARD_ID, version_number)
            # An update creates a new dashboard version; viewers see it once it's published.
            self.qs.update_dashboard_published_version(
                AwsAccountId=self.account, DashboardId=DASHBOARD_ID, VersionNumber=version_number
            )
            self.qs.tag_resource(
                ResourceArn=self.asset_arn("dashboard", DASHBOARD_ID), Tags=self.version_tags()
            )
            print(
                f"  dashboard {DASHBOARD_ID} updated to version {DASHBOARD_VERSION}"
                f" (Quick dashboard version {version_number}, published)"
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
            print("  Skipped. Your current analysis and dashboard are kept as they are. Re-run")
            print(f"  with --from-step {DASHBOARD_STEP} to update them later.")
            self.log.write(event="dashboard_update_skipped", reason=reason)
            return
        print("  Skipped. No analysis or dashboard was created. You can build your own in Quick")
        print("  from the quick-usage-* datasets, or re-run with --from-step "
              f"{DASHBOARD_STEP} to create the starter one.")
        self.log.write(event="dashboard_skipped", reason=reason)

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
            if e.response["Error"]["Code"] in ("ResourceNotFoundException", "NoSuchKey", "404"):
                return False
            raise

    def object_exists(self, key):
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key, ExpectedBucketOwner=self.account)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def removal_phrase(self, key, state):
        if key == "bucket":
            if "bucket" in state.todo:
                return "DELETE BUCKET AND USAGE LOGS"
            return "DELETE USAGE LOGS FROM BUCKET"
        return {
            "dashboard": "DELETE DASHBOARD",
            "refresh": "DELETE REFRESH SCHEDULES",
            "datasets": "DELETE DATASETS",
            "athena": "DELETE ATHENA RESOURCES",
            "deliveries": "STOP USAGE LOG DELIVERY",
            "quick_access": "I REMOVED QUICK ACCESS TO MY BUCKET",
            "deny_default": "I SET DEFAULT ACCESS BACK TO ALLOW",
            "owner_access": "DELETE OWNER ACCESS POLICY",
        }[key]

    # undoes the dashboard step
    def find_dashboard_to_remove(self):
        todo, lines = {}, []
        if self.get_or_none(
            self.qs.describe_dashboard, "Dashboard", AwsAccountId=self.account, DashboardId=DASHBOARD_ID
        ):
            todo["dashboard"] = [DASHBOARD_ID]
            lines.append(f"dashboard {DASHBOARD_ID}")
        analysis = self.get_or_none(
            self.qs.describe_analysis, "Analysis", AwsAccountId=self.account, AnalysisId=ANALYSIS_ID
        )
        if analysis is not None:
            todo["analysis"] = [ANALYSIS_ID]
            note = (
                " (deleted, still in Quick's recovery window)"
                if analysis.get("Status") == "DELETED"
                else ""
            )
            lines.append(f"analysis {ANALYSIS_ID}{note}")
        if not todo:
            lines.append("no starter dashboard or analysis")
        return StepState(not todo, lines, todo)

    def plan_remove_dashboard(self, state):
        return [
            "Delete the starter dashboard and analysis. Your changes to them are lost,",
            "and anyone you shared the dashboard with loses it. The analysis is deleted without",
            "Quick's recovery window, so a later setup can create it again. To keep a copy, use",
            "Save as in Quick before you confirm.",
        ]

    def remove_dashboard(self, state):
        if "dashboard" in state.todo:
            self.delete_if_present(
                self.qs.delete_dashboard, AwsAccountId=self.account, DashboardId=DASHBOARD_ID
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

    # undoes the refresh step
    def find_refresh_to_remove(self):
        todo, lines = {}, []
        for d in self.selected:
            ds_id = f"quick-usage-{d}"
            try:
                schedules = self.qs.list_refresh_schedules(
                    AwsAccountId=self.account, DataSetId=ds_id
                )["RefreshSchedules"]
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
                continue
            ids = [s["ScheduleId"] for s in schedules]
            if REFRESH_SCHEDULE_ID in ids:
                todo[d] = [REFRESH_SCHEDULE_ID]
                lines.append(f"{ds_id}: daily refresh schedule {REFRESH_SCHEDULE_ID}")
            others = [i for i in ids if i != REFRESH_SCHEDULE_ID]
            if others:
                lines.append(
                    f"{ds_id}: other schedules ({', '.join(others)}) weren't created by this "
                    "script and are kept (they go if the dataset is deleted)"
                )
        if not todo:
            lines.append("no daily refresh schedules from this script")
        return StepState(not todo, lines, todo)

    def plan_remove_refresh(self, state):
        return [
            f"Delete the daily refresh schedule ({REFRESH_SCHEDULE_ID}) on: "
            + ", ".join(f"quick-usage-{d}" for d in state.todo),
            "The datasets stop updating on their own. Nothing else changes.",
        ]

    def remove_refresh(self, state):
        for d in state.todo:
            self.delete_if_present(
                self.qs.delete_refresh_schedule,
                AwsAccountId=self.account,
                DataSetId=f"quick-usage-{d}",
                ScheduleId=REFRESH_SCHEDULE_ID,
            )
            print(f"  {d}: daily refresh schedule deleted")
            self.log.write(
                event="resource_deleted", refresh_schedule=REFRESH_SCHEDULE_ID, dataset=d
            )

    # undoes the datasets step (the Quick datasets and data sources)
    def find_datasets_to_remove(self):
        todo, lines = {}, []
        bucket_there = self.bucket_exists()
        for d in self.selected:
            ds_id = f"quick-usage-{d}"
            parts = []
            if self.get_or_none(
                self.qs.describe_data_set, "DataSet", AwsAccountId=self.account, DataSetId=ds_id
            ):
                parts.append("dataset")
            src = self.get_or_none(
                self.qs.describe_data_source,
                "DataSource",
                AwsAccountId=self.account,
                DataSourceId=ds_id,
            )
            if src is not None:
                params = src.get("DataSourceParameters", {})
                athena = params.get("AthenaParameters", {})
                legacy = params.get("S3Parameters", {}).get("ManifestFileLocation", {})
                if athena:
                    if athena.get("WorkGroup") == ATHENA_WORKGROUP and athena.get(
                        "RoleArn"
                    ) == self.data_source_role_arn():
                        parts.append("data source")
                    else:
                        lines.append(
                            f"{ds_id}: the data source uses workgroup {athena.get('WorkGroup')} / "
                            f"role {athena.get('RoleArn')}, not this script's. Kept; delete it "
                            "yourself if it's yours."
                        )
                elif legacy:  # an S3 (manifest) data source
                    if legacy.get("Bucket") == self.bucket:
                        parts.append("data source")
                    else:
                        lines.append(
                            f"{ds_id}: the data source reads s3://{legacy.get('Bucket')}/"
                            f"{legacy.get('Key')}, not this bucket. Kept; delete it yourself if "
                            "it's yours."
                        )
                else:
                    lines.append(
                        f"{ds_id}: a data source with this id exists but isn't this script's "
                        "(no Athena workgroup/role or S3 manifest match). Kept; delete it "
                        "yourself if it's yours."
                    )
            if bucket_there and self.object_exists(self.manifest_key(d)):
                parts.append("manifest")  # an S3 upload manifest for this dataset
            if parts:
                todo[d] = parts
                lines.append(f"{ds_id}: {', '.join(parts)}")
        if not todo:
            lines.append("no datasets or data sources from this script")
        return StepState(not todo, lines, todo)

    def shared_athena_present(self):
        """The account-wide Athena/Glue resources step 6 creates that destroy would remove.

        The Glue database is only listed when it holds nothing but this script's tables, which
        is exactly when destroy removes it; a database kept because the customer added their own
        tables is not reported, so the post-removal completion check doesn't trip on it.
        """
        present = []
        if self.workgroup_is_ours():
            present.append("workgroup")
        if self.glue_database_present() and self.glue_database_only_ours():
            present.append("Glue database")
        return present

    def workgroup_present(self):
        try:
            self.athena.get_work_group(WorkGroup=ATHENA_WORKGROUP)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("InvalidRequestException", "ResourceNotFoundException"):
                return False
            raise

    def workgroup_is_ours(self):
        """True only if the workgroup exists and carries this script's ManagedBy tag.

        Destroy uses this (not just existence) so it never deletes a customer workgroup that
        happens to share the name — the same ownership check setup's ensure_workgroup applies.
        Deleting a workgroup is irreversible for its saved queries and query history, which
        aren't in S3.
        """
        if not self.workgroup_present():
            return False
        tags = self.athena.list_tags_for_resource(ResourceARN=self.workgroup_arn()).get("Tags", [])
        return any(
            t.get("Key") == WORKGROUP_TAG["Key"] and t.get("Value") == WORKGROUP_TAG["Value"]
            for t in tags
        )

    def glue_database_present(self):
        try:
            self.glue.get_database(Name=GLUE_DATABASE)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityNotFoundException":
                return False
            raise

    def glue_database_only_ours(self):
        """True if every table in our database is one this script made (quick_usage_*)."""
        names = []
        token = None
        while True:
            kw = {"DatabaseName": GLUE_DATABASE}
            if token:
                kw["NextToken"] = token
            resp = self.glue.get_tables(**kw)
            names += [t["Name"] for t in resp.get("TableList", [])]
            token = resp.get("NextToken")
            if not token:
                break
        return all(n.startswith("quick_usage_") for n in names)

    def plan_remove_datasets(self, state):
        return [
            "Delete these Quick datasets (with their SPICE data) and their Athena data sources:",
            *[f"  quick-usage-{d}: {', '.join(parts)}" for d, parts in state.todo.items()],
            "Any analysis or dashboard you built yourself on these datasets stops working.",
            "The Glue tables and the log files in the bucket aren't touched in this step.",
        ]

    def remove_datasets(self, state):
        for d, parts in state.todo.items():
            ds_id = f"quick-usage-{d}"
            if "dataset" in parts:
                self.delete_if_present(
                    self.qs.delete_data_set, AwsAccountId=self.account, DataSetId=ds_id
                )
                print(f"  {d}: dataset {ds_id} deleted")
            if "data source" in parts:
                self.delete_if_present(
                    self.qs.delete_data_source, AwsAccountId=self.account, DataSourceId=ds_id
                )
                print(f"  {d}: data source {ds_id} deleted")
            if "manifest" in parts:
                self.s3.delete_object(
                    Bucket=self.bucket, Key=self.manifest_key(d), ExpectedBucketOwner=self.account
                )
                print(f"  {d}: manifest deleted")
            self.log.write(event="resource_deleted", dataset=d, parts=parts)

    # undoes the athena step (the Athena/Glue resources)
    def find_athena_to_remove(self):
        todo, lines = {}, []
        for d in self.selected:
            if self.glue_table_for(d) is not None:
                todo[d] = ["Glue table"]
                lines.append(f"quick-usage-{d}: Glue table {glue_table_name(d)}")
        shared = self.shared_athena_present()
        if shared:
            lines.append(f"shared: {', '.join(shared)}")
        if self.workgroup_present() and not self.workgroup_is_ours():
            lines.append(
                f"Athena workgroup {ATHENA_WORKGROUP} exists but isn't tagged "
                "ManagedBy=quick_usage_datasets_setup; kept."
            )
        done = not todo and not shared
        if done:
            lines.append("no Glue tables, database or workgroup from this script")
        return StepState(done, lines, todo)

    def plan_remove_athena(self, state):
        lines = ["Delete these Glue external tables:"]
        lines += [f"  {glue_table_name(d)}" for d in state.todo]
        shared = self.shared_athena_present()
        if shared:
            lines.append(f"Also remove the shared {', '.join(shared)}.")
        lines.append("The log files in the bucket aren't touched in this step.")
        return lines

    def remove_athena(self, state):
        for d in state.todo:
            self.delete_if_present(
                self.glue.delete_table, DatabaseName=GLUE_DATABASE, Name=glue_table_name(d)
            )
            print(f"  {d}: Glue table {glue_table_name(d)} deleted")
            self.log.write(event="resource_deleted", dataset=d, glue_table=glue_table_name(d))
        self.remove_shared_athena()

    def remove_shared_athena(self):
        """Remove the account-wide workgroup and (if only our tables are left) the Glue database."""
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

    # undoes the quick_access step (step 5)
    def find_quick_access_to_remove(self):
        try:
            granted, _, _ = self.simulate_quick_access()
        except ClientError as e:
            return StepState(
                False,
                [f"Can't check the grant automatically ({e.response['Error']['Code']})."],
                {"grant": [self.bucket]},
                unknown=True,
            )
        if granted:
            return StepState(
                False,
                [f"Quick can still read the bucket (the step {QUICK_ACCESS_STEP} grant)."],
                {"grant": [self.bucket]},
            )
        return StepState(True, ["Quick can't read the bucket; no grant to remove."])

    def plan_remove_quick_access(self, state):
        return [
            "MANUAL STEP (you do this in the Quick console; the script waits for you):",
            "  Sign in to Quick as an admin and open the 'AWS resources' page:",
            "    "
            + link(
                f"https://{self.region}.quicksight.aws.amazon.com/sn/console/account/"
                f"{self.account}/resources"
            ),
            "  (or: your user name, top right > Manage account > AWS resources)",
            "  1. Under 'Amazon S3', click 'Select S3 buckets'.",
            f"  2. Untick {self.bucket}. Leave every other bucket as it is.",
            "  3. Click Finish, then Save at the bottom of the page.",
            f"  If you added the read statements to your own policy on Quick's role instead (step "
            f"{QUICK_ACCESS_STEP}, case B),",
            "  remove those 3 statements from that policy now, in place of steps 1-3.",
            f"This removes the access that step {QUICK_ACCESS_STEP} gave every Quick author "
            "in the account.",
            "Type the confirmation phrase only after you have saved the change.",
        ]

    def remove_quick_access(self, state):
        """Check the grant is gone. Returns 'kept' if the customer leaves it in place."""
        while True:
            try:
                granted, lines, details = self.simulate_quick_access()
            except ClientError as e:
                print(f"  Could not verify automatically ({e.response['Error']['Code']}).")
                self.log.write(event="verify_quick_access_removed", outcome="unverified")
                return None
            self.log.write(
                event="verify_quick_access_removed",
                outcome="still_granted" if granted else "removed",
                **details,
            )
            if not granted:
                print("  Verified: Quick can no longer read the bucket.")
                return None
            for line in lines:
                print(line)
            print(f"\n  Quick can still read s3://{self.bucket}. Check that it's unticked under")
            print("  'Amazon S3' and that you clicked Finish and Save. If Quick has access to all")
            print("  your S3 buckets, unticking one bucket doesn't change that.")
            try:
                again = input(
                    f"  Press Enter to check again, or type {SKIP_WORD} to leave it and continue: "
                )
            except (EOFError, KeyboardInterrupt):
                raise Abort("Stopped while removing Quick's access to the bucket.")
            if again.strip().upper() == SKIP_WORD:
                print("  Left in place.")
                self.log.write(event="quick_access_kept")
                return "kept"

    # undoes the deny_default step (step 4)
    def find_deny_default_to_remove(self):
        record = self.deny_default_record()
        if record and record[0] == "denied":
            return StepState(
                False,
                [f"default access: setup had you set it to Deny on {record[1][:10]}"],
                {"restore": ["Allow"]},
            )
        if record and record[0] == "already":
            return StepState(True, ["default access: was already Deny before setup; left as it is"])
        return StepState(True, ["default access: setup didn't change it"])

    def plan_remove_deny_default(self, state):
        return [
            "MANUAL STEP. Setup had you set Quick's default resource access to Deny.",
            bold("Setting it back to Allow applies to your WHOLE Quick account: every Quick user"),
            bold("and group without an IAM policy assignment gets access again to all AWS data Quick"),
            bold("can read (Amazon S3, Athena, Redshift, RDS), not just this bucket."),
            "If you want to keep Deny, type SKIP.",
            "",
            "  Sign in to Quick as an admin and open the 'Default resource access' page:",
            "    " + self.default_access_link(),
            "  1. Choose the option that allows access to all AWS data and resources.",
            "  2. Save.",
            "Type the confirmation phrase only after you have saved the change.",
        ]

    def remove_deny_default(self, state):
        print("  Recorded: default access set back to Allow.")
        self.log.write(event="deny_default", outcome="restored")

    # undoes the access step 6 sets up: data source role, owner's IAM policy assignment and policy
    def find_owner_access_to_remove(self):
        todo, lines = {}, []
        assignment = self.owner_assignment()
        if assignment is not None:
            if assignment.get("PolicyArn") == self.owner_policy_arn():
                todo["assignment"] = [self.owner_assignment_name()]
                lines.append(f"Quick IAM policy assignment {self.owner_assignment_name()}")
            else:
                lines.append(
                    f"Quick IAM policy assignment {self.owner_assignment_name()} uses another "
                    "policy. Kept; delete it yourself if it's yours."
                )
        if self.owner_policy_exists():
            todo["policy"] = [self.owner_policy_arn()]
            lines.append(f"IAM policy {self.owner_policy_name()}")
        role = self.data_source_role()
        if role is not None:
            if self.is_our_data_source_role(role):
                todo["role"] = [self.data_source_role_arn()]
                lines.append(f"IAM role {self.data_source_role_name()}")
            else:
                lines.append(
                    f"IAM role {self.data_source_role_name()} can be assumed by something other "
                    "than Quick. Kept; delete it yourself if it's yours."
                )
        if not todo:
            lines.append("no data source role, owner access policy or assignment from this script")
        return StepState(not todo, lines, todo)

    def plan_remove_owner_access(self, state):
        return [
            "Delete the IAM role the data sources read the bucket through, and the IAM policy",
            "assignment and IAM policy that let the dataset owner read the usage logs while",
            "default access is Deny. Nothing else uses them once the datasets are gone. Other IAM",
            "roles and IAM policy assignments aren't touched.",
        ]

    def remove_owner_access(self, state):
        if "assignment" in state.todo:
            self.delete_if_present(
                self.qs.delete_iam_policy_assignment,
                AwsAccountId=self.account,
                AssignmentName=self.owner_assignment_name(),
                Namespace="default",
            )
            print(f"  Quick IAM policy assignment {self.owner_assignment_name()} deleted")
            self.log.write(event="resource_deleted", iam_policy_assignment=self.owner_assignment_name())
        if "policy" in state.todo:
            try:
                self.iam.delete_policy(PolicyArn=self.owner_policy_arn())
            except ClientError as e:
                if e.response["Error"]["Code"] != "NoSuchEntity":
                    raise
            print(f"  IAM policy {self.owner_policy_name()} deleted")
            self.log.write(event="resource_deleted", iam_policy=self.owner_policy_arn())
        if "role" in state.todo:
            name = self.data_source_role_name()
            for call, kwargs in (
                (self.iam.delete_role_policy, {"RoleName": name, "PolicyName": "QuickUsageLogsRead"}),
                (self.iam.delete_role, {"RoleName": name}),
            ):
                try:
                    call(**kwargs)
                except ClientError as e:
                    if e.response["Error"]["Code"] != "NoSuchEntity":
                        raise
            print(f"  IAM role {name} deleted")
            self.log.write(event="resource_deleted", iam_role=self.data_source_role_arn())
        if "assignment" in state.todo:
            # Deleting is eventually consistent too: wait, last, until Describe stops returning
            # it, so the check after this destroy step doesn't stop on an assignment that is
            # going away. Last, so the policy and role deletes above never depend on it.
            self.wait_for_owner_assignment("default", present=False)

    # undoes the deliveries step
    def find_deliveries_to_remove(self):
        todo, lines = {}, []
        by_source = {}
        for page in self.logs.get_paginator("describe_deliveries").paginate():
            for dl in page["deliveries"]:
                by_source.setdefault(dl["deliverySourceName"], []).append(dl)
        for d in self.selected:
            name = self.source_name(d)
            src = self.get_or_none(self.logs.get_delivery_source, "deliverySource", name=name)
            dest = self.get_or_none(
                self.logs.get_delivery_destination, "deliveryDestination", name=name
            )
            if src is None and dest is None:
                continue
            if src is not None and self.resource_arn not in src.get("resourceArns", []):
                lines.append(f"{name}: delivery source isn't for this Quick account. Kept.")
                continue
            if dest is not None:
                target = dest["deliveryDestinationConfiguration"]["destinationResourceArn"].rstrip(
                    "/"
                )
                if target != self.expected_destination(d):
                    lines.append(
                        f"{name}: delivery destination sends to {target}, not this bucket. Kept."
                    )
                    continue
            mine = [
                dl
                for dl in by_source.get(name, [])
                if dest and dl["deliveryDestinationArn"] == dest["arn"]
            ]
            if [dl for dl in by_source.get(name, []) if dl not in mine]:
                lines.append(f"{name}: the delivery source also sends to other destinations. Kept.")
                continue
            parts = {
                "deliveries": [dl["id"] for dl in mine],
                "destination": dest is not None,
                "source": src is not None,
            }
            todo[d] = parts
            what = [f"delivery {i}" for i in parts["deliveries"]]
            what += ["destination"] if parts["destination"] else []
            what += ["source"] if parts["source"] else []
            lines.append(f"{name}: {', '.join(what)}")
        if not todo:
            lines.append("no log deliveries from this script")
        return StepState(not todo, lines, todo)

    def plan_remove_deliveries(self, state):
        return [
            "Stop sending Quick usage logs to the bucket: delete the deliveries, delivery",
            "destinations and delivery sources for: " + ", ".join(state.todo),
            "Logs already in the bucket stay until the bucket step. Usage from now on isn't",
            "logged, and can't be delivered later (there is no backfill).",
        ]

    def remove_deliveries(self, state):
        for d, parts in state.todo.items():
            name = self.source_name(d)
            for delivery_id in parts["deliveries"]:
                self.delete_if_present(self.logs.delete_delivery, id=delivery_id)
            if parts["destination"]:
                self.delete_if_present(self.logs.delete_delivery_destination, name=name)
            if parts["source"]:
                self.delete_if_present(self.logs.delete_delivery_source, name=name)
            print(f"  {d}: log delivery stopped")
            self.log.write(
                event="resource_deleted", delivery_source=name, deliveries=parts["deliveries"]
            )

    # undoes the bucket step
    def bucket_created_by_script(self):
        """True if step 2 created this bucket: it has our tag, or a confirmation log says so."""
        try:
            if BUCKET_TAG in self.s3.get_bucket_tagging(Bucket=self.bucket)["TagSet"]:
                return True
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchTagSet", "NoSuchTagSetError"):
                raise
        # Buckets made before the script tagged them: step 2 logs event=resource, bucket=<name>.
        return any(
            e.get("event") == "resource" and e.get("bucket") == self.bucket
            for e in self.earlier_log_entries()
        )

    def other_files_in_bucket(self):
        """True if the bucket holds anything outside the script's folder."""
        resp = self.s3.list_object_versions(Bucket=self.bucket, Delimiter="/", MaxKeys=1000)
        prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
        return bool(
            resp.get("Versions")
            or resp.get("DeleteMarkers")
            or [p for p in prefixes if p != PREFIX_ROOT + "/"]
            or resp.get("IsTruncated")
        )

    def script_files(self, keep_confirmation_logs):
        """Every object version under the script's folder, as delete_objects entries."""
        found = []
        for page in self.s3.get_paginator("list_object_versions").paginate(
            Bucket=self.bucket, Prefix=PREFIX_ROOT + "/"
        ):
            for v in page.get("Versions", []) + page.get("DeleteMarkers", []):
                if keep_confirmation_logs and v["Key"].startswith(LOG_PREFIX + "/"):
                    continue
                found.append({"Key": v["Key"], "VersionId": v["VersionId"]})
        return found

    def find_bucket_to_remove(self):
        if not self.bucket_exists():
            return StepState(True, [f"bucket s3://{self.bucket} doesn't exist"])
        ours = self.bucket_created_by_script()
        remove_whole_bucket = ours and not self.other_files_in_bucket()
        files = self.script_files(keep_confirmation_logs=not remove_whole_bucket)
        todo, lines = {}, []
        if files:
            todo["files"] = files
        if remove_whole_bucket:
            todo["bucket"] = [self.bucket]
            lines.append(f"bucket s3://{self.bucket}: created by this script, nothing else in it")
        elif ours:
            lines.append(
                f"bucket s3://{self.bucket}: created by this script, but it has other files "
                "too. Kept."
            )
        else:
            lines.append(f"bucket s3://{self.bucket}: not created by this script. Kept.")
        lines.append(f"{len(files)} file(s) in {PREFIX_ROOT}/ to delete")
        return StepState(not todo, lines, todo)

    def plan_remove_bucket(self, state):
        n = len(state.todo.get("files", []))
        log_path = getattr(self.log, "path", "the current folder")
        if "bucket" in state.todo:
            return [
                f"Delete all {n} file(s) in s3://{self.bucket}, then the bucket itself.",
                "This can't be undone: the usage logs are gone, and earlier usage can't be",
                "delivered again. The confirmation logs of earlier runs (the record of what was",
                f"approved) are deleted too. This run's log stays local: {log_path}",
            ]
        return [
            f"Delete {n} file(s) in s3://{self.bucket}/{PREFIX_ROOT}/ (usage logs and",
            "manifests). This can't be undone. The bucket, its other files and the confirmation",
            f"logs in {LOG_PREFIX}/ are kept. CloudWatch Logs' write statement for",
            "delivery.logs.amazonaws.com stays in the bucket policy; remove it by hand if you want.",
        ]

    def remove_bucket(self, state):
        files = state.todo.get("files", [])
        for attempt in range(3):
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
            if "bucket" not in state.todo:
                return
            try:
                self.s3.delete_bucket(Bucket=self.bucket, ExpectedBucketOwner=self.account)
                print(f"  bucket s3://{self.bucket} deleted")
                self.log.write(event="resource_deleted", bucket=self.bucket)
                return
            except ClientError as e:
                if e.response["Error"]["Code"] != "BucketNotEmpty" or attempt == 2:
                    raise
                # A last log file landed after the listing. List again and retry.
                files = self.script_files(keep_confirmation_logs=False)

    # ---------- verify step ----------
    def plan_verify(self):
        return ["Read-only. List deliveries and show what has landed in the bucket."]

    def run_verify(self, state=None):
        names = {self.source_name(d) for d in self.selected}
        for page in self.logs.get_paginator("describe_deliveries").paginate():
            for dl in page["deliveries"]:
                if dl["deliverySourceName"] in names:
                    print(
                        f"  delivery {dl['id']}: {dl['deliverySourceName']} -> "
                        f"{dl['deliveryDestinationArn']}"
                    )
        for d in self.selected:
            print(f"  {d}: data delivered = {self.has_data(d)}")
        if self.get_or_none(
            self.qs.describe_dashboard, "Dashboard", AwsAccountId=self.account, DashboardId=DASHBOARD_ID
        ):
            print(f"  starter dashboard: {self.quick_link('dashboards', DASHBOARD_ID)}")


def glue_table_name(dataset):
    """Glue/Athena external-table name for a dataset, e.g. quick_usage_chat."""
    return f"quick_usage_{dataset}"


def glue_columns(fields):
    """Glue external-table columns: all string; the custom SQL casts the typed ones.

    Names are the lowercased field names because the OpenX JSON SerDe lowercases JSON keys.
    """
    return [
        {"Name": (f if isinstance(f, str) else f[0]).lower(), "Type": "string"} for f in fields
    ]


def athena_custom_sql(database, table, fields):
    """SELECT that casts the typed columns and aliases every column back to its field name.

    Glue columns are lowercase; the aliases restore the exact names the datasets use (logType,
    accountId, ...) so the SPICE columns match the usage log field names.
    """
    select = []
    for f in fields:
        name = f if isinstance(f, str) else f[0]
        col = name.lower()
        cast = None if isinstance(f, str) else ATHENA_CAST[f[1]]
        select.append(
            f'TRY_CAST("{col}" AS {cast}) AS "{name}"' if cast else f'"{col}" AS "{name}"'
        )
    # event_datetime: a real DATETIME parsed from the epoch event_timestamp, available for any
    # time-based analysis and as the look-back column if a customer later enables incremental
    # refresh. event_timestamp is milliseconds for some log types and seconds for others, so pick
    # the unit by magnitude (the same > 1e11 heuristic the dashboard uses). from_unixtime returns
    # a timestamp WITH time zone on Athena engine v3; CAST to a plain timestamp so it matches the
    # DATETIME column type.
    select.append(
        'CAST(from_unixtime(CASE WHEN TRY_CAST("event_timestamp" AS bigint) > 100000000000 '
        'THEN TRY_CAST("event_timestamp" AS bigint) / 1000 '
        'ELSE TRY_CAST("event_timestamp" AS bigint) END) AS timestamp) AS "'
        + EVENT_DATETIME_COLUMN
        + '"'
    )
    return "SELECT " + ",\n       ".join(select) + f'\nFROM "{database}"."{table}"'


def athena_sql_columns(fields):
    """QuickSight types for the custom SQL output: STRING, or INTEGER/DECIMAL where declared.

    Includes the derived event_datetime (DATETIME), a general-purpose timestamp and the natural
    look-back column if a customer later enables incremental refresh.
    """
    return [
        {"Name": f if isinstance(f, str) else f[0], "Type": "STRING" if isinstance(f, str) else f[1]}
        for f in fields
    ] + [{"Name": EVENT_DATETIME_COLUMN, "Type": "DATETIME"}]


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
    ("Deny default access to AWS data (optional, recommended, manual)", "deny_default", True),
    ("Grant Quick access to the bucket (not needed, skipped)", "quick_access", True),
    ("Create the Athena and Glue resources", "athena", True),
    ("Create Quick data sources and datasets", "datasets", True),
    ("Daily refresh (optional)", "refresh", True),
    ("Starter dashboard (optional)", "dashboard", True),
    ("Verify", "verify", False),
]
# Optional steps can be skipped by typing SKIP or with their --no-<step> flag.
OPTIONAL_STEPS = {"deny_default", "refresh", "dashboard"}
# The data sources read the bucket through their own IAM role (S3Parameters.RoleArn),
# so the Quick console bucket grant (step 5) isn't needed. The step keeps its number so step
# numbers, --from-step and confirmation logs stay the same; it is always skipped. Destroy still
# offers to remove such a grant if one exists.
NOT_NEEDED_STEPS = {"quick_access"}
# Steps the customer does in the Quick console. The script can't check their result afterwards.
MANUAL_STEPS = {"deny_default", "quick_access"}
STEP = {key: number for number, (_, key, _) in enumerate(STEPS, start=1)}
# Named constant per step, so nothing refers to a step by a bare number (which shifts whenever
# a step is inserted). Use these everywhere a step number is needed.
PRECHECK_STEP = STEP["precheck"]
BUCKET_STEP = STEP["bucket"]
DELIVERIES_STEP = STEP["deliveries"]
DENY_DEFAULT_STEP = STEP["deny_default"]
QUICK_ACCESS_STEP = STEP["quick_access"]
ATHENA_STEP = STEP["athena"]
DATASETS_STEP = STEP["datasets"]
REFRESH_STEP = STEP["refresh"]
DASHBOARD_STEP = STEP["dashboard"]
VERIFY_STEP = STEP["verify"]


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
    return f"That doesn't match. Type it exactly as shown, in CAPITAL letters: {expected}"


SKIP_WORD = "SKIP"


def ask_retention(default, log):
    """Ask how many days to keep usage logs and Athena results before deletion.

    A blank line (or EOF/Ctrl-C in a non-interactive run) uses the default.
    """
    while True:
        try:
            raw = input(
                "\n  New bucket: how many days should usage logs and Athena results be kept "
                f"before they're\n  automatically deleted? [{default}]: "
            ).strip()
        except EOFError:
            raw = ""  # non-interactive run (e.g. piped stdin): fall back to the default
        except KeyboardInterrupt:
            print()
            raise Abort("Stopped at the retention question. Nothing was changed.")
        if not raw:
            days = default
        elif raw.isdigit() and int(raw) >= 1:
            days = int(raw)
        else:
            print(f"    Enter a positive whole number of days, or press Enter for {default}.")
            continue
        log.write(event="retention_chosen", days=days, source="default" if not raw else "typed")
        return days


def ask_until(log, step, step_name, prompt, expected, allow_skip=False, alternatives=None):
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
    print(f"  (To stop without changing anything else, type '{STOP_WORD}' or press Ctrl-C.)")
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


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument(
        "--region",
        help="AWS region (default: your session's region, e.g. CloudShell's region)",
    )
    p.add_argument(
        "--bucket",
        help="S3 bucket for the logs (default: quick-usage-<account>-<region code>)",
    )
    p.add_argument(
        "--quick-admin-arn",
        help="Quick user ARN that will own the datasets (default: you, if you are a "
        "Quick admin)",
    )
    p.add_argument(
        "--datasets",
        default=",".join(DATASETS),
        help=f"comma-separated subset of: {', '.join(DATASETS)}",
    )
    p.add_argument(
        "--refresh-time",
        default=DEFAULT_REFRESH_TIME,
        help="time of day (HH:MM) for the optional daily refresh (default 02:00)",
    )
    p.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="timezone for the daily refresh, e.g. America/New_York (default UTC)",
    )
    p.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help=f"days to keep usage logs and Athena results before deletion, for a bucket this "
        f"script creates (default: ask, {DEFAULT_RETENTION_DAYS})",
    )
    p.add_argument(
        "--no-deny-default",
        action="store_true",
        help="skip the optional (recommended) step that denies default access to AWS data",
    )
    p.add_argument(
        "--no-refresh",
        action="store_true",
        help="skip the optional daily refresh step without asking",
    )
    p.add_argument(
        "--no-dashboard",
        action="store_true",
        help="skip the optional starter dashboard step without asking",
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
        help="remove what this script created, in reverse order (asks before each removal)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--profile")
    args = p.parse_args()
    args.datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    unknown = set(args.datasets) - set(DATASETS)
    if unknown:
        p.error(f"unknown datasets: {sorted(unknown)}")
    try:
        datetime.datetime.strptime(args.refresh_time, "%H:%M")
    except ValueError:
        p.error(f"--refresh-time must be HH:MM, for example 02:00 (got {args.refresh_time})")
    if args.destroy and args.from_step != 1:
        p.error("--destroy can't be combined with --from-step")
    if args.retention_days is not None and args.retention_days < 1:
        p.error(f"--retention-days must be a positive whole number (got {args.retention_days})")
    if args.destroy and set(args.datasets) != set(DATASETS):
        p.error("--destroy always covers every dataset; leave out --datasets")
    if not 1 <= args.from_step <= len(STEPS):
        p.error(f"--from-step must be between 1 and {len(STEPS)} (got {args.from_step})")
    return args


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
    log_path = f"quick-usage-setup-{run_id}.jsonl"
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

    # Work out the remaining settings, then show them all for one confirmation.
    print("\nWorking out the settings (read-only; nothing is changed yet)...", flush=True)
    try:
        setup.bucket, bucket_source = setup.resolve_bucket()
        args.bucket = setup.bucket
        if args.from_step <= DASHBOARD_STEP and not args.destroy:
            admin_arn, admin_role, admin_source = setup.resolve_admin(identity["Arn"])
            args.quick_admin_arn = admin_arn
            owner = (
                f"{admin_arn.split(':user/', 1)[1].partition('/')[2]} "
                f"({admin_role}, {admin_source})"
            )
        else:
            admin_arn = admin_role = admin_source = None
            owner = "not needed for destroy" if args.destroy else (
                f"not needed (starting at step {args.from_step})"
            )
        bucket_state = setup.bucket_state()
        # Resolve the log retention. For a new bucket, ask (default 365; dry runs stay
        # non-interactive). For a bucket we already own, adopt its current retention so a plain
        # re-run doesn't reset it; --retention-days still overrides. A customer's own bucket is
        # left alone.
        creating_bucket = not args.destroy and not setup.bucket_exists()
        if not args.destroy and args.retention_days is None:
            try:
                if creating_bucket and not args.dry_run:
                    # New bucket: ask how long to keep the data (dry runs stay non-interactive).
                    args.retention_days = ask_retention(DEFAULT_RETENTION_DAYS, log)
                elif not creating_bucket and setup.bucket_is_ours():
                    # Existing bucket we own: keep whatever retention it already has, so a plain
                    # re-run doesn't reset it (falls back to the default if it has none).
                    args.retention_days = setup.existing_retention_days()
            except ClientError as e:
                raise Abort(
                    "Couldn't read the bucket's tags or lifecycle to work out the log retention "
                    f"({e.response['Error']['Code']}). Re-run with --retention-days to set it "
                    "explicitly."
                )
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
        print("\nWe'll set up Quick usage datasets with these settings:")
    print(f"  AWS account:   {identity['Account']}  (signed in as {caller})")
    print(f"  Region:        {args.region}  ({region_source})")
    print(f"  Bucket:        {setup.bucket}  ({bucket_source}; {bucket_state})")
    print(f"  Dataset owner: {owner}")
    print(f"  Datasets:      {', '.join(args.datasets)}")
    if creating_bucket:
        print(
            f"  Log retention: {setup.retention_days()} days "
            "(usage logs + Athena results; manifests and setup logs are kept)"
        )
    print(f"  Confirmation log: {log_path}")
    print(
        "\nTo change any of these, type 'stop' and re-run with --region, --bucket or "
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
            print("\n\nInterrupted. Part of the current destroy step may already be done.")
            log.write(event="run_interrupted", mode="destroy")
            print(f"To continue, run:\n    {destroy_command(args)}")
            code = 130
        except Exception as e:
            print(f"\nDestroy failed: {describe_error(e)}")
            log.write(event="run_failed", mode="destroy", error=repr(e), trace=traceback.format_exc())
            print(f"To continue, run:\n    {destroy_command(args)}")
            code = 2
        log.close()
        upload_log(setup, log_path, args)
        return code
    code = 0
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
            if key in NOT_NEEDED_STEPS:
                getattr(setup, f"skip_{key}")("not needed")
                log.write(event="step_skipped", step=number, step_name=name)
                continue
            optional = key in OPTIONAL_STEPS
            skip_flag = getattr(args, f"no_{key}", False) if optional else False
            if skip_flag:
                getattr(setup, f"skip_{key}")(f"--no-{key}")
                log.write(event="step_skipped", step=number, step_name=name)
                continue
            if key in CHECKED_STEPS:
                state = getattr(setup, f"check_{key}")()
                for line in state.lines:
                    print(f"  {line}")
                if state.done:
                    print(f"  Step {number} is already complete. Nothing changed; moving on.")
                    log.write(
                        event="step_already_done",
                        step=number,
                        step_name=name,
                        details=state.lines,
                    )
                    continue
                if state.todo:
                    print(f"  Still to do: {', '.join(state.todo)}")
            for line in getattr(setup, f"plan_{key}")():
                print(f"  {line}")
            if args.dry_run and changes:
                continue
            if changes:
                alternatives = setup.confirmation_alternatives(key)
                answer = confirm(
                    log,
                    number,
                    name,
                    setup.confirmation_phrase(key),
                    # A step with its own skip phrase doesn't also accept the plain SKIP word.
                    allow_skip=optional and "skip" not in alternatives.values(),
                    alternatives=alternatives,
                )
                if answer == "skip":
                    getattr(setup, f"skip_{key}")("typed SKIP")
                    log.write(event="step_skipped", step=number, step_name=name)
                    continue
                if not answer:
                    print(f"\nStopped at step {number}. Nothing in step {number} was changed.")
                    log.write(event="run_stopped", step=number)
                    print_resume(log, args, number)
                    code = 1
                    break
                setup.confirmed_as = answer
            getattr(setup, f"run_{key}")(state)
            # Check the step's own work before moving on, so a silent failure stops here.
            if key in CHECKED_STEPS and key not in MANUAL_STEPS:
                after = getattr(setup, f"check_{key}")()
                if not after.done and not after.unknown:
                    raise Abort(
                        f"Step {number} ran, but the check afterwards says it isn't complete:\n"
                        + "\n".join(f"    {line}" for line in after.lines),
                        resume_step=number,
                    )
            log.write(event="step_done", step=number, step_name=name)
        else:
            log.write(event="run_complete")
            if not args.dry_run:
                print("\nAll steps are complete. Re-running the script is safe: finished steps")
                print("are detected and skipped.")
    except Abort as e:
        resume = e.resume_step or current
        print(f"\nStopped: {e}")
        log.write(event="run_aborted", step=current, reason=str(e))
        print_resume(log, args, resume)
        code = 1
    except KeyboardInterrupt:
        print(f"\n\nInterrupted during step {current}. Part of that step may already be done.")
        log.write(event="run_interrupted", step=current)
        print_resume(log, args, current)
        code = 130
    except Exception as e:
        print(f"\nStep {current} failed: {describe_error(e)}")
        log.write(event="run_failed", step=current, error=repr(e), trace=traceback.format_exc())
        print_resume(log, args, current)
        code = 2
    log.close()
    upload_log(setup, log_path, args)
    return code


# Steps with a read-only "is it already done?" check. Pre-checks and Verify are read-only anyway.
CHECKED_STEPS = {
    "bucket",
    "deliveries",
    "deny_default",
    "quick_access",
    "athena",
    "datasets",
    "refresh",
    "dashboard",
}


def verify_earlier_steps(setup, log, from_step):
    """Before starting at from_step, check (read-only) that every earlier step is complete."""
    print(f"\nStarting at step {from_step}. Checking that steps 1-{from_step - 1} are complete:")
    for number, (name, key, _) in enumerate(STEPS, start=1):
        if number >= from_step:
            break
        if key == "precheck":
            setup.run_precheck()
            print(f"  Step {number} ({name}): passed")
            continue
        if key not in CHECKED_STEPS or key in OPTIONAL_STEPS or key in NOT_NEEDED_STEPS:
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
        if state.unknown:
            print(f"  Step {number} ({name}): couldn't verify automatically; assuming it's done.")
            continue
        if not state.done:
            raise Abort(
                f"You asked to start at step {from_step}, but step {number} ({name}) isn't "
                "complete:\n" + "\n".join(f"    {line}" for line in state.lines),
                resume_step=number,
            )
        print(f"  Step {number} ({name}): done")


# Destroy undoes the setup steps in reverse: (name, key, setup step it undoes).
TEARDOWN = [
    ("Delete the starter dashboard and analysis", "dashboard", DASHBOARD_STEP),
    ("Delete the daily refresh schedules", "refresh", REFRESH_STEP),
    ("Delete the Quick datasets and data sources", "datasets", DATASETS_STEP),
    ("Delete the Athena and Glue resources", "athena", ATHENA_STEP),
    ("Stop log delivery", "deliveries", DELIVERIES_STEP),
    ("Remove Quick's access to the bucket (manual)", "quick_access", QUICK_ACCESS_STEP),
    ("Set default access to AWS data back to Allow (manual)", "deny_default", DENY_DEFAULT_STEP),
    ("Delete the data source role and the owner's IAM policy assignment", "owner_access", ATHENA_STEP),
    ("Delete the usage logs and the bucket", "bucket", BUCKET_STEP),
]
# If you keep one of these, the parts that depend on it are kept too, so what remains keeps working.
TEARDOWN_NEEDS = {
    "datasets": ["dashboard"],
    "athena": ["datasets"],
    "quick_access": ["datasets"],
    "owner_access": ["datasets", "athena"],
    "bucket": ["deliveries", "datasets", "athena"],
}


def run_destroy(setup, log, args):
    """Undo what the setup steps created. Returns the exit code."""
    setup.selected = list(DATASETS)  # destroy always covers everything the script creates
    account = setup.account
    print("\n=== Destroy: remove what this script created ===")
    print("Only things this script creates are removed, under their fixed names. Nothing has")
    print("been deleted yet. This is what the script finds in this account:")
    found = {}
    for number, (name, key, undoes) in enumerate(TEARDOWN, start=1):
        state = getattr(setup, f"find_{key}_to_remove")()
        found[key] = state
        status = "nothing to remove" if state.done else "to remove"
        print(f"\n  {number}. {name} (undoes step {undoes}): {status}")
        for line in state.lines:
            print(f"       {line}")
    log.write(
        event="destroy_inventory",
        found={k: {"done": s.done, "unknown": s.unknown, "details": s.lines} for k, s in found.items()},
    )
    if all(s.done for s in found.values()):
        print("\nNothing from this script is left in this account. Nothing was changed.")
        log.write(event="destroy_complete", kept=[])
        return 0
    if args.dry_run:
        for number, (name, key, _) in enumerate(TEARDOWN, start=1):
            if not found[key].done:
                print(f"\n=== Destroy {number}/{len(TEARDOWN)}: {name} (dry run) ===")
                for line in getattr(setup, f"plan_remove_{key}")(found[key]):
                    print(f"  {line}")
        return 0
    print("\nEach removal asks for its own confirmation, and you can type SKIP to keep it.")
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
        state = getattr(setup, f"find_{key}_to_remove")()  # re-read just before changing anything
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
            log.write(event="destroy_step_kept", step=step, step_name=name, reason="dependency", needs=blocked)
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
            log.write(event="destroy_step_kept", step=step, step_name=name, reason="typed SKIP")
            continue
        if not answer:
            print(f"\nStopped at destroy step {number}. Nothing in this step was deleted.")
            log.write(event="destroy_stopped", step=step)
            print(f"To continue, run:\n    {destroy_command(args)}")
            print("Anything already removed is detected and skipped.")
            return 1
        outcome = getattr(setup, f"remove_{key}")(state)
        if outcome == "kept":
            kept.append(key)
            log.write(event="destroy_step_kept", step=step, step_name=name, reason="grant left in place")
            continue
        if key not in MANUAL_STEPS:  # console changes can't be re-checked here
            after = getattr(setup, f"find_{key}_to_remove")()
            if not after.done:
                raise Abort(
                    f"Destroy step {number} ran, but the check afterwards still finds:\n"
                    + "\n".join(f"    {line}" for line in after.lines)
                )
        log.write(event="destroy_step_done", step=step, step_name=name)
    log.write(event="destroy_complete", kept=kept)
    if kept:
        print("\nDone. You chose to keep: " + ", ".join(n for n, k, _ in TEARDOWN if k in kept) + ".")
        print(f"To remove them later, run:\n    {destroy_command(args)}")
    else:
        print("\nDone. Everything this script created has been removed.")
        print("To set up again, run the script without --destroy.")
    return 0


def destroy_command(args):
    parts = ["python3", sys.argv[0], "--destroy"]
    if args.profile:
        parts += ["--profile", args.profile]
    parts += ["--region", args.region]
    if args.bucket:
        parts += ["--bucket", args.bucket]
    return " ".join(shlex.quote(p) for p in parts)


def describe_error(e):
    """One line for the customer; the full trace goes to the confirmation log."""
    if isinstance(e, ClientError):
        err = e.response.get("Error", {})
        op = getattr(e, "operation_name", "AWS call")
        hint = ""
        if err.get("Code") in ("ExpiredToken", "ExpiredTokenException", "RequestExpired"):
            hint = " Your AWS credentials expired; refresh them and re-run."
        elif "AccessDenied" in err.get("Code", ""):
            hint = " Your AWS identity is missing a permission for this call."
        return f"{op} returned {err.get('Code')}: {err.get('Message')}.{hint}"
    return repr(e)


def resume_command(args, step):
    """The exact command to continue from a step, with every setting this run resolved."""
    parts = ["python3", sys.argv[0], "--from-step", str(step)]
    if args.profile:
        parts += ["--profile", args.profile]
    parts += ["--region", args.region]
    if args.bucket:
        parts += ["--bucket", args.bucket]
    if args.quick_admin_arn and step <= DASHBOARD_STEP:
        parts += ["--quick-admin-arn", args.quick_admin_arn]
    if set(args.datasets) != set(DATASETS):
        parts += ["--datasets", ",".join(args.datasets)]
    if args.refresh_time != DEFAULT_REFRESH_TIME:
        parts += ["--refresh-time", args.refresh_time]
    if args.timezone != DEFAULT_TIMEZONE:
        parts += ["--timezone", args.timezone]
    if args.retention_days is not None and args.retention_days != DEFAULT_RETENTION_DAYS:
        parts += ["--retention-days", str(args.retention_days)]
    if args.no_deny_default:
        parts.append("--no-deny-default")
    if args.no_refresh:
        parts.append("--no-refresh")
    if args.no_dashboard:
        parts.append("--no-dashboard")
    return " ".join(shlex.quote(p) for p in parts)


def print_resume(log, args, step):
    command = resume_command(args, step)
    print("\nTo continue from where it stopped, fix the problem above, then run:")
    print(f"    {command}")
    print("Finished steps (and finished parts of a step) are detected and skipped, so this is")
    print("safe to run more than once.")
    log.write(event="resume_hint", step=step, command=command)


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


if __name__ == "__main__":
    sys.exit(main())
