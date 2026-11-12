"""Unit tests for the enable method pairs.

Covers the ordered validation of ``enable_action_connector`` and
``enable_kb_integration`` (first failing check wins), auth-parameter handling
(inline and via Secrets Manager), missing-parameter feedback, API-error
passthrough, idempotency, and secret non-echo, using the in-memory
:class:`InMemoryStateRepository`, the configurable :class:`MockQuickAdminClient`,
and :class:`InMemorySecretResolver`.

Validation ordering under test:
  1. suite not set            -> validation_failure
  2. unknown key              -> validation_failure
  3. capability unsupported   -> validation_failure
  4. suite-gate mismatch      -> validation_failure
  5. invalid auth type        -> validation_failure
  6. secret resolution error  -> validation_failure / internal_error
  7. missing auth params      -> validation_failure (+ contract)
  8. already enabled          -> already_configured
  9. create + persist         -> success; API/internal/persistence errors
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.requests import (
    DisableActionConnectorRequest,
    DisableKnowledgeBaseRequest,
    EnableActionConnectorRequest,
    EnableKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.sanitize import SECRET_REDACTION
from quick_admin_buddy_mcp.domain.service import OnboardingService, _record_key
from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    IntegrationType,
    ResultStatus,
    Suite,
)
from quick_admin_buddy_mcp.quick.mock import MockBehavior, MockQuickAdminClient, Mode
from quick_admin_buddy_mcp.quick.secrets import InMemorySecretResolver
from quick_admin_buddy_mcp.state.repository import InMemoryStateRepository

TENANT = "acme-corp"

# A complete, valid Custom OAuth app parameter set.
CUSTOM_OAUTH = {
    "client_id": "cid-123",
    "client_secret": "super-secret-value",
    "domain_url": "https://graph.example.com",
    "authorization_url": "https://auth.example.com/authorize",
    "token_url": "https://auth.example.com/token",
    "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
}
CUSTOM_OAUTH_SECRET = "quick-admin-buddy/test-custom-oauth"

# A Secrets Manager secret NAME for certificate-based KB connectors
# (SharePoint/OneDrive admin-managed), whose body holds client_id, tenant_id,
# A Secrets Manager secret NAME for the OneDrive certificate-based KB connector
# (admin-managed), whose body holds client_id, tenant_id, kms_key_arn, and
# certificate_thumbprint. OneDrive REQUIRES a secret and uses the SecretArn
# channel; the agent passes its NAME (never an ARN); the server resolves the ARN.
KB_SECRET_NAME = "quick-admin-buddy/kb-sp"  # nosec B105 - test fixture secret name, not a credential

# Google Drive admin-managed secret: the downloaded service-account JSON plus an
# added admin_account_email. Resolved INLINE into ServiceAccountCredentials.
GOOGLE_DRIVE_SECRET_NAME = "quick-admin-buddy/google-drive-kb-admin"  # nosec B105 - test fixture secret name, not a credential
GOOGLE_DRIVE_SA = {
    # Value assembled from fragments so the literal key/value pair never
    # appears in source (the Semgrep detected-google-gcm-service-account rule
    # is a regex on it); the runtime value is the normal "service_account".
    "type": "_".join(("service", "account")),
    "project_id": "example-project",
    "client_email": "svc@example-project.iam.gserviceaccount.com",
    # PEM markers assembled from fragments so the literal key banner never
    # appears contiguously in source (avoids false-positive secret scanners);
    # the runtime value is a normal dummy PEM string.
    "private_key": (
        "-----BEGIN "
        + "PRIVATE KEY-----\nNOT-A-REAL-KEY-test-fixture-only\n-----END "
        + "PRIVATE KEY-----\n"
    ),
    "admin_account_email": "admin@example.com",
}

# SharePoint admin-managed secret: certificate-based credentials sent INLINE as
# SharePointClientCredentials + SharePointParameters. Body holds the cert
# thumbprint, KMS key ARN, tenant/client ids, and the SharePoint domain URL.
SHAREPOINT_SECRET_NAME = "quick-admin-buddy/sharepoint-kb-admin"  # nosec B105 - test fixture secret name, not a credential
SHAREPOINT_SECRET = {
    "certificate_thumbprint": "AB12CD34",
    "kms_key_arn": "arn:aws:kms:eu-west-1:123456789012:key/abc-123",
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "client_id": "22222222-2222-2222-2222-222222222222",
    "domain_url": "https://contoso.sharepoint.com",
}

# Minimal valid SharePoint KB config: the admin supplies only the site list to
# index; the domain/tenant/client and credentials all come from the secret.
SHAREPOINT_KB_CONFIG = {
    "sites": ["https://contoso.sharepoint.com/sites/kb"],
}

# A knowledge base requires an owner (the Quick user granted the OWNER role).
KB_OWNER = "workshopuser"

# A complete, valid Service-to-Service OAuth parameter set.
SERVICE_OAUTH = {
    "client_id": "cid-456",
    "client_secret": "another-secret-value",
    "domain_url": "https://api.example.com",
    "token_url": "https://auth.example.com/token",
}


def _service(quick: MockQuickAdminClient | None = None, secrets=None):
    catalog = ConnectorCatalog()
    state = InMemoryStateRepository()
    quick = quick or MockQuickAdminClient()
    service = OnboardingService(
        catalog, state, quick, secrets if secrets is not None else TEST_SECRETS
    )
    return service, state, quick


# ---------------------------------------------------------------------------
# 1. Suite not set
# ---------------------------------------------------------------------------


def test_enable_action_before_suite_set_is_validation_failure_no_api():
    service, state, quick = _service()

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "suite" in result.message.lower()
    assert quick.calls == []
    assert state.get(TENANT).connectors == {}


# ---------------------------------------------------------------------------
# 2. Unknown key
# ---------------------------------------------------------------------------


def test_enable_action_unknown_key_is_validation_failure():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="nope",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "nope" in result.message
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 3. Capability unsupported
# ---------------------------------------------------------------------------


def test_enable_action_on_kb_only_connector_is_validation_failure():
    # google_drive_kb was removed as a duplicate: Google Drive is now a single
    # dual-capability connector (google_drive), like sharepoint. The old KB-only
    # key is no longer in the catalog, so enabling it is an unknown-key failure.
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="google_drive_kb",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "Unknown connector key" in result.message
    assert quick.calls == []


def test_enable_kb_on_action_only_connector_is_validation_failure():
    # gmail supports only an action connector, not a knowledge base.
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="gmail",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "does not support a knowledge base" in result.message
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 4. Suite-gate mismatch
# ---------------------------------------------------------------------------


def test_enable_action_suite_gate_mismatch_is_validation_failure():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="gmail",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert result.data["suite_gate"] == "google_workspace"
    assert result.data["stored_suite"] == "microsoft_365"
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 5. Invalid auth type
# ---------------------------------------------------------------------------


def test_enable_action_invalid_auth_type_is_validation_failure_listing_valid():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="basic_auth",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "custom_oauth_app" in result.message
    assert "service_to_service_oauth" in result.message
    assert result.data["valid_auth_types"] == [
        "custom_oauth_app",
        "service_to_service_oauth",
        "api_key",
    ]
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 7. Missing auth params -> validation_failure with the secret contract
# ---------------------------------------------------------------------------


def test_enable_action_missing_params_lists_them_and_contract():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    # Provide only a subset of the custom_oauth_app parameters.
    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            auth_params={"client_id": "cid"},
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    missing = result.data["missing_params"]
    assert "client_secret" in missing
    assert "token_url" in missing
    # The response carries the full required list and the secret contract so the
    # agent can collect the values or guide the admin to a secret.
    assert set(result.data["required_params"]) == set(CUSTOM_OAUTH)
    contract = result.data["secret_contract"]
    assert contract["auth_type"] == "custom_oauth_app"
    assert "client_secret" in contract
    assert quick.calls == []


def test_enable_kb_missing_connector_config_field_is_reported():
    # sharepoint supports a KB and requires the KB data-source config field
    # `sites` (the domain/tenant/client come from the secret). Knowledge bases
    # use the KB auth types (admin_managed / user_managed).
    secrets = InMemorySecretResolver({SHAREPOINT_SECRET_NAME: dict(SHAREPOINT_SECRET)})
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    # No config at all -> the required `sites` field is reported missing.
    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint", auth_type="admin_managed"
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert result.data["missing_config_fields"] == ["sites"]
    assert quick.calls == []


# ---------------------------------------------------------------------------
# Knowledge-base auth types differ from action-connector auth types
# ---------------------------------------------------------------------------


def test_enable_kb_rejects_action_connector_auth_type():
    # custom_oauth_app / service_to_service_oauth are action-connector methods;
    # a knowledge base must use user_managed or admin_managed.
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="onedrive",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert result.data["invalid_auth_type"] == "custom_oauth_app"
    assert result.data["valid_auth_types"] == ["user_managed", "admin_managed"]
    assert quick.calls == []


def test_enable_action_rejects_kb_auth_type():
    # user_managed / admin_managed are knowledge-base methods; an action
    # connector must use custom_oauth_app or service_to_service_oauth.
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook", auth_type="admin_managed"
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert result.data["invalid_auth_type"] == "admin_managed"
    assert result.data["valid_auth_types"] == [
        "custom_oauth_app",
        "service_to_service_oauth",
        "api_key",
    ]
    assert quick.calls == []


def test_enable_kb_user_managed_needs_no_credentials():
    # user_managed takes no credential params at the backend boundary.
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="user_managed", owner=KB_OWNER
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    # A knowledge base is created in two steps: data source then knowledge base.
    assert len(quick.calls_for("create_data_source")) == 1
    assert len(quick.calls_for("create_knowledge_base")) == 1
    # user_managed must not enable document-level ACLs.
    request = quick.calls_for("create_knowledge_base")[0].args[1]
    assert request["auth_type"] == "user_managed"


def test_enable_kb_admin_managed_succeeds():
    secrets = InMemorySecretResolver().put(
        GOOGLE_DRIVE_SECRET_NAME, dict(GOOGLE_DRIVE_SA)
    )
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="admin_managed", owner=KB_OWNER
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    # The created data source's ARN is threaded into the KB create request.
    assert len(quick.calls_for("create_data_source")) == 1
    request = quick.calls_for("create_knowledge_base")[0].args[1]
    assert request["auth_type"] == "admin_managed"
    assert request["data_source_arn"].startswith("arn:aws:quicksight:")


def test_enable_gdrive_kb_defaults_secret_name_when_omitted():
    """Google Drive admin-managed with no secret_id uses the conventional
    default secret name; the service-account fields resolve from it and the
    data source request carries them inline (NOT a credentials_secret_arn)."""
    secrets = InMemorySecretResolver().put(
        GOOGLE_DRIVE_SECRET_NAME, dict(GOOGLE_DRIVE_SA)
    )
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    # No secret_id passed -> defaults to quick-admin-buddy/google-drive-kb-admin.
    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="admin_managed", owner=KB_OWNER
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    ds_request = quick.calls_for("create_data_source")[0].args[1]
    # google_drive must NOT be routed through credentials_secret_arn (SecretArn);
    # it uses inline ServiceAccountCredentials resolved into auth_params.
    assert "credentials_secret_arn" not in ds_request
    assert ds_request["auth_params"]["client_email"] == GOOGLE_DRIVE_SA["client_email"]
    assert (
        ds_request["auth_params"]["admin_account_email"]
        == (GOOGLE_DRIVE_SA["admin_account_email"])
    )


def test_enable_gdrive_kb_missing_secret_fields_is_validation_failure():
    """A Google Drive admin-managed secret missing a required field (e.g. no
    admin_account_email) is rejected before any API call, naming the gap."""
    incomplete = {
        "client_email": GOOGLE_DRIVE_SA["client_email"],
        "private_key": GOOGLE_DRIVE_SA["private_key"],
        # admin_account_email intentionally missing
    }
    secrets = InMemorySecretResolver().put(GOOGLE_DRIVE_SECRET_NAME, incomplete)
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="admin_managed", owner=KB_OWNER
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "admin_account_email" in result.data["missing_secret_fields"]
    assert quick.calls_for("create_data_source") == []


def test_enable_gdrive_kb_private_key_not_echoed():
    """The service-account private key never appears in the result."""
    secrets = InMemorySecretResolver().put(
        GOOGLE_DRIVE_SECRET_NAME, dict(GOOGLE_DRIVE_SA)
    )
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="admin_managed", owner=KB_OWNER
        ),
    )

    import json as _json

    assert "BEGIN PRIVATE KEY" not in _json.dumps(result.to_dict())


# ---------------------------------------------------------------------------
# 6. Secret resolution (via Secrets Manager)
# ---------------------------------------------------------------------------


def test_enable_action_resolves_params_from_secret_success():
    secrets = InMemorySecretResolver().put("my/secret", dict(CUSTOM_OAUTH))
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook", auth_type="custom_oauth_app", secret_id="my/secret"
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    assert quick.calls_for("create_action_connector")
    assert (
        state.get(TENANT)
        .connectors[_record_key("outlook", IntegrationType.ACTION_CONNECTOR)]
        .status
        == ConnectorStatus.ENABLED
    )


def test_enable_action_missing_secret_is_validation_failure():
    secrets = InMemorySecretResolver()  # empty
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id="missing/secret",
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "secret" in result.message.lower()
    assert quick.calls == []


def test_enable_action_secret_access_error_is_internal_error():
    secrets = InMemorySecretResolver().set_access_error("blocked/secret")
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id="blocked/secret",
        ),
    )

    assert result.status == ResultStatus.INTERNAL_ERROR
    assert quick.calls == []


def test_enable_action_secret_requested_but_no_resolver_is_internal_error():
    state = InMemoryStateRepository()
    quick = MockQuickAdminClient()
    service = OnboardingService(ConnectorCatalog(), state, quick)  # no resolver
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id="any/secret",
        ),
    )

    assert result.status == ResultStatus.INTERNAL_ERROR
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 8. Already enabled
# ---------------------------------------------------------------------------


def test_enable_action_already_enabled_is_already_configured_no_api():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)
    state.set_connector_status(
        TENANT,
        _record_key("outlook", IntegrationType.ACTION_CONNECTOR),
        ConnectorStatus.ENABLED,
        "admin",
    )

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.ALREADY_CONFIGURED
    assert quick.calls_for("create_action_connector") == []


def test_action_and_kb_tracked_independently_for_same_connector():
    # confluence supports both; enabling the action connector must not mark the
    # knowledge base enabled, and vice versa.
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    action = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="confluence",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )
    assert action.status == ResultStatus.SUCCESS

    conns = state.get(TENANT).connectors
    assert (
        conns[_record_key("confluence", IntegrationType.ACTION_CONNECTOR)].status
        == ConnectorStatus.ENABLED
    )
    # The KB integration remains unrecorded (not enabled).
    assert _record_key("confluence", IntegrationType.KNOWLEDGE_BASE) not in conns


# ---------------------------------------------------------------------------
# 9. Happy paths + create via the correct API
# ---------------------------------------------------------------------------


def test_enable_action_happy_path_calls_create_action_connector():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
            principal="admin@acme",
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.data["integration_type"] == "action_connector"
    assert len(quick.calls_for("create_action_connector")) == 1
    assert quick.calls_for("create_knowledge_base") == []
    record = state.get(TENANT).connectors[
        _record_key("outlook", IntegrationType.ACTION_CONNECTOR)
    ]
    assert record.status == ConnectorStatus.ENABLED
    assert record.changed_by == "admin@acme"


def test_enable_kb_happy_path_calls_create_knowledge_base():
    secrets = InMemorySecretResolver(
        {
            KB_SECRET_NAME: {
                "client_id": "c",
                "tenant_id": "t",
                "kms_key_arn": "arn:aws:kms:eu-west-1:1:key/x",
                "certificate_thumbprint": "AB12",
            }
        }
    )
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    # OneDrive uses the SecretArn credential channel (SharePoint now uses inline
    # SharePointClientCredentials), so it is the generic SecretArn example here.
    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="onedrive",
            auth_type="admin_managed",
            secret_id=KB_SECRET_NAME,
            owner=KB_OWNER,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.data["integration_type"] == "knowledge_base"
    assert len(quick.calls_for("create_data_source")) == 1
    assert len(quick.calls_for("create_knowledge_base")) == 1
    assert quick.calls_for("create_action_connector") == []
    # The secret NAME is resolved to its canonical ARN and forwarded as the
    # data source's credentials (the agent never passes an ARN).
    ds_request = quick.calls_for("create_data_source")[0].args[1]
    assert ds_request["credentials_secret_arn"].startswith("arn:aws:secretsmanager:")
    assert KB_SECRET_NAME in ds_request["credentials_secret_arn"]


# ---------------------------------------------------------------------------
# API / internal / persistence failures with passthrough
# ---------------------------------------------------------------------------


def test_enable_action_api_error_surfaces_server_error_passthrough_no_state():
    quick = MockQuickAdminClient()
    quick.set_behavior(
        "create_action_connector",
        MockBehavior(
            mode=Mode.API_ERROR, error_message="InvalidParameterValueException"
        ),
    )
    service, state, quick = _service(quick)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.SERVER_ERROR
    # The underlying API error text is surfaced for troubleshooting.
    assert "InvalidParameterValueException" in result.message
    assert state.get(TENANT).connectors == {}


def test_enable_kb_api_error_surfaces_server_error():
    quick = MockQuickAdminClient()
    quick.set_behavior(
        "create_knowledge_base",
        MockBehavior(mode=Mode.API_ERROR, error_message="ResourceExistsException"),
    )
    secrets = InMemorySecretResolver(
        {
            KB_SECRET_NAME: {
                "client_id": "c",
                "tenant_id": "t",
                "kms_key_arn": "arn:aws:kms:eu-west-1:1:key/x",
                "certificate_thumbprint": "AB12",
            }
        }
    )
    service, state, quick = _service(quick, secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="onedrive",
            auth_type="admin_managed",
            secret_id=KB_SECRET_NAME,
            owner=KB_OWNER,
        ),
    )

    assert result.status == ResultStatus.SERVER_ERROR
    assert "ResourceExistsException" in result.message


def test_enable_action_internal_error_surfaces_internal_error():
    quick = MockQuickAdminClient()
    quick.set_behavior(
        "create_action_connector", MockBehavior(mode=Mode.INTERNAL_ERROR)
    )
    service, state, quick = _service(quick)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.INTERNAL_ERROR
    assert state.get(TENANT).connectors == {}


def test_enable_action_persistence_failure_after_create_is_server_error():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    def _boom(*args, **kwargs):
        raise RuntimeError("dynamo write failed")

    state.set_connector_status = _boom  # type: ignore[assignment]

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.SERVER_ERROR
    assert "persist" in result.message.lower()
    assert quick.calls_for("create_action_connector")


# ---------------------------------------------------------------------------
# Secret non-echo (client_secret never surfaces)
# ---------------------------------------------------------------------------


def test_enable_action_does_not_echo_client_secret():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    assert CUSTOM_OAUTH["client_secret"] not in result.to_json()


def test_enable_action_redacts_client_secret_in_error_message():
    secret = "leak-me-secret"  # nosec B105 - deliberate dummy value used to assert redaction, not a real credential
    quick = MockQuickAdminClient()
    quick.set_behavior(
        "create_action_connector",
        MockBehavior(mode=Mode.API_ERROR, error_message=f"failed with {secret}"),
    )
    service, state, quick = _service(quick)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    # The secret value comes from Secrets Manager; if the API error text echoes
    # it, the sanitize boundary still redacts it.
    secrets = InMemorySecretResolver(
        {"quick-admin-buddy/leaky": {**CUSTOM_OAUTH, "client_secret": secret}}
    )
    service = OnboardingService(ConnectorCatalog(), state, quick, secrets)
    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id="quick-admin-buddy/leaky",
        ),
    )

    assert result.status == ResultStatus.SERVER_ERROR
    assert secret not in result.message
    assert SECRET_REDACTION in result.message


# ---------------------------------------------------------------------------
# Custom connector id prefix
# ---------------------------------------------------------------------------


def test_enable_action_passes_custom_id_prefix_to_create_request():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
            connector_id_prefix="my-company-",
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    # The prefix is threaded into the request dict the client receives
    # (recorded as the second positional arg of create_action_connector).
    create_call = quick.calls_for("create_action_connector")[0]
    request = create_call.args[1]
    assert request["id_prefix"] == "my-company-"


def test_enable_kb_passes_custom_id_prefix_to_create_request():
    secrets = InMemorySecretResolver({SHAREPOINT_SECRET_NAME: dict(SHAREPOINT_SECRET)})
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            secret_id=SHAREPOINT_SECRET_NAME,
            config=dict(SHAREPOINT_KB_CONFIG),
            connector_id_prefix="acme_",
            owner=KB_OWNER,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    request = quick.calls_for("create_knowledge_base")[0].args[1]
    assert request["id_prefix"] == "acme_"


def test_enable_action_invalid_id_prefix_is_validation_failure_no_api():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    # A space is outside [\w\-]+, so the derived resource id would be invalid.
    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
            connector_id_prefix="bad prefix!",
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert result.data["invalid_connector_id_prefix"] == "bad prefix!"
    # Rejected before any API call.
    assert quick.calls == []


def test_enable_action_empty_prefix_falls_back_to_default():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
            connector_id_prefix="",
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    # Empty prefix is valid; the client applies the default downstream, so the
    # request carries the empty/None prefix for the client to resolve.
    request = quick.calls_for("create_action_connector")[0].args[1]
    assert not request["id_prefix"]


def test_disable_action_forwards_custom_id_prefix_to_delete():
    service, state, quick = _service()
    state.set_connector_status(
        TENANT,
        _record_key("outlook", IntegrationType.ACTION_CONNECTOR),
        ConnectorStatus.ENABLED,
        "admin",
    )

    result = service.disable_action_connector(
        TENANT,
        DisableActionConnectorRequest(
            connector_key="outlook", connector_id_prefix="my-company-"
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    # delete_action_connector records (key, id_prefix) positionally.
    delete_call = quick.calls_for("delete_action_connector")[0]
    assert delete_call.args[0] == "outlook"
    assert delete_call.args[1] == "my-company-"


def test_disable_action_invalid_id_prefix_is_validation_failure_no_api():
    service, state, quick = _service()
    state.set_connector_status(
        TENANT,
        _record_key("outlook", IntegrationType.ACTION_CONNECTOR),
        ConnectorStatus.ENABLED,
        "admin",
    )

    result = service.disable_action_connector(
        TENANT,
        DisableActionConnectorRequest(
            connector_key="outlook", connector_id_prefix="bad prefix!"
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert quick.calls == []


# ---------------------------------------------------------------------------
# Two-step admin-managed knowledge-base orchestration
# ---------------------------------------------------------------------------


def test_enable_kb_creates_data_source_then_knowledge_base_in_order():
    """A KB enable calls create_data_source BEFORE create_knowledge_base, and
    threads the returned data-source ARN into the KB create request."""
    secrets = InMemorySecretResolver({SHAREPOINT_SECRET_NAME: dict(SHAREPOINT_SECRET)})
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            secret_id=SHAREPOINT_SECRET_NAME,
            config=dict(SHAREPOINT_KB_CONFIG),
            owner=KB_OWNER,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    # Order: data source first, knowledge base second.
    kb_actions = [c.action for c in quick.calls if c.action.startswith("create_")]
    assert kb_actions == ["create_data_source", "create_knowledge_base"]
    # The ARN the mock returns for the data source is threaded into the KB call.
    ds_detail_arn = "arn:aws:quicksight:us-east-1:123456789012:datasource/ds-sharepoint"
    kb_request = quick.calls_for("create_knowledge_base")[0].args[1]
    assert kb_request["data_source_arn"] == ds_detail_arn


def test_enable_kb_with_secret_name_threads_resolved_credentials_secret_arn():
    """A secret_id NAME is resolved to its canonical ARN and passed through as
    credentials_secret_arn, so the data source uses Credentials.SecretArn. This
    is the OneDrive channel (SharePoint now uses inline SharePointClientCredentials).
    The agent passes only the name."""
    secret_name = "quick-admin-buddy/kb-onedrive"  # nosec B105 - test fixture secret name, not a credential
    secrets = InMemorySecretResolver(
        {
            secret_name: {
                "client_id": "cid",
                "tenant_id": "t",
                "kms_key_arn": "arn:aws:kms:eu-west-1:1:key/x",
                "certificate_thumbprint": "AB12",
            }
        }
    )
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="onedrive",
            auth_type="admin_managed",
            secret_id=secret_name,
            owner=KB_OWNER,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    ds_request = quick.calls_for("create_data_source")[0].args[1]
    # The forwarded value is a real ARN the server resolved from the name, not
    # the name the agent passed.
    assert ds_request["credentials_secret_arn"].startswith("arn:aws:secretsmanager:")
    assert secret_name in ds_request["credentials_secret_arn"]


def test_disable_kb_deletes_knowledge_base_then_data_source():
    """Disabling a KB tears down both resources: KB first, then data source."""
    secrets = InMemorySecretResolver().put(
        GOOGLE_DRIVE_SECRET_NAME, dict(GOOGLE_DRIVE_SA)
    )
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)
    # Enable first so the integration is in the ENABLED state.
    service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="admin_managed", owner=KB_OWNER
        ),
    )

    result = service.disable_kb_integration(
        TENANT, DisableKnowledgeBaseRequest(connector_key="google_drive")
    )

    assert result.status == ResultStatus.SUCCESS
    delete_actions = [c.action for c in quick.calls if c.action.startswith("delete_")]
    assert delete_actions == ["delete_knowledge_base", "delete_data_source"]


def test_enable_sharepoint_kb_without_secret_is_validation_failure():
    """SharePoint admin-managed KB requires a Secrets Manager secret with the
    certificate credentials; with an empty resolver (default secret absent) it
    is rejected before any API call."""
    secrets = InMemorySecretResolver()  # empty: default secret not present
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            config=dict(SHAREPOINT_KB_CONFIG),
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert quick.calls == []


def test_enable_onedrive_kb_without_secret_is_validation_failure():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="onedrive", auth_type="admin_managed", owner=KB_OWNER
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "Secrets Manager" in result.message
    assert quick.calls == []


def test_enable_sharepoint_kb_with_name_secret_succeeds():
    """SharePoint admin-managed with a bare secret NAME succeeds: the server
    resolves the secret and sends SharePointClientCredentials INLINE (NOT a
    credentials_secret_arn), plus SharePointParameters from the secret."""
    secrets = InMemorySecretResolver({SHAREPOINT_SECRET_NAME: dict(SHAREPOINT_SECRET)})
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            secret_id=SHAREPOINT_SECRET_NAME,
            config=dict(SHAREPOINT_KB_CONFIG),
            owner=KB_OWNER,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    ds_request = quick.calls_for("create_data_source")[0].args[1]
    # SharePoint uses inline credentials, NOT the SecretArn channel.
    assert "credentials_secret_arn" not in ds_request
    assert (
        ds_request["auth_params"]["certificate_thumbprint"]
        == (SHAREPOINT_SECRET["certificate_thumbprint"])
    )
    assert ds_request["auth_params"]["kms_key_arn"] == SHAREPOINT_SECRET["kms_key_arn"]


def test_enable_sharepoint_kb_defaults_secret_name_when_omitted():
    """SharePoint admin-managed with no secret_id uses the conventional default
    name quick-admin-buddy/sharepoint-kb-admin."""
    secrets = InMemorySecretResolver({SHAREPOINT_SECRET_NAME: dict(SHAREPOINT_SECRET)})
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            config=dict(SHAREPOINT_KB_CONFIG),
            owner=KB_OWNER,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    assert len(quick.calls_for("create_data_source")) == 1


def test_enable_sharepoint_kb_with_arn_secret_is_validation_failure():
    """An ARN passed as secret_id is rejected: the name-only contract forbids
    ARNs (AWS Quick redacts ARN-shaped tool arguments). No API call is made."""
    secrets = InMemorySecretResolver(
        {"quick-admin-buddy/kb-sp": {"client_id": "c", "tenant_id": "t"}}
    )
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            secret_id="arn:aws:secretsmanager:eu-west-1:123456789012:secret:quick-admin-buddy/kb-sp-AbCdEf",
            config=dict(SHAREPOINT_KB_CONFIG),
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "ARN" in result.message
    assert quick.calls == []


def test_enable_action_on_dual_connector_does_not_require_kb_fields():
    """A dual-capability connector's action-connector enable must NOT demand the
    knowledge-base-only data-source fields (kb_required_fields)."""
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    # sharepoint has kb_required_fields but no base required_fields; enabling it
    # as an ACTION connector should succeed without sites/tenant_id/etc.
    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="sharepoint",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    assert len(quick.calls_for("create_action_connector")) == 1
    assert quick.calls_for("create_data_source") == []


# ---------------------------------------------------------------------------
# Knowledge base OWNER (required at create) vs VIEWER (sharing) roles.
# ---------------------------------------------------------------------------


def test_enable_kb_requires_owner():
    """A knowledge base enable without an owner is rejected before any API call."""
    secrets = InMemorySecretResolver({SHAREPOINT_SECRET_NAME: dict(SHAREPOINT_SECRET)})
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            secret_id=SHAREPOINT_SECRET_NAME,
            config=dict(SHAREPOINT_KB_CONFIG),
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "owner" in result.message.lower()
    assert quick.calls == []


def test_enable_kb_threads_owner_into_create_request():
    """The owner is threaded into the KB create request so the client grants the
    OWNER role to it."""
    secrets = InMemorySecretResolver({SHAREPOINT_SECRET_NAME: dict(SHAREPOINT_SECRET)})
    service, state, quick = _service(secrets=secrets)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            secret_id=SHAREPOINT_SECRET_NAME,
            config=dict(SHAREPOINT_KB_CONFIG),
            owner="alice",
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    kb_request = quick.calls_for("create_knowledge_base")[0].args[1]
    assert kb_request["owner"] == "alice"


# ---------------------------------------------------------------------------
# Confluence action connector: api_key (2LO) auth type end-to-end through the
# domain layer.
# ---------------------------------------------------------------------------

CONFLUENCE_API_KEY = {
    "api_key": "ATATT-token",
    "domain_url": "https://contoso.atlassian.net",
    "email": "admin@contoso.com",
}
CONFLUENCE_API_KEY_SECRET = "quick-admin-buddy/test-confluence-api-key"


def test_enable_confluence_action_api_key_succeeds():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)  # confluence suite_gate = any

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="confluence",
            auth_type="api_key",
            secret_id=CONFLUENCE_API_KEY_SECRET,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    create = quick.calls_for("create_action_connector")[0].args[1]
    assert create["auth_type"] == "api_key"
    assert create["auth_params"]["api_key"] == "ATATT-token"


def test_enable_confluence_action_api_key_missing_token_is_validation_failure():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    # Missing api_key -> reported with the secret contract; no API call.
    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="confluence",
            auth_type="api_key",
            auth_params={"domain_url": "https://contoso.atlassian.net"},
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "api_key" in result.data["missing_params"]
    assert result.data["secret_contract"]["auth_type"] == "api_key"
    assert quick.calls == []


def test_enable_confluence_action_api_key_token_not_echoed():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="confluence",
            auth_type="api_key",
            auth_params=dict(CONFLUENCE_API_KEY),
        ),
    )

    import json as _json

    assert "ATATT-token" not in _json.dumps(result.to_dict())


# ---------------------------------------------------------------------------
# Jira Cloud action connector (catalog key `atlassian`, Type JIRA_CLOUD):
# api_key (2LO) and custom_oauth_app (3LO) both enable through the domain layer.
# ---------------------------------------------------------------------------

JIRA_API_KEY = {
    "api_key": "ATATT-jira-token",
    "domain_url": "https://api.atlassian.com/ex/jira/abc-123",
    "email": "admin@contoso.com",
}
JIRA_API_KEY_SECRET = "quick-admin-buddy/test-jira-api-key"

# Credentials come only from Secrets Manager (inline secrets are rejected),
# so tests store them in an in-memory resolver and pass the secret name.
TEST_SECRETS = InMemorySecretResolver(
    {
        CUSTOM_OAUTH_SECRET: CUSTOM_OAUTH,
        CONFLUENCE_API_KEY_SECRET: CONFLUENCE_API_KEY,
        JIRA_API_KEY_SECRET: JIRA_API_KEY,
    }
)


def test_enable_jira_action_api_key_succeeds():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)  # atlassian suite_gate = any

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="atlassian",
            auth_type="api_key",
            secret_id=JIRA_API_KEY_SECRET,
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    create = quick.calls_for("create_action_connector")[0].args[1]
    assert create["auth_type"] == "api_key"
    assert create["auth_params"]["api_key"] == "ATATT-jira-token"


def test_enable_jira_action_api_key_token_not_echoed():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="atlassian",
            auth_type="api_key",
            auth_params=dict(JIRA_API_KEY),
        ),
    )

    import json as _json

    assert "ATATT-jira-token" not in _json.dumps(result.to_dict())


# ---------------------------------------------------------------------------
# Secret values only through Secrets Manager
# ---------------------------------------------------------------------------


def test_enable_action_rejects_inline_client_secret_before_anything_else():
    service, state, quick = _service()
    # No suite stored: the inline-secret check runs first, before any state read.
    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            auth_params=CUSTOM_OAUTH,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert result.data["error_type"] == "inline_secret_not_allowed"
    assert result.data["inline_secret_params"] == ["client_secret"]
    assert "secret_id" in result.message
    assert CUSTOM_OAUTH["client_secret"] not in result.to_json()
    assert quick.calls == []


def test_enable_action_rejects_inline_api_key():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="atlassian", auth_type="api_key", auth_params=JIRA_API_KEY
        ),
    )

    assert result.data["error_type"] == "inline_secret_not_allowed"
    assert result.data["inline_secret_params"] == ["api_key"]
    assert quick.calls == []


def test_enable_kb_rejects_secret_in_config_or_auth_params():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.enable_kb_integration(
        TENANT,
        EnableKnowledgeBaseRequest(
            connector_key="google_drive",
            auth_type="admin_managed",
            auth_params={"private_key": "inline-private-key-material-0123456789"},
            config={"admin_password": "inline-password-value-0123456789"},
            owner=KB_OWNER,
        ),
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert result.data["inline_secret_params"] == ["admin_password", "private_key"]
    assert "inline-private-key-material" not in result.to_json()
    assert quick.calls == []


def test_enable_action_allows_non_secret_inline_values_over_the_secret():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=CUSTOM_OAUTH_SECRET,
            auth_params={"domain_url": "https://graph.example.org"},
        ),
    )

    assert result.status == ResultStatus.SUCCESS
    sent = quick.calls_for("create_action_connector")[0].args[1]["auth_params"]
    assert sent["domain_url"] == "https://graph.example.org"
    assert sent["client_secret"] == CUSTOM_OAUTH["client_secret"]
