#!/usr/bin/env bash
#
# Deploys the Amazon Quick chat agent (registered-user embedding) to Salesforce.
#
# Steps:
#   1. AWS:        Lambda + API Gateway (REST, API key + usage plan) via CloudFormation
#   2. Salesforce: Named Credential URL from the stack output, then the component metadata
#   3. API key:    YOU copy it from the API Gateway console (it is never output or printed);
#                  paste it at a hidden prompt, or enter it in Salesforce Setup yourself
#   4. Smoke test: calls the Apex controller in the org to mint one embed URL
#
# Usage: ./scripts/deploy.sh --sf-org <alias> [options]   (see --help)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/aws/.build"
NC_FILE="$ROOT/force-app/main/default/namedCredentials/Quick_Chat_Embed_API.namedCredential-meta.xml"
EXTERNAL_CREDENTIAL="Quick_Chat_Embed_API"
PRINCIPAL="ApiKeyPrincipal"
PERMSET="Quickchat_Agent_wAPI_User"
TEST_CLASS="QuickchatAgentwAPIControllerTest"

# Only this sample's components are deployed; nothing else in the org is touched.
SHARED_METADATA=(
  "CspTrustedSite:AmazonQuick"
  "CspTrustedSite:AmazonQuickSight"
)
API_METADATA=(
  "ExternalCredential:Quick_Chat_Embed_API"
  "NamedCredential:Quick_Chat_Embed_API"
  "ApexClass:QuickchatAgentwAPIController"
  "ApexClass:QuickchatAgentwAPIControllerTest"
  "StaticResource:QuickChatEmbeddingSDK"
  "LightningComponentBundle:quickchatAgentwAPI"
  "PermissionSet:Quickchat_Agent_wAPI_User"
)
# amazon-quicksight-embedding-sdk version downloaded into the static resource.
SDK_VERSION="2.12.1"
SDK_FILE="$ROOT/force-app/main/default/staticresources/QuickChatEmbeddingSDK.js"

STACK_NAME="quick-chat-embed"
REGION=""
SF_ORG=""
NAMESPACE="default"
IDENTITY_REGION=""
EMBED_REGION=""
ALLOWED_DOMAINS=""
SESSION_MINUTES="600"
SKIP_AWS=false
SKIP_SF=false
SKIP_KEY=false
SKIP_SMOKE=false

usage() {
  cat <<EOF
Usage: ./scripts/deploy.sh --sf-org <alias> [options]

Required:
  --sf-org <alias>            Salesforce org alias or username (see: sf org list)

AWS options:
  --aws-profile <name>        AWS CLI profile (default: current credentials)
  --region <region>           Region for the stack (default: profile region, else us-east-1)
  --stack-name <name>         CloudFormation stack name (default: $STACK_NAME)
  --namespace <ns>            Quick namespace (default: $NAMESPACE)
  --identity-region <region>  Quick identity region (default: stack region)
  --embed-region <region>     Quick region hosting the agent (default: stack region)
  --allowed-domains <list>    Comma-separated embed origins, max 3
                              (default: derived from the Salesforce org's My Domain)
  --session-minutes <n>       Quick session lifetime 15-600 (default: $SESSION_MINUTES)

Step control:
  --skip-aws                  Reuse the existing stack's outputs
  --skip-salesforce           Skip the Salesforce metadata deploy
  --skip-api-key              Don't prompt for the API key (enter it in Setup yourself)
  --skip-smoke-test           Don't call the Apex controller after deploying
  -h, --help                  Show this help
EOF
}

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sf-org)          SF_ORG="${2:?}"; shift 2 ;;
    --aws-profile)     export AWS_PROFILE="${2:?}"; shift 2 ;;
    --region)          REGION="${2:?}"; shift 2 ;;
    --stack-name)      STACK_NAME="${2:?}"; shift 2 ;;
    --namespace)       NAMESPACE="${2:?}"; shift 2 ;;
    --identity-region) IDENTITY_REGION="${2:?}"; shift 2 ;;
    --embed-region)    EMBED_REGION="${2:?}"; shift 2 ;;
    --allowed-domains) ALLOWED_DOMAINS="${2:?}"; shift 2 ;;
    --session-minutes) SESSION_MINUTES="${2:?}"; shift 2 ;;
    --skip-aws)        SKIP_AWS=true; shift ;;
    --skip-salesforce) SKIP_SF=true; shift ;;
    --skip-api-key)    SKIP_KEY=true; shift ;;
    --skip-smoke-test) SKIP_SMOKE=true; shift ;;
    -h|--help)         usage; exit 0 ;;
    *)                 usage; die "Unknown option: $1" ;;
  esac
done

[[ -n "$SF_ORG" ]] || { usage; die "--sf-org is required."; }

WITH_API=true

required=(sf jq aws python3 npm tar)
for cmd in "${required[@]}"; do
  command -v "$cmd" >/dev/null || die "'$cmd' is required but not installed."
done

API_VERSION="$(jq -r '.sourceApiVersion' "$ROOT/sfdx-project.json")"

# ------------------------------------------------------------------ preflight
log "Checking access"
ORG_JSON="$(sf org display --target-org "$SF_ORG" --json 2>/dev/null)" \
  || die "Salesforce org '$SF_ORG' not found. Run: sf org login web --alias $SF_ORG"
INSTANCE_URL="$(jq -r '.result.instanceUrl' <<<"$ORG_JSON")"
SF_USERNAME="$(jq -r '.result.username' <<<"$ORG_JSON")"
info "Salesforce org: $SF_USERNAME ($INSTANCE_URL)"

if [[ "$WITH_API" == true ]]; then
  REGION="${REGION:-$(aws configure get region 2>/dev/null || true)}"
  REGION="${REGION:-us-east-1}"
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" \
    || die "AWS credentials not available. Run 'aws configure' or pass --aws-profile."
  info "AWS account: $ACCOUNT_ID  region: $REGION  stack: $STACK_NAME"

  if [[ -z "$ALLOWED_DOMAINS" ]]; then
    host="${INSTANCE_URL#https://}"; host="${host%%/*}"
    if [[ "$host" == *.my.salesforce.com ]]; then
      ALLOWED_DOMAINS="https://${host%.my.salesforce.com}.lightning.force.com,https://$host"
    else
      warn "Could not derive the Lightning domain from $INSTANCE_URL; using Quick's configured domains."
    fi
  fi
  info "Allowed embed domains: ${ALLOWED_DOMAINS:-<Quick account settings>}"
fi

# ------------------------------------------------------------------ 1. AWS (api)
stack_output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

if [[ "$WITH_API" == true ]]; then
  if [[ "$SKIP_AWS" == false ]]; then
    log "Packaging Lambda"
    BUCKET="$(echo "${STACK_NAME}-artifacts-${ACCOUNT_ID}-${REGION}" | tr '[:upper:]' '[:lower:]')"
    if ! aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
      info "Creating private artifacts bucket s3://$BUCKET"
      if [[ "$REGION" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
      else
        aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
          --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
      fi
      aws s3api put-public-access-block --bucket "$BUCKET" --region "$REGION" \
        --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
    fi
    mkdir -p "$BUILD_DIR"
    aws cloudformation package --region "$REGION" \
      --template-file "$ROOT/aws/template.yaml" \
      --s3-bucket "$BUCKET" --s3-prefix "$STACK_NAME" \
      --output-template-file "$BUILD_DIR/packaged.yaml" >/dev/null

    log "Deploying CloudFormation stack $STACK_NAME"
    aws cloudformation deploy --region "$REGION" \
      --stack-name "$STACK_NAME" \
      --template-file "$BUILD_DIR/packaged.yaml" \
      --capabilities CAPABILITY_IAM \
      --no-fail-on-empty-changeset \
      --parameter-overrides \
        "QuickNamespace=$NAMESPACE" \
        "QuickIdentityRegion=$IDENTITY_REGION" \
        "QuickEmbedRegion=$EMBED_REGION" \
        "AllowedDomains=$ALLOWED_DOMAINS" \
        "SessionLifetimeMinutes=$SESSION_MINUTES"
  else
    log "Skipping AWS deploy; reading outputs from $STACK_NAME"
  fi

  API_BASE_URL="$(stack_output ApiBaseUrl)"
  API_KEY_NAME="$(stack_output ApiKeyName)"
  API_KEY_CONSOLE_URL="$(stack_output ApiKeyConsoleUrl)"
  [[ "$API_BASE_URL" == https://* ]] || die "Stack $STACK_NAME has no ApiBaseUrl output. Deploy without --skip-aws first."
  info "API base URL: $API_BASE_URL"
fi

# ------------------------------------------------------------------ 2. Salesforce
if [[ "$SKIP_SF" == false ]]; then
  SF_METADATA=("${SHARED_METADATA[@]}")
  if [[ "$WITH_API" == true ]]; then
    # Redeploying the External Credential resets its principal and wipes the
    # stored ApiKey value. Deploy it only when it doesn't exist in the org yet.
    if sf api request rest "/services/data/v$API_VERSION/named-credentials/external-credentials/$EXTERNAL_CREDENTIAL" \
         --target-org "$SF_ORG" 2>/dev/null | jq -e '.developerName? // empty' >/dev/null 2>&1; then
      info "External Credential $EXTERNAL_CREDENTIAL already exists; not redeploying it (keeps the stored API key)."
      for m in "${API_METADATA[@]}"; do
        [[ "$m" == "ExternalCredential:$EXTERNAL_CREDENTIAL" ]] || SF_METADATA+=("$m")
      done
    else
      SF_METADATA+=("${API_METADATA[@]}")
    fi
  fi

  if [[ ! -s "$SDK_FILE" ]]; then
    log "Downloading amazon-quicksight-embedding-sdk@$SDK_VERSION"
    sdk_tmp="$(mktemp -d)"
    (cd "$sdk_tmp" && npm pack "amazon-quicksight-embedding-sdk@$SDK_VERSION" --silent >/dev/null \
       && tar xzf ./*.tgz package/dist/quicksight-embedding-js-sdk.min.js) \
      || die "Could not download the embedding SDK from npm."
    cp "$sdk_tmp/package/dist/quicksight-embedding-js-sdk.min.js" "$SDK_FILE"
    rm -rf "$sdk_tmp"
    info "${SDK_FILE#"$ROOT"/}"
  fi

  if [[ "$WITH_API" == true ]]; then
    log "Setting Named Credential URL"
    python3 - "$NC_FILE" "$API_BASE_URL" <<'PY'
import re, sys
path, url = sys.argv[1], sys.argv[2]
xml = open(path, encoding="utf-8").read()
pattern = r"(<parameterName>Url</parameterName>\s*<parameterType>Url</parameterType>\s*<parameterValue>)[^<]*(</parameterValue>)"
new, n = re.subn(pattern, lambda m: m.group(1) + url + m.group(2), xml)
if n != 1:
    sys.exit("Could not find the Url parameter in " + path)
open(path, "w", encoding="utf-8").write(new)
PY
    info "${NC_FILE#"$ROOT"/} -> $API_BASE_URL"
  fi

  log "Deploying Salesforce metadata to $SF_USERNAME"
  md_args=()
  for m in "${SF_METADATA[@]}"; do md_args+=(--metadata "$m"); info "$m"; done
  test_args=()
  # Apex tests only apply when deploying Apex (api / both).
  [[ "$WITH_API" == true ]] && test_args=(--test-level RunSpecifiedTests --tests "$TEST_CLASS")
  info "Deploying (this can take a minute)..."
  # Use JSON output and print a compact summary. The CLI's own table squeezes
  # the long Path column to fit the terminal, which makes it unreadable.
  deploy_json="$(cd "$ROOT" && sf project deploy start --target-org "$SF_ORG" "${md_args[@]}" \
    ${test_args[@]+"${test_args[@]}"} --wait 30 --json 2>/dev/null || true)"
  deploy_json="${deploy_json:-{\}}"
  jq -r '"    Status: \(.result.status // .name // "Unknown")"' <<<"$deploy_json"
  # Component table (one row per component; source and -meta.xml files collapse).
  jq -r '
    [ (.result.files // [])[] ] | unique_by(.type + ":" + .fullName)
    | select(length > 0)
    | (["STATE", "TYPE", "NAME"] | @tsv), (.[] | [.state, .type, .fullName] | @tsv)
  ' <<<"$deploy_json" | column -t -s $'\t' | sed 's/^/    /'
  # Apex test results (only when tests ran) and any errors.
  jq -r '
    ( .result.details.runTestResult // empty | select((.numTestsRun // 0 | tonumber) > 0)
      | "    Apex tests: \(.numTestsRun) run, \(.numFailures // 0) failed",
        ( [.failures // empty] | flatten | .[] | "      FAIL \(.name).\(.methodName): \(.message)" ) ),
    ( [ .result.details.componentFailures // empty ] | flatten | .[]
      | "    ERROR \(.componentType):\(.fullName): \(.problem)" ),
    ( if (.status // 0) != 0 and (.message // "") != "" then "    ERROR \(.message)" else empty end )
  ' <<<"$deploy_json"
  [[ "$(jq -r '.result.status // empty' <<<"$deploy_json")" == "Succeeded" ]] \
    || die "Salesforce deploy failed (see errors above)."

  if [[ "$WITH_API" == true ]]; then
    log "Assigning permission set $PERMSET"
    if ! out="$(sf org assign permset --name "$PERMSET" --target-org "$SF_ORG" 2>&1)"; then
      if grep -qi "duplicate" <<<"$out"; then info "Already assigned."; else echo "$out"; die "Permission set assignment failed."; fi
    fi
  fi
fi

# ------------------------------------------------------------------ 3. API key (api, manual)
set_credential() {
  # Key arrives on stdin only, so it never appears in process args or output.
  local method="$1" body
  body="$(jq -Rn --arg ec "$EXTERNAL_CREDENTIAL" --arg p "$PRINCIPAL" '
    {externalCredential:$ec, principalName:$p, principalType:"NamedPrincipal",
     authenticationProtocol:"Custom",
     credentials:{ApiKey:{value:input, encrypted:true}}}')"
  printf '%s' "$body" | sf api request rest \
    "/services/data/v$API_VERSION/named-credentials/credential" \
    --method "$method" --header "Content-Type: application/json" \
    --body - --target-org "$SF_ORG" >/dev/null 2>&1
}

print_manual_key_steps() {
  cat <<EOF

    To enter the API key in Salesforce yourself:
      Setup > Named Credentials > External Credentials > Quick Chat Embed API
      > Principals > ApiKeyPrincipal > Edit > Authentication Parameters
        Name:  ApiKey   (exact spelling)
        Value: <the API key value, not the key ID>
EOF
}

if [[ "$WITH_API" == true ]]; then
  log "API key (manual step)"
  cat <<EOF
    The API key value is not in the stack outputs and this script never prints it.
    1. Open: $API_KEY_CONSOLE_URL
       (API Gateway > API keys > $API_KEY_NAME)
    2. Click "Show" and copy the value (~40 letters/digits, not the 10-character key ID).
EOF

  if [[ "$SKIP_KEY" == true || ! -t 0 ]]; then
    print_manual_key_steps
  else
    printf '\n    3. Paste the key here (input hidden), or press Enter to keep the current key: '
    IFS= read -r -s API_KEY_INPUT || API_KEY_INPUT=""
    printf '\n'
    if [[ -z "$API_KEY_INPUT" ]]; then
      info "Keeping the key already stored in Salesforce (if any)."
    elif [[ ! "$API_KEY_INPUT" =~ ^[A-Za-z0-9]{20,128}$ ]]; then
      unset API_KEY_INPUT
      warn "That doesn't look like an API Gateway key (20-128 letters/digits). Not stored."
      print_manual_key_steps
    else
      # Create the credential; if it already exists, update it.
      if printf '%s' "$API_KEY_INPUT" | set_credential POST || printf '%s' "$API_KEY_INPUT" | set_credential PUT; then
        info "Stored in External Credential $EXTERNAL_CREDENTIAL / $PRINCIPAL (encrypted)."
      else
        warn "Could not store the key through the API."
        print_manual_key_steps
      fi
      unset API_KEY_INPUT
    fi
  fi
fi

# ------------------------------------------------------------------ 4. Smoke test (api)
if [[ "$WITH_API" == true && "$SKIP_SMOKE" == false ]]; then
  log "Smoke test: minting one embed URL as $SF_USERNAME"
  apex_file="$(mktemp -t quickchat-smoke)"
  trap 'rm -f "$apex_file"' EXIT
  # Same callout as QuickchatAgentwAPIController.getEmbedUrl(). The controller
  # itself can't be called here: AuraHandledException is uncatchable in
  # anonymous Apex. Only the status code is printed, never the embed URL.
  cat >"$apex_file" <<'APEX'
HttpRequest req = new HttpRequest();
req.setEndpoint('callout:Quick_Chat_Embed_API/embed-url');
req.setMethod('POST');
req.setHeader('Content-Type', 'application/json');
req.setHeader('x-api-key', '{!$Credential.Quick_Chat_Embed_API.ApiKey}');
req.setBody(JSON.serialize(new Map<String, Object>{ 'userName' => UserInfo.getUserEmail() }));
try {
    System.debug('SMOKE_RESULT ' + new Http().send(req).getStatusCode());
} catch (Exception e) {
    System.debug('SMOKE_RESULT EXC ' + e.getMessage());
}
APEX
  # Only read USER_DEBUG lines; the echoed Apex source also contains "SMOKE_RESULT".
  result="$(sf apex run --file "$apex_file" --target-org "$SF_ORG" 2>&1 \
    | grep 'USER_DEBUG' | grep -o 'SMOKE_RESULT.*' | head -1 || true)"
  case "$result" in
    "SMOKE_RESULT 200") info "Embed URL minted successfully." ;;
    "SMOKE_RESULT 403") warn "API Gateway returned 403: the API key in Salesforce is missing or wrong. Re-run with --skip-aws --skip-salesforce and paste the key." ;;
    "SMOKE_RESULT 404") warn "No Amazon Quick user matches $SF_USERNAME's email. Register one (see README > Quick users)." ;;
    SMOKE_RESULT\ *)    warn "Embed call failed: ${result#SMOKE_RESULT }" ;;
    *)                  warn "Could not read the smoke test result. Check the component in the browser." ;;
  esac
fi

log "Done"
info "Add 'Quick Chat Agent w API' to a page in Lightning App Builder."
exit 0
