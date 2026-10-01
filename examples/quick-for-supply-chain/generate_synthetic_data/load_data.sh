#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# Supply Chain — DATA pipeline orchestrator
# ═══════════════════════════════════════════════════════════════
# Single entry point for loading ALL demo data. Independent of deploy_agentcore.sh
# (which handles the application/infra stack). Each step is toggleable.
#
# Steps (default = all):
#   --generate         (a) python generate_synthetic_data/generators/generate_all_data.py --output ./synthetic_data/
#   --database         (b) python -m generate_synthetic_data.pipeline.db_loader  (create + load; honors DB_ENGINE)
#   --snowflake        (b) alias of --database (kept for back-compat; load is engine-aware)
#   --s3               (c) python -m generate_synthetic_data.pipeline.s3_uploader
#   --salesforce       (d) python -m generate_synthetic_data.pipeline.salesforce_prep
#
# Extra flags:
#   --dry-run     Pass through to each step (no external calls).
#   --use-copy    Use the bulk COPY path for the DB load (scalable path).
#   -h|--help     Show usage.
#
# Config comes from the environment. A local .env is auto-sourced if present.
# Account/region-agnostic AND database-agnostic: the structured-data loader
# honors DB_ENGINE (snowflake | postgres | sqlalchemy). Everything is read
# from env (DB_ENGINE, AWS_REGION, S3_BUCKET, SNOWFLAKE_*, PG*, DATABASE_URL,
# SQLALCHEMY_URL, DB_SECRET_ARN, SNOWFLAKE_SECRET_ARN, SF_*, ...).
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# ── Load .env if present (export all sourced vars) ──────────────
if [[ -f "$PROJECT_DIR/.env" ]]; then
  echo "  Loading environment from .env"
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

# ── Pick a Python interpreter ───────────────────────────────────
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON="python"
fi

# ── Flags ───────────────────────────────────────────────────────
RUN_GENERATE=false
RUN_DATABASE=false
RUN_S3=false
RUN_SALESFORCE=false
DRY_RUN=""
USE_COPY=""
ANY_STEP_SELECTED=false

usage() {
  sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --generate)    RUN_GENERATE=true;   ANY_STEP_SELECTED=true ;;
    --database)    RUN_DATABASE=true;   ANY_STEP_SELECTED=true ;;
    --snowflake)   RUN_DATABASE=true;   ANY_STEP_SELECTED=true ;;  # back-compat alias
    --s3)          RUN_S3=true;         ANY_STEP_SELECTED=true ;;
    --salesforce)  RUN_SALESFORCE=true; ANY_STEP_SELECTED=true ;;
    --dry-run)     DRY_RUN="--dry-run" ;;
    --use-copy)    USE_COPY="--use-copy" ;;
    -h|--help)     usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
  shift
done

# Default = run everything when no specific step was selected.
if [[ "$ANY_STEP_SELECTED" == false ]]; then
  RUN_GENERATE=true
  RUN_DATABASE=true
  RUN_S3=true
  RUN_SALESFORCE=true
fi

echo "═══════════════════════════════════════════════════════════════"
echo "  Supply Chain — DATA Pipeline"
echo "═══════════════════════════════════════════════════════════════"
echo "  Project:   $PROJECT_DIR"
echo "  Python:    $($PYTHON --version 2>&1)"
echo "  Steps:     generate=$RUN_GENERATE db=$RUN_DATABASE s3=$RUN_S3 salesforce=$RUN_SALESFORCE"
echo "  Dry-run:   ${DRY_RUN:-no}"
echo "  DB engine: ${DB_ENGINE:-snowflake} (load-mode=${USE_COPY:-write_pandas/sql/to_sql})"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Track outcomes for the final summary.
SUMMARY_GENERATE="skipped"
SUMMARY_DATABASE="skipped"
SUMMARY_S3="skipped"
SUMMARY_SALESFORCE="skipped"

# ── (a) Generate data ───────────────────────────────────────────
if [[ "$RUN_GENERATE" == true ]]; then
  echo "━━━ Step A: Generate data (single source of truth) ━━━"
  "$PYTHON" generate_synthetic_data/generators/generate_all_data.py --output ./synthetic_data/
  SUMMARY_GENERATE="done"
  echo ""
fi

# ── (b) Structured DB load (engine-aware via DB_ENGINE) ─────────
if [[ "$RUN_DATABASE" == true ]]; then
  echo "━━━ Step B: Database load — create tables + load (DB_ENGINE=${DB_ENGINE:-snowflake}) ━━━"
  "$PYTHON" -m generate_synthetic_data.pipeline.db_loader ${USE_COPY} ${DRY_RUN}
  SUMMARY_DATABASE="done"
  echo ""
fi

# ── (c) S3 unstructured docs ────────────────────────────────────
if [[ "$RUN_S3" == true ]]; then
  echo "━━━ Step C: Upload unstructured docs to S3 ━━━"
  "$PYTHON" -m generate_synthetic_data.pipeline.s3_uploader ${DRY_RUN}
  SUMMARY_S3="done"
  echo ""
fi

# ── (d) Salesforce prep ─────────────────────────────────────────
if [[ "$RUN_SALESFORCE" == true ]]; then
  echo "━━━ Step D: Salesforce prep + validate ━━━"
  "$PYTHON" -m generate_synthetic_data.pipeline.salesforce_prep ${DRY_RUN}
  SUMMARY_SALESFORCE="done"
  echo ""
fi

echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ DATA PIPELINE COMPLETE"
echo "═══════════════════════════════════════════════════════════════"
echo "  Generate:   $SUMMARY_GENERATE"
echo "  Database:   $SUMMARY_DATABASE (engine=${DB_ENGINE:-snowflake})"
echo "  S3:         $SUMMARY_S3"
echo "  Salesforce: $SUMMARY_SALESFORCE"
echo "═══════════════════════════════════════════════════════════════"
