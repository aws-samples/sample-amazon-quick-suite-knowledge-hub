# Building a Quick App on the Migrator MCP Connector

This document is the build specification you hand to **Amazon Quick** so it can
generate a web app on top of the migrator MCP connector. Register the
migrator MCP connector's five actions — `preview_migration`,
`migrate_resources`, `list_backups`, `get_backup`, and `restore_backup` — then
paste the prompt below into Amazon Quick's app builder. The app discovers the
connector and action IDs at build time (via `search_action_connectors` /
`get_action_connector_details`), so there are no IDs to hardcode.

## Testing with Apps in Quick

You build and test this tool using
[Apps in Amazon Quick](https://docs.aws.amazon.com/quick/latest/userguide/using-amazon-quick-apps.html),
which lets you build interactive web applications by describing what you need in
natural language. You tell the Apps in Quick agent what your app should do, who
it is for, and what data it needs, and the agent builds a working app in real
time while you watch — you then refine it through ongoing conversation, and
publish and share it for daily use across your organization.

Apps in Quick bridge the gap between data visualization and custom application
development: whether you need a dashboard, an internal tool, a data entry
interface, or a document viewer, you can create it conversationally and publish
it for your team in minutes.

For this migrator that means you don't hand-write the front end. Once the MCP
action connector is registered, you:

1. Open Apps in Quick and start a new app.
2. Paste the prompt below (it uses the five migrator actions:
   `preview_migration`, `migrate_resources`, `list_backups`, `get_backup`,
   and `restore_backup`).
3. When prompted, register/select the migrator MCP action connector so the app
   can discover its connector and action IDs.
4. Watch the agent build the app, then test it live — run **Preview** to scan
   the source and target accounts (agents, connectors, knowledge bases, spaces,
   and flows) with the CREATE/UPDATE mapping, select assets and run **Migrate**
   to promote them, review past runs under **History**, and browse/restore
   pre-update snapshots under **Backups**.
5. Iterate conversationally to refine, then publish and share the app.

## Prompt

```text
Build "Quick Migrator" — a single-page app to migrate Agents, Connectors, Knowledge Bases, Spaces, and Flows between AWS accounts, with migration history and S3 backup browsing/restore.

═══════════════════════════════════════════ CONNECTOR SETUP ═══════════════════════════════════════════

The app uses one MCP action connector with 5 actions. Before writing code:

Use search_action_connectors to find the MCP connector that has actions named preview_migration, migrate_resources, list_backups, get_backup, and restore_backup
Use get_action_connector_details and get_action_schemas to discover IDs and schemas
Register via register_runtime_integration with integration_type ACTION and all 5 action IDs
Store discovered connector ID and action IDs as constants — never hardcode from this prompt
If author denies registration, do NOT generate connector-calling code
Actions use MCP format (mcpInvokeActionInput with name + JSON.stringify'd arguments). Responses via mcpInvokeActionOutput.content[].textContent.text — check mcpInvokeActionError first. API often returns double/triple stringified JSON — implement recursive deep-unwrap up to 5 levels. 10-minute timeout on all calls.

App Storage: "migration-history" (Shared), "user-prefs" (Private).

═══════════════════════════════════════════ 5 ACTIONS ═══════════════════════════════════════════

preview_migration (read) — Scans source and target accounts Input: source_account_id, target_account_id (required), resource_type (default "all", accepts "agent"/"connector"/"knowledge_base"/"space"/"flow"/"all"), search_by (default "all", accepts "all"/"id"/"name"), value (default ""), region (default "us-east-1") Returns: { source: {agents:[], connectors:[], knowledge_bases:[], spaces:[], flows:[]}, target: {same}, mapping: [{id, in_target, action: "CREATE"/"UPDATE"}] }

migrate_resources (write) — Migrates ONE resource per call Input: source_account_id, target_account_id, resource_type, search_by (always "id"), value (MUST be bare UUID), region, source_env (default "dev"), target_env (default "prod"), qs_service_role (default "aws-quicksight-service-role-v0") Returns: { overall_status: "SUCCESS"/"FAILED", migrated: {agents:[], connectors:[], knowledge_bases:[], spaces:[], flows:[]}, skipped_permissions: [{resource_type, resource_id, unresolved_principals:[], reason}], resource_linkages:[], user_message, error_message }

list_backups (read) — Searches backup catalog, metadata only Input: query (optional substring filter), region Returns: { bucket, count, assets: [{name, asset_id, folder, latest_version, versions: [{version, key, last_modified, size}]}] }

get_backup (read) — Fetches full backup envelope for one version Input: asset_id (required), version (default 0 = latest), region Returns: { status, s3_key, version, backup: {schema_version, backed_up_at, resource_type, resource_id, name, resource: {full describe}, dependencies: {action_connectors:[], ...}} }

restore_backup (write) — Reverts target resource (takes pre-restore backup first) Input: asset_id (required), version, region Returns: { status, asset_id, restored_from_version, pre_restore_backup, result, dependencies, errors }

═══════════════════════════════════════════ 3 TABS ═══════════════════════════════════════════

Pill-style tabs centered at top: 🚀 Migrate | 📜 History | 💾 Backups

── 🚀 MIGRATE TAB (3-step flow) ──

Step 1 — Scan: Purple gradient header with title "Quick Migrator". Inputs: Source/Target Account ID (12-digit validated, cannot match), Region (default "us-east-1"). Scan Filters ALWAYS VISIBLE (not collapsible): Resource Type dropdown (All/Agent/Connector/Knowledge Base/Space/Flow), Search By dropdown (All/By ID/By Name — "all" disables value field), Value input (required when by id/name — scan button disabled if empty).

PARALLEL SCANNING: When type=All and search=All, fire 5 parallel preview_migration calls (one per resource type) via Promise.allSettled to avoid socket timeout. Merge results. Partial failures show succeeded types. Single type or filtered search uses one call.

Frosted-glass loading overlay with spinner and auto-advancing step labels. Cache results keyed by all inputs. Show cache indicator + Force Rescan button. Persist account IDs/region to Private App Storage with debounced saves.

Step 2 — Select Assets: Tabbed browser (Agents/Connectors/KBs/Spaces/Flows) with count badges. All deselected by default. Select All/Deselect All per tab. Each asset shows: checkbox, icon, name, ID, CREATE badge (green) or UPDATE badge (blue) from mapping, target info line. Agents/Spaces expandable to show nested children (connectors, KBs, spaces) — children included by default, individually excludable. Claimed child IDs filtered from standalone lists.

Step 3 — Migrate: Selected count + source→target display. Collapsible Advanced Options (Source Env, Target Env, Service Role). Confirmation modal. Build one call per asset, deduplicated. SANITIZE every ID to bare UUID via regex extraction — never send JSON-wrapped or truncated values. Execute sequentially.

ERROR DETECTION: Do NOT trust API summary. Track which call produced each result. Check each for overall_status "FAILED". Recursive scan for error indicators. Recompute summary from actual results. Save full results + errors to Shared App Storage.

── RESULTS VIEW ──

Shared MigrationDetail component (used by both results page and history): Status header (green/amber/red gradient, compact mode for history uses inline badge). 5 stat cards by type. Per-type resource tables (Name/ID/Status badge). Linkages section. Permissions Need Manual Mapping section (resource name, type, reason, unresolved principal ARNs). Failed section with error messages. Raw JSON toggle. Results page wraps this + "Start New Migration" button.

── 📜 HISTORY TAB ──

Loads from Shared App Storage sorted newest first. Expandable cards: collapsed shows source→target, timestamp, region, success badge. Expanded renders MigrationDetail in compact mode — same full breakdown as results page. Recomputes summary from actual overall_status fields, never trusts stored counts. Handles old records without results.

── 💾 BACKUPS TAB ──

Split-panel layout. Search bar calls list_backups (empty=all, Enter triggers). Left panel: asset cards with expandable version rows (newest first, showing version badge, date, size). Clicking version calls get_backup. Right panel: detail view with gradient header (type icon, name, version, date), metadata grid, dependencies section, raw JSON toggle, amber Restore button → confirm modal (warns about overwrite, notes pre-restore backup) → shows success/error result.

═══════════════════════════════════════════ PARSING RULES ═══════════════════════════════════════════

Scan response: Build mapping lookup (id→inTarget/action) and target lookup. Extract assets via recursive array search under multiple key variants (agents/chat_agents/Agents, connectors/action_connectors/Connectors, etc.) up to 6 levels deep, deduplicating. Agents/Spaces extract nested children.

ID extraction keys MUST include: knowledge_base_id, knowledgeBaseId, resource_id, resourceId (missing these caused a critical malformed-value bug). Fallback: scan values for UUID regex. NEVER fall back to truncated JSON.stringify — return dash instead.

Name extraction: try standard name keys then any short non-ARN non-UUID string.

Migration result parser: extract succeeded from migrated arrays, skipped_permissions with unresolved principals, failed from overall_status, linkages.

═══════════════════════════════════════════ DESIGN & CONSTRAINTS ═══════════════════════════════════════════

All inline styles. Purple gradient theme. Rounded cards, soft shadows. System font. Green=created/success, blue=updated, amber=warnings, red=failed. Selected items: purple inset border. Max widths: 760px migrate, 880px results/history, 960px backups.

Constraints: 5 resource types (agent/connector/knowledge_base/space/flow). Value sent to migrate MUST be bare UUID. Scan filters always visible. Never trust API summary — recompute. Cache scans by all params. Parallel scan with Promise.allSettled. 10-min timeout. No form elements. QuickIntegrationError and PageStorageError messages rendered as-is. Connector/action IDs discovered at build time, never hardcoded.
```
