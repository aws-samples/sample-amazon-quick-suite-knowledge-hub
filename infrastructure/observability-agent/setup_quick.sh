#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Quick Observability — QuickSight sharing + Space provisioning (post-deploy).
#
# The CDK stack (quick-observability-mcp) creates the Athena data source and 9
# SPICE datasets, but QuickSight resources are owned by the creating service
# and are NOT visible to a human user until explicitly shared. This script:
#   1. Resolves a QuickSight principal (user OR group) you pass in.
#   2. Grants that principal CO_OWNER on the Athena data source + 9 datasets.
#   3. Creates a Q Topic over the datasets and shares it.
#   4. Creates a Quick Space, attaches the datasets + Topic, and shares it.
#
# Why a script and not CDK: Amazon Quick Spaces and Topic ownership are not
# fully expressible in CloudFormation, and the owning principal is per-account
# input — matching the quick-for-supply-chain example's setup_quick.sh.
#
# Usage:
#   ./setup_quick.sh (--quicksight-user <username> | --quicksight-group <name>) ...
#                    [--region us-east-1] [--namespace default]
#                    [--space-id quick-observability] [--topic-id quick-observability]
#
#   --quicksight-user / --quicksight-group are REPEATABLE: pass each flag more
#   than once to share with several principals, e.g.
#       ./setup_quick.sh --quicksight-user alice --quicksight-user bob
#       ./setup_quick.sh --quicksight-group analysts --quicksight-user admin
# ---------------------------------------------------------------------------
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
QS_NAMESPACE="default"
SPACE_ID="quick-observability"
TOPIC_ID="quick-observability"
TOPIC_NAME="Quick Observability"
ATHENA_DS_ID="quick-observability-athena"
# Repeatable principals: each entry is "user:<name>" or "group:<name>".
PRINCIPAL_SPECS=()

# The 9 datasets created by the CDK stack (data_set_id values).
DATASET_IDS=(
  quick-obs-chat-events
  quick-obs-feedback-events
  quick-obs-agent-hours
  quick-obs-agent-metadata
  quick-obs-index-usage
  quick-obs-kb-file-sync
  quick-obs-dlp-events
  quick-obs-cloudtrail-events
  quick-obs-metric-snapshots
  quick-obs-user-summary
)
DASHBOARD_ID="quick-observability-usage"

usage() {
  cat >&2 <<EOF
Usage:
  ./setup_quick.sh (--quicksight-user <username> | --quicksight-group <name>) ...
                   [--region <region>] [--namespace <ns>]
                   [--space-id <id>] [--topic-id <id>]

  --quicksight-user / --quicksight-group may be repeated to share with several
  principals. At least one is required.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quicksight-user)  PRINCIPAL_SPECS+=("user:$2");  shift 2 ;;
    --quicksight-group) PRINCIPAL_SPECS+=("group:$2"); shift 2 ;;
    --region)           REGION="$2";                   shift 2 ;;
    --namespace)        QS_NAMESPACE="$2";             shift 2 ;;
    --space-id)         SPACE_ID="$2";                 shift 2 ;;
    --topic-id)         TOPIC_ID="$2";                 shift 2 ;;
    -h|--help)          usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "${#PRINCIPAL_SPECS[@]}" -eq 0 ]]; then
  echo "ERROR: provide at least one --quicksight-user or --quicksight-group." >&2
  usage; exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "Account ${ACCOUNT_ID} / region ${REGION} / namespace ${QS_NAMESPACE}"

# ── Resolve each principal spec to an ARN (fail if any is unresolved) ──────
PRINCIPAL_ARNS=()
PRINCIPAL_LABELS=()
for spec in "${PRINCIPAL_SPECS[@]}"; do
  kind="${spec%%:*}"; name="${spec#*:}"
  if [[ "$kind" == "user" ]]; then
    label="user '${name}'"
    arn="$(aws quicksight describe-user \
      --aws-account-id "${ACCOUNT_ID}" --namespace "${QS_NAMESPACE}" \
      --user-name "${name}" --region "${REGION}" \
      --query "User.Arn" --output text 2>/dev/null || true)"
  else
    label="group '${name}'"
    arn="$(aws quicksight describe-group \
      --aws-account-id "${ACCOUNT_ID}" --namespace "${QS_NAMESPACE}" \
      --group-name "${name}" --region "${REGION}" \
      --query "Group.Arn" --output text 2>/dev/null || true)"
  fi
  if [[ -z "${arn}" || "${arn}" == "None" ]]; then
    echo "ERROR: could not resolve ${label} in namespace '${QS_NAMESPACE}'." >&2
    exit 1
  fi
  PRINCIPAL_ARNS+=("${arn}")
  PRINCIPAL_LABELS+=("${label}")
  echo "  Resolved ${label} -> ${arn}"
done

qs_supports() { aws quicksight help 2>/dev/null | grep -q " $1"; }

# ── Helper: grant CO_OWNER permissions on a resource to ALL principals ─────
grant_owner() {
  local op="$1" id_flag="$2" id_val="$3" actions="$4"
  local acts_json grants i
  acts_json="$(printf '%s' "$actions" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read().strip().split(",")))')"
  grants="["
  for i in "${!PRINCIPAL_ARNS[@]}"; do
    [[ "$i" -gt 0 ]] && grants+=","
    grants+="{\"Principal\":\"${PRINCIPAL_ARNS[$i]}\",\"Actions\":${acts_json}}"
  done
  grants+="]"
  aws quicksight "$op" \
    --aws-account-id "${ACCOUNT_ID}" \
    "$id_flag" "$id_val" \
    --region "${REGION}" \
    --grant-permissions "${grants}" \
    >/dev/null 2>&1 \
    && echo "      shared ${id_val} with ${#PRINCIPAL_ARNS[@]} principal(s)" \
    || echo "      WARN could not share ${id_val} (check actions/ownership)"
}

_ds_arn() { printf 'arn:aws:quicksight:%s:%s:dataset/%s' "${REGION}" "${ACCOUNT_ID}" "$1"; }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# ── Step 1: Share the Athena data source ───────────────────────────────────
echo "=== Step 1: Share data source ${ATHENA_DS_ID} ==="
grant_owner update-data-source-permissions --data-source-id "${ATHENA_DS_ID}" \
  "quicksight:DescribeDataSource,quicksight:DescribeDataSourcePermissions,quicksight:PassDataSource,quicksight:UpdateDataSource,quicksight:DeleteDataSource,quicksight:UpdateDataSourcePermissions"

# ── Step 2: Share the 9 datasets ───────────────────────────────────────────
echo "=== Step 2: Share datasets ==="
PRESENT_DATASETS=()
for ds_id in "${DATASET_IDS[@]}"; do
  if aws quicksight describe-data-set --aws-account-id "${ACCOUNT_ID}" \
        --data-set-id "${ds_id}" --region "${REGION}" >/dev/null 2>&1; then
    grant_owner update-data-set-permissions --data-set-id "${ds_id}" \
      "quicksight:DescribeDataSet,quicksight:DescribeDataSetPermissions,quicksight:PassDataSet,quicksight:DescribeIngestion,quicksight:ListIngestions,quicksight:UpdateDataSet,quicksight:DeleteDataSet,quicksight:CreateIngestion,quicksight:CancelIngestion,quicksight:UpdateDataSetPermissions"
    PRESENT_DATASETS+=("${ds_id}")
  else
    echo "      WARN dataset ${ds_id} not found — skipping"
  fi
done

# ── Step 3: Create a Q Topic over the datasets and share it ────────────────
echo "=== Step 3: Topic ${TOPIC_ID} ==="
TOPIC_ARN=""
if qs_supports "create-topic-v2"; then
  # Datasets + genuine join relationships. Relations are declared ONLY where a
  # real parent-child / dimension join exists; joining fact tables on the
  # shared user_arn dimension would fan out and corrupt aggregates, so those
  # are intentionally left out (analysts can still filter by user per dataset).
  #
  #   feedback_events.conversation_id -> chat_events.conversation_id
  #   chat_events.agent_id            -> agent_metadata.agent_id
  topic_json="${TMP_DIR}/topic.json"
  # map dataset id -> whether it was shared/present
  _present() { case " ${PRESENT_DATASETS[*]} " in *" $1 "*) return 0;; *) return 1;; esac; }
  {
    printf '{"Name":"%s","Description":"Amazon Quick observability datasets + relationships.","DataSets":[' "${TOPIC_NAME}"
    first=1
    for ds_id in "${PRESENT_DATASETS[@]}"; do
      [[ $first -eq 0 ]] && printf ','
      printf '{"DataSetArn":"%s","DataSetName":"%s"}' "$(_ds_arn "$ds_id")" "${ds_id#quick-obs-}"
      first=0
    done
    printf '],"DataSetRelations":['
    first=1
    # feedback -> chat on conversation_id
    if _present quick-obs-feedback-events && _present quick-obs-chat-events; then
      [[ $first -eq 0 ]] && printf ','
      printf '{"Left":{"DataSetArn":"%s","ColumnNames":["conversation_id"]},"Right":{"DataSetArn":"%s","ColumnNames":["conversation_id"]}}' \
        "$(_ds_arn quick-obs-feedback-events)" "$(_ds_arn quick-obs-chat-events)"
      first=0
    fi
    # chat -> agent_metadata on agent_id
    if _present quick-obs-chat-events && _present quick-obs-agent-metadata; then
      [[ $first -eq 0 ]] && printf ','
      printf '{"Left":{"DataSetArn":"%s","ColumnNames":["agent_id"]},"Right":{"DataSetArn":"%s","ColumnNames":["agent_id"]}}' \
        "$(_ds_arn quick-obs-chat-events)" "$(_ds_arn quick-obs-agent-metadata)"
      first=0
    fi
    printf ']}'
  } > "${topic_json}"

  if aws quicksight describe-topic-v2 --aws-account-id "${ACCOUNT_ID}" --topic-id "${TOPIC_ID}" \
        --region "${REGION}" >/dev/null 2>&1; then
    aws quicksight delete-topic-v2 --aws-account-id "${ACCOUNT_ID}" --topic-id "${TOPIC_ID}" \
      --region "${REGION}" >/dev/null 2>&1 || true
    sleep 3
  fi
  TOPIC_ARN="$(aws quicksight create-topic-v2 \
    --aws-account-id "${ACCOUNT_ID}" --topic-id "${TOPIC_ID}" \
    --topic "file://${topic_json}" --region "${REGION}" \
    --query "Arn" --output text 2>/dev/null || true)"
  if [[ -n "${TOPIC_ARN}" && "${TOPIC_ARN}" != "None" ]]; then
    echo "  Created Topic '${TOPIC_ID}'."
    grant_owner update-topic-permissions-v2 --topic-id "${TOPIC_ID}" \
      "quicksight:DescribeTopic,quicksight:PassTopic,quicksight:DeleteTopic,quicksight:UpdateTopic,quicksight:DescribeTopicPermissions,quicksight:UpdateTopicPermissions"
  else
    echo "  WARN create-topic-v2 failed — continuing."
  fi
else
  echo "  WARN this CLI has no create-topic-v2 — skipping Topic."
fi

# ── Step 4: Create the Space ───────────────────────────────────────────────
echo "=== Step 4: Space ${SPACE_ID} ==="
if qs_supports "describe-space" && \
   aws quicksight describe-space --aws-account-id "${ACCOUNT_ID}" \
     --space-id "${SPACE_ID}" --region "${REGION}" >/dev/null 2>&1; then
  echo "  Space '${SPACE_ID}' already exists — reusing."
elif qs_supports "create-space"; then
  aws quicksight create-space \
    --aws-account-id "${ACCOUNT_ID}" --space-id "${SPACE_ID}" \
    --name "${TOPIC_NAME}" \
    --description "Amazon Quick observability — datasets, Topic, and metrics." \
    --region "${REGION}" >/dev/null 2>&1 \
    && echo "  Created Space '${SPACE_ID}'." \
    || echo "  WARN create-space failed — continuing."
else
  echo "  WARN this CLI has no create-space — skipping Space."
fi

# ── Step 5: Attach datasets + Topic to the Space ───────────────────────────
echo "=== Step 5: Attach resources to Space ==="
if qs_supports "update-space-resources"; then
  add_resources="${TMP_DIR}/add_resources.json"
  {
    printf '['
    first=1
    for ds_id in "${PRESENT_DATASETS[@]}"; do
      [[ $first -eq 0 ]] && printf ','
      printf '{"ResourceType":"DATA_SET","ResourceDetails":{"resourceArn":"%s"}}' "$(_ds_arn "$ds_id")"
      first=0
    done
    if [[ -n "${TOPIC_ARN}" && "${TOPIC_ARN}" != "None" ]]; then
      [[ $first -eq 0 ]] && printf ','
      printf '{"ResourceType":"TOPIC","ResourceDetails":{"resourceArn":"%s"}}' "${TOPIC_ARN}"
      first=0
    fi
    # Attach the dashboard to the Space too, if it exists.
    DASH_ARN="$(aws quicksight describe-dashboard --aws-account-id "${ACCOUNT_ID}" \
      --dashboard-id "${DASHBOARD_ID}" --region "${REGION}" \
      --query 'Dashboard.Arn' --output text 2>/dev/null || true)"
    if [[ -n "${DASH_ARN}" && "${DASH_ARN}" != "None" ]]; then
      [[ $first -eq 0 ]] && printf ','
      printf '{"ResourceType":"DASHBOARD","ResourceDetails":{"resourceArn":"%s"}}' "${DASH_ARN}"
      first=0
    fi
    printf ']'
  } > "${add_resources}"
  aws quicksight update-space-resources \
    --aws-account-id "${ACCOUNT_ID}" --space-id "${SPACE_ID}" \
    --add-resources "file://${add_resources}" --region "${REGION}" >/dev/null 2>&1 \
    && echo "  Attached ${#PRESENT_DATASETS[@]} dataset(s)$( [[ -n "${TOPIC_ARN}" && "${TOPIC_ARN}" != "None" ]] && echo ' + Topic' ) to '${SPACE_ID}'" \
    || echo "  WARN update-space-resources failed — attach manually in the console."
else
  echo "  WARN this CLI has no update-space-resources — attach manually."
fi

# ── Step 6: Share the Space (OWNER) with the principal ─────────────────────
echo "=== Step 6: Share Space ==="
if qs_supports "update-space-permissions"; then
  grant_owner update-space-permissions --space-id "${SPACE_ID}" \
    "quicksight:DescribeSpace,quicksight:DescribeSpacePermissions,quicksight:UpdateSpace,quicksight:DeleteSpace,quicksight:UpdateSpacePermissions,quicksight:CreateDocument,quicksight:DeleteDocument,quicksight:ListDocument,quicksight:GetDocument,quicksight:CreateSpaceFolder,quicksight:DeleteSpaceFolder,quicksight:UpdateSpaceFolder,quicksight:MoveSpaceFolderMember,quicksight:ListSpaceFolderMembers"
fi

# ── Step 7: Share the dashboard with the principal ─────────────────────────
echo "=== Step 7: Share dashboard ${DASHBOARD_ID} ==="
if aws quicksight describe-dashboard --aws-account-id "${ACCOUNT_ID}" \
     --dashboard-id "${DASHBOARD_ID}" --region "${REGION}" >/dev/null 2>&1; then
  grant_owner update-dashboard-permissions --dashboard-id "${DASHBOARD_ID}" \
    "quicksight:DescribeDashboard,quicksight:ListDashboardVersions,quicksight:QueryDashboard,quicksight:UpdateDashboard,quicksight:DeleteDashboard,quicksight:DescribeDashboardPermissions,quicksight:UpdateDashboardPermissions,quicksight:UpdateDashboardPublishedVersion"
else
  echo "  WARN dashboard ${DASHBOARD_ID} not found — skipping (deploy the stack first)."
fi

echo ""
echo "Done. Shared with: ${PRINCIPAL_LABELS[*]}"
echo "Open Amazon Quick as any of those principals: the datasets, Topic,"
echo "dashboard, and Space '${SPACE_ID}' should now be visible."
