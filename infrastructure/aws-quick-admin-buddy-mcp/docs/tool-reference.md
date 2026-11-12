# Tool Reference

The Quick Admin Buddy MCP server exposes **twelve** tools over the Model Context
Protocol (MCP). They create and share two kinds of AWS Quick integrations,
**action connectors** ([`CreateActionConnector`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateActionConnector.html)) and **knowledge bases**
([`CreateDataSource`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateDataSource.html) + [`CreateKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateKnowledgeBase.html)), and track the client-side AWS
Quick **extensions**, which have no admin API.

| Tool | Purpose |
|---|---|
| [`list_connector_catalog`](#list_connector_catalog) | List the connector catalog, optionally filtered by suite. |
| [`set_productivity_suite`](#set_productivity_suite) | Record the tenant's productivity suite. |
| [`enable_action_connector`](#enable_action_connector) | Create an action connector for a catalog connector. |
| [`disable_action_connector`](#disable_action_connector) | Delete an action connector the server created. |
| [`enable_kb_integration`](#enable_kb_integration) | Create a knowledge base (data source + knowledge base). |
| [`disable_kb_integration`](#disable_kb_integration) | Delete a knowledge base and its data source. |
| [`share_action_connector`](#share_action_connector) | Share an existing action connector with a user or group. |
| [`share_kb_integration`](#share_kb_integration) | Give a user or group read access to an existing knowledge base. |
| [`get_tenant_configuration`](#get_tenant_configuration) | Return the stored suite and every connector's status. |
| [`get_onboarding_summary`](#get_onboarding_summary) | Return a stage-grouped recap of the onboarding. |
| [`list_extensions`](#list_extensions) | List the extensions and their admin-attested status. |
| [`set_extension_status`](#set_extension_status) | Record an extension's admin-attested status. |

Each tool returns a structured **Tool_Result**, a JSON object with three fields:

| Field | Type | Meaning |
|---|---|---|
| `status` | string | The `Result_Status` (see below). Branch on this. |
| `message` | string | A confirmation (for `success`/`already_configured`) or an actionable cause-and-remedy description (all other statuses). Always non-empty. |
| `data` | object | Status-specific payload. Never contains raw credential values or internal stack detail. |

## Tenant identity

None of the tools accept a tenant argument. This deployment maps every
authenticated caller to a **single shared tenant**, resolved from the
`ONBOARDING_TENANT` environment variable (set from the `TenantId` template
parameter, default `shared`). The AgentCore Gateway's Cognito CUSTOM_JWT
authorizer gates access (a valid Quick admin token is required to call the
server), but the caller's identity is not propagated to the backend — so there
is exactly one tenant and no tool can select or spoof another. See
[auth-model.md](./auth-model.md).

## Result_Status values

Every Tool_Result carries exactly one of these seven statuses:

| Result_Status | Meaning |
|---|---|
| `success` | The operation completed. |
| `already_configured` | The requested state was already in effect; the operation was an idempotent no-op. |
| `validation_failure` | The input was rejected (bad suite value, unknown connector key, missing required fields, suite gate mismatch, or suite not set). No state change. |
| `suite_conflict` | Switching the productivity suite would invalidate one or more enabled connectors. No state change. |
| `authorization_required` | The connector needs a third-party (OAuth-style) authorization grant that has not been completed. The authorization URL is returned verbatim. |
| `server_error` | A failure **returned by** a Quick Admin API action, or a state-persistence failure after a successful API call. Actionable message; credentials and stack detail stripped. |
| `internal_error` | A failure **within** the MCP server itself — including network failures, timeouts, and IAM authentication failures. No fallback credentials are attempted. |

When the failure is the backend's own state store (the Onboarding_State DynamoDB
table is missing, not accessible to the execution role, its KMS key is
unusable, or it is unreachable), the result also carries
`data.state_store_error`:

```json
{
  "error_type": "state_store_unavailable",
  "reason": "table_not_found",
  "table_name": "OnboardingState",
  "region": "us-east-1",
  "error_code": "ResourceNotFoundException",
  "operation": "read",
  "retryable": false
}
```

`reason` is one of `table_not_found`, `access_denied`,
`encryption_key_unavailable` or `unavailable`. The status is `internal_error`
when nothing was changed, or `server_error` when a Quick resource was already
created/deleted and only recording it failed (the `message` says which). When
`retryable` is `false`, retrying cannot succeed: the backend infrastructure (the
CloudFormation stack's table, IAM policies or KMS key) must be reviewed first.

### Missing users in `share_with` and `owner`

When `share_with` (action connectors, `share_*` tools) or `owner` (knowledge
bases) names a Quick user that does not exist, the server returns
`validation_failure` before creating or changing anything, with
`data.error_type = "principal_not_found"`, `data.invalid_principal` and
`data.principal_field` (`share_with` or `owner`). The most common cause is
passing the user's **email instead of the AWS Quick username** (the two are
often different; user names are case-sensitive). When the value looks like an
email, the message suggests the part before the `@` as a possible username.

The server checks with [`DescribeUser`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DescribeUser.html). Group ARNs, users in another account, or
a lookup that is not permitted (for example a stack that predates the
[`DescribeUser`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DescribeUser.html) grant) are not checked, and the request proceeds to AWS Quick
as before.

---

## `list_connector_catalog`

List the connector catalog, optionally filtered by productivity suite. Needs no
tenant and touches no state.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `suite` | string | optional | Productivity suite filter: `google_workspace` or `microsoft_365`. Omit to return the full catalog. |

**Success payload (`data`)**

- No filter: `{ "connectors": [ { "key", "display_name", "category", "suite_gate" }, ... ] }` — all 14 entries.
- With a valid filter: `{ "suite": "<suite>", "connectors": [ ... ] }` — entries whose `suite_gate` equals the filter **or** is `any`.

**Result_Status values:**

| Status | When |
|---|---|
| `success` | Catalog (full or filtered) returned. |
| `validation_failure` | `suite` is neither `google_workspace` nor `microsoft_365`. `data` carries `invalid_suite` and `valid_suites`. |

---

## `set_productivity_suite`

Record the tenant's productivity suite, which gates all subsequent connector
operations.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `suite` | string | required | `google_workspace` or `microsoft_365`. |

**Success payload (`data`)**

- First set: `{ "suite": "<suite>" }`.
- Safe switch (no enabled connectors gated to the old suite): `{ "suite": "<new>", "previous_suite": "<old>" }`.

**Result_Status values:**

| Status | When | `data` |
|---|---|---|
| `success` | Suite persisted (first set, or a safe switch). | `suite` (and `previous_suite` on a switch) |
| `already_configured` | The same suite is already stored. | `suite` |
| `validation_failure` | Invalid suite value. | `invalid_suite`, `valid_suites` |
| `suite_conflict` | A different suite requested while connectors gated to the stored suite are enabled. State unchanged. | `current_suite`, `requested_suite`, `conflicting_connectors` |

`any`-gated connectors survive a suite switch and do not cause a conflict.

---

## Common inputs for the integration tools

These inputs work the same way on every enable, disable, and share tool.

**`connector_key`** is a key from the [connector catalog](#connector-catalog-14-entries),
for example `outlook` or `sharepoint`.

**`connector_id_prefix`** (optional) sets the prefix of the AWS Quick
resource id. The id is `<prefix><connector_key>`, and the default prefix is
`quickadminbuddy-`, so the default Outlook action connector id is
`quickadminbuddy-outlook`. The prefix may contain only letters, digits,
underscores, and hyphens (`[\w\-]+`); anything else returns
`validation_failure` with `data.invalid_connector_id_prefix`. Pass the **same
prefix** to the disable and share tools, because they derive the resource id
the same way.

**`secret_id`** (optional) is the **name** of a Secrets Manager secret, for
example `quick-admin-buddy/outlook-custom-oauth`. Never pass a full
`arn:aws:secretsmanager:...` ARN: AWS Quick redacts ARN-shaped tool
arguments, so the server rejects an ARN with `validation_failure`. If you only
have the ARN, pass the part after `:secret:`, including the trailing `-xxxxxx`
suffix. When the server needs the ARN (OneDrive knowledge bases), it resolves
it from the name with [`DescribeSecret`](https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_DescribeSecret.html), which returns the ARN without the
secret value. The runtime role can read only secrets named
`quick-admin-buddy/*`.

**Secret values come only from Secrets Manager.** A secret value passed inline
in `auth_params` or `config` is rejected before anything else is checked, with
`validation_failure`, `data.error_type: "inline_secret_not_allowed"`, and
`data.inline_secret_params` (the rejected key names, never their values). A key
counts as secret-bearing when it is `client_secret`, `api_key`, `private_key`,
`password`, `access_token`, `refresh_token`, `session_token`, or
`service_credentials_ref`, or contains `secret`, `password`, or `private_key`.
Credentials passed in the chat reach the model context before the server sees
them, so the server doesn't accept them at all.

**`auth_params`** (optional) carries non-secret authentication parameters
inline, as a flat object with snake_case keys (for example `client_id` or
`domain_url`). When both `secret_id` and `auth_params` are given, the server
loads the secret first and then applies `auth_params` on top, so an inline value
overrides the same key in the secret. An `auth_type` key in the secret body is
ignored.

**Principals** (`share_with`, `owner`) are either a bare AWS Quick user name,
which the server expands to a user in the `default` namespace of the account
and Region the Runtime runs in, or a full Quick user or group ARN, which is
used unchanged. Use a full ARN for a group or a non-default namespace. Pass
the AWS Quick **username**, not the user's email address; a user that does
not exist is rejected before anything is created (see
[Missing users](#missing-users-in-share_with-and-owner)).

Every result of these tools carries `data.connector_key` and
`data.integration_type` (`action_connector` or `knowledge_base`). Credential
values are never returned: secret-looking values are redacted from every
`message` and `data`.

---

## `enable_action_connector`

Create an AWS Quick **action connector** for a catalog connector
([`CreateActionConnector`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateActionConnector.html)) and record it as `enabled`.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `connector_key` | string | required | Catalog key. The connector must support action connectors (all 14 do). |
| `auth_type` | string | required | `custom_oauth_app`, `service_to_service_oauth`, or `api_key`. See the table below. |
| `secret_id` | string | required in practice | Name of the Secrets Manager secret that holds the auth parameters. Every action connector auth type needs a secret value (`client_secret` or `api_key`), which can come only from this secret. |
| `auth_params` | object | optional | Non-secret auth parameters inline, overriding the same keys in the secret. Secret-bearing keys are rejected. |
| `config` | object | optional | Connector settings. `name` and `description` override the connector's display name (default: the connector key) and description. |
| `share_with` | string | optional | Principal to grant owner-equivalent access to the new connector. Without it, only the server's execution role can see the connector. |
| `connector_id_prefix` | string | optional | Resource id prefix (default `quickadminbuddy-`). |

Required auth parameters per `auth_type`:

| `auth_type` | Flow | Required parameters |
|---|---|---|
| `custom_oauth_app` | Three-legged OAuth (3LO) with your own OAuth app; per-user delegated access. | `client_id`, `client_secret`, `domain_url`, `authorization_url`, `token_url`, `redirect_url` |
| `service_to_service_oauth` | Two-legged client-credentials OAuth; actions run as a service identity. | `client_id`, `client_secret`, `domain_url`, `token_url` |
| `api_key` | Two-legged API token, for example Atlassian Confluence Cloud. | `api_key`, `domain_url` (optional `email`, the Atlassian account email) |

The secret is a JSON object with these keys, for example:

```json
{
  "client_id": "<client_id>",
  "client_secret": "<client_secret>",
  "domain_url": "<domain_url>",
  "token_url": "<token_url>"
}
```

**Success payload (`data`):** `connector_key`, `integration_type`.

**Result_Status values** (checks run in this order; the first failure wins):

| Status | When | Extra `data` |
|---|---|---|
| `validation_failure` | `secret_id` is an ARN. | — |
| `validation_failure` | A secret value was passed inline in `auth_params` or `config`. Nothing is read or created. | `error_type` (`inline_secret_not_allowed`), `inline_secret_params` |
| `validation_failure` | No productivity suite has been set yet. | — |
| `validation_failure` | Unknown connector key. | — |
| `validation_failure` | Invalid `connector_id_prefix`. | `invalid_connector_id_prefix` |
| `validation_failure` | The connector is gated to the other suite. | `suite_gate`, `stored_suite` |
| `validation_failure` | `auth_type` is missing or not valid for an action connector. | `invalid_auth_type`, `valid_auth_types` |
| `validation_failure` | The referenced secret does not exist or is not a JSON object. | `secret_id` |
| `internal_error` | The secret could not be read (for example, access denied). | — |
| `validation_failure` | Required auth parameters are missing. | `auth_type`, `missing_params`, `missing_config_fields`, `required_params`, `secret_contract` |
| `already_configured` | This action connector is already `enabled` in the onboarding state. No API call. | — |
| `validation_failure` | `share_with` names a Quick user that does not exist. Nothing is created. | `error_type` (`principal_not_found`), `invalid_principal`, `principal_field` |
| `server_error` | [`CreateActionConnector`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateActionConnector.html) failed. The sanitized API error is in `message`. | — |
| `internal_error` | Network, timeout, or IAM failure calling AWS Quick. | — |
| `server_error` | The connector was created but its `enabled` status could not be recorded. | `state_store_error` when the state table is the cause |
| `success` | Connector created and recorded `enabled`. | — |

When the onboarding state table can't be read at the start, the result is
`internal_error` with `data.state_store_error` and nothing is created.

---

## `disable_action_connector`

Delete an action connector the server created ([`DeleteActionConnector`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DeleteActionConnector.html)) and
record it as `not_configured`.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `connector_key` | string | required | Catalog key. |
| `connector_id_prefix` | string | optional | The prefix used when the connector was enabled (default `quickadminbuddy-`). |

**Success payload (`data`):** `connector_key`, `integration_type`.

**Result_Status values** (in check order):

| Status | When | Extra `data` |
|---|---|---|
| `validation_failure` | Unknown connector key, or invalid `connector_id_prefix`. | `invalid_connector_id_prefix` for a bad prefix |
| `internal_error` | The onboarding state could not be read. Nothing was deleted. | `state_store_error` |
| `already_configured` | The action connector is not `enabled` in the onboarding state (never enabled, or already disabled). No API call. | — |
| `server_error` | [`DeleteActionConnector`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DeleteActionConnector.html) failed, including when the connector no longer exists in AWS Quick. | — |
| `internal_error` | Network, timeout, or IAM failure calling AWS Quick. | — |
| `server_error` | The connector was deleted but its `not_configured` status could not be recorded. | `state_store_error` when the state table is the cause |
| `success` | Connector deleted and recorded `not_configured`. | — |

The tool only deletes connectors this server recorded as `enabled`. It does not
delete a connector created in the console or by another deployment.

---

## `enable_kb_integration`

Create an AWS Quick **knowledge base** for a catalog connector and record it
as `enabled`. A knowledge base is created in two steps: [`CreateDataSource`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateDataSource.html) (the
source connection and service credentials), then [`CreateKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateKnowledgeBase.html), which
references the new data source by ARN. Both resources get the id
`<prefix><connector_key>`.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `connector_key` | string | required | Catalog key of a connector whose knowledge base the API can create: `onedrive`, `google_drive`, or `sharepoint`. `confluence` is rejected; see the note below the table. |
| `auth_type` | string | required | `admin_managed` (service credentials; document-level ACLs enforced) or `user_managed` (no credentials; ACLs off). The OAuth app methods of action connectors are not valid here. |
| `owner` | string | required | Principal who owns the knowledge base. AWS Quick grants it the OWNER role at creation. Without an owner the knowledge base would be visible only to the server's execution role, so the server rejects the call. |
| `secret_id` | string | depends | Name of the Secrets Manager secret with the source's service credentials. See the table below. |
| `auth_params` | object | optional | Non-secret fields inline, overriding the same keys from the secret. Secret-bearing keys are rejected. |
| `config` | object | depends | Source-connection settings, plus optional `name` and `description`. See the table below. |
| `connector_id_prefix` | string | optional | Resource id prefix (default `quickadminbuddy-`). |
| `share_with` | string | optional | **Ignored** for knowledge bases. Use [`share_kb_integration`](#share_kb_integration) after creation. |

`admin_managed` requirements per connector:

| Connector | Secret | Secret JSON keys | Required `config` | Sent to AWS Quick as |
|---|---|---|---|---|
| `sharepoint` | Required. Defaults to `quick-admin-buddy/sharepoint-kb-admin` when `secret_id` is omitted. | `certificate_thumbprint`, `kms_key_arn`, `tenant_id`, `client_id`, `domain_url` | `sites` (list of site URLs to index) | `Credentials.SharePointClientCredentials` (inline) plus `SharePointParameters` |
| `onedrive` | Required. No default name. | `client_id`, `tenant_id`, `kms_key_arn`, `certificate_thumbprint` | — | `Credentials.SecretArn` (the server resolves the name to its ARN) |
| `google_drive` | Required. Defaults to `quick-admin-buddy/google-drive-kb-admin` when `secret_id` is omitted. | The downloaded Google service-account JSON (`client_email`, `private_key`, …) plus an added `admin_account_email` | — | `Credentials.ServiceAccountCredentials` (inline) |

A Confluence knowledge base can only be created in the AWS Quick console.
`CreateKnowledgeBase` supports `S3_KNOWLEDGE_BASE`, `WEB_CRAWLER`,
`GOOGLE_DRIVE`, `SHAREPOINT`, and `ONE_DRIVE` only, and `DataSourceCredentials`
has no member for Confluence's Atlassian admin credentials. The server returns
`validation_failure` with `data.error_type: "console_only_knowledge_base"` for
`confluence` before reading any secret or calling the API.

The certificate-based connectors (SharePoint, OneDrive) can't take their
credentials fully inline: the KMS signing-key ARN and certificate thumbprint
must live in the secret. Grant the AWS Quick service role access to the
secret and `kms:Sign` on the signing key.

`user_managed` needs no credentials at the backend. The server creates the
knowledge base with document-level ACLs off; each user's provider sign-in
happens in the AWS Quick console.

**Success payload (`data`):** `connector_key`, `integration_type`.

**Result_Status values** (checks run in this order; the first failure wins):

| Status | When | Extra `data` |
|---|---|---|
| `validation_failure` | `secret_id` is an ARN. | — |
| `validation_failure` | A secret value was passed inline in `auth_params` or `config`. Nothing is read or created. | `error_type` (`inline_secret_not_allowed`), `inline_secret_params` |
| `validation_failure` | No productivity suite has been set yet. | — |
| `validation_failure` | Unknown connector key, or the connector doesn't support knowledge bases. | — |
| `validation_failure` | The connector's knowledge base can only be created in the AWS Quick console (`confluence`). Nothing is read or created. | `error_type` (`console_only_knowledge_base`) |
| `validation_failure` | Invalid `connector_id_prefix`. | `invalid_connector_id_prefix` |
| `validation_failure` | The connector is gated to the other suite. | `suite_gate`, `stored_suite` |
| `validation_failure` | `auth_type` is missing or not valid for a knowledge base. | `invalid_auth_type`, `valid_auth_types` (`user_managed`, `admin_managed`) |
| `validation_failure` | The referenced (or default) secret does not exist or is not a JSON object. | `secret_id` |
| `internal_error` | The secret could not be read (for example, access denied). | — |
| `validation_failure` | Required `config` fields are missing. | `auth_type`, `missing_params`, `missing_config_fields`, `required_params`, `secret_contract` |
| `validation_failure` | OneDrive `admin_managed` without a `secret_id`. | `auth_type`, `required_secret_schema` |
| `validation_failure` | SharePoint or Google Drive `admin_managed`, and the secret lacks required keys. | `auth_type`, `secret_id`, `missing_secret_fields`, `required_secret_schema` |
| `validation_failure` | `owner` is missing. | — |
| `already_configured` | This knowledge base is already `enabled` in the onboarding state. No API call. | — |
| `validation_failure` | `owner` names a Quick user that does not exist. Nothing is created. | `error_type` (`principal_not_found`), `invalid_principal`, `principal_field` |
| `validation_failure` | OneDrive: the secret name could not be resolved to an ARN. | `secret_id` |
| `server_error` | [`CreateDataSource`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateDataSource.html) failed. Nothing was created, so nothing is rolled back. | — |
| `internal_error` | Network, timeout, or IAM failure calling [`CreateDataSource`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateDataSource.html). | — |
| `server_error` | [`CreateKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateKnowledgeBase.html) failed. The data source is rolled back. | `rollback` |
| `internal_error` | Network, timeout, or IAM failure calling [`CreateKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateKnowledgeBase.html). The knowledge base (which may exist) and the data source are rolled back. | `rollback` |
| `internal_error` | Both resources were created, but the `enabled` status could not be recorded, so both were rolled back completely. | `rollback`, plus `state_store_error` when the state table is the cause |
| `server_error` | As above, but the rollback was incomplete. | `rollback`, plus `state_store_error` when the state table is the cause |
| `success` | Data source and knowledge base created, and recorded `enabled`. | — |

**Rollback.** A failed enable leaves nothing behind in AWS Quick: once
[`CreateDataSource`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateDataSource.html) has succeeded, any later failure deletes what was created
(the knowledge base first, when it may exist, then the data source). A
resource that is already missing counts as deleted. `data.rollback` reports the
outcome:

| Key | Meaning |
|---|---|
| `complete` | `true` when everything created was deleted. |
| `data_source_id` | Id of the data source, `<prefix><connector_key>`. |
| `data_source_deleted` | Whether the data source is gone. |
| `knowledge_base_id`, `knowledge_base_deleted` | Present only when the knowledge base may have been created. |

When `complete` is `false`, the `message` names the resources to delete in
AWS Quick before you retry; otherwise the retry fails because the id is
already taken. A [`CreateDataSource`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_CreateDataSource.html) failure is never rolled back, because a
same-id data source that already existed is not the server's to delete.

---

## `disable_kb_integration`

Delete a knowledge base the server created and its data source
([`DeleteKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DeleteKnowledgeBase.html), then [`DeleteDataSource`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DeleteDataSource.html)), and record it as
`not_configured`.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `connector_key` | string | required | Catalog key of a connector that supports knowledge bases. |
| `connector_id_prefix` | string | optional | The prefix used when the knowledge base was enabled (default `quickadminbuddy-`). |

**Success payload (`data`):** `connector_key`, `integration_type`.

**Result_Status values** (in check order):

| Status | When | Extra `data` |
|---|---|---|
| `validation_failure` | Unknown connector key, the connector doesn't support knowledge bases, or invalid `connector_id_prefix`. | `invalid_connector_id_prefix` for a bad prefix |
| `internal_error` | The onboarding state could not be read. Nothing was deleted. | `state_store_error` |
| `already_configured` | The knowledge base is not `enabled` in the onboarding state. No API call. | — |
| `server_error` | [`DeleteKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DeleteKnowledgeBase.html) failed (other than "not found"). Nothing was deleted and the state stays `enabled`. | — |
| `internal_error` | Network, timeout, or IAM failure calling [`DeleteKnowledgeBase`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DeleteKnowledgeBase.html). The state stays `enabled`. | — |
| `server_error` | The knowledge base was deleted but its `not_configured` status could not be recorded. | `state_store_error` when the state table is the cause |
| `server_error` | The knowledge base was deleted and recorded `not_configured`, but [`DeleteDataSource`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_DeleteDataSource.html) failed. | `data_source_id`, `data_source_deleted` (`false`) |
| `success` | Knowledge base and data source deleted, and recorded `not_configured`. | — |

**State after a delete.** As soon as the knowledge base is deleted, or is
already missing in AWS Quick, the integration is recorded as
`not_configured`, even if the data source delete then fails. A data source
that is already missing counts as deleted. When `data.data_source_deleted` is
`false`, the knowledge base is disabled (a retry returns `already_configured`),
but delete the data source `data.data_source_id` in AWS Quick before you
enable the knowledge base again; otherwise that enable fails because the id is
taken.

---

## `share_action_connector`

Grant a principal owner-equivalent permissions (see, describe, update, delete,
list, and manage permissions) on an existing action connector
([`UpdateActionConnectorPermissions`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_UpdateActionConnectorPermissions.html)). Use it to share a connector that was
enabled without `share_with`, or to add another user or group later.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `connector_key` | string | required | Catalog key. |
| `share_with` | string | required | User name or user/group ARN to share with. |
| `connector_id_prefix` | string | optional | The prefix used when the connector was created (default `quickadminbuddy-`). |

The connector does not have to be `enabled` in the onboarding state. AWS
Quick is the source of truth: if the connector doesn't exist, the API call
fails and the tool returns `server_error`.

**Success payload (`data`):** `connector_key`, `integration_type`, `shared_with`.

**Result_Status values:**

| Status | When | Extra `data` |
|---|---|---|
| `success` | Permissions granted. | `shared_with` |
| `validation_failure` | Unknown connector key, empty `share_with`, or invalid `connector_id_prefix`. | `invalid_connector_id_prefix` for a bad prefix |
| `validation_failure` | `share_with` names a Quick user that does not exist. Nothing is changed. | `error_type` (`principal_not_found`), `invalid_principal`, `principal_field` |
| `server_error` | [`UpdateActionConnectorPermissions`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_UpdateActionConnectorPermissions.html) failed (for example, the connector or principal doesn't exist). | — |
| `internal_error` | Network, timeout, or IAM failure calling AWS Quick. | — |

---

## `share_kb_integration`

Grant a principal the **VIEWER** role on an existing knowledge base
([`UpdateKnowledgeBasePermissions`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_UpdateKnowledgeBasePermissions.html)). AWS Quick only accepts the fixed OWNER
and VIEWER roles for knowledge bases, so sharing grants read access; full
management stays with the `owner` set at creation.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `connector_key` | string | required | Catalog key of a connector that supports knowledge bases. |
| `share_with` | string | required | User name or user/group ARN to share with. |
| `connector_id_prefix` | string | optional | The prefix used when the knowledge base was created (default `quickadminbuddy-`). |

As with `share_action_connector`, the knowledge base does not have to be
`enabled` in the onboarding state.

**Success payload (`data`):** `connector_key`, `integration_type`, `shared_with`.

**Result_Status values:**

| Status | When | Extra `data` |
|---|---|---|
| `success` | VIEWER role granted. | `shared_with` |
| `validation_failure` | Unknown connector key, the connector doesn't support knowledge bases, empty `share_with`, or invalid `connector_id_prefix`. | `invalid_connector_id_prefix` for a bad prefix |
| `validation_failure` | `share_with` names a Quick user that does not exist. Nothing is changed. | `error_type` (`principal_not_found`), `invalid_principal`, `principal_field` |
| `server_error` | [`UpdateKnowledgeBasePermissions`](https://docs.aws.amazon.com/quicksight/latest/APIReference/API_UpdateKnowledgeBasePermissions.html) failed (for example, the knowledge base or principal doesn't exist). | — |
| `internal_error` | Network, timeout, or IAM failure calling AWS Quick. | — |

---

## `get_tenant_configuration`

Return the tenant's current onboarding state — the stored suite plus the status
of every catalog connector.

**Inputs** — none.

**Success payload (`data`)**

```json
{
  "suite": "microsoft_365",
  "connectors": {
    "outlook":    { "status": "enabled",        "last_changed_at": "2025-01-15T10:22:00Z", "changed_by": "admin@acme" },
    "sharepoint": { "status": "not_configured", "last_changed_at": null, "changed_by": null }
  }
}
```

- `suite` is the stored productivity suite, or `null` when none has been stored.
- `connectors` covers **all 14** catalog keys. Each reports a `status` in
  `{enabled, not_configured, error}`; unrecorded connectors default to
  `not_configured`. When a connector has a recorded change, its exact
  `last_changed_at` (ISO-8601 UTC) and `changed_by` principal are echoed;
  otherwise both are `null`.

**Result_Status values:**

| Status | When |
|---|---|
| `success` | State retrieved and projected across the full catalog. |
| `server_error` | The onboarding data could not be retrieved; no partial data is returned. |
| `internal_error` | Internal/network/timeout failure. |

---

## `get_onboarding_summary`

Return a stage-grouped recap of the onboarding for writing a plain-language
summary of enabled versus skipped connectors.

**Inputs** — none.

**Success payload (`data`)**

```json
{
  "suite": "microsoft_365",
  "stages": {
    "productivity suite":      { "suite": "microsoft_365" },
    "productivity connectors": { "connectors": { "outlook": "enabled", "ms_teams": "not_configured", ... } },
    "enterprise knowledge":    { "connectors": { "sharepoint": "enabled", ... } },
    "other connectors":        { "connectors": { "atlassian": "not_configured" } }
  }
}
```

- `stages` always carries all four stages. The `productivity suite` stage is
  reported separately (it is not a catalog entry) and carries the stored suite.
- Every one of the 14 catalog entries appears under exactly one of the other
  three stages — none omitted or duplicated.
- Each connector's final status is one of `{enabled, skipped, not_configured}`.
  A stored `enabled` maps to `enabled`; `not_configured` and `error` both map to
  `not_configured`.

**Result_Status values:**

| Status | When |
|---|---|
| `success` | Summary built. |
| `server_error` | The stored suite could not be retrieved; no partial summary is returned. |
| `internal_error` | Internal/network/timeout failure. |

---

## `list_extensions`

List the AWS Quick extensions and each one's admin-attested status. No
AWS Quick API is called; extensions have none.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `suite` | string | optional | `google_workspace` or `microsoft_365`. Defaults to the stored suite. If neither is set, every extension is returned. |

**Success payload (`data`):** `suite` (the suite used, or `null`) and
`extensions`, a list whose items carry `key`, `display_name`, `suite_gate`,
`docs_url`, `description`, `status` (`installed`, `skipped`, or
`not_configured`, the default), `last_changed_at`, and `changed_by`.

**Result_Status values:**

| Status | When | Extra `data` |
|---|---|---|
| `success` | Extensions returned. | — |
| `validation_failure` | `suite` is not a valid suite. | `invalid_suite`, `valid_suites` |
| `internal_error` | The onboarding state could not be read. | `state_store_error` |
| `server_error` | Any other failure reading the onboarding state. | — |

---

## `set_extension_status`

Record the admin-attested status of an extension. This is an admin
declaration; nothing is installed or verified.

**Inputs:**

| Name | Type | Required | Meaning |
|---|---|---|---|
| `extension_key` | string | required | A key from the [extension catalog](#extension-catalog-5-entries). |
| `status` | string | required | `installed`, `skipped`, or `not_configured`. |

**Success payload (`data`):** `extension_key`, `status`.

**Result_Status values** (in check order):

| Status | When | Extra `data` |
|---|---|---|
| `validation_failure` | Unknown extension key. | `extension_key`, `valid_extensions` |
| `validation_failure` | Invalid status. | `extension_key`, `valid_statuses` |
| `validation_failure` | A suite is stored and the extension is gated to the other suite. | `extension_key`, `suite_gate`, `stored_suite` |
| `internal_error` | The onboarding state could not be read or written. | `extension_key`, plus `state_store_error` when the state table is the cause |
| `success` | Status recorded. | `extension_key`, `status` |

---

## Connector catalog (14 entries)

The catalog is a fixed, compile-time constant. `Suite_Gate` is one of
`microsoft_365`, `google_workspace`, or `any` (`any` matches every suite).
`Stage` is used for summary grouping. "Required config (KB)" lists the fields
required only when enabling the connector as a knowledge base (used to
create its backing data source); action-connector enables of the same connector
do not require them.

| Connector Key | Display Name | Category | Suite Gate | Stage | Action | KB | Required config (KB) |
|---|---|---|---|---|---|---|---|
| `outlook` | Outlook | Productivity - email | `microsoft_365` | productivity connectors | ✓ | — | — |
| `ms_teams` | Microsoft Teams | Productivity - messaging | `microsoft_365` | productivity connectors | ✓ | — | — |
| `onedrive` | OneDrive | Productivity - cloud storage | `microsoft_365` | productivity connectors | ✓ | ✓ | — *(secret required)* |
| `gmail` | Gmail | Productivity - email | `google_workspace` | productivity connectors | ✓ | — | — |
| `google_chat` | Google Chat | Productivity - messaging | `google_workspace` | productivity connectors | ✓ | — | — |
| `google_drive` | Google Drive | Productivity - cloud storage | `google_workspace` | productivity connectors | ✓ | ✓ | — |
| `google_calendar` | Google Calendar | Productivity - calendar | `google_workspace` | productivity connectors | ✓ | — | — |
| `google_docs` | Google Docs | Productivity - documents | `google_workspace` | productivity connectors | ✓ | — | — |
| `google_meet` | Google Meet | Productivity - meetings | `google_workspace` | productivity connectors | ✓ | — | — |
| `google_sheets` | Google Sheets | Productivity - spreadsheets | `google_workspace` | productivity connectors | ✓ | — | — |
| `google_slides` | Google Slides | Productivity - presentations | `google_workspace` | productivity connectors | ✓ | — | — |
| `sharepoint` | SharePoint | Enterprise knowledge | `microsoft_365` | enterprise knowledge | ✓ | ✓ | `sites` *(+ secret required)* |
| `confluence` | Confluence | Enterprise knowledge | `any` | enterprise knowledge | ✓ | ✓ *(console only)* | — |
| `atlassian` | Jira | Other | `any` | other connectors | ✓ | — | — |

> **Outlook** covers email, calendar, and contacts, so there is no separate
> Microsoft Calendar entry (unlike Google Calendar, which is its own AWS
> Quick integration). For **SharePoint**, the `sharepoint_domain`, `tenant_id`,
> and `client_id` come from the mandatory secret, not from `config`.
>
> **Google Drive** is a single dual-capability entry (`google_drive`), enabled
> as an action connector via `enable_action_connector` and as a knowledge base
> via `enable_kb_integration` — the same single-key pattern as `sharepoint`
> and `onedrive`. The former separate `google_drive_kb` key has
> been **removed**; use `google_drive` for the knowledge base.

**Credential handling.** Credentials are never echoed back: secret-looking
values are redacted from every `message` and `data`. Pass secrets by **name**
(`secret_id`), never by ARN. The per-connector secret contents for knowledge
bases are listed under [`enable_kb_integration`](#enable_kb_integration).

## Extension catalog (5 entries)

**Extensions** are client-side AWS Quick add-ins the user installs in their
Office app or browser. They have **no AWS Quick admin API**, so the backend
records an **admin-attested** status only — `list_extensions` returns the
suite-gated set and `set_extension_status` persists the admin's declaration
(`installed` / `skipped` / `not_configured`). The set is suite-gated exactly
like connectors.

| Extension Key | Display Name | Suite Gate | Official guide |
|---|---|---|---|
| `outlook_addin` | Microsoft Outlook extension | `microsoft_365` | [outlook-extension](https://docs.aws.amazon.com/quick/latest/userguide/outlook-extension.html) |
| `excel_addin` | Microsoft Excel extension | `microsoft_365` | [excel-extension](https://docs.aws.amazon.com/quick/latest/userguide/excel-extension.html) |
| `word_addin` | Microsoft Word extension | `microsoft_365` | [word-extension](https://docs.aws.amazon.com/quick/latest/userguide/word-extension.html) |
| `powerpoint_addin` | Microsoft PowerPoint extension | `microsoft_365` | [powerpoint-extension](https://docs.aws.amazon.com/quick/latest/userguide/powerpoint-extension.html) |
| `browser_extension` | Browser extension | `google_workspace` | [browser-extension](https://docs.aws.amazon.com/quick/latest/userguide/browser-extension.html) |

`get_tenant_configuration` and `get_onboarding_summary` both include an
`extensions` map (suite-filtered) reporting each extension's status.
