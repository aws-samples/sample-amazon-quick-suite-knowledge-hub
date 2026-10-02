#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# PHASE 3 — setup_quick.sh  (Quick datasets + KB + Space + agent + sharing)
#
# Reads the uploaded data from S3 (bucket populated in Phase 2), then:
#   - ensures the Quick Space exists,
#   - catalogs each CSV prefix under s3://$BUCKET/structured/<TABLE>/ as a Glue table,
#   - creates ONE QuickSight Athena data source over the Glue database,
#   - creates one SPICE dataset per Glue table (all through that Athena source),
#   - creates an S3 knowledge base over s3://$BUCKET/knowledge-base/,
#   - attaches all datasets + KB to the Space,
#   - creates/updates the Amazon Quick agent (attached to the Space),
#   - shares every resource (as owner) with a QuickSight user OR group.
#
# NO bucket creation, NO upload, NO interactive pause — granting Quick access
# to the bucket + linking the Agent Registry / creating MCP connectors are
# documented MANUAL pre-reqs done between Phase 2 and Phase 3 (see README).
#
# The table list is read from the S3 'structured/' prefix (not local files), so
# this works even without local synthetic_data present.
#
# This is Phase 3 of a THREE-phase deployment:
#   Phase 1  ./deploy_agentcore.sh       — AgentCore infra
#   Phase 2  ./generate_and_upload.sh    — synthetic data + S3 upload
#   Phase 3  ./setup_quick.sh            — Quick datasets + KB + Space + agent (this script)
#
# Account/region-agnostic and idempotent.
#
# MANDATORY:
#   --s3-bucket <name>                      (the bucket populated in Phase 2)
#   AND exactly ONE of:
#     --quicksight-user  <username>
#     --quicksight-group <groupname>
#
# Optional:
#   --action-connectors <id1,id2,...>       MCP connector IDs to attach to the agent
#   --namespace <ns>                        QuickSight namespace (default: default)
#   --space-id <id>                         default: supply-chain-management
#   --kb-id <id>                            default: sc-supply-chain-kb
#   --agent-id <id>                         default: sc-supply-chain-agent
#
# Usage:
#   ./setup_quick.sh --s3-bucket <bucket> --quicksight-user <username>
#   ./setup_quick.sh --s3-bucket <bucket> --quicksight-group <groupname>
# ═══════════════════════════════════════════════════════════════

usage() {
  cat >&2 <<'USAGE'
Usage:
  ./setup_quick.sh --s3-bucket <bucket> (--quicksight-user <username> | --quicksight-group <groupname>)
                   [--action-connectors <id1,id2,...>] [--namespace <ns>]
                   [--space-id <id>] [--kb-id <id>] [--agent-id <id>]

Required:
  --s3-bucket <name>          S3 bucket populated in Phase 2 (generate_and_upload.sh)
  exactly ONE principal:
    --quicksight-user  <name>
    --quicksight-group <name>
USAGE
}

# ── Args ────────────────────────────────────────────────────────
BUCKET=""
QUICKSIGHT_USER=""
QUICKSIGHT_GROUP=""
QS_NAMESPACE="default"
ACTION_CONNECTORS=""
SPACE_ID="supply-chain-management"
KB_ID="sc-supply-chain-kb"
AGENT_ID="sc-supply-chain-agent"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --s3-bucket)         BUCKET="$2";            shift 2 ;;
    --quicksight-user)   QUICKSIGHT_USER="$2";   shift 2 ;;
    --quicksight-group)  QUICKSIGHT_GROUP="$2";  shift 2 ;;
    --action-connectors) ACTION_CONNECTORS="$2"; shift 2 ;;
    --namespace)         QS_NAMESPACE="$2";      shift 2 ;;
    --space-id)          SPACE_ID="$2";          shift 2 ;;
    --kb-id)             KB_ID="$2";             shift 2 ;;
    --agent-id)          AGENT_ID="$2";          shift 2 ;;
    -h|--help)           usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

# ── Validate mandatory inputs ───────────────────────────────────
if [[ -z "${BUCKET}" ]]; then
  echo "ERROR: --s3-bucket <name> is required." >&2
  usage
  exit 1
fi
if [[ -z "${QUICKSIGHT_USER}" && -z "${QUICKSIGHT_GROUP}" ]]; then
  echo "ERROR: provide a principal — either --quicksight-user OR --quicksight-group." >&2
  usage
  exit 1
fi
if [[ -n "${QUICKSIGHT_USER}" && -n "${QUICKSIGHT_GROUP}" ]]; then
  echo "ERROR: provide either --quicksight-user OR --quicksight-group, not both." >&2
  usage
  exit 1
fi

# ── Locate repo root (this script lives at repo root) ───────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# S3 key prefixes (purpose-named): datasets vs knowledge-base docs.
DATASET_PREFIX="structured"
KB_PREFIX="knowledge-base"

# ── Derive account / region (no hardcoded IDs) ──────────────────
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
KB_NAME="${QUICK_KB_NAME:-Supply Chain Knowledge Base}"
AGENT_NAME="${QUICK_AGENT_NAME:-Supply Chain Operations Agent}"
INSTRUCTIONS_FILE="agent/AGENT_INSTRUCTIONS.md"

# ── Glue catalog + single Athena data source (feeds all datasets) ──
GLUE_DATABASE="${GLUE_DATABASE:-sc_supply_chain}"
ATHENA_WORKGROUP="${ATHENA_WORKGROUP:-primary}"
ATHENA_DS_ID="${ATHENA_DS_ID:-sc-supply-chain-athena}"

echo "═══════════════════════════════════════════════════════════════"
echo "  Supply Chain Agents — PHASE 3: Quick datasets + KB + Space + agent"
echo "═══════════════════════════════════════════════════════════════"
echo "  Account: ${ACCOUNT_ID}   Region: ${REGION}"
echo "  Bucket:  s3://${BUCKET}"
echo "  Glue DB: ${GLUE_DATABASE}   Athena WG: ${ATHENA_WORKGROUP}"
echo "  Space:   ${SPACE_ID}"
echo "  KB:      ${KB_ID}"
echo "  Agent:   ${AGENT_ID}  (${AGENT_NAME})"
echo ""

# ── Ensure the CLI exposes the 'quicksight' service ─────────────
qs_supports() {
  # $1 = sub-command name (e.g. create-knowledge-base)
  aws quicksight help 2>/dev/null | grep -qw "$1"
}
if ! aws quicksight help >/dev/null 2>&1; then
  echo "ERROR: the installed AWS CLI does not expose the 'quicksight' service." >&2
  echo "       Upgrade the CLI and re-run." >&2
  exit 1
fi

# ── Resolve the QuickSight principal ARN (user OR group) — FAIL if unresolved ──
PRINCIPAL_ARN=""
PRINCIPAL_LABEL=""
if [[ -n "${QUICKSIGHT_USER}" ]]; then
  PRINCIPAL_LABEL="user '${QUICKSIGHT_USER}'"
  PRINCIPAL_ARN="$(aws quicksight describe-user \
    --aws-account-id "${ACCOUNT_ID}" --namespace "${QS_NAMESPACE}" \
    --user-name "${QUICKSIGHT_USER}" --region "${REGION}" \
    --query "User.Arn" --output text 2>/dev/null || true)"
else
  PRINCIPAL_LABEL="group '${QUICKSIGHT_GROUP}'"
  PRINCIPAL_ARN="$(aws quicksight describe-group \
    --aws-account-id "${ACCOUNT_ID}" --namespace "${QS_NAMESPACE}" \
    --group-name "${QUICKSIGHT_GROUP}" --region "${REGION}" \
    --query "Group.Arn" --output text 2>/dev/null || true)"
fi
if [[ -z "${PRINCIPAL_ARN}" || "${PRINCIPAL_ARN}" == "None" ]]; then
  echo "ERROR: could not resolve ${PRINCIPAL_LABEL} in namespace '${QS_NAMESPACE}'." >&2
  echo "       Check the name and that it is registered in QuickSight in this namespace." >&2
  exit 1
fi
echo "  Resolved ${PRINCIPAL_LABEL} → ${PRINCIPAL_ARN}"
echo ""

# ── Helper: grant CO_OWNER permissions on a resource to the principal ─
# Args: $1 = permissions op, $2 = id flag, $3 = id value, $4 = comma-separated actions
grant_owner() {
  local op="$1" id_flag="$2" id_val="$3" actions="$4"
  local acts_json
  acts_json="$(printf '%s' "$actions" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read().strip().split(",")))')"
  aws quicksight "$op" \
    --aws-account-id "${ACCOUNT_ID}" \
    "$id_flag" "$id_val" \
    --region "${REGION}" \
    --grant-permissions "[{\"Principal\":\"${PRINCIPAL_ARN}\",\"Actions\":${acts_json}}]" \
    >/dev/null 2>&1 \
    && echo "      shared ${id_val} with ${PRINCIPAL_LABEL}" \
    || echo "      ⚠️  could not share ${id_val} (check permission actions/ownership)"
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# ── Step 1: Ensure the Space exists ─────────────────────────────
echo "━━━ Step 1: Quick Space ━━━"
if qs_supports "describe-space" && \
   aws quicksight describe-space \
      --aws-account-id "${ACCOUNT_ID}" --space-id "${SPACE_ID}" \
      --region "${REGION}" >/dev/null 2>&1; then
  echo "  Space '${SPACE_ID}' already exists — reusing."
elif qs_supports "create-space"; then
  aws quicksight create-space \
    --aws-account-id "${ACCOUNT_ID}" \
    --space-id "${SPACE_ID}" \
    --name "${SPACE_ID}" \
    --description "Supply Chain Operations — structured data + documents." \
    --region "${REGION}" >/dev/null 2>&1 || echo "  ⚠️  create-space failed — continuing."
  echo "  Created Space '${SPACE_ID}'."
else
  echo "  ⚠️  This CLI has no create-space/describe-space — skipping Space creation."
fi
echo ""

# ── Step 2: Discover CSV tables from the S3 'structured/' prefix ────
# Layout is one folder per table: structured/<TABLE>/<TABLE>.csv, so we list the
# common prefixes (aws s3 ls prints 'PRE ACCOUNTS/' etc.).
echo "━━━ Step 2: Discover datasets from s3://${BUCKET}/${DATASET_PREFIX}/ ━━━"
TABLES=()
while IFS= read -r _t; do
  [[ -n "$_t" ]] && TABLES+=("$_t")
done < <(aws s3 ls "s3://${BUCKET}/${DATASET_PREFIX}/" --region "${REGION}" 2>/dev/null \
  | awk '/PRE /{print $NF}' | sed 's#/$##')
if [[ "${#TABLES[@]}" -eq 0 ]]; then
  echo "ERROR: no table folders found under s3://${BUCKET}/${DATASET_PREFIX}/." >&2
  echo "       Run Phase 2 (./generate_and_upload.sh) first." >&2
  exit 1
fi
echo "  Found ${#TABLES[@]} table(s): ${TABLES[*]}"
echo ""

# ── Step 3: Glue catalog — one external table per S3 table prefix ─
echo "━━━ Step 3: Glue catalog (${GLUE_DATABASE}) ━━━"
# Create the Glue database if missing (ignore AlreadyExists).
aws glue create-database \
  --database-input "{\"Name\":\"${GLUE_DATABASE}\"}" \
  --region "${REGION}" >/dev/null 2>&1 \
  && echo "  ✔ Created Glue database '${GLUE_DATABASE}'." \
  || echo "  Glue database '${GLUE_DATABASE}' already exists (or create ignored) — reusing."

# table_lc-per-table so datasets in Step 4 can reference the exact Glue name.
declare -a TABLE_LCS=()
for table in "${TABLES[@]}"; do
  [[ -z "$table" ]] && continue
  # Glue table names allow underscores — keep the lowercased raw name.
  table_lc="$(printf '%s' "$table" | tr '[:upper:]' '[:lower:]')"
  s3_uri="s3://${BUCKET}/${DATASET_PREFIX}/${table}/${table}.csv"
  table_location="s3://${BUCKET}/${DATASET_PREFIX}/${table}/"

  # Read the CSV header from S3 to build the Glue columns (all 'string').
  header="$(aws s3 cp "${s3_uri}" - --region "${REGION}" 2>/dev/null | head -1 | tr -d '\r')"
  if [[ -z "${header}" ]]; then
    echo "  ⚠️  could not read header for ${table} (${s3_uri}) — skipping."
    continue
  fi

  # Build the Glue TableInput JSON (columns all 'string', OpenCSVSerde, skip header).
  table_input="${TMP_DIR}/${table}_glue.json"
  python3 - "$header" "$table_lc" "$table_location" > "$table_input" <<'PY'
import json, sys
header, table_lc, location = sys.argv[1], sys.argv[2], sys.argv[3]
cols = [c.strip() for c in header.split(",") if c.strip() != ""]
columns = [{"Name": c, "Type": "string"} for c in cols]
table_input = {
    "Name": table_lc,
    "TableType": "EXTERNAL_TABLE",
    "Parameters": {"classification": "csv", "skip.header.line.count": "1"},
    "StorageDescriptor": {
        "Columns": columns,
        "Location": location,
        "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
        "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
        "SerdeInfo": {
            "SerializationLibrary": "org.apache.hadoop.hive.serde2.OpenCSVSerde",
            "Parameters": {"separatorChar": ","}
        }
    }
}
print(json.dumps(table_input))
PY

  # Create/replace the Glue table (delete if exists, then create).
  if aws glue get-table --database-name "${GLUE_DATABASE}" --name "${table_lc}" \
        --region "${REGION}" >/dev/null 2>&1; then
    aws glue delete-table --database-name "${GLUE_DATABASE}" --name "${table_lc}" \
      --region "${REGION}" >/dev/null 2>&1 || true
  fi
  aws glue create-table \
    --database-name "${GLUE_DATABASE}" \
    --table-input "file://${table_input}" \
    --region "${REGION}" >/dev/null 2>&1 \
    && echo "  ✔ Glue table ${GLUE_DATABASE}.${table_lc}" \
    || echo "  ⚠️  create-table failed for ${table_lc} — check inputs/permissions."

  TABLE_LCS+=("${table_lc}")
done
echo "  Cataloged ${#TABLE_LCS[@]} Glue table(s)."
echo ""

# ── Step 3b: ONE QuickSight Athena data source over the Glue DB ──
echo "━━━ Step 3b: QuickSight Athena data source (${ATHENA_DS_ID}) ━━━"
ATHENA_DS_ARN=""
# If a data source already exists but is in a FAILED state, delete it so we recreate cleanly.
_existing_status="$(aws quicksight describe-data-source \
  --aws-account-id "${ACCOUNT_ID}" --data-source-id "${ATHENA_DS_ID}" \
  --region "${REGION}" --query "DataSource.Status" --output text 2>/dev/null || true)"
if [[ "${_existing_status}" == *FAILED* ]]; then
  echo "  Existing Athena data source is in ${_existing_status} — deleting to recreate."
  aws quicksight delete-data-source \
    --aws-account-id "${ACCOUNT_ID}" --data-source-id "${ATHENA_DS_ID}" \
    --region "${REGION}" >/dev/null 2>&1 || true
  sleep 3
  _existing_status=""
fi
if [[ -n "${_existing_status}" ]] && aws quicksight describe-data-source \
      --aws-account-id "${ACCOUNT_ID}" --data-source-id "${ATHENA_DS_ID}" \
      --region "${REGION}" >/dev/null 2>&1; then
  echo "  Athena data source '${ATHENA_DS_ID}' already exists (${_existing_status}) — reusing."
  ATHENA_DS_ARN="$(aws quicksight describe-data-source \
    --aws-account-id "${ACCOUNT_ID}" --data-source-id "${ATHENA_DS_ID}" \
    --region "${REGION}" --query "DataSource.Arn" --output text 2>/dev/null || true)"
else
  ATHENA_DS_ARN="$(aws quicksight create-data-source \
    --aws-account-id "${ACCOUNT_ID}" \
    --data-source-id "${ATHENA_DS_ID}" \
    --name "${ATHENA_DS_ID}" \
    --type ATHENA \
    --region "${REGION}" \
    --data-source-parameters "{\"AthenaParameters\":{\"WorkGroup\":\"${ATHENA_WORKGROUP}\"}}" \
    --query "Arn" --output text 2>/dev/null || true)"
  [[ -n "${ATHENA_DS_ARN}" && "${ATHENA_DS_ARN}" != "None" ]] \
    && echo "  ✔ Created Athena data source '${ATHENA_DS_ID}'." \
    || echo "  ⚠️  create-data-source (ATHENA) failed — check WorkGroup/permissions."
fi
# ARN may be null immediately after create — poll briefly.
if [[ -z "${ATHENA_DS_ARN}" || "${ATHENA_DS_ARN}" == "None" ]]; then
  for _ in $(seq 1 10); do
    ATHENA_DS_ARN="$(aws quicksight describe-data-source \
      --aws-account-id "${ACCOUNT_ID}" --data-source-id "${ATHENA_DS_ID}" \
      --region "${REGION}" --query "DataSource.Arn" --output text 2>/dev/null || true)"
    [[ -n "${ATHENA_DS_ARN}" && "${ATHENA_DS_ARN}" != "None" ]] && break
    sleep 3
  done
fi
echo "  Athena DS ARN: ${ATHENA_DS_ARN}"

# Hard gate: the Athena data source MUST reach CREATION_SUCCESSFUL, else every
# create-data-set below fails with a dependency error and Phase 3 produces 0 datasets.
_ds_status=""
for _ in $(seq 1 10); do
  _ds_status="$(aws quicksight describe-data-source \
    --aws-account-id "${ACCOUNT_ID}" --data-source-id "${ATHENA_DS_ID}" \
    --region "${REGION}" --query "DataSource.Status" --output text 2>/dev/null || true)"
  [[ "${_ds_status}" == "CREATION_SUCCESSFUL" || "${_ds_status}" == "UPDATE_SUCCESSFUL" ]] && break
  [[ "${_ds_status}" == *FAILED* ]] && break
  sleep 3
done
if [[ "${_ds_status}" != "CREATION_SUCCESSFUL" && "${_ds_status}" != "UPDATE_SUCCESSFUL" ]]; then
  _ds_err="$(aws quicksight describe-data-source \
    --aws-account-id "${ACCOUNT_ID}" --data-source-id "${ATHENA_DS_ID}" \
    --region "${REGION}" --query "DataSource.ErrorInfo" --output json 2>/dev/null || true)"
  echo "ERROR: Athena data source '${ATHENA_DS_ID}' status='${_ds_status}'." >&2
  echo "       ${_ds_err}" >&2
  echo "       Most common cause: QuickSight lacks access to Athena/Glue/S3." >&2
  echo "       Fix: QuickSight console → Manage QuickSight → Security & permissions →" >&2
  echo "            QuickSight access to AWS services → Manage → enable Amazon Athena" >&2
  echo "            (and confirm Amazon S3 includes the data bucket), then re-run this script." >&2
  exit 1
fi
echo ""

# ── Cleanup: remove old per-table S3 data sources if they linger ─
echo "━━━ Cleanup: remove legacy per-table S3 data sources ━━━"
for table in "${TABLES[@]}"; do
  [[ -z "$table" ]] && continue
  table_lc="$(printf '%s' "$table" | tr '[:upper:]' '[:lower:]')"
  old_src_id="sc-${table_lc}-src"
  if aws quicksight describe-data-source \
        --aws-account-id "${ACCOUNT_ID}" --data-source-id "${old_src_id}" \
        --region "${REGION}" >/dev/null 2>&1; then
    aws quicksight delete-data-source \
      --aws-account-id "${ACCOUNT_ID}" --data-source-id "${old_src_id}" \
      --region "${REGION}" >/dev/null 2>&1 \
      && echo "  removed legacy data source ${old_src_id}" \
      || echo "  ⚠️  could not remove legacy data source ${old_src_id}"
  fi
done
echo ""

# ── Step 4: One QuickSight dataset (SPICE) per Glue table ───────
echo "━━━ Step 4: QuickSight datasets (SPICE, via Athena/Glue) ━━━"
CREATED=()
DATASET_ARNS=()

for table in "${TABLES[@]}"; do
  [[ -z "$table" ]] && continue
  table_lc="$(printf '%s' "$table" | tr '[:upper:]' '[:lower:]')"
  # PhysicalTableMap key must match [0-9a-zA-Z-]* (no underscores) and be <=64 chars.
  table_key="$(printf '%s' "$table_lc" | tr -c 'a-z0-9-' '-' | sed 's/-\{1,\}/-/g; s/^-//; s/-$//')"
  table_key="${table_key:0:64}"
  ds_id="sc-${table_lc}"
  s3_uri="s3://${BUCKET}/${DATASET_PREFIX}/${table}/${table}.csv"

  if [[ -z "${ATHENA_DS_ARN}" || "${ATHENA_DS_ARN}" == "None" ]]; then
    echo "  ⚠️  no Athena data source ARN — skipping ${ds_id}."
    continue
  fi

  # InputColumns (all STRING) from the CSV header — downloaded from S3.
  header="$(aws s3 cp "${s3_uri}" - --region "${REGION}" 2>/dev/null | head -1 | tr -d '\r')"
  if [[ -z "${header}" ]]; then
    echo "  ⚠️  could not read header for ${table} — skipping."
    continue
  fi
  input_columns="$TMP_DIR/${table}_cols.json"
  python3 - "$header" > "$input_columns" <<'PY'
import json, sys
header = sys.argv[1]
cols = [c.strip() for c in header.split(",") if c.strip() != ""]
print(json.dumps([{"Name": c, "Type": "STRING"} for c in cols]))
PY

  cols_json="$(cat "$input_columns")"
  physical_map="$TMP_DIR/${table}_ptm.json"
  cat > "${physical_map}" <<JSON
{
  "${table_key}": {
    "RelationalTable": {
      "DataSourceArn": "${ATHENA_DS_ARN}",
      "Catalog": "AwsDataCatalog",
      "Schema": "${GLUE_DATABASE}",
      "Name": "${table_lc}",
      "InputColumns": ${cols_json}
    }
  }
}
JSON

  if aws quicksight describe-data-set \
        --aws-account-id "${ACCOUNT_ID}" --data-set-id "${ds_id}" \
        --region "${REGION}" >/dev/null 2>&1; then
    aws quicksight delete-data-set \
      --aws-account-id "${ACCOUNT_ID}" --data-set-id "${ds_id}" \
      --region "${REGION}" >/dev/null 2>&1 || true
    # delete is async — wait until it's actually gone before recreating.
    for _ in $(seq 1 15); do
      aws quicksight describe-data-set --aws-account-id "${ACCOUNT_ID}" \
        --data-set-id "${ds_id}" --region "${REGION}" >/dev/null 2>&1 || break
      sleep 2
    done
  fi

  # create-data-set can still briefly see the deleting dataset — retry on conflict.
  ds_arn=""
  for _ in $(seq 1 10); do
    ds_arn="$(aws quicksight create-data-set \
      --aws-account-id "${ACCOUNT_ID}" \
      --data-set-id "${ds_id}" \
      --name "${ds_id}" \
      --import-mode SPICE \
      --region "${REGION}" \
      --physical-table-map "file://${physical_map}" \
      --query "Arn" --output text 2>/dev/null || true)"
    [[ -n "${ds_arn}" && "${ds_arn}" != "None" ]] && break
    sleep 3
  done

  if [[ -n "${ds_arn}" && "${ds_arn}" != "None" ]]; then
    echo "  ✔ ${ds_id}  (Glue table ${GLUE_DATABASE}.${table_lc})"
    CREATED+=("${ds_id}")
    DATASET_ARNS+=("${ds_arn}")

    # Share the dataset (as owner) with the QuickSight principal.
    grant_owner update-data-set-permissions --data-set-id "${ds_id}" \
      "quicksight:DescribeDataSet,quicksight:DescribeDataSetPermissions,quicksight:PassDataSet,quicksight:DescribeIngestion,quicksight:ListIngestions,quicksight:UpdateDataSet,quicksight:DeleteDataSet,quicksight:CreateIngestion,quicksight:CancelIngestion,quicksight:UpdateDataSetPermissions"
  else
    echo "  ⚠️  FAILED to create ${ds_id} — skipping (dataset not created)."
  fi
done

# Share the SINGLE Athena data source (as owner) once with the principal.
if [[ -n "${ATHENA_DS_ARN}" && "${ATHENA_DS_ARN}" != "None" ]]; then
  grant_owner update-data-source-permissions --data-source-id "${ATHENA_DS_ID}" \
    "quicksight:DescribeDataSource,quicksight:DescribeDataSourcePermissions,quicksight:PassDataSource,quicksight:UpdateDataSource,quicksight:DeleteDataSource,quicksight:UpdateDataSourcePermissions"
fi

echo "  Total datasets: ${#CREATED[@]}"
echo ""

# ── Step 4c: QuickSight Topic (v2) with table relationships ─────
# Encodes the 19-table join graph (FK-style relationships derived from the
# schema's shared key columns) so cross-table questions resolve correctly.
echo "━━━ Step 4c: QuickSight Topic (relationships) ━━━"
TOPIC_ID="${TOPIC_ID:-sc-supply-chain-topic}"
TOPIC_NAME="${TOPIC_NAME:-Supply Chain}"
TOPIC_ARN=""
if qs_supports "create-topic-v2"; then
  # Relationship graph: "child_table:child_col:parent_table:parent_col"
  RELATIONSHIPS=(
    "orders:account_id:accounts:account_id"
    "opportunities:account_id:accounts:account_id"
    "quotes:account_id:accounts:account_id"
    "order_items:order_id:orders:order_id"
    "shipments:order_id:orders:order_id"
    "orders:tracking_number:shipments:tracking_number"
    "order_items:sku:products:sku"
    "inventory:sku:products:sku"
    "sales_history:sku:products:sku"
    "po_line_items:sku:products:sku"
    "quote_line_items:sku:products:sku"
    "products:warehouse:warehouses:warehouse_id"
    "inventory:warehouse:warehouses:warehouse_id"
    "sales_history:warehouse:warehouses:warehouse_id"
    "shipments:origin_warehouse:warehouses:warehouse_id"
    "supplier_contracts:supplier_id:suppliers:supplier_id"
    "purchase_orders:supplier_id:suppliers:supplier_id"
    "invoices:supplier_id:suppliers:supplier_id"
    "gl_allocation_map:vendor_id:suppliers:supplier_id"
    "po_line_items:po_number:purchase_orders:po_number"
    "invoices:po_number:purchase_orders:po_number"
    "quote_line_items:quote_id:quotes:quote_id"
    "shipment_events:tracking_number:shipments:tracking_number"
  )
  # Helper: is a lowercased table among the successfully-created datasets?
  _ds_created() { case " ${CREATED[*]} " in *" sc-$1 "*) return 0;; *) return 1;; esac; }
  # Deterministic dataset ARN for a lowercased table name.
  _ds_arn() { printf 'arn:aws:quicksight:%s:%s:dataset/sc-%s' "${REGION}" "${ACCOUNT_ID}" "$1"; }

  # Build the Topic JSON (DataSets[] + DataSetRelations[]) from the created datasets.
  topic_json="${TMP_DIR}/topic.json"
  {
    printf '{"Name":"%s","Description":"Supply chain tables + relationships.","DataSets":[' "${TOPIC_NAME}"
    first=1
    for ds_id in "${CREATED[@]}"; do
      table_lc="${ds_id#sc-}"
      [[ $first -eq 0 ]] && printf ','
      printf '{"DataSetArn":"%s","DataSetName":"%s"}' "$(_ds_arn "$table_lc")" "$table_lc"
      first=0
    done
    printf '],"DataSetRelations":['
    first=1
    for rel in "${RELATIONSHIPS[@]}"; do
      IFS=':' read -r ct cc pt pc <<< "$rel"
      # Only emit if both datasets were actually created.
      _ds_created "$ct" && _ds_created "$pt" || continue
      # Column names in the datasets are UPPERCASE (from CSV headers).
      cc_u="$(printf '%s' "$cc" | tr '[:lower:]' '[:upper:]')"
      pc_u="$(printf '%s' "$pc" | tr '[:lower:]' '[:upper:]')"
      [[ $first -eq 0 ]] && printf ','
      printf '{"Left":{"DataSetArn":"%s","ColumnNames":["%s"]},"Right":{"DataSetArn":"%s","ColumnNames":["%s"]}}' \
        "$(_ds_arn "$ct")" "$cc_u" "$(_ds_arn "$pt")" "$pc_u"
      first=0
    done
    printf ']}'
  } > "${topic_json}"

  # Create (delete-then-create for idempotency; v2 topics require the v2 ops).
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
    echo "  ✔ Created Topic '${TOPIC_ID}' with $(grep -o '"Left"' "${topic_json}" | wc -l | tr -d ' ') relationships."
    # Share the topic (as owner) with the principal.
    grant_owner update-topic-permissions-v2 --topic-id "${TOPIC_ID}" \
      "quicksight:DescribeTopic,quicksight:PassTopic,quicksight:DeleteTopic,quicksight:UpdateTopic,quicksight:DescribeTopicPermissions,quicksight:UpdateTopicPermissions"
  else
    echo "  ⚠️  create-topic-v2 failed — check inputs/permissions."
  fi
else
  echo "  ⚠️  This CLI has no create-topic-v2 — skipping Topic creation."
fi
echo ""

# ── Step 4b: S3 Knowledge Base over the documents ───────────────
echo "━━━ Step 4b: S3 Knowledge Base (documents) ━━━"
KB_ARN=""
if qs_supports "create-knowledge-base"; then
  kb_src_id="${KB_ID}-src"

  # KB uses a dedicated S3_KNOWLEDGE_BASE data source (BucketUrl, not a manifest).
  if aws quicksight describe-data-source \
        --aws-account-id "${ACCOUNT_ID}" --data-source-id "${kb_src_id}" \
        --region "${REGION}" >/dev/null 2>&1; then
    aws quicksight delete-data-source \
      --aws-account-id "${ACCOUNT_ID}" --data-source-id "${kb_src_id}" \
      --region "${REGION}" >/dev/null 2>&1 || true
    sleep 2
  fi
  kb_src_arn="$(aws quicksight create-data-source \
    --aws-account-id "${ACCOUNT_ID}" --data-source-id "${kb_src_id}" \
    --name "${kb_src_id}" --type S3_KNOWLEDGE_BASE --region "${REGION}" \
    --data-source-parameters "{\"S3KnowledgeBaseParameters\":{\"BucketUrl\":\"s3://${BUCKET}\"}}" \
    --query "Arn" --output text 2>/dev/null || true)"

  # KnowledgeBaseConfiguration: S3V2 template scoped to the knowledge-base/ prefix.
  kb_template="${TMP_DIR}/kb_template.json"
  cat > "${kb_template}" <<JSON
{
  "templateConfiguration": {
    "template": {
      "deletionProtectionConfiguration": { "enableDeletionProtection": "false", "deletionProtectionThreshold": "15" },
      "type": "S3V2",
      "filterConfiguration": { "inclusionPatterns": [], "maxFileSizeInMegaBytes": "10240", "inclusionPrefixes": ["${KB_PREFIX}/"], "exclusionPatterns": [], "exclusionPrefixes": [] },
      "connectionConfiguration": { "bucketName": "${BUCKET}", "bucketOwnerAccountId": "${ACCOUNT_ID}" }
    }
  }
}
JSON

  if aws quicksight describe-knowledge-base \
        --aws-account-id "${ACCOUNT_ID}" --knowledge-base-id "${KB_ID}" \
        --region "${REGION}" >/dev/null 2>&1; then
    echo "  KB '${KB_ID}' already exists — reusing."
  else
    aws quicksight create-knowledge-base \
      --aws-account-id "${ACCOUNT_ID}" \
      --knowledge-base-id "${KB_ID}" \
      --name "${KB_NAME}" \
      --data-source-arn "${kb_src_arn}" \
      --knowledge-base-configuration "file://${kb_template}" \
      --primary-owner-arn "${PRINCIPAL_ARN}" \
      --region "${REGION}" >/dev/null 2>&1 \
      && echo "  ✔ Created KB '${KB_ID}' (provisioning; owner: ${PRINCIPAL_LABEL})" \
      || echo "  ⚠️  create-knowledge-base failed — check inputs/permissions."
  fi

  # ARN is null while CREATING — poll briefly so we can attach it to the Space.
  for _ in $(seq 1 20); do
    KB_ARN="$(aws quicksight describe-knowledge-base \
      --aws-account-id "${ACCOUNT_ID}" --knowledge-base-id "${KB_ID}" \
      --region "${REGION}" --query "KnowledgeBase.KnowledgeBaseArn" --output text 2>/dev/null || true)"
    [[ -n "${KB_ARN}" && "${KB_ARN}" != "None" ]] && break
    sleep 3
  done
  [[ -n "${KB_ARN}" && "${KB_ARN}" != "None" ]] && echo "  KB ARN: ${KB_ARN}" \
    || echo "  (KB still provisioning — attach to Space may need a re-run)"
else
  echo "  ⚠️  This CLI has no create-knowledge-base — skipping KB creation."
fi

# KB ownership: the principal is set as OWNER at creation via --primary-owner-arn above.
# For an already-existing KB (created without that flag), QuickSight only permits the
# creating admin to hold OWNER, so this post-hoc grant falls back to VIEWER.
if [[ -n "${KB_ARN}" && "${KB_ARN}" != "None" ]]; then
  grant_owner update-knowledge-base-permissions --knowledge-base-id "${KB_ID}" \
    "quicksight:DescribeKnowledgeBase"
fi
echo ""

# ── Step 5: Attach datasets + KB to the Space ───────────────────
echo "━━━ Step 5: Attach resources to Space '${SPACE_ID}' ━━━"
if qs_supports "update-space-resources"; then
  # Build the --add-resources array from dataset ARNs + KB ARN.
  add_resources="${TMP_DIR}/add_resources.json"
  {
    printf '['
    first=1
    for arn in "${DATASET_ARNS[@]:-}"; do
      [[ -z "$arn" ]] && continue
      [[ $first -eq 0 ]] && printf ','
      printf '{"ResourceType":"DATA_SET","ResourceDetails":{"resourceArn":"%s"}}' "$arn"
      first=0
    done
    if [[ -n "${KB_ARN}" && "${KB_ARN}" != "None" ]]; then
      [[ $first -eq 0 ]] && printf ','
      printf '{"ResourceType":"KNOWLEDGE_BASE","ResourceDetails":{"resourceArn":"%s"}}' "${KB_ARN}"
      first=0
    fi
    if [[ -n "${TOPIC_ARN}" && "${TOPIC_ARN}" != "None" ]]; then
      [[ $first -eq 0 ]] && printf ','
      printf '{"ResourceType":"TOPIC","ResourceDetails":{"resourceArn":"%s"}}' "${TOPIC_ARN}"
      first=0
    fi
    printf ']'
  } > "${add_resources}"

  if [[ "$(cat "${add_resources}")" == "[]" ]]; then
    echo "  (no resource ARNs to attach — skipping)"
  else
    aws quicksight update-space-resources \
      --aws-account-id "${ACCOUNT_ID}" \
      --space-id "${SPACE_ID}" \
      --add-resources "file://${add_resources}" \
      --region "${REGION}" >/dev/null 2>&1 \
      && echo "  ✔ Attached ${#DATASET_ARNS[@]} dataset(s)$( [[ -n "${KB_ARN}" && "${KB_ARN}" != "None" ]] && echo ' + 1 KB' ) to '${SPACE_ID}'" \
      || echo "  ⚠️  update-space-resources failed — attach manually in the console."
  fi
else
  echo "  ⚠️  This CLI has no update-space-resources — attach resources manually."
fi

# Share the Space (as owner) with the QuickSight principal.
if qs_supports "update-space-permissions"; then
  # Full Space OWNER role. The 5 "space-level" actions alone are NOT a recognized
  # role, so the space would not appear in the user's console Spaces list. QuickSight
  # registers OWNER only with the document + space-folder actions included (this exact
  # set is what the console grants a space creator), which is what drives visibility.
  grant_owner update-space-permissions --space-id "${SPACE_ID}" \
    "quicksight:DescribeSpace,quicksight:DescribeSpacePermissions,quicksight:UpdateSpace,quicksight:DeleteSpace,quicksight:UpdateSpacePermissions,quicksight:CreateDocument,quicksight:DeleteDocument,quicksight:ListDocument,quicksight:GetDocument,quicksight:CreateSpaceFolder,quicksight:DeleteSpaceFolder,quicksight:UpdateSpaceFolder,quicksight:MoveSpaceFolderMember,quicksight:ListSpaceFolderMembers"
fi
echo ""

# ── Step 6: Create/update the Amazon Quick agent ────────────────
echo "━━━ Step 6: Amazon Quick agent ━━━"
# The agent's --spaces expects the Space ARN (not the id).
SPACE_ARN="$(aws quicksight describe-space \
  --aws-account-id "${ACCOUNT_ID}" --space-id "${SPACE_ID}" \
  --region "${REGION}" --query "spaceArn" --output text 2>/dev/null || true)"
if ! qs_supports "create-agent"; then
  echo "  ⚠️  This CLI has no 'quicksight create-agent' — skipping agent creation."
elif [[ ! -f "${INSTRUCTIONS_FILE}" ]]; then
  echo "  ⚠️  ${INSTRUCTIONS_FILE} not found — skipping agent creation."
else
  # Optional stylistic fields — only sent if set (avoids invalid enum values).
  AGENT_TONE="${QUICK_AGENT_TONE:-}"
  AGENT_OUTPUT_STYLE="${QUICK_AGENT_OUTPUT_STYLE:-}"
  AGENT_RESPONSE_LENGTH="${QUICK_AGENT_RESPONSE_LENGTH:-}"

  # Instructions = the file minus comment lines.
  INSTRUCTIONS="$(grep -v '^#' "${INSTRUCTIONS_FILE}")"

  # Build CustomPromptInput.NewPrompt JSON.
  NEW_PROMPT="$(python3 - "$INSTRUCTIONS" "$AGENT_TONE" "$AGENT_OUTPUT_STYLE" "$AGENT_RESPONSE_LENGTH" <<'PY'
import json, sys
instr, tone, style, length = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
np = {"Identity": "Supply Chain Operations Agent", "CustomInstructions": instr}
if tone:   np["Tone"] = tone
if style:  np["OutputStyle"] = style
if length: np["ResponseLength"] = length
print(json.dumps({"NewPrompt": np}))
PY
)"

  STARTER_PROMPTS='["Show me all open orders","Should we approve this invoice?","Any active supply chain disruptions?"]'
  WELCOME="Hi! I can help with orders, quotes, invoice approvals, supplier governance, and disruption response. What do you need?"

  # Optional: attach MCP connectors (created from the Agent Registry cards).
  AC_CREATE=(); AC_UPDATE=()
  if [[ -n "${ACTION_CONNECTORS}" ]]; then
    ac_json="$(printf '%s' "${ACTION_CONNECTORS}" | python3 -c 'import json,sys;print(json.dumps([x.strip() for x in sys.stdin.read().split(",") if x.strip()]))')"
    AC_CREATE=(--action-connectors "${ac_json}")
    AC_UPDATE=(--action-connectors-to-add "${ac_json}")
    echo "  Action connectors: ${ACTION_CONNECTORS}"
  fi

  # Create or update the agent (idempotent).
  if aws quicksight describe-agent \
        --aws-account-id "${ACCOUNT_ID}" --agent-id "${AGENT_ID}" \
        --region "${REGION}" >/dev/null 2>&1; then
    echo "  Agent '${AGENT_ID}' exists — updating."
    # Only attach the space if it isn't already attached (avoid duplicate entries on re-run).
    SPACE_ADD=()
    if ! aws quicksight describe-agent \
          --aws-account-id "${ACCOUNT_ID}" --agent-id "${AGENT_ID}" --region "${REGION}" \
          --query "Agent.Spaces" --output text 2>/dev/null | grep -q "${SPACE_ARN}"; then
      SPACE_ADD=(--spaces-to-add "[\"${SPACE_ARN}\"]")
    fi
    aws quicksight update-agent \
      --aws-account-id "${ACCOUNT_ID}" \
      --agent-id "${AGENT_ID}" \
      --name "${AGENT_NAME}" \
      --description "Supply chain operations: orders, quotes, invoices, governance, disruptions." \
      ${SPACE_ADD[@]+"${SPACE_ADD[@]}"} \
      --starter-prompts "${STARTER_PROMPTS}" \
      --welcome-message "${WELCOME}" \
      --custom-prompt-input "${NEW_PROMPT}" \
      ${AC_UPDATE[@]+"${AC_UPDATE[@]}"} \
      --region "${REGION}" >/dev/null \
      && echo "  ✔ Updated agent '${AGENT_ID}'." \
      || echo "  ⚠️  update-agent failed — check inputs/permissions."
  else
    aws quicksight create-agent \
      --aws-account-id "${ACCOUNT_ID}" \
      --agent-id "${AGENT_ID}" \
      --name "${AGENT_NAME}" \
      --description "Supply chain operations: orders, quotes, invoices, governance, disruptions." \
      --spaces "[\"${SPACE_ARN}\"]" \
      --starter-prompts "${STARTER_PROMPTS}" \
      --welcome-message "${WELCOME}" \
      --agent-lifecycle "PUBLISHED" \
      --custom-prompt-input "${NEW_PROMPT}" \
      ${AC_CREATE[@]+"${AC_CREATE[@]}"} \
      --region "${REGION}" >/dev/null \
      && echo "  ✔ Created agent '${AGENT_ID}' (lifecycle: PUBLISHED)." \
      || echo "  ⚠️  create-agent failed — check inputs/permissions."
  fi

  # Share the agent (as owner) with the principal.
  if qs_supports "update-agent-permissions"; then
    acts='["quicksight:DescribeAgent","quicksight:UpdateAgent","quicksight:DeleteAgent","quicksight:DescribeAgentPermissions","quicksight:UpdateAgentPermissions"]'
    aws quicksight update-agent-permissions \
      --aws-account-id "${ACCOUNT_ID}" --agent-id "${AGENT_ID}" --region "${REGION}" \
      --grant-permissions "[{\"Principal\":\"${PRINCIPAL_ARN}\",\"Actions\":${acts}}]" >/dev/null 2>&1 \
      && echo "  ✔ Shared agent with ${PRINCIPAL_LABEL}." \
      || echo "  ⚠️  could not share agent (check permission actions/ownership)."
  fi
fi
echo ""

# ── Summary ─────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ PHASE 3 COMPLETE"
echo "═══════════════════════════════════════════════════════════════"
echo "  Bucket:          s3://${BUCKET}"
echo "  Datasets (CSV):  s3://${BUCKET}/${DATASET_PREFIX}/<TABLE>/"
echo "  Glue database:   ${GLUE_DATABASE}  (Athena WorkGroup: ${ATHENA_WORKGROUP})"
echo "  Athena source:   ${ATHENA_DS_ID}"
echo "  Knowledge base:  s3://${BUCKET}/${KB_PREFIX}/"
echo "  Datasets:        ${#CREATED[@]}"
echo "  Knowledge base:  ${KB_ID}"
echo "  Space:           ${SPACE_ID}"
echo "  Agent:           ${AGENT_ID}  (${AGENT_NAME})"
echo "  Shared with:     ${PRINCIPAL_LABEL}"
echo ""
echo "  Open Amazon Quick → Agents → '${AGENT_NAME}' (attached to Space '${SPACE_ID}')."
echo "  Edit instructions in ${INSTRUCTIONS_FILE} and re-run to update."
echo ""
