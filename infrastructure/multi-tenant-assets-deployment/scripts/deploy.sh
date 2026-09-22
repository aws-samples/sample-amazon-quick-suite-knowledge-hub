#!/usr/bin/env bash
#
# deploy.sh — Build the MCP server artifact and deploy it via CloudFormation.
#
# Deploys the Quick Resource Migrator AgentCore Runtime with:
#   - Cognito user pool + resource server (custom scope) + M2M app client WITH secret
#   - VPC network mode (runtime ENIs in your private subnets)
#   - Cognito JWT inbound authorization (app client ID wired into AllowedClients)
#
# Steps:
#   1. Run build.sh  → build/deployment.zip (py3.14 / linux aarch64)
#   2. Upload deployment.zip to S3 (captures VersionId if bucket is versioned)
#   3. Deploy/update the CFN stack
#   4. Retrieve the Cognito client secret (CFN cannot output it) and print
#      ready-to-use token instructions
#
# Usage:
#   ./deploy.sh \
#     --artifact-bucket my-agentcore-artifacts \
#     --source-role-arn arn:aws:iam::<SOURCE_ACCOUNT_ID>:role/quick-space-migrator-role \
#     --target-role-arn arn:aws:iam::<TARGET_ACCOUNT_ID>:role/quick-space-migrator-role \
#     --cognito-domain-prefix quick-migrator-111122223333 \
#     --subnet-ids subnet-aaa,subnet-bbb \
#     --security-group-ids sg-aaa \
#     [--region us-east-1] \
#     [--stack-name quick-space-migrator] \
#     [--artifact-key quick-space-migrator/deployment.zip] \
#     [--resource-server-id quick-migrator] \
#     [--resource-server-scope invoke] \
#     [--show-secret]     # print the client secret to stdout (default: masked)
#
set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────
REGION="us-east-1"
STACK_NAME="quick-space-migrator"
ARTIFACT_KEY="quick-space-migrator/deployment.zip"
RESOURCE_SERVER_ID="quick-migrator"
RESOURCE_SERVER_SCOPE="invoke"
ARTIFACT_BUCKET=""
SOURCE_ROLE_ARN=""
TARGET_ROLE_ARN=""
EXECUTION_ROLE_ARN=""
RUNNER_ROLE_STACK=""
COGNITO_DOMAIN_PREFIX=""
SUBNET_IDS=""
SECURITY_GROUP_IDS=""
NETWORK_STACK=""
SHOW_SECRET="false"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${REPO_ROOT}/infrastructure/agentcore-runtime.yaml"

# ── Parse args ──────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --artifact-bucket)        ARTIFACT_BUCKET="$2"; shift 2 ;;
    --artifact-key)           ARTIFACT_KEY="$2"; shift 2 ;;
    --source-role-arn)        SOURCE_ROLE_ARN="$2"; shift 2 ;;
    --target-role-arn)        TARGET_ROLE_ARN="$2"; shift 2 ;;
    --execution-role-arn)     EXECUTION_ROLE_ARN="$2"; shift 2 ;;
    --runner-role-stack)      RUNNER_ROLE_STACK="$2"; shift 2 ;;
    --cognito-domain-prefix)  COGNITO_DOMAIN_PREFIX="$2"; shift 2 ;;
    --resource-server-id)     RESOURCE_SERVER_ID="$2"; shift 2 ;;
    --resource-server-scope)  RESOURCE_SERVER_SCOPE="$2"; shift 2 ;;
    --subnet-ids)             SUBNET_IDS="$2"; shift 2 ;;
    --security-group-ids)     SECURITY_GROUP_IDS="$2"; shift 2 ;;
    --network-stack)          NETWORK_STACK="$2"; shift 2 ;;
    --region)                 REGION="$2"; shift 2 ;;
    --stack-name)             STACK_NAME="$2"; shift 2 ;;
    --show-secret)            SHOW_SECRET="true"; shift 1 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Validate required ───────────────────────────────────────────────
: "${ARTIFACT_BUCKET:?--artifact-bucket is required}"
: "${SOURCE_ROLE_ARN:?--source-role-arn is required}"
: "${TARGET_ROLE_ARN:?--target-role-arn is required}"

# Resolve the execution role ARN from the runner-role stack if provided.
if [[ -n "${RUNNER_ROLE_STACK}" ]]; then
  echo "→ Resolving execution role from stack '${RUNNER_ROLE_STACK}'..."
  EXECUTION_ROLE_ARN="$(aws cloudformation describe-stacks --region "${REGION}" \
    --stack-name "${RUNNER_ROLE_STACK}" \
    --query "Stacks[0].Outputs[?OutputKey=='ExecutionRoleArn'].OutputValue" --output text)"
  echo "   Execution role: ${EXECUTION_ROLE_ARN}"
fi
: "${EXECUTION_ROLE_ARN:?--execution-role-arn (or --runner-role-stack) is required}"
: "${COGNITO_DOMAIN_PREFIX:?--cognito-domain-prefix is required}"

# If a network stack is given, resolve subnets + runtime SG from its outputs.
if [[ -n "${NETWORK_STACK}" ]]; then
  echo "→ Resolving networking from stack '${NETWORK_STACK}'..."
  net_out() {
    aws cloudformation describe-stacks --region "${REGION}" --stack-name "${NETWORK_STACK}" \
      --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
  }
  SUBNET_IDS="$(net_out PrivateSubnetIds)"
  SECURITY_GROUP_IDS="$(net_out RuntimeSecurityGroupId)"
  echo "   Subnets: ${SUBNET_IDS}"
  echo "   Runtime SG: ${SECURITY_GROUP_IDS}"
fi

: "${SUBNET_IDS:?--subnet-ids is required (VPC mode)}"
: "${SECURITY_GROUP_IDS:?--security-group-ids is required (VPC mode)}"

echo "════════════════════════════════════════════════════════"
echo "  Quick Resource Migrator — deploy (Cognito JWT + VPC)"
echo "  Region:      ${REGION}"
echo "  Stack:       ${STACK_NAME}"
echo "  Artifact:    s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}"
echo "  Cognito dom: ${COGNITO_DOMAIN_PREFIX}"
echo "  Subnets:     ${SUBNET_IDS}"
echo "  SGs:         ${SECURITY_GROUP_IDS}"
echo "════════════════════════════════════════════════════════"

# ── 1. Build the artifact ───────────────────────────────────────────
echo "→ [1/5] Building deployment.zip..."
bash "${SCRIPT_DIR}/build.sh"
ZIP_PATH="${REPO_ROOT}/build/deployment.zip"
[[ -f "${ZIP_PATH}" ]] || { echo "✗ build.sh did not produce ${ZIP_PATH}"; exit 1; }

# ── 2. Ensure the artifact bucket exists (versioning + encryption + no public access) ──
echo "→ [2/5] Ensuring artifact bucket s3://${ARTIFACT_BUCKET}..."
if aws s3api head-bucket --bucket "${ARTIFACT_BUCKET}" --region "${REGION}" 2>/dev/null; then
  echo "   ✓ Bucket already exists"
else
  echo "   Creating bucket..."
  if [[ "${REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${ARTIFACT_BUCKET}" --region "${REGION}"
  else
    aws s3api create-bucket --bucket "${ARTIFACT_BUCKET}" --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}"
  fi
  echo "   ✓ Bucket created"
fi

# Versioning (required for artifact VersionId pinning)
aws s3api put-bucket-versioning \
  --bucket "${ARTIFACT_BUCKET}" --region "${REGION}" \
  --versioning-configuration Status=Enabled
# Default encryption (SSE-S3)
aws s3api put-bucket-encryption \
  --bucket "${ARTIFACT_BUCKET}" --region "${REGION}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
# Block all public access
aws s3api put-public-access-block \
  --bucket "${ARTIFACT_BUCKET}" --region "${REGION}" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
echo "   ✓ Versioning + encryption + public-access-block configured"

# ── 3. Upload to S3 ─────────────────────────────────────────────────
echo "→ [3/5] Uploading to s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}..."
aws s3api put-object \
  --bucket "${ARTIFACT_BUCKET}" \
  --key "${ARTIFACT_KEY}" \
  --body "${ZIP_PATH}" \
  --region "${REGION}" >/tmp/put-object.json

VERSION_ID="$(python3 -c "import json; print(json.load(open('/tmp/put-object.json')).get('VersionId',''))" 2>/dev/null || echo "")"
[[ "${VERSION_ID}" == "null" ]] && VERSION_ID=""
if [[ -n "${VERSION_ID}" ]]; then
  echo "   ✓ Uploaded (VersionId: ${VERSION_ID})"
else
  echo "   ✓ Uploaded (bucket not versioned)"
fi

# ── 3. Deploy the CFN stack ─────────────────────────────────────────
echo "→ [4/5] Deploying CloudFormation stack '${STACK_NAME}'..."
# List<...> params take comma-delimited values as a single quoted override.
aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${STACK_NAME}" \
  --template-file "${TEMPLATE}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "RuntimeName=${STACK_NAME//-/_}" \
    "ArtifactBucket=${ARTIFACT_BUCKET}" \
    "ArtifactKey=${ARTIFACT_KEY}" \
    "ArtifactVersionId=${VERSION_ID}" \
    "SourceRoleArn=${SOURCE_ROLE_ARN}" \
    "TargetRoleArn=${TARGET_ROLE_ARN}" \
    "ExecutionRoleArn=${EXECUTION_ROLE_ARN}" \
    "CognitoDomainPrefix=${COGNITO_DOMAIN_PREFIX}" \
    "ResourceServerIdentifier=${RESOURCE_SERVER_ID}" \
    "ResourceServerScope=${RESOURCE_SERVER_SCOPE}" \
    "VpcSubnetIds=${SUBNET_IDS}" \
    "VpcSecurityGroupIds=${SECURITY_GROUP_IDS}"

# ── 4. Fetch outputs + client secret ────────────────────────────────
echo "→ [5/5] Retrieving stack outputs + Cognito client secret..."
get_out() {
  aws cloudformation describe-stacks --region "${REGION}" --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
USER_POOL_ID="$(get_out UserPoolId)"
APP_CLIENT_ID="$(get_out AppClientId)"
TOKEN_ENDPOINT="$(get_out TokenEndpoint)"
OAUTH_SCOPE="$(get_out OAuthScope)"
RUNTIME_ARN="$(get_out RuntimeArn)"

# CFN cannot output the client secret — retrieve it from the Cognito API.
CLIENT_SECRET="$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "${USER_POOL_ID}" \
  --client-id "${APP_CLIENT_ID}" \
  --region "${REGION}" \
  --query "UserPoolClient.ClientSecret" --output text)"

if [[ "${SHOW_SECRET}" == "true" ]]; then
  SECRET_DISPLAY="${CLIENT_SECRET}"
else
  SECRET_DISPLAY="****${CLIENT_SECRET: -4} (use --show-secret to reveal)"
fi

echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✓ Deploy complete"
echo "════════════════════════════════════════════════════════"
echo "  Runtime ARN     : ${RUNTIME_ARN}"
echo "  User Pool ID    : ${USER_POOL_ID}"
echo "  App Client ID   : ${APP_CLIENT_ID}"
echo "  Client Secret   : ${SECRET_DISPLAY}"
echo "  Token endpoint  : ${TOKEN_ENDPOINT}"
echo "  OAuth scope     : ${OAUTH_SCOPE}"
echo ""
echo "  Get a JWT (client_credentials) and invoke:"
echo "  ─────────────────────────────────────────"
cat <<EOF
  TOKEN=\$(curl -s -X POST "${TOKEN_ENDPOINT}" \\
    -H "Content-Type: application/x-www-form-urlencoded" \\
    -d "grant_type=client_credentials" \\
    -d "client_id=${APP_CLIENT_ID}" \\
    -d "client_secret=<CLIENT_SECRET>" \\
    -d "scope=${OAUTH_SCOPE}" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

  # Present \$TOKEN as the bearer JWT on InvokeAgentRuntime.
EOF
echo "════════════════════════════════════════════════════════"
