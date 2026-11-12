"""Enable operations: create an action connector or a knowledge base."""

from __future__ import annotations

from quick_admin_buddy_mcp.domain import auth as auth_contract
from quick_admin_buddy_mcp.domain.operations.base import (
    _DEFAULT_PRINCIPAL,
    _GATE_ANY,
    _KB_INLINE_DEFAULT_SECRET,
    _KB_INLINE_SECRET_SCHEMA,
    _KB_SECRET_REQUIRED_KEYS,
    _KB_SERVICE_ACCOUNT_KEYS,
    Operation,
    _invalid_id_prefix,
    _is_not_found,
    _record_key,
    _secret_values,
)
from quick_admin_buddy_mcp.domain.requests import (
    EnableActionConnectorRequest,
    EnableKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.types import (
    AuthType,
    ConnectorStatus,
    IntegrationType,
    ResultStatus,
    ToolResult,
)
from quick_admin_buddy_mcp.quick.client import (
    QuickAdminApiError,
    QuickAdminInternalError,
    resource_id,
)
from quick_admin_buddy_mcp.quick.secrets import (
    SecretAccessError,
    SecretResolutionError,
    is_secret_arn,
)
from quick_admin_buddy_mcp.state.repository import (
    StateStoreError,
    TenantId,
)


class _EnableIntegration(Operation):
    """Shared create, validate, roll back, and record logic for both enable operations."""

    def _enable_integration(
        self,
        tenant: TenantId,
        key: str,
        integration_type: IntegrationType,
        auth_type_raw: str | None,
        auth_params: dict | None,
        secret_id: str | None,
        config: dict | None,
        principal: str | None,
        share_with: str | None = None,
        connector_id_prefix: str | None = None,
        owner: str | None = None,
    ) -> ToolResult:
        """Validate, authenticate, create, and record one integration.

        Ordered checks (first failing check wins):

        0. ``secret_id`` must be a secret name, not an ARN, and no secret value
           may be passed inline in ``auth_params`` or ``config`` → else
           ``validation_failure``.
        1. A Productivity_Suite must be stored → else ``validation_failure``.
        2. ``key`` must exist in the catalog → else ``validation_failure``.
        3. The connector must support this integration type → else
           ``validation_failure`` naming the unsupported capability.
        3b. ``connector_id_prefix`` (if given) must match ``[\\w\\-]+`` → else
           ``validation_failure`` so the derived resource id stays API-valid.
        4. The connector's Suite_Gate must match the stored suite or be ``any``.
        5. ``auth_type`` must be a valid :class:`AuthType` → else
           ``validation_failure`` listing the valid auth types.
        6. Auth parameters are resolved from the Secrets Manager secret (when
           ``secret_id`` is given) and/or the inline ``auth_params``. A missing
           secret → ``validation_failure``; an access failure → ``internal_error``.
        7. All required auth parameters for the auth type (and any connector
           ``required_fields``) must be present → else ``validation_failure``
           listing the missing items and the secret contract, so the agent can
           collect them or fix the secret.
        8. If this integration is already ``enabled`` → ``already_configured``.
        9. Create via ``CreateActionConnector``, or ``CreateDataSource`` then
           ``CreateKnowledgeBase``. An API error → ``server_error`` carrying the
           reported error; an internal error → ``internal_error``. If
           ``CreateKnowledgeBase`` fails, the data source (and, after an
           internal error, the possibly-created knowledge base) is rolled back
           and ``data["rollback"]`` reports the outcome.
        10. Persist the ``enabled`` status for this integration. A persistence
            failure after a successful create → ``server_error`` for an action
            connector; a knowledge base is rolled back instead (``internal_error``
            when the rollback is complete, ``server_error`` otherwise).
        """
        label = self._integration_label(integration_type)
        # Redact any secret-looking inline value from every result this method
        # may return, regardless of branch.
        secrets = auth_contract.secret_values(auth_params or {}) + _secret_values(
            config
        )
        base_data = {"connector_key": key, "integration_type": integration_type.value}

        # 0. Name-only contract. Every secret-accepting tool takes the secret
        # NAME/ID, never an ARN. AWS Quick auto-redacts ARN-shaped tool
        # arguments, which would silently corrupt a forwarded ARN, so reject one
        # up front with an actionable message instead of failing later with an
        # opaque Secrets Manager error.
        if is_secret_arn(secret_id):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"secret_id must be the Secrets Manager secret NAME, not an "
                    f"ARN, for this {label}. You passed an 'arn:...' value; pass "
                    "just the secret name instead (for example "
                    "'quick-admin-buddy/sharepoint-kb'). If you only have the "
                    "ARN, use the part after ':secret:' (keep the trailing "
                    "'-xxxxxx' suffix). The server resolves the ARN itself when "
                    "one is needed."
                ),
                dict(base_data),
                secrets,
            )

        # 0b. Secret values only through Secrets Manager. A credential passed
        # inline (auth_params or config) has already gone through the chat and
        # the model context before it reaches the server; redacting it from the
        # result cannot undo that. Reject it before anything else so the agent
        # asks for a secret instead. Only the key names are echoed back.
        inline_secrets = sorted(
            set(auth_contract.inline_secret_params(auth_params))
            | set(auth_contract.inline_secret_params(config))
        )
        if inline_secrets:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"The {label} for {key!r} was NOT created: secret values "
                    f"({', '.join(inline_secrets)}) cannot be passed inline. "
                    "Store them in an AWS Secrets Manager secret named "
                    "'quick-admin-buddy/<name>' and pass its NAME as secret_id. "
                    "Non-secret values such as client_id or domain_url may still "
                    "be passed inline."
                ),
                {
                    **base_data,
                    "error_type": "inline_secret_not_allowed",
                    "inline_secret_params": inline_secrets,
                },
                secrets,
            )

        try:
            state = self._state.get(tenant)
        except StateStoreError as exc:
            return self._state_error(
                exc,
                f"The {label} for {key!r} was NOT created.",
                dict(base_data),
                secrets,
            )

        # 1. Suite must be set first.
        if state.suite is None:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Cannot enable a {label} before a productivity suite is set. "
                    "Set the productivity suite first, then retry."
                ),
                dict(base_data),
                secrets,
            )

        # 2. Connector key must exist.
        entry = self._catalog.get(key)
        if entry is None:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Unknown connector key {key!r}. "
                    "Retry with a connector key from the catalog."
                ),
                dict(base_data),
                secrets,
            )

        # 3. Connector must support this integration type.
        if (
            integration_type == IntegrationType.ACTION_CONNECTOR
            and not entry.supports_action
        ):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Connector {key!r} does not support an action connector. "
                    "Use a knowledge base integration, or choose another connector."
                ),
                dict(base_data),
                secrets,
            )
        if integration_type == IntegrationType.KNOWLEDGE_BASE and not entry.supports_kb:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Connector {key!r} does not support a knowledge base integration. "
                    "Use an action connector, or choose another connector."
                ),
                dict(base_data),
                secrets,
            )

        # 3b. A custom connector-id prefix must keep the resource id valid.
        if _invalid_id_prefix(connector_id_prefix):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid connector id prefix {connector_id_prefix!r}. "
                    "A prefix may contain only letters, digits, underscores, and "
                    "hyphens (pattern [\\w\\-]+), since the resulting connector id "
                    f"is {connector_id_prefix!r} + the connector key. "
                    "Retry with a valid prefix, or omit it to use the default."
                ),
                {**base_data, "invalid_connector_id_prefix": connector_id_prefix},
                secrets,
            )

        # 4. Suite gate must match the stored suite or be "any".
        if entry.suite_gate != _GATE_ANY and entry.suite_gate != state.suite.value:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Connector {key!r} is gated to {entry.suite_gate!r} but the "
                    f"tenant's productivity suite is {state.suite.value!r}. "
                    "Enable a connector valid for the stored suite, or change the suite."
                ),
                {
                    **base_data,
                    "suite_gate": entry.suite_gate,
                    "stored_suite": state.suite.value,
                },
                secrets,
            )

        # 5. Auth type must be valid AND allowed for this integration type.
        # Action connectors and knowledge bases accept DIFFERENT auth types, so
        # a value that is valid for one is rejected for the other (e.g.
        # custom_oauth_app is not a knowledge-base auth method).
        allowed_for_type = auth_contract.valid_auth_types_for(integration_type)
        auth_type = auth_contract.parse_auth_type(auth_type_raw)
        if auth_type is None or not auth_contract.is_auth_type_allowed(
            auth_type, integration_type
        ):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid or missing auth type {auth_type_raw!r} for a {label}. "
                    f"Valid options for a {label} are: {', '.join(allowed_for_type)}. "
                    "Retry with one of those."
                ),
                {
                    **base_data,
                    "invalid_auth_type": auth_type_raw,
                    "valid_auth_types": list(allowed_for_type),
                },
                secrets,
            )

        # 5b. Default the inline-secret KB connector's secret name. Google Drive
        # and SharePoint admin-managed are secret-backed (credentials sent inline
        # from the secret); when the caller omits secret_id, fall back to the
        # conventional default name per connector so the common case needs no
        # argument. An explicit secret_id always wins.
        if (
            integration_type == IntegrationType.KNOWLEDGE_BASE
            and key in _KB_SERVICE_ACCOUNT_KEYS
            and auth_type == AuthType.KB_ADMIN_MANAGED
            and not secret_id
        ):
            secret_id = _KB_INLINE_DEFAULT_SECRET.get(key)

        # 6. Resolve auth parameters (secret and/or inline).
        resolved, secret_vals, error = self._resolve_auth_params(
            key, integration_type, auth_params, secret_id
        )
        if error is not None:
            return error
        secrets = secrets + secret_vals

        # 7. Required auth parameters (and connector config fields) must be present.
        missing_auth = auth_contract.missing_params(auth_type, resolved)
        provided_cfg = config or {}
        # Base config fields apply to every integration type; knowledge-base
        # connectors additionally require the source-connection fields needed to
        # create their backing data source (CreateDataSource). Action-connector
        # enables of a dual-capability connector are NOT forced to supply those.
        required_cfg_fields = tuple(entry.required_fields)
        if integration_type == IntegrationType.KNOWLEDGE_BASE:
            required_cfg_fields = required_cfg_fields + tuple(entry.kb_required_fields)
        missing_cfg = [
            field_name
            for field_name in required_cfg_fields
            if field_name not in provided_cfg
            or provided_cfg[field_name] is None
            or provided_cfg[field_name] == ""
        ]
        if missing_auth or missing_cfg:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"The {label} for {key!r} is missing required "
                    f"{auth_type.value} parameter(s): "
                    f"{', '.join(missing_auth) or 'none'}"
                    + (
                        f"; and connector config field(s): {', '.join(missing_cfg)}"
                        if missing_cfg
                        else ""
                    )
                    + ". Store them in a Secrets Manager secret that follows the "
                    "contract and pass its name as secret_id (secret values "
                    "cannot be passed inline), then retry."
                ),
                {
                    **base_data,
                    "auth_type": auth_type.value,
                    "missing_params": missing_auth,
                    "missing_config_fields": missing_cfg,
                    "required_params": list(auth_contract.required_params(auth_type)),
                    "secret_contract": auth_contract.secret_contract_example(auth_type),
                },
                secrets,
            )

        # 7b. Certificate-based KB connectors (SharePoint, OneDrive) MUST be
        # configured with a Secrets Manager secret: their service credentials
        # (client_id, tenant_id, kms_key_arn, certificate_thumbprint) can only
        # be carried in a secret Quick reads, never inline (no API field exists
        # for the KMS key ARN or thumbprint). Require a secret NAME before any
        # API call. The server resolves the name to its canonical ARN in step 9
        # for the data source's Credentials.SecretArn (an ARN is never passed in).
        if (
            integration_type == IntegrationType.KNOWLEDGE_BASE
            and auth_type == AuthType.KB_ADMIN_MANAGED
            and key in _KB_SECRET_REQUIRED_KEYS
        ):
            if not secret_id:
                return self._ok(
                    ResultStatus.VALIDATION_FAILURE,
                    (
                        f"The {label} for {key!r} (admin-managed) requires a "
                        "Secrets Manager secret. Provide secret_id as the secret "
                        "NAME (not an ARN) of a Secrets Manager secret whose JSON "
                        "body has the keys client_id, tenant_id, kms_key_arn, and "
                        "certificate_thumbprint (the schema AWS Quick reads "
                        "for SharePoint/OneDrive certificate-based access). The "
                        "KMS key ARN and certificate thumbprint cannot be passed "
                        "inline — they must live in the secret. Create the "
                        "secret (name it 'quick-admin-buddy/<...>'), grant the "
                        "Quick service role access to it and kms:Sign on the "
                        "signing key, then retry with its NAME."
                    ),
                    {
                        **base_data,
                        "auth_type": auth_type.value,
                        "required_secret_schema": [
                            "client_id",
                            "tenant_id",
                            "kms_key_arn",
                            "certificate_thumbprint",
                        ],
                    },
                    secrets,
                )

        # 7c. Inline-secret KB connectors (Google Drive, SharePoint) MUST be
        # configured with a Secrets Manager secret whose fields are sent INLINE
        # on the data source (ServiceAccountCredentials / SharePointClientCredentials
        # plus the *Parameters). secret_id defaults in step 5b, so a missing
        # secret surfaces here as missing fields with an actionable message.
        if (
            integration_type == IntegrationType.KNOWLEDGE_BASE
            and auth_type == AuthType.KB_ADMIN_MANAGED
            and key in _KB_SERVICE_ACCOUNT_KEYS
        ):
            schema = _KB_INLINE_SECRET_SCHEMA.get(key, ())
            missing_sa = [f for f in schema if not resolved.get(f)]
            if missing_sa:
                default_secret = _KB_INLINE_DEFAULT_SECRET.get(key)
                if key == "sharepoint":
                    detail = (
                        "whose JSON body holds the SharePoint certificate-based "
                        "credentials: certificate_thumbprint and kms_key_arn (the "
                        "AWS KMS signing-key ARN), plus tenant_id, client_id, and "
                        "domain_url. These map to the API's "
                        "SharePointClientCredentials (CertThumbprint / KmsKeyArn) "
                        "and SharePointParameters (TenantId / ClientId / "
                        "SharePointDomain)."
                    )
                else:
                    detail = (
                        "whose JSON body is the downloaded Google service-account "
                        "key (so it includes client_email and private_key) PLUS an "
                        "added admin_account_email (the Google Workspace admin the "
                        "service account impersonates via domain-wide delegation). "
                        "These map to the API's ServiceAccountCredentials "
                        "(ClientEmail / ServiceAccountPrivateKey / AdminAccountEmail)."
                    )
                return self._ok(
                    ResultStatus.VALIDATION_FAILURE,
                    (
                        f"The {label} for {key!r} (admin-managed) requires a "
                        "Secrets Manager secret, but it is missing: "
                        f"{', '.join(missing_sa)}. Provide secret_id as the secret "
                        f"NAME (not an ARN; default {default_secret!r} if omitted) "
                        f"of a secret {detail} Create/fix the secret, then retry."
                    ),
                    {
                        **base_data,
                        "auth_type": auth_type.value,
                        "secret_id": secret_id,
                        "missing_secret_fields": missing_sa,
                        "required_secret_schema": list(schema),
                    },
                    secrets,
                )

        # 7d. A knowledge base MUST have a human owner. Quick grants the OWNER
        # role to `owner` at create time (via Permissions); without it the KB is
        # visible only to the server's execution role. Require it before any API
        # call so the agent collects it from the admin.
        if integration_type == IntegrationType.KNOWLEDGE_BASE and not owner:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Creating the {label} for {key!r} requires an owner. Provide "
                    "'owner' as the Quick user who should own the knowledge base "
                    "(a bare user name, assumed in the 'default' namespace, or a "
                    "full user ARN). They are granted the OWNER role so a human "
                    "can manage the knowledge base; without it the KB is visible "
                    "only to the server's execution role. Ask the admin who "
                    "should own it, then retry."
                ),
                dict(base_data),
                secrets,
            )

        # 8. Already enabled → idempotent no-op, no API call.
        record_key = _record_key(key, integration_type)
        existing = state.connectors.get(record_key)
        if existing is not None and existing.status == ConnectorStatus.ENABLED:
            return self._ok(
                ResultStatus.ALREADY_CONFIGURED,
                f"The {label} for {key!r} is already enabled.",
                dict(base_data),
                secrets,
            )

        # 8b. The Quick user being granted access (share_with for an action
        # connector, owner for a knowledge base) must exist; otherwise Quick
        # rejects the create with an opaque InvalidParameterValueException.
        if integration_type == IntegrationType.KNOWLEDGE_BASE:
            principal_field, principal_value = "owner", owner
        else:
            principal_field, principal_value = "share_with", share_with
        missing = self._missing_principal(
            principal_field, principal_value, label, key, base_data, secrets
        )
        if missing is not None:
            return missing

        # 9. Create via the appropriate AWS Quick API.
        request = {
            "name": provided_cfg.get("name") or key,
            "description": provided_cfg.get("description"),
            "auth_type": auth_type.value,
            "auth_params": resolved,
            "config": {k: v for k, v in provided_cfg.items() if k != "principal"},
            "share_with": share_with,
            "owner": owner,
            "id_prefix": connector_id_prefix,
        }
        # Certificate-based knowledge-base connectors (SharePoint/OneDrive) read
        # their service credentials through the data source's
        # Credentials.SecretArn — which needs the secret's *canonical ARN*. The
        # agent only ever passes the NAME (ARNs are rejected in step 0 and
        # redacted by Quick), so resolve the name to its ARN here, server-side.
        # A secret is mandatory for these connectors (enforced in step 7b). Other
        # KB connectors (e.g. Confluence) keep using the inline OAuth credentials
        # already read into ``resolved`` by step 6, so they are left untouched.
        if (
            integration_type == IntegrationType.KNOWLEDGE_BASE
            and key in _KB_SECRET_REQUIRED_KEYS
            and secret_id
            and self._secrets is not None
        ):
            try:
                request["credentials_secret_arn"] = self._secrets.resolve_secret_arn(
                    secret_id
                )
            except SecretResolutionError as exc:
                return self._ok(
                    ResultStatus.VALIDATION_FAILURE,
                    (
                        f"Could not resolve the secret {secret_id!r} for {key!r}: "
                        f"{exc} Pass the secret NAME (not an ARN) of an existing "
                        "Secrets Manager secret, then retry."
                    ),
                    {**base_data, "secret_id": secret_id},
                    secrets,
                )
            except SecretAccessError as exc:
                return self._internal_error(
                    "resolving the authentication secret ARN",
                    key,
                    exc,
                    secrets,
                    base_data,
                )
        try:
            if integration_type == IntegrationType.ACTION_CONNECTOR:
                self._quick.create_action_connector(key, request)
            else:
                # A knowledge base is created in two steps: first its data
                # source (which holds the source connection + service
                # credentials), then the knowledge base that references the
                # data source by ARN. Thread the created ARN into the KB
                # request between the two calls. If CreateDataSource itself
                # fails, nothing was created and nothing is rolled back (an
                # existing resource with the same id is not ours to delete).
                data_source = self._quick.create_data_source(key, request)
                request["data_source_arn"] = getattr(data_source, "arn", "") or ""
        except QuickAdminApiError as exc:
            return self._server_error(
                f"creating the {label}", key, exc, secrets, base_data
            )
        except QuickAdminInternalError as exc:
            return self._internal_error(
                f"creating the {label}", key, exc, secrets, base_data
            )

        if integration_type == IntegrationType.KNOWLEDGE_BASE:
            try:
                self._quick.create_knowledge_base(key, request)
            except (QuickAdminApiError, QuickAdminInternalError) as exc:
                # The data source exists; roll it back so a failed enable
                # leaves nothing behind. An API error means the knowledge base
                # was not created. An internal error (e.g. a timeout) leaves
                # that unknown, so the knowledge base is deleted too.
                internal = isinstance(exc, QuickAdminInternalError)
                rollback, note = self._rollback_knowledge_base(
                    key, connector_id_prefix, delete_kb=internal
                )
                data = {**base_data, "rollback": rollback}
                builder = self._internal_error if internal else self._server_error
                return builder(
                    f"creating the {label}", key, exc, secrets, data, suffix=note
                )

        # 10. Persist the enabled status for this integration. For a knowledge
        # base, a failed write rolls the creation back: an unrecorded knowledge
        # base could never be disabled through this server.
        acting = principal or provided_cfg.get("principal") or _DEFAULT_PRINCIPAL
        try:
            self._state.set_connector_status(
                tenant, record_key, ConnectorStatus.ENABLED, acting
            )
        except Exception as exc:  # noqa: BLE001 - classified into a Tool_Result
            if integration_type == IntegrationType.KNOWLEDGE_BASE:
                return self._kb_persist_failure(
                    exc, key, connector_id_prefix, label, base_data, secrets
                )
            return self._persist_failure(exc, key, label, base_data, secrets)

        return self._ok(
            ResultStatus.SUCCESS,
            f"The {label} for {key!r} has been enabled.",
            dict(base_data),
            secrets,
        )

    def _kb_persist_failure(
        self,
        exc: Exception,
        key: str,
        id_prefix: str | None,
        label: str,
        base_data: dict,
        secrets: tuple[str, ...],
    ) -> ToolResult:
        """Roll back a created knowledge base whose enabled status was not saved.

        After a complete rollback nothing was changed, so a state-store failure
        is ``internal_error`` (the documented contract for "nothing changed");
        otherwise it is ``server_error`` because resources were left behind.
        """
        rollback, note = self._rollback_knowledge_base(key, id_prefix, delete_kb=True)
        data = {**base_data, "rollback": rollback}
        complete = rollback["complete"]
        if isinstance(exc, StateStoreError):
            return self._state_error(
                exc,
                (
                    f"The {label} for {key!r} was created in AWS Quick, but its "
                    f"enabled status could not be recorded on the backend. {note}"
                ),
                data,
                secrets,
                status=(
                    ResultStatus.INTERNAL_ERROR
                    if complete
                    else ResultStatus.SERVER_ERROR
                ),
            )
        return self._ok(
            ResultStatus.INTERNAL_ERROR if complete else ResultStatus.SERVER_ERROR,
            (
                f"The {label} for {key!r} was created through the AWS Quick "
                f"API but its enabled status could not be persisted: {exc}. {note}"
            ),
            data,
            secrets,
        )

    def _persist_failure(
        self,
        exc: Exception,
        key: str,
        label: str,
        base_data: dict,
        secrets: tuple[str, ...],
    ) -> ToolResult:
        """Report an action connector created but not recorded as enabled."""
        if isinstance(exc, StateStoreError):
            return self._state_error(
                exc,
                (
                    f"The {label} for {key!r} WAS created in AWS Quick, but its "
                    "enabled status could not be recorded on the backend."
                ),
                dict(base_data),
                secrets,
                status=ResultStatus.SERVER_ERROR,
            )
        return self._ok(
            ResultStatus.SERVER_ERROR,
            (
                f"The {label} for {key!r} was created through the AWS Quick "
                f"API but its enabled status could not be persisted: {exc}. "
                "Retry the operation to reconcile the stored state."
            ),
            dict(base_data),
            secrets,
        )

    def _rollback_knowledge_base(
        self, key: str, id_prefix: str | None, *, delete_kb: bool
    ) -> tuple[dict, str]:
        """Delete what a failed knowledge-base enable created in AWS Quick.

        A knowledge base is created as a data source plus a knowledge base, so a
        failure after ``CreateDataSource`` succeeded would otherwise leave the
        data source behind (and block every retry with a duplicate-id error).
        This removes, best effort, the knowledge base (when ``delete_kb`` - it
        may exist) and then the data source. A "not found" response counts as
        removed.

        Returns ``(rollback, note)``: ``rollback`` is the machine-readable
        outcome for ``data["rollback"]`` and ``note`` is the sentence that tells
        the admin whether anything was left behind.
        """
        kb_id = resource_id(key, id_prefix)
        rollback: dict = {"data_source_id": kb_id}
        failures: list[str] = []

        if delete_kb:
            rollback["knowledge_base_id"] = kb_id
            try:
                self._quick.delete_knowledge_base(key, id_prefix)
                rollback["knowledge_base_deleted"] = True
            except (QuickAdminApiError, QuickAdminInternalError) as exc:
                rollback["knowledge_base_deleted"] = _is_not_found(exc)
                if not _is_not_found(exc):
                    failures.append(f"deleting knowledge base {kb_id!r} failed: {exc}")

        try:
            self._quick.delete_data_source(key, id_prefix)
            rollback["data_source_deleted"] = True
        except (QuickAdminApiError, QuickAdminInternalError) as exc:
            rollback["data_source_deleted"] = _is_not_found(exc)
            if not _is_not_found(exc):
                failures.append(f"deleting data source {kb_id!r} failed: {exc}")

        rollback["complete"] = not failures
        if not failures:
            note = (
                "The knowledge base creation was rolled back: nothing it created "
                "is left in AWS Quick. Fix the cause, then retry."
            )
        else:
            leftovers = [f"data source {kb_id!r}"]
            if delete_kb and not rollback.get("knowledge_base_deleted"):
                leftovers.insert(0, f"knowledge base {kb_id!r}")
            note = (
                "The rollback was incomplete ("
                + "; ".join(failures)
                + "). Delete the "
                + " and the ".join(leftovers)
                + " in AWS Quick before retrying."
            )
        return rollback, note

    def _resolve_auth_params(
        self,
        key: str,
        integration_type: IntegrationType,
        auth_params: dict | None,
        secret_id: str | None,
    ) -> tuple[dict, tuple[str, ...], ToolResult | None]:
        """Resolve auth parameters from a secret and/or inline values.

        Returns ``(resolved, secret_values, error)``. When ``secret_id`` is
        given, the secret JSON is loaded first and inline ``auth_params`` are
        layered on top (inline overrides). ``error`` is a ready-to-return
        :class:`ToolResult` when resolution fails:

        * no resolver configured → ``internal_error``;
        * secret missing / not a JSON object → ``validation_failure``;
        * secret access failure → ``internal_error``.
        """
        base_data = {"connector_key": key, "integration_type": integration_type.value}
        resolved: dict = {}

        if secret_id:
            if self._secrets is None:
                return (
                    {},
                    (),
                    self._internal_error(
                        "resolving the authentication secret",
                        key,
                        RuntimeError("no secret resolver is configured"),
                        (),
                        base_data,
                    ),
                )
            try:
                payload = self._secrets.get_secret_json(secret_id)
            except SecretResolutionError as exc:
                return (
                    {},
                    (),
                    self._ok(
                        ResultStatus.VALIDATION_FAILURE,
                        (
                            f"Could not use the referenced secret for {key!r}: {exc} "
                            "Fix the secret (a JSON object of snake_case auth "
                            "parameters) or provide the parameters inline, then retry."
                        ),
                        {**base_data, "secret_id": secret_id},
                    ),
                )
            except SecretAccessError as exc:
                return (
                    {},
                    (),
                    self._internal_error(
                        "reading the authentication secret", key, exc, (), base_data
                    ),
                )
            if isinstance(payload, dict):
                # The optional "auth_type" hint in the secret is not a parameter.
                resolved.update({k: v for k, v in payload.items() if k != "auth_type"})

        for name, value in (auth_params or {}).items():
            if value is not None:
                resolved[name] = value

        return resolved, auth_contract.secret_values(resolved), None


class EnableActionConnector(_EnableIntegration):
    """``enable_action_connector``: create an AWS Quick action connector."""

    def __call__(
        self, tenant: TenantId, request: EnableActionConnectorRequest
    ) -> ToolResult:
        """Enable a connector as an **action connector** (``CreateActionConnector``).

        ``request.share_with`` optionally names a Quick user (bare name, assumed
        in the ``default`` namespace, or a full user ARN) to grant
        owner-equivalent permissions to on the connector at creation time, via
        the API's ``Permissions`` property - so the admin (typically themselves)
        can see and manage the connector immediately rather than it being
        visible only to the Runtime's own execution role.

        ``request.connector_id_prefix`` optionally overrides the prefix used to
        build the connector's ``ActionConnectorId`` (default
        ``quickadminbuddy-``); the id becomes ``<prefix><connector_key>``. It
        must match ``[\\w\\-]+``. The same prefix must be supplied to
        :meth:`disable_action_connector` to target the same resource.
        """
        return self._enable_integration(
            tenant,
            request.connector_key,
            IntegrationType.ACTION_CONNECTOR,
            request.auth_type,
            request.auth_params,
            request.secret_id,
            request.config,
            request.principal,
            request.share_with,
            request.connector_id_prefix,
        )


class EnableKnowledgeBase(_EnableIntegration):
    """``enable_kb_integration``: create a data source and the knowledge base that uses it."""

    def __call__(
        self, tenant: TenantId, request: EnableKnowledgeBaseRequest
    ) -> ToolResult:
        """Enable a connector as a **knowledge base** (``CreateKnowledgeBase``).

        ``request.owner`` names the Quick user (bare name, assumed in the
        ``default`` namespace, or a full user ARN) who should OWN the created
        knowledge base. AWS Quick grants it the fixed **OWNER** role at
        creation time via the API's ``Permissions`` property, so a human owns
        and can manage the KB (without it, the KB is visible only to the
        server's execution role). A knowledge base has no arbitrary
        "share_with" grant at create time - its Actions must match the
        OWNER/VIEWER roles - so ``share_with`` is not used for knowledge bases
        here; use :meth:`share_kb_integration` to grant another user the VIEWER
        role afterward.

        ``request.connector_id_prefix`` optionally overrides the prefix used to
        build the ``KnowledgeBaseId`` (default ``quickadminbuddy-``); the id
        becomes ``<prefix><connector_key>``. It must match ``[\\w\\-]+``. The
        same prefix must be supplied to :meth:`disable_kb_integration` to
        target the same resource.
        """
        return self._enable_integration(
            tenant,
            request.connector_key,
            IntegrationType.KNOWLEDGE_BASE,
            request.auth_type,
            request.auth_params,
            request.secret_id,
            request.config,
            request.principal,
            request.share_with,
            request.connector_id_prefix,
            request.owner,
        )
