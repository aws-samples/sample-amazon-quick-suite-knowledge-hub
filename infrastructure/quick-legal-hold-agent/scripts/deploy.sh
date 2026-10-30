#!/usr/bin/env bash
#
# deploy.sh - ONE-CLICK deployment for the standalone quick-legalhold-mcp stack.
#
# Runs the entire pipeline end-to-end:
#   0. Preflight   - verify node / npm / aws / python3 are on PATH.
#   1. Identity    - confirm AWS credentials resolve (sts get-caller-identity).
#   2. Install     - npm install (uses project-local ./.npm-cache via .npmrc).
#   3. Bootstrap   - ensure the CDK bootstrap stack (CDKToolkit) exists in the
#                    target account/region; run `cdk bootstrap` only if missing.
#   4. Diff        - print the additive change set (informational; never fails run).
#   5. Deploy      - cdk deploy --require-approval never.
#   6. Outputs     - write all CloudFormation outputs to outputs/deployment-outputs.json.
#
# Everything this stack needs is created BY the stack (KMS, S3 WORM, DynamoDB,
# Firehose + filter Lambda, Cognito pool/clients/test-user, AgentCore Gateway +
# interceptor). There are NO manual post-deploy steps for a working deployment.
#
# Usage:
#   ./scripts/deploy.sh
#   AWS_REGION=us-east-1 ./scripts/deploy.sh
#   QUICK_REDIRECT_URI=<uri> ./scripts/deploy.sh      # override Quick redirect URI
#
set -euo pipefail
cd "$(dirname "$0")/.."

STACK="${STACK:-quick-legalhold-mcp}"
REGION="${AWS_REGION:-us-east-1}"
QUICK_REDIRECT_URI="${QUICK_REDIRECT_URI:-}"
# OPTIONAL TEST-ONLY 3LO user. Setting TEST_USER_PASSWORD creates the synthetic
# Cognito user 'legal-admin-test' with that permanent password (passes
# -c createTestUser=true). Leave it unset for a real deployment (no test user).
# Value must satisfy the pool policy (>=12 chars, upper/lower/number/symbol):
#   TEST_USER_PASSWORD='YourStr0ng!Passw0rd' ./scripts/deploy.sh
TEST_USER_PASSWORD="${TEST_USER_PASSWORD:-}"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m    ✓ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m    ✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
log "Step 0/6: Preflight — required tools"
for tool in node npm aws python3; do
  command -v "$tool" >/dev/null 2>&1 || die "'$tool' not found on PATH"
  ok "$tool -> $(command -v "$tool")"
done

# ---------------------------------------------------------------------------
log "Step 1/6: AWS identity"
CALLER="$(aws sts get-caller-identity --output json 2>/dev/null)" \
  || die "AWS credentials not resolving. Configure creds (aws configure / SSO / env) and retry."
ACCOUNT="$(printf '%s' "$CALLER" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')"
ok "Account $ACCOUNT / region $REGION"

# Validate the optional test-user password up front so a weak value fails here
# instead of mid-deploy inside the Cognito custom resource.
if [[ -n "$TEST_USER_PASSWORD" ]]; then
  TEST_USER_PASSWORD="$TEST_USER_PASSWORD" python3 - <<'PY' \
    || die "TEST_USER_PASSWORD must be >=12 chars with upper, lower, number and symbol."
import os, re, sys
p = os.environ["TEST_USER_PASSWORD"]
ok = (len(p) >= 12 and re.search(r"[A-Z]", p) and re.search(r"[a-z]", p)
      and re.search(r"[0-9]", p) and re.search(r"[^A-Za-z0-9]", p))
sys.exit(0 if ok else 1)
PY
  ok "test user 'legal-admin-test' will be created (TEST_USER_PASSWORD set)"
else
  ok "no TEST_USER_PASSWORD set - skipping synthetic test user"
fi

# ---------------------------------------------------------------------------
log "Step 2/6: Install npm dependencies"
npm install
ok "dependencies installed"

# ---------------------------------------------------------------------------
log "Step 3/6: CDK bootstrap check ($ACCOUNT/$REGION)"
if aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" \
     >/dev/null 2>&1; then
  ok "CDKToolkit already bootstrapped"
else
  log "    bootstrapping environment aws://$ACCOUNT/$REGION"
  npx cdk bootstrap "aws://$ACCOUNT/$REGION"
  ok "bootstrap complete"
fi

# ---------------------------------------------------------------------------
# Assemble optional context flags.
CTX_ARGS=()
if [[ -n "$QUICK_REDIRECT_URI" ]]; then
  CTX_ARGS+=(-c "quickRedirectUri=$QUICK_REDIRECT_URI")
  ok "using quickRedirectUri=$QUICK_REDIRECT_URI"
fi
# Enable the test user only when a password was supplied (never committed /
# never defaulted). Without both flags the stack skips the user entirely.
if [[ -n "$TEST_USER_PASSWORD" ]]; then
  CTX_ARGS+=(-c "createTestUser=true" -c "testUserPassword=$TEST_USER_PASSWORD")
fi

# ---------------------------------------------------------------------------
log "Step 4/6: Diff (informational)"
npx cdk diff ${CTX_ARGS[@]+"${CTX_ARGS[@]}"} || true

# ---------------------------------------------------------------------------
log "Step 5/6: Deploy"
mkdir -p outputs
npx cdk deploy \
  --require-approval never \
  --outputs-file outputs/cfn-outputs-raw.json \
  ${CTX_ARGS[@]+"${CTX_ARGS[@]}"}
ok "stack deployed"

# ---------------------------------------------------------------------------
log "Step 6/6: Capture outputs -> outputs/deployment-outputs.json"
aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs" --output json \
  | python3 -c '
import json, sys
outs = json.load(sys.stdin) or []
flat = {o["OutputKey"]: o["OutputValue"] for o in outs}
with open("outputs/deployment-outputs.json", "w") as f:
    json.dump(flat, f, indent=2)
print(f"    wrote {len(flat)} outputs")
for k in ("GatewayUrl","GatewayId","ThreeLoTokenUrl","CognitoDiscoveryUrl","ThreeLoClientId"):
    if k in flat: print(f"      {k} = {flat[k]}")
'
ok "outputs captured"

log "Deployment complete for stack '$STACK' in $REGION."
echo "    Quick connector values:  ./scripts/quick-connector-info.sh"
echo "    Validate:                 register the MCP connector in Amazon Quick, then use the agent in chat"
echo "    Teardown:                 ./scripts/teardown.sh"
