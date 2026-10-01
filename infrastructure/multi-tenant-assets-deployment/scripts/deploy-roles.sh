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
# CI/CD (no local profiles): instead of --*-profile, pass a role to assume per
# account. The job's ambient credentials (OIDC/env/instance role) assume each:
#     [--runner-assume-role-arn arn:aws:iam::<runner>:role/deployer] \
#     [--source-assume-role-arn arn:aws:iam::<source>:role/deployer] \
#     [--target-assume-role-arn arn:aws:iam::<target>:role/deployer]
# Precedence per account: assume-role-arn > --*-profile > ambient/default creds.
# (If both a profile and an assume-arn are given for an account, the profile is
#  used as the BASE identity that performs the assume-role.)
#
set -euo pipefail

REGION="us-east-1"
RUNTIME_NAME="quick_space_migrator"
ROLE_NAME="quick-space-migrator-role"
EXTERNAL_ID=""
RUNNER_PROFILE=""
SOURCE_PROFILE=""
TARGET_PROFILE=""
RUNNER_ASSUME_ROLE_ARN=""
SOURCE_ASSUME_ROLE_ARN=""
TARGET_ASSUME_ROLE_ARN=""
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
    --runner-assume-role-arn)  RUNNER_ASSUME_ROLE_ARN="$2"; shift 2 ;;
    --source-assume-role-arn)  SOURCE_ASSUME_ROLE_ARN="$2"; shift 2 ;;
    --target-assume-role-arn)  TARGET_ASSUME_ROLE_ARN="$2"; shift 2 ;;
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

# Per-account auth resolver for CI/CD.
#
# Prints shell `export` lines that set temporary credentials for an account,
# so a phase can run inside a subshell with the right identity:
#     ( eval "$(auth_env "$ASSUME_ARN" "$PROFILE" "$SESSION")"; aws ... )
#
# Precedence (per the agreed design):
#   1. assume-role-arn set  → sts:assume-role (base creds = this account's
#      --profile if given, else ambient/default) and export the temp creds.
#   2. else                 → print nothing; callers fall back to `prof()`
#                             (a --profile flag) or ambient/default creds.
auth_env() {
  local assume_arn="$1" base_profile="$2" session="$3"
  [[ -z "${assume_arn}" ]] && return 0
  local creds
  creds="$(aws sts assume-role \
    $(prof "${base_profile}") --region "${REGION}" \
    --role-arn "${assume_arn}" \
    --role-session-name "${session}" \
    --query "Credentials.[AccessKeyId,SecretAccessKey,SessionToken]" \
    --output text)"
  if [[ -z "${creds}" || "${creds}" == *None* ]]; then
    echo "echo '✗ Failed to assume ${assume_arn}' >&2; exit 1"
    return 0
  fi
  local ak sk st
  ak="$(echo "${creds}" | awk '{print $1}')"
  sk="$(echo "${creds}" | awk '{print $2}')"
  st="$(echo "${creds}" | awk '{print $3}')"
  # Clear any inherited profile so the exported keys take effect cleanly.
  printf 'unset AWS_PROFILE; export AWS_ACCESS_KEY_ID=%q AWS_SECRET_ACCESS_KEY=%q AWS_SESSION_TOKEN=%q\n' \
    "${ak}" "${sk}" "${st}"
}

# Auth flags for a single inline call (used only when NOT assuming a role):
# returns `--profile X` or empty. When an assume-arn is set for the account,
# the call must instead run inside a subshell seeded by auth_env (see phases).
auth_flags() {
  local assume_arn="$1" base_profile="$2"
  [[ -n "${assume_arn}" ]] && { echo ""; return 0; }
  prof "${base_profile}"
}

# Resolve an account ID from a profile/assumed-role via STS.
account_for() {
  local assume_arn="$1" base_profile="$2" session="$3"
  if [[ -n "${assume_arn}" ]]; then
    ( eval "$(auth_env "${assume_arn}" "${base_profile}" "${session}")"
      aws sts get-caller-identity --region "${REGION}" --query Account --output text )
  else
    aws sts get-caller-identity $(prof "${base_profile}") --query Account --output text
  fi
}

echo "→ Resolving account IDs..."
RUNNER_ACCOUNT="$(account_for "${RUNNER_ASSUME_ROLE_ARN}" "${RUNNER_PROFILE}" "deploy-roles-runner")"
SOURCE_ACCOUNT="$(account_for "${SOURCE_ASSUME_ROLE_ARN}" "${SOURCE_PROFILE}" "deploy-roles-source")"
TARGET_ACCOUNT="$(account_for "${TARGET_ASSUME_ROLE_ARN}" "${TARGET_PROFILE}" "deploy-roles-target")"

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
(
  eval "$(auth_env "${RUNNER_ASSUME_ROLE_ARN}" "${RUNNER_PROFILE}" "deploy-roles-runner")"
  aws cloudformation deploy \
    $(auth_flags "${RUNNER_ASSUME_ROLE_ARN}" "${RUNNER_PROFILE}") --region "${REGION}" \
    --stack-name "${RUNNER_ROLE_STACK}" \
    --template-file "${RUNNER_TEMPLATE}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
      "RuntimeName=${RUNTIME_NAME}" \
      "ArtifactBucket=${ARTIFACT_BUCKET}" \
      "SourceRoleArn=${SOURCE_ROLE_ARN}" \
      "TargetRoleArn=${TARGET_ROLE_ARN}"
)
echo "   ✓ Runner exec role ready: ${EXEC_ROLE_ARN}"

# ── 2. Source quick-space-migrator-role ──
echo "→ [2/3] Source role (${SOURCE_ACCOUNT})..."
(
  eval "$(auth_env "${SOURCE_ASSUME_ROLE_ARN}" "${SOURCE_PROFILE}" "deploy-roles-source")"
  aws cloudformation deploy \
    $(auth_flags "${SOURCE_ASSUME_ROLE_ARN}" "${SOURCE_PROFILE}") --region "${REGION}" \
    --stack-name "${ROLE_NAME}-stack" \
    --template-file "${QUICK_TEMPLATE}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
      "RoleName=${ROLE_NAME}" \
      "RunnerExecRoleArn=${EXEC_ROLE_ARN}" \
      "ExternalId=${EXTERNAL_ID}"
)
echo "   ✓ Source role ready"

# ── 3. Target quick-space-migrator-role ──
echo "→ [3/3] Target role (${TARGET_ACCOUNT})..."
(
  eval "$(auth_env "${TARGET_ASSUME_ROLE_ARN}" "${TARGET_PROFILE}" "deploy-roles-target")"
  aws cloudformation deploy \
    $(auth_flags "${TARGET_ASSUME_ROLE_ARN}" "${TARGET_PROFILE}") --region "${REGION}" \
    --stack-name "${ROLE_NAME}-stack" \
    --template-file "${QUICK_TEMPLATE}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
      "RoleName=${ROLE_NAME}" \
      "RunnerExecRoleArn=${EXEC_ROLE_ARN}" \
      "ExternalId=${EXTERNAL_ID}"
)
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
