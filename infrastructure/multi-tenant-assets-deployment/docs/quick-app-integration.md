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
   to promote them (the queue is dependency-ordered — connectors → KBs → flows →
   agents → spaces), review past runs and the analytics dashboard under
   **History**, and browse/restore pre-update snapshots under **Backups**.
5. Iterate conversationally to refine, then publish and share the app.

## Prompt

```text
Build "Quick Migrator" — a single-page app to migrate Agents, Connectors, Knowledge Bases, Spaces, and Flows between AWS accounts, with migration history analytics and S3 backup browsing/restore.

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

migrate_resources (write) — Migrates ONE resource per call Input: source_account_id, target_account_id, resource_type, search_by (always "id"), value (MUST be bare UUID), region, source_env (default "dev"), target_env (default "prod"), qs_service_role (default "aws-quicksight-service-role-v0"), target_kb_bucket (optional; S3 KBs only — target bucket, else one named "knowledge-base-<source>-<accountId>" is created; a user-supplied name MUST start with "knowledge-base-"), target_data_source_arn (optional; credentialed KB types — SharePoint/Confluence/Google Drive/QBusiness — ARN of a data source you pre-created in the target, since their credentials can't be copied) Returns: { overall_status: "SUCCESS"/"FAILED", migrated: {agents:[], connectors:[], knowledge_bases:[], spaces:[], flows:[]}, skipped_permissions: [{resource_type, resource_id, unresolved_principals:[], reason}], resource_linkages:[], user_message, error_message }

KB TYPE BEHAVIOR (migrate_resources): S3_KNOWLEDGE_BASE and WEB_CRAWLER migrate automatically (S3 provisions/points at a knowledge-base-* bucket; web crawler is NO_AUTH). SHAREPOINT/CONFLUENCE/GOOGLE_DRIVE/QBUSINESS need target_data_source_arn (a connection created in the target UI first) — without it they return status "SKIPPED" with guidance. Statuses per KB: CREATED / UPDATED / SKIPPED: <reason> / FAILED: <reason>. The FE should surface SKIPPED KBs distinctly (amber) with the reason so the user knows to create the connection and re-run with target_data_source_arn.

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

🆕 DEPENDENCY-AWARE MIGRATION ORDERING:
After the user confirms migration, the app MUST automatically reorder the migration queue so that dependencies are created before their dependents. The ordering rules:
  1. Connectors first (standalone, no dependencies on other migrated types)
  2. Knowledge Bases second (may reference connectors but not agents/spaces)
  3. Flows third (may reference connectors and KBs)
  4. Agents fourth (depend on connectors, KBs, and flows)
  5. Spaces last (link to agents, connectors, and KBs — all should exist first)
Within each tier, maintain the user's original selection order.

Before executing, display a "Migration Plan" preview inside the confirmation modal showing the ordered queue grouped by tier with a brief explanation (e.g., "Connectors migrated first so Agents can reference them"). If a user selects a Space but none of its linked child resources (connectors/KBs), show an amber warning: "⚠ Space '{name}' references resources not included in this migration — target ARNs may not resolve. Consider including them."

Step 3 — Migrate: Selected count + source→target display. Collapsible Advanced Options: Source Env, Target Env, Service Role, plus a Knowledge Base Migration Options sub-section (amber/gold background) with Target KB Bucket (optional, S3 KBs only — if blank auto-created; must start with "knowledge-base-") and Target Data Source ARN (optional, for credentialed KB types — SharePoint/Confluence/Google Drive/QBusiness). Confirmation modal. Build one call per asset, deduplicated, executed in dependency order. SANITIZE every ID to bare UUID via regex extraction — never send JSON-wrapped or truncated values. Execute sequentially.

ERROR DETECTION: Do NOT trust API summary. Track which call produced each result. Check each for overall_status "FAILED". Recursive scan for error indicators. Recompute summary from actual results. Save full results + errors to Shared App Storage.

── RESULTS VIEW ──

Shared MigrationDetail component (used by both results page and history): Status header (green/amber/red gradient, compact mode for history uses inline badge). 5 stat cards by type. Per-type resource tables (Name/ID/Status badge). SKIPPED KBs shown distinctly with amber badge and reason text. Linkages section. Permissions Need Manual Mapping section (resource name, type, reason, unresolved principal ARNs). Failed section with error messages. Raw JSON toggle. Results page wraps this + "Start New Migration" button.

── 📜 HISTORY TAB ──

🆕 Two sub-sections: Analytics Dashboard (top) and Migration Log (bottom).

🆕 ANALYTICS DASHBOARD (top of History tab):
Aggregates ALL migration history records from Shared App Storage into 4 visual cards displayed in a 2×2 grid:

  1. Migrations Over Time (bar chart) — X-axis: date (grouped by day), Y-axis: count of migrations. Green bars for successful, red bars for failed, stacked. Use last 30 days of data. If no migrations, show "No migration data yet" placeholder.

  2. Success Rate (donut/ring chart) — Large centered percentage, ring segments: green=succeeded, amber=partial (has both successes and failures in same migration), red=all-failed. Show total count in center.

  3. Resource Type Breakdown (horizontal bar chart) — One bar per resource type (Agent/Connector/KB/Space/Flow) showing total migrated count across all history. Bars colored by type icon color. Sorted descending by count.

  4. Top Account Pairs (compact table) — Top 5 most-used source→target account pairs with migration count and last-used date. Helps identify common migration corridors.

All charts rendered with inline SVG (no external chart library needed — keep it lightweight). Charts recompute on every History tab mount from stored data. Show a subtle "Based on N migrations" footer under the grid. If history is empty, show a single centered empty state: "No migrations recorded yet. Complete a migration to see analytics here."

MIGRATION LOG (bottom of History tab):
Loads from Shared App Storage sorted newest first. Expandable cards: collapsed shows source→target, timestamp, region, success badge. Expanded renders MigrationDetail in compact mode — same full breakdown as results page. Recomputes summary from actual overall_status fields, never trusts stored counts. Handles old records without results.

── 💾 BACKUPS TAB ──

Split-panel layout. Search bar calls list_backups (empty=all, Enter triggers). Left panel: asset cards with expandable version rows (newest first, showing version badge, date, size). Clicking version calls get_backup. Right panel: detail view with gradient header (type icon, name, version, date), metadata grid, dependencies section, raw JSON toggle, amber Restore button → confirm modal (warns about overwrite, notes pre-restore backup) → shows success/error result.

═══════════════════════════════════════════ PARSING RULES ═══════════════════════════════════════════

Scan response: Build mapping lookup (id→inTarget/action) and target lookup. Extract assets via recursive array search under multiple key variants (agents/chat_agents/Agents, connectors/action_connectors/Connectors, etc.) up to 6 levels deep, deduplicating. Agents/Spaces extract nested children.

ID extraction keys MUST include: knowledge_base_id, knowledgeBaseId, resource_id, resourceId (missing these caused a critical malformed-value bug). Fallback: scan values for UUID regex. NEVER fall back to truncated JSON.stringify — return dash instead.

Name extraction: try standard name keys then any short non-ARN non-UUID string.

Migration result parser: extract succeeded from migrated arrays, skipped_permissions with unresolved principals, failed from overall_status, linkages. Surface SKIPPED KBs (status starting with "SKIPPED") distinctly from failures.

═══════════════════════════════════════════ DESIGN & CONSTRAINTS ═══════════════════════════════════════════

All inline styles. Purple gradient theme. Rounded cards, soft shadows. System font. Green=created/success, blue=updated, amber=warnings/skipped, red=failed. Selected items: purple inset border. Max widths: 760px migrate, 880px results/history, 960px backups.

🆕 Analytics charts: inline SVG only (no external charting library). Purple/green/red/amber color scheme consistent with app theme. Charts should be responsive within their card containers. Tooltips on hover for chart data points (using title attributes for simplicity).

Constraints: 5 resource types (agent/connector/knowledge_base/space/flow). Value sent to migrate MUST be bare UUID. Scan filters always visible. Never trust API summary — recompute. Cache scans by all params. Parallel scan with Promise.allSettled. 10-min timeout. No form elements. QuickIntegrationError and PageStorageError messages rendered as-is. Connector/action IDs discovered at build time, never hardcoded. 🆕 Migration queue MUST be dependency-ordered (connectors → KBs → flows → agents → spaces). 🆕 KB migration must pass target_kb_bucket and target_data_source_arn when provided by user.
```
