#!/bin/bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# PHASE 1 — deploy_agentcore.sh  (AgentCore infrastructure only)
#
# Builds & pushes the agent container, deploys the entire AgentCore stack
# (Cognito + 4 business MCP Lambdas + 4 Gateways + 4 Targets + Runtime) from a
# single template.yaml, then registers everything to the AWS Agent Registry.
#
# This is Phase 1 of a THREE-phase deployment:
#   Phase 1  ./deploy_agentcore.sh       — AgentCore infra (this script)
#   Phase 2  ./generate_and_upload.sh    — synthetic data + S3 upload
#   Phase 3  ./setup_quick.sh            — Quick datasets + KB + Space + agent
# Manual AWS-console steps sit BETWEEN each phase (see README / NEXT STEPS).
#
# Account/region-agnostic and idempotent — no hardcoded account IDs.
#
# Usage:
#   ./deploy_agentcore.sh [environment]              # deploy/update (default env: dev)
#   ./deploy_agentcore.sh [environment] --recreate   # DESTROY existing stack+registry, then deploy fresh
#
# --recreate is destructive (deletes the CloudFormation stack and the
# standalone Agent Registry) and is gated behind the explicit flag.
# ═══════════════════════════════════════════════════════════════

# ─── Args ───────────────────────────────────────────────────
ENVIRONMENT="dev"
RECREATE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --recreate)          RECREATE=true; shift ;;
    -* ) echo "Unknown flag: $1"; exit 1 ;;
    *  ) ENVIRONMENT="$1"; shift ;;
  esac
done

echo "═══════════════════════════════════════════════════════════════"
echo "  Supply Chain Agents — PHASE 1: AgentCore Deployment"
echo "═══════════════════════════════════════════════════════════════"

# ─── Derive account / region (no hardcoded IDs) ─────────────
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="sc-agents-${ENVIRONMENT}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

ECR_REPO="sc-order-fulfillment-agent"
IMAGE_TAG="${ENVIRONMENT}"
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
CONTAINER_URI="${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG}"

# Prefer the project venv Python (has boto3>=1.43.94 for agent-registry-control).
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

echo "  Region:      ${REGION}"
echo "  Account:     ${ACCOUNT_ID}"
echo "  Environment: ${ENVIRONMENT}"
echo "  Stack:       ${STACK_NAME}"
echo "  Container:   ${CONTAINER_URI}"
echo "  Mode:        $([ "${RECREATE}" = true ] && echo 'RECREATE (destroy + deploy)' || echo 'deploy/update')"
echo ""

# ─── Step 0 (optional): Tear down existing stack + registry ─
if [ "${RECREATE}" = true ]; then
  echo "━━━ Step 0: Teardown (--recreate) ━━━"

  # 0a. Delete the standalone Agent Registry (records + registry). Not part of CFN.
  echo "  • Deleting Agent Registry (records + registry) ..."
  "${PYTHON}" registry/deploy_registry.py --region "${REGION}" --delete || \
    echo "    (registry not present or already deleted)"

  # 0b. Delete the CloudFormation stack (Cognito, Lambdas, gateways, targets, runtime).
  if aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" >/dev/null 2>&1; then
    echo "  • Deleting CloudFormation stack ${STACK_NAME} ..."
    aws cloudformation delete-stack --stack-name "${STACK_NAME}" --region "${REGION}"
    aws cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}" --region "${REGION}" \
      && echo "    stack deleted"
  else
    echo "  • No existing stack ${STACK_NAME} — skipping."
  fi
  echo ""
fi

# ─── Step 1: Ensure ECR repo (idempotent) ──────────────────
echo "━━━ Step 1: ECR repository ━━━"
aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${ECR_REPO}" --region "${REGION}" >/dev/null
echo "  Repository ready: ${ECR_REPO}"

# ─── Step 2: Build & push agent container (arm64) ───────────
echo ""
echo "━━━ Step 2: Build & push agent container (linux/arm64) ━━━"
# Log in to public ECR (base image lives there) and to the account's private ECR.
aws ecr-public get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin public.ecr.aws >/dev/null 2>&1 || true
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"
docker build --platform linux/arm64 -t "${CONTAINER_URI}" src/agents/order-fulfillment/
docker push "${CONTAINER_URI}"
echo "  Pushed: ${CONTAINER_URI}"

# ─── Step 3: SAM build ──────────────────────────────────────
echo ""
echo "━━━ Step 3: SAM build ━━━"
sam build --template-file template.yaml

# ─── Step 4: SAM deploy (single unified stack) ─────────────
echo ""
echo "━━━ Step 4: SAM deploy ━━━"
sam deploy \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --no-fail-on-empty-changeset \
  --no-confirm-changeset \
  --parameter-overrides \
      Environment="${ENVIRONMENT}" \
      ContainerUri="${CONTAINER_URI}" \
      TestUserName="${TEST_USER_NAME:-supply-chain-test-user}" \
      TestUserPassword="${TEST_USER_PASSWORD:-AmazonQuick1@}"

# ─── Step 5: Show key outputs ───────────────────────────────
echo ""
echo "━━━ Step 5: Stack outputs ━━━"
aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" \
  --query "Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}" --output table

# ─── Step 5b: Set a PERMANENT password for the Cognito test user ─
# CloudFormation creates the user but can't set a permanent password; do it here.
echo ""
echo "━━━ Step 5b: Cognito test user password ━━━"
TEST_USER_NAME="${TEST_USER_NAME:-supply-chain-test-user}"
TEST_USER_PASSWORD="${TEST_USER_PASSWORD:-AmazonQuick1@}"
POOL_ID="$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue | [0]" --output text 2>/dev/null || true)"
if [ -n "${POOL_ID}" ] && [ "${POOL_ID}" != "None" ]; then
  aws cognito-idp admin-set-user-password \
    --user-pool-id "${POOL_ID}" \
    --username "${TEST_USER_NAME}" \
    --password "${TEST_USER_PASSWORD}" \
    --permanent \
    --region "${REGION}" >/dev/null 2>&1 \
    && echo "  ✔ Set permanent password for '${TEST_USER_NAME}' (pool ${POOL_ID})." \
    || echo "  ⚠️  Could not set password for '${TEST_USER_NAME}' — set it manually with admin-set-user-password."
else
  echo "  ⚠️  UserPoolId output not found — skipping test-user password."
fi

# ─── Step 6: Register to AWS Agent Registry ─────────────────
echo ""
echo "━━━ Step 6: Register to AWS Agent Registry ━━━"
"${PYTHON}" registry/deploy_registry.py --region "${REGION}"

# ─── Step 7: Print connector setup values (copy/paste for Quick) ─
echo ""
echo "━━━ Step 7: MCP connector 'Authenticate' values (copy into Quick) ━━━"
_get_out() { aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text 2>/dev/null; }
POOL_ID_OUT="$(_get_out UserPoolId)"
CLIENT_ID_OUT="$(_get_out CognitoClientId)"
TOKEN_URL_OUT="$(_get_out TokenEndpoint)"
AUTH_URL_OUT="$(_get_out AuthorizeEndpoint)"
CLIENT_SECRET_OUT="$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "${POOL_ID_OUT}" --client-id "${CLIENT_ID_OUT}" --region "${REGION}" \
  --query "UserPoolClient.ClientSecret" --output text 2>/dev/null || true)"
REDIRECT_URL_OUT="https://${REGION}.quicksight.aws.amazon.com/sn/oauthcallback"

# Agent endpoint: the AgentCore Runtime DIRECT invocation URL (ARN is URL-encoded
# into the path). Used to configure the sc-order-fulfillment-agent connector MANUALLY
# in Quick (it is registered as an AGENT record, so Quick does not auto-surface it).
RUNTIME_ARN_OUT="$(_get_out AgentRuntimeArn)"
AGENT_ENDPOINT_OUT="$(RA="${RUNTIME_ARN_OUT}" REG="${REGION}" python3 -c '
import os, urllib.parse
arn=os.environ["RA"]; reg=os.environ["REG"]
print(f"https://bedrock-agentcore.{reg}.amazonaws.com/runtimes/{urllib.parse.quote(arn, safe=\"\")}/invocations?qualifier=DEFAULT")' 2>/dev/null || true)"

echo ""
echo "  When creating each MCP connector in Quick, choose:"
echo "    Authentication method → User authentication"
echo "    Auth configuration    → OAUTH2_AUTHORIZATION_CODE"
echo "    Public OAuth client   → leave UNCHECKED (confidential client)"
echo "  then copy these values:"
echo ""

# Render a clean, auto-sized 2-column table (Field | Value). Passed as
# NUL-free "Field|Value" lines; awk computes column widths and draws borders.
print_kv_table() {
  awk -F '\t' '
    { k[NR]=$1; v[NR]=$2; if (length($1)>kw) kw=length($1); if (length($2)>vw) vw=length($2); n=NR }
    END {
      if (kw<5) kw=5; if (vw<5) vw=5;
      sep="  +"; for(i=0;i<kw+2;i++)sep=sep"-"; sep=sep"+"; for(i=0;i<vw+2;i++)sep=sep"-"; sep=sep"+";
      printf "%s\n", sep;
      printf "  | %-*s | %-*s |\n", kw, "Field", vw, "Value";
      printf "%s\n", sep;
      for(i=1;i<=n;i++) printf "  | %-*s | %-*s |\n", kw, k[i], vw, v[i];
      printf "%s\n", sep;
    }'
}

printf '%s\t%s\n' \
  "Client ID"         "${CLIENT_ID_OUT}" \
  "Client secret"     "${CLIENT_SECRET_OUT}" \
  "Token URL"         "${TOKEN_URL_OUT}" \
  "Authorization URL" "${AUTH_URL_OUT}" \
  "Redirect URL"      "${REDIRECT_URL_OUT}" \
  "Sign-in user"      "${TEST_USER_NAME:-supply-chain-test-user}" \
  "Sign-in password"  "${TEST_USER_PASSWORD:-AmazonQuick1@}" \
  | print_kv_table

echo ""
echo "  (Authorization-code client; the Redirect URL above is already registered as"
echo "   its Cognito callback — no manual callback setup needed.)"

echo ""
echo "  ── sc-order-fulfillment-agent (AGENT record) — configure as a connector MANUALLY ──"
echo "  Not auto-surfaced by Quick; add it as a Custom MCP connector (same Client ID/secret"
echo "  + user sign-in as the table above). Fields:"
echo ""
echo "    Connector name : sc-order-fulfillment-agent"
echo "    Agent endpoint (copy the full URL on the next line):"
echo ""
echo "${AGENT_ENDPOINT_OUT}"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ INFRASTRUCTURE DEPLOYED — stack ${STACK_NAME}"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "✅ AgentCore deployed. Next:"
echo "   1) ./generate_and_upload.sh   (generate synthetic data + upload to S3)"
echo "   2) [MANUAL in AWS console] add the S3 bucket to Amazon Quick AWS resources, AND enable/link the Agent Registry in Quick + create the MCP connectors (see README)"
echo "   3) ./setup_quick.sh --s3-bucket <bucket> (--quicksight-user <u> | --quicksight-group <g>)   (datasets + KB + Space + agent)"
echo ""
