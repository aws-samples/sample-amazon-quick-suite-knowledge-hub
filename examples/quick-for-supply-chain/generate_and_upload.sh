#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# PHASE 2 — generate_and_upload.sh  (synthetic data + S3 upload ONLY)
#
# Generates the synthetic supply-chain data and uploads it to S3. NO QuickSight
# calls at all — datasets/KB/Space/agent are created later in Phase 3.
#
# This is Phase 2 of a THREE-phase deployment:
#   Phase 1  ./deploy_agentcore.sh       — AgentCore infra
#   Phase 2  ./generate_and_upload.sh    — synthetic data + S3 upload (this script)
#   Phase 3  ./setup_quick.sh            — Quick datasets + KB + Space + agent
#
# Uploads (one prefix per table so Glue/Athena can catalog each as a table):
#   synthetic_data/structured/tables/<TABLE>.csv → s3://$BUCKET/structured/<TABLE>/<TABLE>.csv
#   synthetic_data/documents/*                    → s3://$BUCKET/knowledge-base/
#
# Account/region-agnostic and idempotent — no hardcoded account IDs.
#
# Usage:
#   ./generate_and_upload.sh
#   ./generate_and_upload.sh --bucket my-bucket
#   AWS_REGION=us-west-2 ./generate_and_upload.sh
# ═══════════════════════════════════════════════════════════════

# ─── Args ───────────────────────────────────────────────────
BUCKET_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket)   BUCKET_OVERRIDE="$2"; shift 2 ;;
    -h|--help)  grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -* ) echo "Unknown flag: $1" >&2; exit 1 ;;
    *  ) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ─── Locate repo root (this script lives at repo root) ──────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STRUCTURED_DIR="synthetic_data/structured/tables"
DOCUMENTS_DIR="synthetic_data/documents"

# S3 key prefixes (purpose-named): datasets vs knowledge-base docs.
DATASET_PREFIX="structured"
KB_PREFIX="knowledge-base"

# ─── Derive account / region / bucket (no hardcoded IDs) ────
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET_OVERRIDE:-${S3_BUCKET:-sc-supply-chain-data-${ACCOUNT_ID}-${REGION}}}"

# Prefer the project venv Python (like deploy_agentcore.sh).
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

echo "═══════════════════════════════════════════════════════════════"
echo "  Supply Chain Agents — PHASE 2: Generate data + upload to S3"
echo "═══════════════════════════════════════════════════════════════"
echo "  Account: ${ACCOUNT_ID}   Region: ${REGION}"
echo "  Bucket:  s3://${BUCKET}"
echo ""

# ─── Step 1: Generate synthetic data ────────────────────────
echo "━━━ Step 1: Generate synthetic data ━━━"
"${PYTHON}" generate_synthetic_data/generators/generate_all_data.py --output ./synthetic_data/
echo ""

# ─── Preflight: generated data present? ─────────────────────
if [[ ! -d "$STRUCTURED_DIR" ]]; then
  echo "ERROR: ${STRUCTURED_DIR} not found after generation." >&2
  exit 1
fi

# ─── Step 2: Create the bucket if missing (region-aware) ────
echo "━━━ Step 2: S3 bucket ━━━"
if aws s3api head-bucket --bucket "${BUCKET}" >/dev/null 2>&1; then
  echo "  Bucket exists: s3://${BUCKET}"
else
  echo "  Creating bucket s3://${BUCKET} in ${REGION}"
  if [[ "${REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}" >/dev/null
  else
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
  echo "  Created bucket s3://${BUCKET}"
fi

aws s3api put-bucket-encryption \
  --bucket "${BUCKET}" \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
echo "  Default encryption: AES256"
echo ""

# ─── Step 3: Upload structured CSVs (per-table prefix) + documents ───
echo "━━━ Step 3: Upload data to S3 ━━━"
# Each CSV goes to its OWN prefix so Glue/Athena can catalog it as a table:
#   structured/<TABLE>/<TABLE>.csv   (e.g. structured/ACCOUNTS/ACCOUNTS.csv)
_csv_count=0
for _csv in "${STRUCTURED_DIR}"/*.csv; do
  [[ -e "$_csv" ]] || continue
  _table="$(basename "${_csv}" .csv)"
  aws s3 cp "${_csv}" "s3://${BUCKET}/${DATASET_PREFIX}/${_table}/${_table}.csv" --sse AES256 >/dev/null
  echo "  Uploaded ${_table}.csv → s3://${BUCKET}/${DATASET_PREFIX}/${_table}/${_table}.csv"
  _csv_count=$((_csv_count + 1))
done
if [[ "${_csv_count}" -eq 0 ]]; then
  echo "ERROR: no CSVs found under ${STRUCTURED_DIR}/ to upload." >&2
  exit 1
fi
echo "  Uploaded ${_csv_count} structured CSV(s) → s3://${BUCKET}/${DATASET_PREFIX}/<TABLE>/"

if [[ -d "$DOCUMENTS_DIR" ]] && [[ -n "$(ls -A "$DOCUMENTS_DIR" 2>/dev/null)" ]]; then
  aws s3 cp "${DOCUMENTS_DIR}/" "s3://${BUCKET}/${KB_PREFIX}/" \
    --recursive --sse AES256
  echo "  Uploaded documents → s3://${BUCKET}/${KB_PREFIX}/"
else
  echo "  (no documents found under ${DOCUMENTS_DIR}/ — skipping documents upload)"
fi
echo ""

# ─── Final: manual next steps ───────────────────────────────
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ PHASE 2 COMPLETE — data generated + uploaded"
echo "═══════════════════════════════════════════════════════════════"
echo "S3 bucket ready: s3://${BUCKET}"
echo "  Structured CSVs are laid out one prefix per table:"
echo "     s3://${BUCKET}/${DATASET_PREFIX}/<TABLE>/<TABLE>.csv"
echo "  (Phase 3 catalogs these as Glue tables behind a single Athena data source.)"
echo "  MANUAL (AWS console) before Phase 3:"
echo "   1) Amazon QuickSight → Manage QuickSight → Security & permissions → QuickSight access to AWS services → Amazon S3 → Manage → check ${BUCKET} → Save."
echo "   2) In Amazon Quick: link the AWS Agent Registry (Manage account → Permissions → AWS Agent Registry) and create the MCP connectors from the cards."
echo "  Then run:"
echo "   ./setup_quick.sh --s3-bucket ${BUCKET} --quicksight-user <username>   (or --quicksight-group <group>)"
echo ""
