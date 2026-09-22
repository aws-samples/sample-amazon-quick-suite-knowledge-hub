# Building a Quick App on the Migrator MCP Connector

This document is the build specification you hand to **Amazon Quick** so it can
generate a web app on top of the migrator MCP connector. You do **not** write
the front-end yourself — you register the connectors described below, then give
Quick these instructions and it produces the app.

The connector and action IDs below are shown as placeholders
(`<MIGRATOR_MCP_CONNECTOR_ID>`, `<PREVIEW_ACTION_ID>`, `<MIGRATE_ACTION_ID>`,
`<SLACK_CONNECTOR_ID>`). Replace them with the IDs from your own registered
connectors. See the main [README](../index.md#integrating-with-amazon-quick)
for how to register the MCP connector against the AgentCore runtime.

> **Resource-driven model.** The migrator promotes individual resources — a
> **resource type** (`agent`, `connector`, or `knowledge_base`) selected by
> **id**, by **name**, or **all**. Spaces are **not** created or linked, and no
> resource-to-Space linkages are produced. The tool names are
> `preview_migration` and `migrate_resources`.

## Overview

A web app that migrates AWS Quick Suite resources (Chat Agents, Action
Connectors, and S3 Knowledge Bases) across AWS accounts using an MCP connector,
with Slack notifications and migration history persistence. Selection is
resource-driven: pick a resource type and select by id, by name, or all. Spaces
are not part of the model.

## Prerequisites / Integrations to Register

Before writing any code, register these two ACTION integrations:

### 1. Migration MCP Connector
Integration Key: <MIGRATOR_MCP_CONNECTOR_ID>
Integration Type: ACTION
Action IDs: ["<PREVIEW_ACTION_ID>", "<MIGRATE_ACTION_ID>"]
Description: "Invoke Quick Suite resource migration actions — preview_migration (scan) and migrate_resources across accounts"
Permission Level: WRITE (because migrate creates resources)
### 2. Slack Connector
Integration Key: <SLACK_CONNECTOR_ID>
Integration Type: ACTION
Action IDs: ["ChatPostMessage"]
Description: "Send Slack notification to channel after migration completes"
Permission Level: WRITE
Architecture
webapp/src/
├── App.tsx                         (Main orchestrator — state management, API calls, tab routing)
├── main.tsx                        (Entry point — DO NOT MODIFY)
└── components/
    ├── MigrationForm.tsx           (Input form with source/target accounts, resource type, selection, region, advanced options)
    ├── MigrationResults.tsx        (Results display: stat cards, resource tables, raw JSON viewer)
    ├── LoadingScreen.tsx           (Animated loading indicator with step progress)
    ├── ConfirmationModal.tsx       (Modal to confirm migration before executing)
    └── MigrationHistory.tsx        (History tab — loads past migrations from shared App Storage)
Prompt / Instructions to Reproduce
Build a Quick Migrator web app with the following functionality:

Core Features:
Migration Form (MigrationForm.tsx)
Fields: Source Account ID (12-digit), Target Account ID (12-digit), Resource Type (agent | connector | knowledge_base; for preview also "all"), Search By (id | name | all, default "all"), Value (the id or name to match; required when Search By is id or name, ignored when "all"), Region (default "us-east-1")
Collapsible "Advanced Options" section with: Source Environment (default "dev"), Target Environment (default "prod"), QuickSight Service Role (default "aws-quicksight-service-role-v0")
Two buttons: "Preview" (requires only source) and "Start Migration" (requires source + target + a concrete resource type)
Info cards at the bottom showing what gets migrated: Chat Agents, Action Connectors, Knowledge Bases
Clean purple gradient design theme
Preview Action — Calls the MCP connector to scan the source account and display an inventory of resources found without making any changes
Connector ID: <MIGRATOR_MCP_CONNECTOR_ID>
Action ID: <PREVIEW_ACTION_ID>
Arguments: { source_account_id, resource_type, search_by, value, region }
Migrate Action — After user confirms via modal, calls the migrate action to create resources in the target account
Action ID: <MIGRATE_ACTION_ID>
Arguments: { source_account_id, target_account_id, resource_type, search_by, value, region, source_env, target_env, qs_service_role }
Confirmation Modal (ConfirmationModal.tsx)
Shows source → target accounts, resource type, selection (search_by + value), and region
Note: "Agents are recreated with their Action Connectors attached (remapped to the target account) but with no Space attachment. Connectors carry placeholder secrets and must be re-authenticated in the target."
No Space or "manual linkage" language anywhere
Loading Screen (LoadingScreen.tsx)
Spinning animation with step indicators (Connecting to MCP server → Resolving/Scanning resources → Creating/Building inventory)
Different messaging for preview vs. migrate
Results Display (MigrationResults.tsx)
Stat cards showing counts: Chat Agents, Action Connectors, Knowledge Bases, S3 Buckets
Resource tables with columns: Name, ID, Status (for agents/connectors/KBs)
Bucket table with columns: Bucket Name, Environment, Status
Skipped Permissions section — renders the skipped_permissions block from the response (principals that could not be resolved to a registered user in the target account), as cards or a table: Resource, Principal, Reason
No Space linkages: the migrator does not create or report resource-to-Space relationships, so there is no linkages section
Copy All button for the skipped-permissions list
Collapsible Raw JSON viewer (always available via Show/Hide toggle)
"← New Migration" button to reset
Preview banner when showing preview results
Slack Notification — Automatically sent after successful migration
Connector ID: <SLACK_CONNECTOR_ID>
Action: ChatPostMessage
Channel: <your-slack-channel>
Message: Markdown formatted with source/target accounts, region, resource type, and migrated resources (agents, connectors, KBs), plus any skipped permissions
Status indicator in results header (sending/sent/failed)
Migration History (MigrationHistory.tsx)
Tab-based navigation: "🚀 Migrate" and "📜 History"
Stores each migration in shared App Storage (table: migration-history)
Each record includes: timestamp, source, target, region, resource type, search_by, value, all resource arrays, skipped permissions, slack notification status
Expandable cards showing full details per migration
Empty state, loading state, error state with retry
Technical Requirements:
Timeout wrapper: All invokeAction calls wrapped in a 2-minute (120000ms) timeout using a withTimeout helper that throws a descriptive error if exceeded
MCP response unwrapping: A unwrapMcpResponse function that:
Checks for mcpInvokeActionError envelope and extracts text content error messages
Extracts text from mcpInvokeActionOutput.content[].textContent.text
Throws on empty response
Parses JSON and throws descriptive error on parse failure (shows first 300 chars of raw)
Data parsing: A parseMigrationData function that handles nested result strings (sometimes the API returns { result: "JSON string" }), checks multiple locations for resource data (migrated, inventory, or root)
Error handling: Catch QuickIntegrationError and display message as-is; other errors show generic message
Storage errors: Catch PageStorageError for history operations
All resource name fields are extracted with fallback chains (e.g., a.name || a.agent_id, c.name || c.connector_id, k.name || k.knowledge_base_id)
Bucket fields: Use bucket for name, env for environment
No Space or manual-linkage language anywhere in the app
App Storage: Use putSharedItem / listSharedItems with table name migration-history
Design / Styling:
Purple gradient theme (#667eea → #764ba2)
Font: system font stack (-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, ...)
Light background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%)
Rounded cards (12–20px radius), soft shadows
All inline styles (no external CSS files)
Responsive with max-widths: form 680px, results 860px, loading 500px
Tab bar with pill-style active indicator
Status badges with colored backgrounds (green for success, amber for sending, red for errors)
Key Constants
const MIGRATOR_CONNECTOR = '<MIGRATOR_MCP_CONNECTOR_ID>';
const PREVIEW_ACTION = '<PREVIEW_ACTION_ID>';
const MIGRATE_ACTION = '<MIGRATE_ACTION_ID>';
const SLACK_CONNECTOR = '<SLACK_CONNECTOR_ID>';
const HISTORY_TABLE = 'migration-history';
const ACTION_TIMEOUT_MS = 120000;
Imports Required
// App.tsx
import { quickSuiteClient, QuickIntegrationError, putSharedItem, PageStorageError } from '@amzn/quick-pages-runtime-lib';

// MigrationHistory.tsx
import { listSharedItems, PageStorageError } from '@amzn/quick-pages-runtime-lib';
API Call Patterns
MCP Invoke (Preview & Migrate)
quickSuiteClient.invokeAction(MIGRATOR_CONNECTOR, {
  ActionId: ACTION_ID,
  InvokeActionInput: {
    mcpInvokeActionInput: {
      arguments: JSON.stringify({ /* params */ }),
    },
  },
});
// Preview arguments:  { source_account_id, resource_type, search_by, value, region }
// Migrate arguments:  { source_account_id, target_account_id, resource_type, search_by, value, region, source_env, target_env, qs_service_role }
Slack Notification
quickSuiteClient.invokeAction(SLACK_CONNECTOR, {
  ActionId: 'ChatPostMessage',
  InvokeActionInput: {
    mcpInvokeActionInput: {
      arguments: JSON.stringify({
        channel: '<your-slack-channel>',
        markdown_text: message,
      }),
    },
  },
});
State Flow
User fills form → clicks Preview → Loading screen → Results (isPreview=true)
User fills form → clicks Start Migration → Confirmation Modal → Confirm → Loading screen → Results (isPreview=false) → Auto Slack notification → Save to history
User clicks ← New Migration → Back to form
User clicks 📜 History tab → Loads from App Storage → Expandable cards
