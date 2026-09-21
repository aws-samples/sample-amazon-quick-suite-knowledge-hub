#!/usr/bin/env bash
#
# deploy-roles.sh — Deploy the IAM roles in the correct order:
#   1. Runner execution role  (CENTRAL account)  — infrastructure/runner-role.yaml
#   2. Source quick-space-migrator-role          — infrastructure/quick-migrator-role.yaml
#   3. Target quick-space-migrator-role          — infrastructure/quick-migrator-role.yaml
#
# The runner role is created FIRST so its ARN can be trusted directly by the
# source/target role trust policies (no chicken-and-egg — the exec role exists
# before it is referenced). The source/target roles are in different accounts,
# so pass an AWS CLI profile for each. Account IDs are derived automatically
# from each profile via sts:GetCallerIdentity.
#
# Usage:
#   ./deploy-roles.sh \
#     --runner-profile central \
#     --source-profile source-acct \
#     --target-profile target-acct \
#     --artifact-bucket my-agentcore-artifacts \
#     [--region us-east-1] \
#     [--runtime-name quick_space_migrator] \
#     [--role-name quick-space-migrator-role] \
#     [--external-id SOME_ID]
#
set -euo pipefail

REGION="us-east-1"
RUNTIME_NAME="quick_space_migrator"
ROLE_NAME="quick-space-migrator-role"
EXTERNAL_ID=""
RUNNER_PROFILE=""
SOURCE_PROFILE=""
TARGET_PROFILE=""
ARTIFACT_BUCKET=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNNER_TEMPLATE="${REPO_ROOT}/infrastructure/runner-role.yaml"
QUICK_TEMPLATE="${REPO_ROOT}/infrastructure/quick-migrator-role.yaml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runner-profile)  RUNNER_PROFILE="$2"; shift 2 ;;
    --source-profile)  SOURCE_PROFILE="$2"; shift 2 ;;
    --target-profile)  TARGET_PROFILE="$2"; shift 2 ;;
    --artifact-bucket) ARTIFACT_BUCKET="$2"; shift 2 ;;
    --region)          REGION="$2"; shift 2 ;;
    --runtime-name)    RUNTIME_NAME="$2"; shift 2 ;;
    --role-name)       ROLE_NAME="$2"; shift 2 ;;
    --external-id)     EXTERNAL_ID="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

: "${ARTIFACT_BUCKET:?--artifact-bucket is required}"

prof() { [[ -n "$1" ]] && echo "--profile $1" || echo ""; }

# Resolve an account ID from a profile via STS (empty profile → default creds).
account_for() {
  local p="$1"
  aws sts get-caller-identity $(prof "$p") --query Account --output text
}

echo "→ Resolving account IDs from profiles..."
RUNNER_ACCOUNT="$(account_for "${RUNNER_PROFILE}")"
SOURCE_ACCOUNT="$(account_for "${SOURCE_PROFILE}")"
TARGET_ACCOUNT="$(account_for "${TARGET_PROFILE}")"

# Fail fast if any lookup came back empty (bad/expired profile).
for pair in "runner:${RUNNER_ACCOUNT}" "source:${SOURCE_ACCOUNT}" "target:${TARGET_ACCOUNT}"; do
  name="${pair%%:*}"; acct="${pair##*:}"
  if [[ -z "${acct}" || "${acct}" == "None" ]]; then
    echo "✗ Could not resolve account ID for the ${name} profile — check the profile/credentials."; exit 1
  fi
done

# Predictable ARNs (names are deterministic across the templates)
EXEC_ROLE_ARN="arn:aws:iam::${RUNNER_ACCOUNT}:role/${RUNTIME_NAME}-exec-role"
SOURCE_ROLE_ARN="arn:aws:iam::${SOURCE_ACCOUNT}:role/${ROLE_NAME}"
TARGET_ROLE_ARN="arn:aws:iam::${TARGET_ACCOUNT}:role/${ROLE_NAME}"

# CloudFormation stack names cannot contain underscores (pattern [a-zA-Z][-a-zA-Z0-9]*).
# RuntimeName keeps underscores (AgentCore requires them), but the stack name is hyphenated.
RUNNER_ROLE_STACK="${RUNTIME_NAME//_/-}-runner-role"

echo "════════════════════════════════════════════════════════"
echo "  Quick Resource Migrator — IAM roles (ordered deploy)"
echo "  Runner:  ${RUNNER_ACCOUNT}  exec role → ${EXEC_ROLE_ARN}"
echo "  Source:  ${SOURCE_ACCOUNT}  → ${SOURCE_ROLE_ARN}"
echo "  Target:  ${TARGET_ACCOUNT}  → ${TARGET_ROLE_ARN}"
echo "════════════════════════════════════════════════════════"

# ── 1. Runner execution role (CENTRAL account) — FIRST ──
echo "→ [1/3] Runner execution role (central ${RUNNER_ACCOUNT})..."
aws cloudformation deploy \
  $(prof "${RUNNER_PROFILE}") --region "${REGION}" \
  --stack-name "${RUNNER_ROLE_STACK}" \
  --template-file "${RUNNER_TEMPLATE}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "RuntimeName=${RUNTIME_NAME}" \
    "ArtifactBucket=${ARTIFACT_BUCKET}" \
    "SourceRoleArn=${SOURCE_ROLE_ARN}" \
    "TargetRoleArn=${TARGET_ROLE_ARN}"
echo "   ✓ Runner exec role ready: ${EXEC_ROLE_ARN}"

# ── 2. Source quick-space-migrator-role ──
echo "→ [2/3] Source role (${SOURCE_ACCOUNT})..."
aws cloudformation deploy \
  $(prof "${SOURCE_PROFILE}") --region "${REGION}" \
  --stack-name "${ROLE_NAME}-stack" \
  --template-file "${QUICK_TEMPLATE}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "RoleName=${ROLE_NAME}" \
    "RunnerExecRoleArn=${EXEC_ROLE_ARN}" \
    "ExternalId=${EXTERNAL_ID}"
echo "   ✓ Source role ready"

# ── 3. Target quick-space-migrator-role ──
echo "→ [3/3] Target role (${TARGET_ACCOUNT})..."
aws cloudformation deploy \
  $(prof "${TARGET_PROFILE}") --region "${REGION}" \
  --stack-name "${ROLE_NAME}-stack" \
  --template-file "${QUICK_TEMPLATE}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "RoleName=${ROLE_NAME}" \
    "RunnerExecRoleArn=${EXEC_ROLE_ARN}" \
    "ExternalId=${EXTERNAL_ID}"
echo "   ✓ Target role ready"

echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✓ All roles deployed"
echo "  Next: ./deploy-network.sh  then  ./deploy.sh"
echo "    ./deploy.sh needs:"
echo "      --source-role-arn ${SOURCE_ROLE_ARN}"
echo "      --target-role-arn ${TARGET_ROLE_ARN}"
echo "      --runner-role-stack ${RUNNER_ROLE_STACK}"
echo "════════════════════════════════════════════════════════"
