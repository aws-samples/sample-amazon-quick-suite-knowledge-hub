"""
AgentCore Gateway REQUEST interceptor.

Runs before the gateway invokes the tool Lambda target. With passRequestHeaders=true,
the inbound request headers (including the validated Authorization: Bearer <JWT>) are
available in the event. We decode the JWT payload (claims) and inject the caller's
identity into the JSON-RPC tool arguments as "_caller", so the tool Lambda can record
the human custodian for place_hold / release_hold.

For 2LO / M2M tokens (no human identity: no email/username), we inject nothing and the
tool Lambda falls back to the request-supplied custodian or "system".

Input  (MCP request interceptor):
  event["mcp"]["gatewayRequest"]["body"]  -> JSON-RPC {method, params:{name, arguments}}
  headers passed somewhere in the event (exact location logged for diagnosis).
Output:
  {"interceptorOutputVersion":"1.0","mcp":{"transformedGatewayRequest":{"body": <body>}}}
"""

import base64
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_PASSTHROUGH = {"interceptorOutputVersion": "1.0", "mcp": {}}


def _b64url_json(segment: str):
    try:
        pad = "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment + pad).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _find_bearer(obj) -> str | None:
    """Recursively search the event for an 'authorization' header value."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if (
                isinstance(k, str)
                and k.lower() == "authorization"
                and isinstance(v, str)
            ):
                return v
            found = _find_bearer(v)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _find_bearer(it)
            if found:
                return found
    return None


def _caller_from_claims(claims: dict):
    if not claims:
        return None
    # Distinguish human (3LO) from machine (2LO/M2M) tokens.
    # A Cognito client_credentials (M2M) access token has token_use=access,
    # sub == client_id, and NO username/email. Treat that as non-human -> no _caller.
    username = claims.get("cognito:username") or claims.get("username")
    email = claims.get("email")
    sub = claims.get("sub")
    client_id = claims.get("client_id")
    is_machine = not username and not email and sub and client_id and sub == client_id
    if is_machine:
        return None
    identity = email or username or sub
    if not identity:
        return None
    source = "jwt:email" if email else ("jwt:username" if username else "jwt:sub")
    return {
        "identity": str(identity),
        "source": source,
        "email": email,
        "sub": sub,
        "username": username,
    }


def lambda_handler(event, context):
    # First-run diagnostic: log the event shape (redacting the raw token).
    try:
        red = json.dumps(event)[:1200]
        logger.info(f"[interceptor] event(redacted-preview)={red}")
    except Exception:  # noqa: BLE001, S110 - redacted preview logging is best-effort
        pass

    mcp = event.get("mcp", {}) if isinstance(event, dict) else {}
    gw_req = mcp.get("gatewayRequest", {})
    body = gw_req.get("body", {})
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:  # noqa: BLE001
            return _PASSTHROUGH

    # Only augment tools/call requests.
    if not isinstance(body, dict) or body.get("method") != "tools/call":
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayRequest": {"body": body}},
        }

    auth = _find_bearer(event) or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else auth
    claims = _b64url_json(token.split(".")[1]) if token.count(".") == 2 else {}
    caller = _caller_from_claims(claims)

    params = body.setdefault("params", {})
    args = params.setdefault("arguments", {})
    if caller:
        args["_caller"] = caller
        logger.info(
            f"[interceptor] injected _caller identity={caller['identity']} source={caller['source']}"
        )
    else:
        logger.info(
            "[interceptor] no human identity in token (M2M/2LO) - no _caller injected"
        )

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": body}},
    }
