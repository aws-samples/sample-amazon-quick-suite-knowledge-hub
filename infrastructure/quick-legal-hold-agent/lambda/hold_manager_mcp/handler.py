"""
Hold Manager MCP tool Lambda — invoked DIRECTLY by an Amazon Bedrock AgentCore Gateway.

AgentCore invocation contract (NOT HTTP, NOT JSON-RPC):
  - The tool name arrives at context.clientContext.custom.bedrockAgentCoreToolName.
    It may be prefixed with 'targetname___'; we strip that to get the bare tool name.
  - Tool parameters arrive in the EVENT ROOT (the event *is* the arguments object).
  - The AWS account id is auto-detected from context.invokedFunctionArn — never a parameter.

Tools:
  search_identities(query)           -> IDC ListUsers/ListGroups filtered by query
  list_group_members(group_name)     -> GetGroupId + ListGroupMemberships + DescribeUser
  place_hold(type, name, matter_id)  -> resolve target then PutItem active hold per user_arn
  release_hold(type, name)           -> resolve then set status=released
  list_holds()                       -> return current holds

Identity resolution is pluggable via IDENTITY_MODE ("direct" default | "idc").
"""

import datetime
import json
import os

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

USERS_TABLE = os.environ["USERS_TABLE"]
IDENTITY_MODE = os.environ.get("IDENTITY_MODE", "direct").lower()
IDENTITY_STORE_ID = os.environ.get("IDENTITY_STORE_ID", "")

_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(USERS_TABLE)


class ResolutionError(Exception):
    """Raised when a target cannot be resolved/verified. Fail closed: never
    write a hold for an unresolved or unverifiable identity."""


# botocore error codes that mean the directory could not be queried (denied).
_DENIED_CODES = {
    "AccessDeniedException",
    "AccessDenied",
    "UnauthorizedException",
    "NotAuthorizedException",
    "ForbiddenException",
}


def _idc():
    return boto3.client("identitystore")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


# ---------------------------------------------------------------------------
# Tool-name + account extraction from the AgentCore context
# ---------------------------------------------------------------------------
def _tool_name(context) -> str:
    name = ""
    cc = getattr(context, "client_context", None) or getattr(
        context, "clientContext", None
    )
    if cc is not None:
        custom = getattr(cc, "custom", None) or {}
        if isinstance(custom, dict):
            name = custom.get("bedrockAgentCoreToolName", "") or ""
    # Strip any 'targetname___' prefix -> bare tool name.
    if "___" in name:
        name = name.split("___", 1)[1]
    return name


def _account_id(context) -> str:
    # Derived from this Lambda's own invoked ARN at runtime; the fallback is a
    # neutral placeholder only used if the ARN is unexpectedly unavailable.
    arn = getattr(context, "invoked_function_arn", "") or ""
    parts = arn.split(":")
    return parts[4] if len(parts) > 4 else "000000000000"


def _b64url_json(segment: str):
    """Decode a base64url JWT segment to a dict (best-effort)."""
    import base64

    try:
        pad = "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment + pad).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _maybe_json_obj(val: str):
    """Parse a JSON object string, returning the dict or None if it isn't valid
    JSON. Keeps the caller free of a bare try/except swallow."""
    try:
        parsed = json.loads(val)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _claims_from_context(context, event) -> dict:
    """
    Best-effort extraction of the caller's JWT claims from the AgentCore
    invocation context. The exact carrier field is discovered empirically and
    logged (see the diagnostic dump in handler()); we check the plausible
    locations in priority order:
      1. clientContext.custom.* fields the Gateway may inject
         (e.g. bedrockAgentCoreIdentity / claims / a raw JWT).
      2. A raw JWT anywhere in custom -> decode its payload.
      3. event-level fallbacks (some setups pass claims in the event).
    Returns a claims dict (possibly empty).
    """
    cc = getattr(context, "client_context", None) or getattr(
        context, "clientContext", None
    )
    custom = {}
    if cc is not None:
        c = getattr(cc, "custom", None)
        if isinstance(c, dict):
            custom = c

    # 0. Interceptor-injected caller identity (preferred path): the REQUEST
    #    interceptor decodes the inbound JWT and injects "_caller" into the tool
    #    arguments, which arrive here in the event root.
    if isinstance(event, dict) and isinstance(event.get("_caller"), dict):
        c = event["_caller"]
        identity = c.get("identity")
        if identity:
            # Pass through the interceptor's chosen identity + source verbatim.
            return {
                "_resolved_custodian": str(identity),
                "_resolved_source": c.get("source", "jwt"),
            }
    for key in ("bedrockAgentCoreIdentity", "claims", "identityClaims", "jwtClaims"):
        val = custom.get(key)
        if isinstance(val, dict) and val:
            return val
        if isinstance(val, str) and val.strip().startswith("{"):
            parsed = _maybe_json_obj(val)
            if parsed:
                return parsed

    # 2. A raw JWT string anywhere in custom -> decode payload segment.
    for _k, v in custom.items():
        if isinstance(v, str) and v.count(".") == 2 and len(v) > 40:
            payload = _b64url_json(v.split(".")[1])
            if payload:
                return payload

    # 3. Event-level fallback.
    if isinstance(event, dict):
        for key in ("claims", "requestContext"):
            val = event.get(key)
            if isinstance(val, dict):
                # API-GW-style nested authorizer claims
                nested = (
                    val.get("authorizer", {}).get("claims")
                    if key == "requestContext"
                    else val
                )
                if isinstance(nested, dict) and nested:
                    return nested
    return {}


def _custodian_from_claims(claims: dict):
    """Return (custodian, source) from token claims, or (None, None)."""
    if not claims:
        return None, None
    # Interceptor already resolved the caller identity + source.
    if claims.get("_resolved_custodian"):
        return str(claims["_resolved_custodian"]), claims.get("_resolved_source", "jwt")
    # A machine (client_credentials) token has no human identity: token_use=access
    # with a client_id and no email/username -> treat as non-human.
    for field in ("email", "cognito:username", "username", "preferred_username", "sub"):
        val = claims.get(field)
        if val:
            return str(val), f"jwt:{field}"
    return None, None


# ---------------------------------------------------------------------------
# Identity resolution (pluggable)
# ---------------------------------------------------------------------------
def _synthetic_arn(name: str, account: str) -> str:
    # For a name that already looks like an ARN, pass through.
    if name.startswith("arn:aws:"):
        return name
    safe = name.strip().replace(" ", "_")
    # Use a QuickSight-style user ARN for realism in the test account.
    return f"arn:aws:quicksight:us-east-1:{account}:user/default/{safe}"


def _region() -> str:
    """Region the Lambda runs in — derived, never hardcoded."""
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )


def _quicksight_arn(user_name: str, account: str) -> str:
    """Build the QuickSight-style user ARN that Quick CHAT_LOGS emit:
    arn:aws:quicksight:<region>:<account>:user/default/<UserName>. This is the
    exact string the filter Lambda matches record user_arn against."""
    safe = (user_name or "").strip()
    return f"arn:aws:quicksight:{_region()}:{account}:user/default/{safe}"


def resolve_users(target_type: str, name: str, account: str) -> list:
    n = (name or "").strip()
    # An exact user_arn is a fully-qualified identifier — accept it directly in
    # either mode (this covers the synthetic test hold in idc mode). A group
    # target is never an ARN.
    if target_type == "user" and n.startswith("arn:aws:"):
        if len(n) < 12:
            raise ResolutionError(f"invalid target arn '{name}'; no hold placed")
        # Best-effort audit subject_key = trailing token of the ARN.
        subject = n.rstrip("/").split("/")[-1]
        return [{"user_arn": n, "display": n, "subject_key": subject}]

    if IDENTITY_MODE == "idc" and IDENTITY_STORE_ID:
        return _resolve_users_idc(target_type, n, account)
    # direct mode: exact supplied name. Minimal validation guard — reject
    # empty / obviously malformed names before writing anything.
    if len(n) < 2 or n.lower() in {"none", "null", "undefined"}:
        raise ResolutionError(f"invalid target name '{name}'; no hold placed")
    return [{"user_arn": _synthetic_arn(n, account), "display": n, "subject_key": n}]


def _resolve_users_idc(target_type: str, name: str, account: str) -> list:
    """Resolve against IAM Identity Center and key each hold on the QuickSight
    ARN built from the member's UserName (the form Quick CHAT_LOGS carry), with
    the IDC UserId/UserName retained for audit. FAIL CLOSED:
    - unresolved user  -> ResolutionError (never fabricate a synthetic hold)
    - missing group    -> ResolutionError
    - member DescribeUser fails -> ResolutionError naming the member; no partial
      / synthetic item written for it
    - denied (SCP etc) -> ResolutionError explaining the directory couldn't be queried
    """
    idc = _idc()
    if target_type == "user":
        try:
            resp = idc.list_users(
                IdentityStoreId=IDENTITY_STORE_ID,
                Filters=[{"AttributePath": "UserName", "AttributeValue": name}],
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _DENIED_CODES:
                raise ResolutionError(
                    f"IAM Identity Center could not be queried (access denied resolving user "
                    f"'{name}'); no hold placed. Supply an exact identifier or use direct mode."
                ) from exc
            raise ResolutionError(
                f"error resolving user '{name}': {code or exc}; no hold placed"
            ) from exc
        users = resp.get("Users", [])
        if not users:
            raise ResolutionError(
                f"user '{name}' not found in IAM Identity Center; no hold placed"
            )
        out = []
        for u in users:
            uname = u.get("UserName") or name
            out.append(
                {
                    "user_arn": _quicksight_arn(uname, account),
                    "display": uname,
                    "idc_user_id": u["UserId"],
                    "idc_user_name": uname,
                    "subject_key": uname,
                }
            )
        return out

    # group: resolve group id, then expand memberships.
    try:
        gid = idc.get_group_id(
            IdentityStoreId=IDENTITY_STORE_ID,
            AlternateIdentifier={
                "UniqueAttribute": {
                    "AttributePath": "DisplayName",
                    "AttributeValue": name,
                }
            },
        )["GroupId"]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException",):
            raise ResolutionError(f"group '{name}' not found; no hold placed") from exc
        if code in _DENIED_CODES:
            raise ResolutionError(
                f"IAM Identity Center could not be queried (access denied resolving group "
                f"'{name}'); no hold placed. Supply an exact identifier or use direct mode."
            ) from exc
        raise ResolutionError(
            f"error resolving group '{name}': {code or exc}; no hold placed"
        ) from exc

    members = []
    try:
        paginator = idc.get_paginator("list_group_memberships")
        for page in paginator.paginate(IdentityStoreId=IDENTITY_STORE_ID, GroupId=gid):
            for m in page.get("GroupMemberships", []):
                uid = m["MemberId"]["UserId"]
                # DescribeUser failure for a member is fatal (fail closed): we
                # never write a broken/synthetic item for an unresolved member.
                try:
                    u = idc.describe_user(IdentityStoreId=IDENTITY_STORE_ID, UserId=uid)
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code", "")
                    if code in _DENIED_CODES:
                        raise ResolutionError(
                            f"IAM Identity Center could not be queried (access denied describing "
                            f"member {uid} of group '{name}'); no hold placed."
                        ) from exc
                    raise ResolutionError(
                        f"error describing member {uid} of group '{name}': {code or exc}; "
                        f"no hold placed."
                    ) from exc
                uname = u.get("UserName")
                if not uname:
                    raise ResolutionError(
                        f"member {uid} of group '{name}' has no UserName; no hold placed."
                    )
                members.append(
                    {
                        "user_arn": _quicksight_arn(uname, account),
                        "display": uname,
                        "idc_user_id": uid,
                        "idc_user_name": uname,
                        "subject_key": uname,
                        "group_name": name,
                    }
                )
    except ResolutionError:
        raise
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _DENIED_CODES:
            raise ResolutionError(
                f"IAM Identity Center could not be queried (access denied expanding group "
                f"'{name}'); no hold placed."
            ) from exc
        raise ResolutionError(
            f"error expanding group '{name}': {code or exc}; no hold placed"
        ) from exc
    if not members:
        raise ResolutionError(f"group '{name}' has no members; no hold placed")
    return members


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
def tool_search_identities(args) -> dict:
    query = (args.get("query") or "").lower()
    if IDENTITY_MODE == "idc" and IDENTITY_STORE_ID:
        idc = _idc()
        try:
            users = idc.list_users(IdentityStoreId=IDENTITY_STORE_ID).get("Users", [])
            groups = idc.list_groups(IdentityStoreId=IDENTITY_STORE_ID).get(
                "Groups", []
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _DENIED_CODES:
                return {
                    "error": "IAM Identity Center could not be queried (access denied); "
                    "search unavailable. Supply exact identifiers or use direct mode."
                }
            return {"error": f"error searching identities: {code or exc}"}

        def mu(u):
            return (
                not query
                or query in u.get("UserName", "").lower()
                or query in u.get("DisplayName", "").lower()
            )

        def mg(g):
            return not query or query in g.get("DisplayName", "").lower()

        return {
            "mode": "idc",
            "users": [
                {"id": u["UserId"], "name": u.get("UserName")} for u in users if mu(u)
            ],
            "groups": [
                {"id": g["GroupId"], "name": g.get("DisplayName")}
                for g in groups
                if mg(g)
            ],
        }
    return {
        "mode": "direct",
        "note": "IDENTITY_MODE=direct: search disabled; supply names directly.",
        "query": query,
        "users": [],
        "groups": [],
    }


def tool_list_group_members(args, account) -> dict:
    name = args.get("group_name") or args.get("name") or ""
    if IDENTITY_MODE == "idc" and IDENTITY_STORE_ID:
        try:
            return {
                "mode": "idc",
                "group": name,
                "members": _resolve_users_idc("group", name, account),
            }
        except ResolutionError as exc:
            return {"error": str(exc)}
    return {
        "mode": "direct",
        "note": "IDENTITY_MODE=direct: group expansion disabled.",
        "group": name,
        "members": [],
    }


def tool_place_hold(args, account, claims) -> dict:
    target_type = args.get("type", "user")
    name = args.get("name")
    matter_id = args.get("matter_id", "UNSPECIFIED")
    if not name:
        return {"error": "name is required"}

    # Custodian precedence: caller's token identity (3LO human) -> request-supplied
    # custodian (2LO/M2M) -> "system".
    claim_custodian, claim_source = _custodian_from_claims(claims)
    if claim_custodian:
        custodian, custodian_source = claim_custodian, claim_source
    elif args.get("custodian"):
        custodian, custodian_source = args["custodian"], "request"
    else:
        custodian, custodian_source = "system", "default"
    print(f"[mcp] place_hold custodian={custodian!r} source={custodian_source}")

    try:
        resolved = resolve_users(target_type, name, account)
    except ResolutionError as exc:
        print(f"[mcp] place_hold resolution failed: {exc}")
        return {"error": str(exc)}

    newly_held = []  # items created now (hold_start = now)
    already_held = []  # {name, hold_start} for members already active (date preserved)
    for r in resolved:
        user_arn = r["user_arn"]
        member_name = r.get("display", name)
        # Idempotent get-before-put: if an active hold already exists, PRESERVE
        # it (including its original hold_start) and never overwrite.
        try:
            existing = _table.get_item(Key={"user_arn": user_arn}).get("Item")
        except Exception as exc:  # noqa: BLE001 - fail closed: do not overwrite on read error
            print(f"[mcp] place_hold get_item error for {user_arn}: {exc}")
            return {
                "error": f"could not verify existing hold for '{member_name}'; no changes made"
            }

        if existing and existing.get("status") == "active":
            already_held.append(
                {
                    "name": member_name,
                    "user_arn": user_arn,
                    "hold_start": existing.get("hold_start"),
                }
            )
            continue

        item = {
            "user_arn": user_arn,
            "status": "active",
            "matter_id": matter_id,
            "hold_start": _now_iso(),
            "custodian": custodian,
            "custodian_source": custodian_source,
            "display": member_name,
            "target_type": target_type,
            "target_name": name,
            "control_path": "mcp",
        }
        # Audit attributes from IDC resolution (present in idc mode). Keep the
        # partition key equal to the CHAT_LOGS user_arn; retain the directory
        # identifiers alongside for chain-of-custody.
        for k in ("idc_user_id", "idc_user_name", "subject_key", "group_name"):
            if r.get(k):
                item[k] = r[k]
        _table.put_item(Item=item)
        newly_held.append(item)

    result = {
        "mode": IDENTITY_MODE,
        "custodian": custodian,
        "custodian_source": custodian_source,
        "newly_held": newly_held,
        "newly_held_count": len(newly_held),
        "already_held": already_held,
        "already_held_count": len(already_held),
    }

    if target_type == "group":
        total = len(resolved)
        new_names = [m.get("display", "?") for m in newly_held]
        result["message"] = (
            f"Started legal hold for group {name!r} ({total} members). "
            f"Newly held: {new_names}. "
            f"Already on hold: {[m['name'] for m in already_held]}."
        )
    else:
        if already_held:
            ah = already_held[0]
            result["message"] = (
                f"{ah['name']} is already on legal hold (since {ah['hold_start']}). "
                f"Nothing to do."
            )
        else:
            result["message"] = (
                f"{name} placed on legal hold (since {newly_held[0]['hold_start']})."
            )
    return result


def tool_release_hold(args, account, claims) -> dict:
    target_type = args.get("type", "user")
    name = args.get("name")
    if not name:
        return {"error": "name is required"}

    claim_custodian, claim_source = _custodian_from_claims(claims)
    released_by = claim_custodian or args.get("custodian") or "system"
    released_by_source = claim_source or (
        "request" if args.get("custodian") else "default"
    )
    print(f"[mcp] release_hold released_by={released_by!r} source={released_by_source}")

    try:
        resolved = resolve_users(target_type, name, account)
    except ResolutionError as exc:
        print(f"[mcp] release_hold resolution failed: {exc}")
        return {"error": str(exc)}
    released = []
    for r in resolved:
        try:
            _table.update_item(
                Key={"user_arn": r["user_arn"]},
                UpdateExpression="SET #s = :released, released_at = :ts, released_by = :rb, released_by_source = :rbs",
                ConditionExpression=Attr("user_arn").exists(),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":released": "released",
                    ":ts": _now_iso(),
                    ":rb": released_by,
                    ":rbs": released_by_source,
                },
            )
            released.append(r["user_arn"])
        except Exception as exc:  # noqa: BLE001
            print(f"[mcp] release skip {r['user_arn']}: {exc}")
    return {
        "released": released,
        "count": len(released),
        "mode": IDENTITY_MODE,
        "released_by": released_by,
        "released_by_source": released_by_source,
    }


def tool_list_holds(args) -> dict:
    items = []
    resp = _table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = _table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return {"holds": items, "count": len(items)}


def _dump_context(context, event):
    """Diagnostic: log the shape of the invocation context so we can discover
    exactly where the Gateway carries the caller's JWT claims. Redacts long
    token-like values."""
    try:
        cc = getattr(context, "client_context", None) or getattr(
            context, "clientContext", None
        )
        custom = {}
        if cc is not None:
            c = getattr(cc, "custom", None)
            if isinstance(c, dict):
                custom = {
                    k: (
                        f"<redacted len={len(v)}>"
                        if isinstance(v, str) and v.count(".") == 2
                        else v
                    )
                    for k, v in c.items()
                }
        print(
            f"[mcp][diag] clientContext.custom keys={list(custom.keys())} custom={json.dumps(custom, default=str)[:1500]}"
        )
        if isinstance(event, dict):
            print(f"[mcp][diag] event keys={list(event.keys())}")
    except Exception as exc:  # noqa: BLE001
        print(f"[mcp][diag] dump error: {exc}")


# ---------------------------------------------------------------------------
# Entry point (AgentCore direct invoke)
# ---------------------------------------------------------------------------
def handler(event, context):
    tool = _tool_name(context)
    account = _account_id(context)
    args = event if isinstance(event, dict) else {}
    _dump_context(context, event)
    claims = _claims_from_context(context, event)
    if claims:
        # Redact nothing sensitive beyond noting which identity fields exist.
        idfields = {
            k: claims.get(k)
            for k in (
                "email",
                "cognito:username",
                "username",
                "sub",
                "token_use",
                "client_id",
            )
            if k in claims
        }
        print(f"[mcp] caller claims present: {json.dumps(idfields, default=str)}")
    else:
        print(
            "[mcp] no caller claims found in context (2LO/M2M or claims not surfaced)"
        )

    print(f"[mcp] tool={tool} account={account} args={json.dumps(args, default=str)}")

    if tool == "search_identities":
        return tool_search_identities(args)
    if tool == "list_group_members":
        return tool_list_group_members(args, account)
    if tool == "place_hold":
        return tool_place_hold(args, account, claims)
    if tool == "release_hold":
        return tool_release_hold(args, account, claims)
    if tool == "list_holds":
        return tool_list_holds(args)

    return {
        "error": f"unknown tool: {tool!r}",
        "known_tools": [
            "search_identities",
            "list_group_members",
            "place_hold",
            "release_hold",
            "list_holds",
        ],
    }
