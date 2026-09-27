#!/usr/bin/env python3
"""
deploy_registry.py — Infrastructure-as-Code for the **standalone GA AWS Agent Registry**.

This targets the dedicated GA service (agent-registry-control), NOT the legacy
registry APIs embedded in bedrock-agentcore-control. ARNs look like
`arn:aws:agent-registry:...` and the console is https://console.aws.amazon.com/agent-registry.

Ref (GA, 2026-08-31):
  https://aws.amazon.com/about-aws/whats-new/2026/08/aws-agent-registry-generally-available/
  https://docs.aws.amazon.com/agent-registry/

Creates (idempotently) the "sc-supply-chain-registry" with an AWS_IAM authorizer and
APPROVE_ALL auto-approval, then registers all already-deployed components as records:
  4 business MCP gateways + 1 Agent runtime (as an MCP record pointing at its direct
  runtime invocation URL) + 1 Skill = 6 records. No MCP infrastructure is created here —
  records are catalog pointers to existing AgentCore gateways/runtime.

All account/region/Cognito/gateway/runtime identifiers are resolved DYNAMICALLY at
runtime via boto3 — nothing is hardcoded. Required live resources that cannot be found
cause a clear failure.

REQUIREMENTS: boto3 >= 1.43.94 (earlier versions lack the agent-registry-control model).

Usage:
  python deploy_registry.py                 # create/refresh registry + records
  python deploy_registry.py --recreate      # delete + rebuild the registry (destructive)
  python deploy_registry.py --dry-run       # preview, no changes
  python deploy_registry.py --region us-east-1
"""
import argparse
import json
import os
import sys
from pathlib import Path
from threading import Event
from urllib.parse import quote as _urlquote

import boto3
from botocore.exceptions import ClientError

_POLL_IDLE = Event()


def _poll_wait(seconds: float) -> None:
    """Wait `seconds` between polls."""
    _POLL_IDLE.wait(timeout=seconds)


# ─── Static configuration (names only — no account/region-specific IDs) ──────────
REGISTRY_NAME = "sc-supply-chain-registry"
REGISTRY_DESCRIPTION = (
    "Supply Chain Agents Registry - Quoting, Governance, Invoice, Disruption MCPs "
    "+ Orchestrator Agent + Ops Skill (standalone AWS Agent Registry, AWS_IAM)"
)

COGNITO_USER_POOL_NAME = "sc-supply-chain-pool"
DEFAULT_RESOURCE_SERVER_ID = "supply-chain-api"
AGENT_RUNTIME_NAME_PREFIX = "sc_order_fulfillment_agent"

SKILL_MD_PATH = Path(__file__).resolve().parent.parent / "skill" / "SKILL.md"

# MCP descriptor schema version accepted by the GA service (MCP server.json format).
MCP_SCHEMA_VERSION = "2025-09-29"
# A2A Agent Card schema version (a2aAgentCard descriptor).
A2A_SCHEMA_VERSION = "0.3.0"

# Logical record name -> live gateway name to resolve via list_gateways.
# (record_name, gateway_name, description)
MCP_GATEWAY_MAP = [
    ("sc-quoting-mcp", "sc-quoting-gateway",
     "Supply Chain Quoting MCP - Pricing engine"),
    ("sc-governance-mcp", "sc-governance-gateway",
     "Supply Chain Governance MCP - compliance checks"),
    ("sc-invoice-processing-mcp", "sc-invoice-gateway",
     "Supply Chain Invoice Processing MCP - approval rules"),
    ("sc-disruption-alert-mcp", "sc-disruption-alert-gateway",
     "Supply Chain Disruption Alert MCP - monitoring, mitigation"),
]


# The MCP 'server.json' schema (2025-09-29) caps `description` at 100 chars.
_MCP_DESC_MAX = 100


def _clamp_desc(text: str) -> str:
    """Clamp a description to the MCP schema's 100-char limit (ASCII-safe)."""
    t = (text or "").encode("ascii", "replace").decode("ascii")
    return t if len(t) <= _MCP_DESC_MAX else t[: _MCP_DESC_MAX - 1].rstrip() + "…".encode("ascii", "replace").decode()


def endpoint_for(gateway_id: str, region: str) -> str:
    return f"https://{gateway_id}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp"


def mcp_data(name, gateway_id, client_id, user_pool_id, domain, region, description):
    """Produce a valid MCP registry 'server.json' document.

    The gateway endpoint goes in remotes[].url. Cognito 3LO consumer hints are
    carried in _meta (namespaced), since the gateways use CUSTOM_JWT auth.
    """
    return json.dumps({
        "name": f"io.supplychain/{name}",
        "description": _clamp_desc(description),
        "version": "1.0.0",
        "remotes": [{
            "type": "streamable-http",
            "url": endpoint_for(gateway_id, region),
        }],
        "_meta": {
            "com.supplychain": {
                "gatewayId": gateway_id,
                "auth": {"type": "cognito_3lo", "userPoolId": user_pool_id,
                         "clientId": client_id, "domain": domain},
            }
        },
    }, separators=(",", ":"))


def runtime_invocation_url(runtime_arn: str, region: str) -> str:
    """Build the DIRECT AgentCore runtime invocation URL from the runtime ARN.

    The ARN is URL-encoded (':' -> %3A, '/' -> %2F) so it can sit in the path:
      https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<ARN>/invocations?qualifier=DEFAULT
    """
    encoded = _urlquote(runtime_arn, safe="")
    return (f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
            f"{encoded}/invocations?qualifier=DEFAULT")


def agent_a2a_card(runtime_arn, runtime_id, client_id, user_pool_id, domain, region, description):
    """Produce an A2A Agent Card (application/json) for the order-fulfillment
    AgentCore Runtime, registered as an AGENT-type record. The agent is reached at
    its direct runtime invocation URL. NOTE: A2A agent records are catalog entries —
    Quick does NOT surface A2A descriptors as connectors, so the Quick agent
    connector for this runtime is configured manually.
    """
    url = runtime_invocation_url(runtime_arn, region)
    return json.dumps({
        "protocolVersion": "0.3.0",
        "name": "sc-order-fulfillment-agent",
        "description": _clamp_desc(description),
        "url": url,
        "preferredTransport": "JSONRPC",
        "version": "1.0.0",
        "capabilities": {"streaming": True, "pushNotifications": False,
                         "stateTransitionHistory": False},
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [{
            "id": "order-fulfillment-orchestration",
            "name": "Order fulfillment orchestration",
            "description": ("Multi-step supply chain reasoning: chains the quoting, "
                            "governance, invoice-processing, and disruption MCP tools "
                            "to fulfill orders and produce contingency plans."),
            "tags": ["supply-chain", "orchestration", "quoting", "governance",
                     "invoice", "disruption"],
        }],
        "_meta": {
            "com.supplychain": {
                "runtimeId": runtime_id,
                "runtimeArn": runtime_arn,
                "auth": {"type": "cognito_3lo", "userPoolId": user_pool_id,
                         "clientId": client_id, "domain": domain},
            }
        },
    }, separators=(",", ":"))


class LiveResourceResolver:
    """Resolves account/region/Cognito/gateway/runtime identifiers at runtime."""

    def __init__(self, region):
        self.region = region
        self.session = boto3.Session(region_name=region)
        self.account_id = None
        self.user_pool_id = None
        self.domain = None
        self.client_id = None
        self.resource_server_id = os.environ.get("RESOURCE_SERVER_ID", DEFAULT_RESOURCE_SERVER_ID)
        self.gateway_ids = {}      # gateway_name -> gatewayId
        self.runtime_id = None
        self.runtime_arn = None

    def log(self, msg):
        print(f"  {msg}")

    def resolve_all(self):
        self._resolve_account()
        self._resolve_cognito()
        self._resolve_gateways()
        self._resolve_runtime()

    def _resolve_account(self):
        self.account_id = self.session.client("sts").get_caller_identity()["Account"]
        self.log(f"Account: {self.account_id}  Region: {self.region}")

    def _resolve_cognito(self):
        idp = self.session.client("cognito-idp")
        pool_id = None
        kwargs = {"MaxResults": 60}
        while True:
            resp = idp.list_user_pools(**kwargs)
            for p in resp.get("UserPools", []):
                if p["Name"] == COGNITO_USER_POOL_NAME:
                    pool_id = p["Id"]
                    break
            if pool_id or "NextToken" not in resp:
                break
            kwargs["NextToken"] = resp["NextToken"]
        if not pool_id:
            sys.exit(f"ERROR: Cognito user pool '{COGNITO_USER_POOL_NAME}' not found in {self.region}")
        self.user_pool_id = pool_id

        pool = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"]
        domain = pool.get("Domain")
        if not domain:
            sys.exit(f"ERROR: Cognito user pool '{COGNITO_USER_POOL_NAME}' has no hosted UI domain")
        self.domain = domain

        # Exactly one non-default app client is expected after the single-client refactor.
        clients = []
        kwargs = {"UserPoolId": pool_id, "MaxResults": 60}
        while True:
            resp = idp.list_user_pool_clients(**kwargs)
            clients.extend(resp.get("UserPoolClients", []))
            if "NextToken" not in resp:
                break
            kwargs["NextToken"] = resp["NextToken"]
        if not clients:
            sys.exit(f"ERROR: no app clients found on user pool {pool_id}")
        if len(clients) > 1:
            names = ", ".join(c.get("ClientName", "?") for c in clients)
            self.log(f"WARNING: expected 1 app client, found {len(clients)}: {names}. Using the first.")
        self.client_id = clients[0]["ClientId"]
        self.log(f"Cognito: pool={self.user_pool_id} domain={self.domain} clientId={self.client_id}")

    def _resolve_gateways(self):
        ctrl = self.session.client("bedrock-agentcore-control")
        gateways = []
        kwargs = {}
        while True:
            resp = ctrl.list_gateways(**kwargs)
            gateways.extend(resp.get("items", resp.get("gateways", [])))
            token = resp.get("nextToken")
            if not token:
                break
            kwargs["nextToken"] = token

        # Map live gateways whose name starts with 'sc-' by their name.
        by_name = {}
        for gw in gateways:
            name = gw.get("name", "")
            gid = gw.get("gatewayId") or gw.get("id")
            if name.startswith("sc-") and gid:
                by_name[name] = gid

        for record_name, gateway_name, _desc in MCP_GATEWAY_MAP:
            match = by_name.get(gateway_name)
            if not match:
                # Allow suffixed live names (e.g. sc-quoting-gateway-cnq4wbqwq9).
                candidates = sorted(
                    (n for n in by_name if n == gateway_name or n.startswith(gateway_name + "-"))
                )
                if candidates:
                    match = by_name[candidates[0]]
            if not match:
                sys.exit(f"ERROR: live MCP gateway '{gateway_name}' not found "
                         f"(available sc- gateways: {sorted(by_name) or 'none'})")
            self.gateway_ids[gateway_name] = match
            self.log(f"Gateway {gateway_name} -> {match}")

    def _resolve_runtime(self):
        ctrl = self.session.client("bedrock-agentcore-control")
        runtimes = []
        kwargs = {}
        while True:
            resp = ctrl.list_agent_runtimes(**kwargs)
            runtimes.extend(resp.get("agentRuntimes", resp.get("items", [])))
            token = resp.get("nextToken")
            if not token:
                break
            kwargs["nextToken"] = token

        candidates = [
            r for r in runtimes
            if (r.get("agentRuntimeName") or r.get("name") or "").startswith(AGENT_RUNTIME_NAME_PREFIX)
        ]
        if not candidates:
            sys.exit(f"ERROR: no agent runtime whose name starts with "
                     f"'{AGENT_RUNTIME_NAME_PREFIX}' was found in {self.region}")

        def version_key(r):
            v = r.get("agentRuntimeVersion") or r.get("version") or "0"
            try:
                return int(str(v).lstrip("vV"))
            except (TypeError, ValueError):
                return 0

        chosen = sorted(candidates, key=version_key)[-1]
        self.runtime_id = chosen.get("agentRuntimeId") or chosen.get("id")
        self.runtime_arn = chosen.get("agentRuntimeArn") or chosen.get("arn")
        if not self.runtime_id:
            sys.exit("ERROR: resolved agent runtime is missing an id")
        # The registry step needs the runtime ARN to build the direct invocation URL.
        # Fall back to constructing it from account/region/runtime_id if the list
        # response did not include it.
        if not self.runtime_arn:
            self.runtime_arn = (f"arn:aws:bedrock-agentcore:{self.region}:"
                                f"{self.account_id}:runtime/{self.runtime_id}")
        self.log(f"Agent runtime: {self.runtime_id} (arn: {self.runtime_arn})")


class RegistryDeployer:
    def __init__(self, region, resolver, dry_run=False):
        self.region = region
        self.r = resolver
        self.dry_run = dry_run
        self.c = boto3.Session(region_name=region).client("agent-registry-control")
        self.registry_id = None

    def log(self, msg):
        print(f"  {msg}")

    # ── registry lifecycle ──────────────────────────────────────────────────
    def find_registry(self):
        paginator_regs = self.c.list_registries().get("registries", [])
        for r in paginator_regs:
            if r["name"] == REGISTRY_NAME:
                return r["registryId"]
        return None

    def destroy(self, rid):
        self.log(f"Tearing down existing registry {REGISTRY_NAME} ({rid})")
        recs = self.c.list_registry_records(registryId=rid).get("registryRecords", [])
        for rec in recs:
            self.log(f"Deleting record {rec['recordId']} ({rec['name']})")
            if not self.dry_run:
                self.c.delete_registry_record(registryId=rid, recordId=rec["recordId"])
        self.log(f"Deleting registry {rid}")
        if not self.dry_run:
            self.c.delete_registry(registryId=rid)
            for _ in range(30):
                if self.find_registry() is None:
                    break
                _poll_wait(2)

    def ensure_registry(self, recreate=False):
        print("\n" + "=" * 60)
        print(f"  Registry: {REGISTRY_NAME} (standalone AWS Agent Registry, AWS_IAM)")
        existing = self.find_registry()
        if recreate and existing:
            self.destroy(existing)
            existing = None
        if existing:
            self.log(f"Already exists: {existing} — reusing. (use --recreate to rebuild)")
            self.registry_id = existing
            self._wait_ready()
            return
        if self.dry_run:
            self.log("[DRY RUN] create_registry AWS_IAM + autoApprovalRules=[APPROVE_ALL]")
            self.registry_id = "DRY_RUN_REGISTRY_ID"
            return
        resp = self.c.create_registry(
            name=REGISTRY_NAME,
            description=REGISTRY_DESCRIPTION,
            discoveryConfiguration={"authorizerType": "AWS_IAM"},
            approvalConfiguration={"autoApprovalRules": ["APPROVE_ALL"]},
        )
        # CreateRegistry returns only registryArn; the ID is the ARN suffix.
        arn = resp["registryArn"]
        self.registry_id = arn.rsplit("/", 1)[-1]
        self.log(f"Created registry: {self.registry_id}  (arn: {arn})")
        self._wait_ready()

    def _wait_ready(self, attempts=60):
        for _ in range(attempts):
            st = self.c.get_registry(registryId=self.registry_id)["status"]
            if st == "READY":
                self.log("Status: READY")
                return
            _poll_wait(3)
        sys.exit(f"ERROR: registry {self.registry_id} did not reach READY")

    # ── records ─────────────────────────────────────────────────────────────
    def _delete_if_exists(self, name):
        if self.dry_run and self.registry_id == "DRY_RUN_REGISTRY_ID":
            return
        for rec in self.c.list_registry_records(registryId=self.registry_id).get("registryRecords", []):
            if rec["name"] == name:
                self.log(f"Replacing existing record '{name}' ({rec['recordId']})")
                if not self.dry_run:
                    self.c.delete_registry_record(registryId=self.registry_id, recordId=rec["recordId"])

    def _create(self, name, record_type, descriptors, description, record_version=None):
        self._delete_if_exists(name)
        if self.dry_run:
            self.log(f"[DRY RUN] create_registry_record {record_type} {name}")
            return
        kwargs = dict(registryId=self.registry_id, name=name, description=description,
                      recordType=record_type, descriptors=descriptors)
        if record_version:
            kwargs["recordVersion"] = record_version
        self.c.create_registry_record(**kwargs)
        self.log(f"  registered {name} ({record_type}).")

    def register_mcps(self):
        print("\n" + "=" * 60); print("  Registering business MCP records (4)")
        for record_name, gateway_name, desc in MCP_GATEWAY_MAP:
            gateway_id = self.r.gateway_ids[gateway_name]
            self.log(f"→ {record_name}  ({gateway_id})")
            self._create(record_name, "MCP",
                         {"mcpServer": {"data": mcp_data(
                             record_name, gateway_id, self.r.client_id,
                             self.r.user_pool_id, self.r.domain, self.region, desc),
                             "dataSchemaVersion": MCP_SCHEMA_VERSION}}, desc)

    def register_agent(self):
        print("\n" + "=" * 60); print("  Registering Agent record (1)")
        desc = "Supply Chain Order Fulfillment Agent (AgentCore Runtime) - multi-step orchestration"
        self.log(f"→ sc-order-fulfillment-agent  (AGENT, runtime {self.r.runtime_id})")
        self._create("sc-order-fulfillment-agent", "AGENT",
                     {"a2aAgentCard": {"data": agent_a2a_card(
                         self.r.runtime_arn, self.r.runtime_id, self.r.client_id,
                         self.r.user_pool_id, self.r.domain, self.region, desc),
                         "dataSchemaVersion": A2A_SCHEMA_VERSION}},
                     desc)

    def register_skill(self):
        print("\n" + "=" * 60); print("  Registering Skill record (1)")
        if not SKILL_MD_PATH.exists():
            sys.exit(f"ERROR: skill markdown not found at {SKILL_MD_PATH}")
        self.log(f"→ supply-chain-ops  (skill from {SKILL_MD_PATH})")
        skill_md = SKILL_MD_PATH.read_text(encoding="utf-8")
        self._create(
            "supply-chain-ops", "SKILL",
            {"agentSkillsDefinition": {"additionalData": {"skillMd": {"data": skill_md}}}},
            "Orchestrates supply chain operations by combining Snowflake data queries with "
            "business-rule MCPs (Quoting, Governance, Invoice Processing, Disruption Alerts). "
            "Handles quotes, invoices, orders, inventory, suppliers, disruptions, pricing, "
            "compliance, shipments, procurement, fulfillment, and payment queues. Generates "
            "professional company-branded HTML documents.",
            record_version="1.0.0")

    def submit_all(self):
        print("\n" + "=" * 60); print("  Submitting records for approval (APPROVE_ALL → APPROVED)")
        if self.dry_run:
            self.log("[DRY RUN] submit all DRAFT records for approval")
            return
        # Records are async: they pass through CREATING before they can be submitted,
        # and APPROVE_ALL moves them DRAFT→APPROVED shortly after submit. Poll until
        # no record is CREATING, submit every DRAFT, and repeat until none remain
        # DRAFT (or a bounded number of attempts).
        for attempt in range(15):
            recs = self.c.list_registry_records(registryId=self.registry_id).get("registryRecords", [])
            if any(r.get("status") == "CREATING" for r in recs):
                _poll_wait(4)
                continue
            drafts = [r for r in recs if r.get("status") == "DRAFT"]
            if not drafts:
                break
            for rec in drafts:
                try:
                    self.c.submit_registry_record_for_approval(
                        registryId=self.registry_id, recordId=rec["recordId"])
                    self.log(f"submitted {rec['name']}")
                except ClientError as e:
                    self.log(f"submit retry {rec['name']}: {str(e)[:80]}")
            _poll_wait(6)

    def summary(self):
        print("\n" + "=" * 60); print("  Summary")
        if self.dry_run:
            self.log("Dry run complete — no changes made.")
            return
        self.log(f"Registry: {self.registry_id}")
        recs = self.c.list_registry_records(registryId=self.registry_id).get("registryRecords", [])
        for r in sorted(recs, key=lambda x: x["name"]):
            self.log(f"  {r['status']:<10} {r['recordType']:<8} {r['name']}")


def main():
    default_region = os.environ.get("AWS_REGION") or "us-east-1"
    ap = argparse.ArgumentParser(description="Deploy the standalone AWS Agent Registry (IaC)")
    ap.add_argument("--region", default=default_region)
    ap.add_argument("--recreate", action="store_true", help="delete + rebuild the registry")
    ap.add_argument("--delete", action="store_true", help="delete the registry (records + registry) and exit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("  AWS Agent Registry (standalone, GA) — Supply Chain IaC")
    print("=" * 60)
    print(f"  Region:   {args.region}")
    print(f"  Registry: {REGISTRY_NAME}  (AWS_IAM, APPROVE_ALL)")
    print(f"  boto3:    {boto3.__version__}")
    mode = "DRY RUN" if args.dry_run else "LIVE"
    if args.delete:
        mode += " + DELETE (removes the registry, then exits)"
    elif args.recreate:
        mode += " + RECREATE (destroys existing registry)"
    print(f"  Mode:     {mode}")

    # --delete: tear down the registry only. Does NOT resolve live gateways/runtime
    # (they may already be gone), so it is safe to run before/after stack deletion.
    if args.delete:
        d = RegistryDeployer(args.region, None, dry_run=args.dry_run)
        existing = d.find_registry()
        if not existing:
            print("\n  Registry not found — nothing to delete.")
            return
        d.destroy(existing)
        print("\n  Registry deleted.")
        return

    print("\n" + "=" * 60)
    print("  Resolving live resources (account, Cognito, gateways, runtime)")
    resolver = LiveResourceResolver(args.region)
    resolver.resolve_all()

    d = RegistryDeployer(args.region, resolver, dry_run=args.dry_run)
    d.ensure_registry(recreate=args.recreate)
    d.register_mcps()
    d.register_agent()
    d.register_skill()
    d.submit_all()
    d.summary()


if __name__ == "__main__":
    main()
