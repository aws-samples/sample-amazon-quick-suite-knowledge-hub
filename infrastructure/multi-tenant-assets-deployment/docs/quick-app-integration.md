# Building a Quick App on the Migrator MCP Connector

This document is the build specification you hand to **Amazon Quick** so it can
generate a web app on top of the migrator MCP connector. Register the
`preview_migration` and `migrate_resources` actions of the migrator MCP
connector, then paste the prompt below into Amazon Quick's app builder and
replace the placeholder connector/action IDs with your own.

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
2. Paste the prompt below (it references the `preview_migration` and
   `migrate_resources` actions).
3. When prompted, register/select the migrator MCP action connector and wire in
   the preview and migrate action IDs.
4. Watch the agent build the app, then test it live — run **Preview** against a
   source account to scan its agents, connectors, knowledge bases, and spaces,
   then select assets and run **Migrate** to promote them to the target account.
5. Iterate conversationally to refine, then publish and share the app.

## Prompt

```text
-- Build a Quick Resource Migration Tool ("Quick Migrator") — a single-page app that lets admins migrate Chat Agents, Action Connectors, S3 Knowledge Bases, and Spaces from one AWS account to another using an MCP action connector.

═══════════════════════════════════════════
  CONNECTOR DETAILS (MUST CONFIGURE FIRST)
═══════════════════════════════════════════

This app requires an MCP action connector registered in the target account's Quick. The connector exposes two actions:

1. Preview Action (read-only scan / discovery):
   - Action name: preview_migration
   - Input: { source_account_id: string, resource_type: string ("agent"|"connector"|"knowledge_base"|"space"|"all"), search_by: string ("all"|"id"|"name"), value: string, region: string }
   - Output: { result: string } — the result field is a STRINGIFIED JSON inventory of agents, connectors, knowledge_bases, and spaces found in the source account.

2. Migrate Action (write):
   - Action name: migrate_resources
   - Input: { source_account_id, target_account_id, resource_type ("agent"|"connector"|"knowledge_base"|"space"), search_by ("all"|"id"|"name"), value, region, source_env, target_env, qs_service_role }
   - Migrates ONE resource type at a time. Use search_by="id" and value=<resource_id> to migrate a specific resource.
   - Agents are recreated with their Action Connectors attached (remapped to the target account).
   - Connectors are recreated with sanitized (placeholder-secret) auth config and must be re-authenticated in the target UI.
   - Knowledge bases provision the target bucket + data source + KB (documents are NOT copied).
   - Spaces are recreated in the target account with their configuration.
   - Output: { result: string } — stringified JSON migration report.

IMPORTANT: You must register the action connector integration before writing any code that calls it. Register both actions (preview and migrate action IDs).

Constants to define (replace with actual values from your connector):
  MIGRATOR_CONNECTOR = '<your-connector-id>'
  PREVIEW_ACTION = '<your-preview-action-id>'
  MIGRATE_ACTION = '<your-migrate-action-id>'
  HISTORY_TABLE = 'migration-history'
  ACTION_TIMEOUT_MS = 600000  (10 minutes — migrations can be slow)

═══════════════════════════════════════════
  APP STRUCTURE & NAVIGATION
═══════════════════════════════════════════

The app has 3 top-level tabs shown as pill-style buttons centered at the top:
  🚀 Migrate — the main 3-step migration flow
  📜 History — past migration records from Shared App Storage
  

═══════════════════════════════════════════
  MIGRATE TAB — 3-STEP FLOW
═══════════════════════════════════════════

Step 1: Scan Source Account
  - Header card with purple gradient, rocket icon, app title "Quick Migrator", and subtitle "Migrate Agents, Connectors, Knowledge Bases & Spaces between accounts"
  - Two input fields: Source Account ID (12-digit) and Target Account ID (12-digit) — validated as exactly 12 digits, cannot be the same
  - Region field (default: "us-east-1")
  - "🔍 Scan Source Account" button
  - Only scans the SOURCE account (not the target — IAM restrictions prevent target scanning)
  - Calls the preview action with { source_account_id, resource_type: "all", region }
  - Shows a frosted-glass loading overlay with spinner and animated step indicators during scan (LoadingOverlay component)
    - Scan steps: "Connecting to MCP server…", "Scanning source account…", "Building asset inventory…"
    - Migration steps: "Connecting to MCP server…", "Creating resources in target…", "Linking agents ↔ spaces ↔ connectors…", "Generating migration report…"
    - Steps auto-advance every 3 seconds for visual feedback
  - Caches scan results in a useRef map keyed by "source|target|region" — re-scanning same pair loads instantly
  - Shows "⚡ Loaded from cache" indicator and "🔄 Force Rescan" button when cache is hit
  - Persists Source Account ID, Target Account ID, and Region to Private App Storage (table: "user-prefs", key: "account-ids") using putPrivateItem with a 1-second debounce so fields auto-populate on next visit

Step 2: Select Assets (shown after successful scan)
  - Tabbed browser with 4 tabs: 🤖 Agents, 🔗 Connectors, 🧠 Knowledge Bases, 📁 Spaces
  - Each tab shows a count badge with the number of discovered assets
  - ALL items are DESELECTED by default — user must explicitly pick what to migrate
  - Each item row shows: checkbox, type icon, name, monospace ID
  - "Select All" / "Deselect All" buttons per tab
  - Agents and Spaces are expandable — clicking the row expands to show nested linked resources (connectors, knowledge bases, spaces) with individual checkboxes
    - Linked resources extracted from parent's raw data by scanning for embedded arrays (connectors, knowledge_bases, spaces keys)
    - Linked resources that appear as children are "claimed" and filtered out of standalone tab lists to avoid duplication
  - Linked resources show a "X/Y linked" count badge and type labels
  - Children are included by default but can be individually excluded via an "excludedIds" set

Step 3: Migrate (shown when at least 1 asset is selected)
  - Shows selected count badge and source → target account badge
  - Collapsible "▸ Advanced Options" section with:
    - Source Env (default "dev")
    - Target Env (default "prod")
    - Quick Service Role (default "aws-Quick-service-role-v0")
  - "🚀 Start Migration" button opens a confirmation modal (ConfirmModal component)
    - Shows source → target, group count, asset count, and a note about resource linkages
  - On confirm, builds individual migrate calls (one per selected asset, using search_by="id") and executes them SEQUENTIALLY
  - Each call sends: { source_account_id, target_account_id, resource_type, search_by: "id", value: <asset_id>, region, source_env, target_env, qs_service_role }
  - Agent children (linked connectors, KBs, spaces not excluded) are also migrated individually
  - Calls are deduplicated by resource ID
  - Shows LoadingOverlay during migration
  - Results aggregated into: { results: [...], errors: [...], summary: { total, succeeded, failed } }
  - On success, navigates to Results View and saves to migration history via Shared App Storage (table: "migration-history", key: timestamp-source)

═══════════════════════════════════════════
  RESULTS VIEW
═══════════════════════════════════════════

Shown after migration completes:
  - Purple gradient header: "✅ Migration Complete" with succeeded/total count
  - 4 stat cards in a row: 🤖 Agents, 🔗 Connectors, 🧠 KBs, 📁 Spaces — each with count
  - Per-type tables (only shown if items exist): Name | ID | Status columns
    - Status shows green "Created" badge or red "Error/Failed" badge
  - Resource Linkages section (if any): shows type → name → target space cards
  - Errors section (if any): red cards with resource name, type, and error message
  - Collapsible "▸ Show Raw JSON" for full response data
  - "← Start New Migration" button to reset

The extract() function handles two response formats:
  - New format: { results: [...], errors: [...], summary } — merges per-call results
  - Legacy format: single response with migrated/inventory root

═══════════════════════════════════════════
  HISTORY TAB
═══════════════════════════════════════════

  - Loads records from Shared App Storage (table: "migration-history", sortOrder: DESC)
  - Each record is an expandable card showing: source → target, timestamp, region
  - Expanded view shows: Source, Target, Region, resource counts (Spaces, Agents, Connectors, KBs), and collapsible Raw JSON
  - Loading state, empty state ("No migrations yet"), and error state with retry button



═══════════════════════════════════════════
  MCP RESPONSE PARSING (CRITICAL)
═══════════════════════════════════════════

The MCP connector returns responses in a specific format that requires careful unwrapping:

1. unwrapMcpResponse(res):
   - Check for mcpInvokeActionError → extract text content error messages and throw
   - Extract text from mcpInvokeActionOutput.content[].textContent.text
   - Join all text blocks, parse as JSON

2. parseMigrationData(parsed):
   - The API often returns DOUBLE or TRIPLE stringified JSON
   - Implement a recursive deepUnwrap function that tries JSON.parse on every string value at every depth (up to 5 levels)

3. buildScanData(data) — parsing the scan response into per-type asset lists:
   - Use a deepFindArray function that recursively searches the ENTIRE response tree for arrays under known key names (up to 6 levels deep), deduplicating by JSON.stringify
   - For agents: agents, chat_agents, Agents, ChatAgents, agent_list, agentList, applications, Applications
   - For connectors: connectors, Connectors, action_connectors, ActionConnectors, connector_list, connectorList
   - For KBs: knowledge_bases, KnowledgeBases, knowledgeBases, kb_list, kbList
   - For spaces: spaces, Spaces, space_list, spaceList, linked_spaces

4. Name extraction priority: name, Name, agent_name, AgentName, connector_name, ConnectorName, title, Title, displayName, display_name, label, kb_name, space_name, SpaceName — then any short non-ARN non-ID string field
5. ID extraction priority: id, Id, ID, agent_id, agentId, connector_id, connectorId, kb_id, kbId, space_id, spaceId

═══════════════════════════════════════════
  COMPONENT STRUCTURE
═══════════════════════════════════════════

Keep modular — extract into separate component files:
  - App.tsx — thin layout shell with tab navigation and 3-step flow orchestration
  - migrationTypes.ts — shared types (Asset, ScanData, TabKey, MigrateCall), constants (connector/action IDs, timeouts), helpers (buildScanData, buildMigrateCalls, unwrapMcpResponse, parseMigrationData, withTimeout), and style tokens (FONT, GRAD, BG)
  - AssetSelector.tsx — tabbed asset browser with select/deselect, expand/collapse for agents & spaces
  - LoadingOverlay.tsx — frosted-glass full-screen overlay with spinner and animated steps
  - ConfirmModal.tsx — confirmation dialog before migration
  - ResultsView.tsx — migration results display with stat cards, tables, linkages, errors
  - HistoryView.tsx — migration history list from Shared App Storage
  - BlueprintTab.tsx — this prompt, copyable

═══════════════════════════════════════════
  DESIGN & STYLING
═══════════════════════════════════════════

- Purple gradient theme: linear-gradient(135deg, #667eea 0%, #764ba2 100%)
- Background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%)
- Font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif
- ALL inline styles — no external CSS files
- Rounded cards (12–20px border-radius), soft shadows (rgba(102,126,234,0.08))
- Max width: 760px for the migrate flow, 880px for results/history
- Tab buttons: pill-style (borderRadius: 30), active = gradient bg + white text, inactive = transparent + purple text
- Step circles: 28px round gradient badges with white number
- Input fields: 1.5px solid #e2e8f0 border, borderRadius 10
- Status badges: green (#dcfce7/#166534) for success, red (#fef2f2/#991b1b) for errors
- Selected asset cards: purple inset border shadow (inset 0 0 0 2px #667eea)

═══════════════════════════════════════════
  KEY CONSTRAINTS & NOTES
═══════════════════════════════════════════

- Do NOT scan the target account — only scan the source.
- Resource types: agent, connector, knowledge_base, space.
- The migrate action uses resource_type/search_by/value parameters, NOT a resources JSON blob.
- Multiple assets are migrated via sequential calls (one call per asset with search_by="id").
- All assets are deselected by default after scan.
- Timeout is set to 600000ms (10 minutes) — wrap all MCP calls with a withTimeout helper.
- Do not use <form> elements — use div and button onClick handlers.
- Wrap quickSuiteClient calls in try-catch; if QuickIntegrationError, render error message as-is.
- Wrap App Storage calls in try-catch; if PageStorageError, render error message as-is.
- Account IDs persisted to Private App Storage (table: "user-prefs", key: "account-ids") with debounced saves.
- Migration history saved to Shared App Storage (table: "migration-history") so all team members can see past migrations.
💡 Tip: After pasting the prompt, the builder will ask you to register the MCP action connector in
```
