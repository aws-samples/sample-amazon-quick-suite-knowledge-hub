#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Quick Observability — one-command deploy.
#
# Runs the whole setup in order, prompting for the inputs it needs:
#   1. `cdk deploy`      — data lake, Firehose, pollers, Gateway, Cognito,
#                          MCP Lambda, QuickSight data source + 9 datasets.
#   2. `setup_quick.sh`  — share the datasets with your QuickSight user(s)/
#                          group(s), create the Q Topic, create the Quick
#                          Space, and attach + share it.
#
# After this finishes there is ONE remaining manual step that has no API:
# register the MCP connector in Amazon Quick (Integrations -> Actions) and
# create the agent — see the README "Quick Integration" section.
#
# Non-interactive use (CI): pass everything as flags and the prompts are
# skipped:
#   ./deploy.sh --region us-east-1 --account 111122223333 \
#               --quicksight-group observability-admins
#
# Interactive use: just run ./deploy.sh and answer the prompts.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"

REGION=""
ACCOUNT=""
PRINCIPAL_ARGS=()   # forwarded to setup_quick.sh (--quicksight-user/-group ...)
SKIP_SHARE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)            REGION="$2";                               shift 2 ;;
    --account)           ACCOUNT="$2";                              shift 2 ;;
    --quicksight-user)   PRINCIPAL_ARGS+=(--quicksight-user "$2");  shift 2 ;;
    --quicksight-group)  PRINCIPAL_ARGS+=(--quicksight-group "$2"); shift 2 ;;
    --skip-share)        SKIP_SHARE=1;                              shift   ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ── Collect inputs (prompt only for what wasn't passed) ────────────────────
if [[ -z "${REGION}" ]]; then
  read -r -p "AWS region [us-east-1]: " REGION
  REGION="${REGION:-us-east-1}"
fi

if [[ -z "${ACCOUNT}" ]]; then
  ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
fi
if [[ -z "${ACCOUNT}" || "${ACCOUNT}" == "None" ]]; then
  echo "ERROR: could not determine AWS account. Configure credentials or pass --account." >&2
  exit 1
fi

# QuickSight principals to share with (unless --skip-share). Prompt if none given.
if [[ "${SKIP_SHARE}" -eq 0 && "${#PRINCIPAL_ARGS[@]}" -eq 0 ]]; then
  echo ""
  echo "Who should see the datasets, Topic, and Space in Amazon Quick?"
  echo "Enter QuickSight usernames and/or group names (space-separated)."
  echo "Prefix a group with 'group:' — e.g.  alice bob group:observability-admins"
  read -r -p "Principals: " -a _principals
  for p in "${_principals[@]:-}"; do
    [[ -z "$p" ]] && continue
    if [[ "$p" == group:* ]]; then
      PRINCIPAL_ARGS+=(--quicksight-group "${p#group:}")
    else
      PRINCIPAL_ARGS+=(--quicksight-user "$p")
    fi
  done
  if [[ "${#PRINCIPAL_ARGS[@]}" -eq 0 ]]; then
    echo "No principals entered — skipping the sharing step."
    SKIP_SHARE=1
  fi
fi

echo ""
echo "============================================================"
echo " Account : ${ACCOUNT}"
echo " Region  : ${REGION}"
if [[ "${SKIP_SHARE}" -eq 1 ]]; then
  echo " Share   : (skipped)"
else
  echo " Share   : ${PRINCIPAL_ARGS[*]}"
fi
echo "============================================================"
echo ""

# ── cdk CLI: prefer a global 'cdk', else fall back to npx ──────────────────
if command -v cdk >/dev/null 2>&1; then
  CDK=(cdk)
else
  echo "No global 'cdk' found — using 'npx aws-cdk@2'."
  export npm_config_cache="${npm_config_cache:-$(mktemp -d)}"
  CDK=(npx --yes aws-cdk@2)
fi

# ── Step 1: cdk deploy ─────────────────────────────────────────────────────
echo "=== Step 1/2: cdk deploy ==="
AWS_REGION="${REGION}" "${CDK[@]}" deploy \
  --app "uv run python app.py" \
  -c account="${ACCOUNT}" -c region="${REGION}" \
  --require-approval never

# ── Step 2: share datasets + create Space/Topic ────────────────────────────
if [[ "${SKIP_SHARE}" -eq 1 ]]; then
  echo ""
  echo "=== Step 2/2: skipped (no principals) ==="
  echo "Run ./setup_quick.sh --quicksight-user <name> later to share."
else
  echo ""
  echo "=== Step 2/2: setup_quick.sh (share + Space/Topic) ==="
  AWS_REGION="${REGION}" bash setup_quick.sh --region "${REGION}" "${PRINCIPAL_ARGS[@]}"
fi

echo ""
echo "Deploy complete."
echo "Remaining manual step (no API): register the MCP connector in Amazon"
echo "Quick (Integrations -> Actions -> Model Context Protocol) using the"
echo "GatewayUrl / ClientId / ClientSecret / CognitoAuthorizeUrl / CognitoTokenUrl"
echo "stack outputs, then attach it to an agent. See the README."
