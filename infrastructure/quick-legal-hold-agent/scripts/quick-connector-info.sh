#!/usr/bin/env bash
#
# quick-connector-info.sh - print the exact values to register this legal-hold
# agent as an MCP connector in Amazon Quick (3LO / authorization_code only).
#
# It reads live CloudFormation outputs + retrieves the 3LO client secret from
# Cognito, so the ONLY manual step left in Quick is pasting these fields into the
# "Add MCP connector" form. Also writes outputs/quick-connector.json.
#
# Usage:
#   ./scripts/quick-connector-info.sh
#   AWS_REGION=us-east-1 STACK=quick-legalhold-mcp ./scripts/quick-connector-info.sh
#
set -uo pipefail
cd "$(dirname "$0")/.."

# NOTE: STACK is the deployed CloudFormation stack name (kept stable as
# 'quick-legalhold-mcp' to avoid recreating live resources). The project/repo
# folder is named quick-legal-hold-agent.
STACK="${STACK:-quick-legalhold-mcp}"
REGION="${AWS_REGION:-us-east-1}"

out() { aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null; }

GATEWAY_URL="$(out GatewayUrl)"
AUTHORIZE_URL="$(out ThreeLoAuthorizeUrl)"
TOKEN_URL="$(out ThreeLoTokenUrl)"
DISCOVERY_URL="$(out CognitoDiscoveryUrl)"
CLIENT_ID="$(out ThreeLoClientId)"
SCOPES="$(out ThreeLoScopes)"
REDIRECT_URI="$(out ThreeLoRedirectUri)"
POOL="$(out CognitoUserPoolId)"
CLIENT_SECRET="$(out ThreeLoClientSecret)"

if [[ -z "$GATEWAY_URL" || "$GATEWAY_URL" == "None" ]]; then
  echo "ERROR: could not read outputs for stack '$STACK' in $REGION. Is it deployed?" >&2
  echo "       Deploy first:  ./scripts/deploy.sh" >&2
  exit 1
fi

# Prefer the stack output; fall back to fetching the confidential 3LO client
# secret directly from Cognito if the output is unavailable.
if [[ -z "$CLIENT_SECRET" || "$CLIENT_SECRET" == "None" ]]; then
  CLIENT_SECRET="$(aws cognito-idp describe-user-pool-client \
    --user-pool-id "$POOL" --client-id "$CLIENT_ID" --region "$REGION" \
    --query UserPoolClient.ClientSecret --output text 2>/dev/null)"
fi
[[ -z "$CLIENT_SECRET" || "$CLIENT_SECRET" == "None" ]] && CLIENT_SECRET="<could not read — check IAM perms>"

# Write a machine-readable artifact too.
python3 - "$GATEWAY_URL" "$AUTHORIZE_URL" "$TOKEN_URL" "$DISCOVERY_URL" \
            "$CLIENT_ID" "$CLIENT_SECRET" "$SCOPES" "$REDIRECT_URI" <<'PY'
import json, sys
keys = ["mcpGatewayUrl","authorizeUrl","tokenUrl","oidcDiscoveryUrl",
        "clientId","clientSecret","scopes","redirectUri"]
doc = dict(zip(keys, sys.argv[1:]))
doc["authType"] = "OAuth 2.0 - authorization_code (3LO, per-user)"
import os
os.makedirs("outputs", exist_ok=True)
with open("outputs/quick-connector.json","w") as f:
    json.dump(doc, f, indent=2)
PY

cat <<INFO

╔══════════════════════════════════════════════════════════════════════╗
║   Amazon Quick — Add MCP connector  (OAuth 2.0 authorization_code)     ║
║   Legal-hold agent (3LO / per-user; no M2M client)                     ║
╚══════════════════════════════════════════════════════════════════════╝

Paste these into  Admin → Connectors → Add MCP connector:

  MCP Gateway URL : ${GATEWAY_URL}
  Auth method     : OAuth 2.0  (authorization code / 3LO)
  Authorize URL   : ${AUTHORIZE_URL}
  Token URL       : ${TOKEN_URL}
  OIDC discovery  : ${DISCOVERY_URL}
  Client ID       : ${CLIENT_ID}
  Client secret   : ${CLIENT_SECRET}
  Scopes          : ${SCOPES}
  Redirect URI    : ${REDIRECT_URI}

Saved machine-readable copy: outputs/quick-connector.json

After saving, Quick runs MCP initialize + tools/list and surfaces the tools:
  search_identities, list_group_members, place_hold, release_hold, list_holds
Scope the connector to your Legal-admin role so only Legal can invoke holds.

INFO
