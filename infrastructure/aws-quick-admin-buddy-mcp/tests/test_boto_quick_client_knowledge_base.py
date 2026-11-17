"""Tests for the two-step admin-managed knowledge-base create path in
:class:`BotoQuickAdminClient`.

An admin-managed knowledge base is created as a *data source*
(``CreateDataSource``) + *knowledge base* (``CreateKnowledgeBase``) pair. These
tests assert, for all four KB connectors (SharePoint, OneDrive, Google Drive,
Confluence):

* the request parameters the client builds pass **botocore's own** parameter
  validation against the live ``quicksight`` service model (so a shape bug like
  the previously-shipped ``SourceType`` / missing ``DataSourceArn`` / un-nested
  ``KnowledgeBaseConfiguration`` is caught here, not in production);
* the ACL dual-switch is set for admin-managed (template
  ``accessControlConfiguration.crawlAcl`` + top-level
  ``AccessControlConfiguration.isACLEnabled``) and off for user-managed;
* inline credentials map to ``Credentials.OAuthClientCredentials`` and a secret
  ARN maps to ``Credentials.SecretArn``;
* a botocore ``ParamValidationError`` is surfaced as a ``QuickAdminApiError``
  (``server_error``) with the real validation detail, NOT swallowed as an
  opaque internal error.

The golden SharePoint shape mirrors a real knowledge base read from the live
service (see the project discussion): ``templateConfiguration.template`` with
``type: SHAREPOINTV3``, ``dataEntityConfiguration.siteUrls``, and
``accessControlConfiguration.crawlAcl``.
"""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import ParamValidationError
from botocore.validate import validate_parameters

from quick_admin_buddy_mcp.quick import (
    BotoQuickAdminClient,
    DataSourceDetail,
    QuickAdminApiError,
)

_REGION = "us-east-1"
_ACCOUNT = "123456789012"


@pytest.fixture(scope="module")
def _models():
    client = boto3.Session().client("quicksight", region_name=_REGION)
    model = client.meta.service_model
    return (
        model.operation_model("CreateDataSource").input_shape,
        model.operation_model("CreateKnowledgeBase").input_shape,
    )


def _client() -> BotoQuickAdminClient:
    # No real network: aws_account_id / region injected; _invoke is not called
    # by the request-building helpers under test.
    return BotoQuickAdminClient(
        client=object(), region_name=_REGION, aws_account_id=_ACCOUNT
    )


# Per-connector minimal admin-managed config that satisfies the data-source
# required members (verified against botocore 1.43.114).
_CASES = {
    "sharepoint": {
        "sites": ["https://contoso.sharepoint.com/sites/kb"],
        "sharepoint_domain": "contoso.sharepoint.com",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "22222222-2222-2222-2222-222222222222",
    },
    "onedrive": {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "client_id": "22222222-2222-2222-2222-222222222222",
    },
    "google_drive": {},
    "confluence": {"confluence_url": "https://contoso.atlassian.net"},
}


@pytest.mark.parametrize("key", sorted(_CASES))
def test_create_data_source_params_pass_botocore_validation(key, _models):
    cds_shape, _ = _models
    client = _client()
    request = {
        "name": key,
        "auth_type": "admin_managed",
        "auth_params": {"client_id": "x", "client_secret": "y"},
        "config": _CASES[key],
        "share_with": "workshopuser",
    }
    params = client._build_data_source_params(key, request)
    # Required members present + correct Type.
    assert params["Type"] in {"SHAREPOINT", "ONE_DRIVE", "GOOGLE_DRIVE", "CONFLUENCE"}
    assert params["AwsAccountId"] == _ACCOUNT
    assert params["DataSourceId"] == f"quickadminbuddy-{key}"
    # Does not raise.
    validate_parameters(params, cds_shape)


def test_sharepoint_tenant_client_id_sourced_from_secret_not_config():
    """For SharePoint/OneDrive, tenant_id/client_id live in the Secrets Manager
    secret (request['auth_params']), NOT in config. They must still populate
    DataSourceParameters, or Quick rejects CreateDataSource with
    InvalidParameterValueException."""
    client = _client()
    request = {
        "name": "sharepoint",
        "auth_type": "admin_managed",
        # The resolved secret body — this is where tenant_id/client_id come from.
        "auth_params": {
            "client_id": "22222222-2222-2222-2222-222222222222",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "kms_key_arn": "arn:aws:kms:us-east-1:1:key/x",
            "certificate_thumbprint": "AB12",
        },
        # Config has only the display/connection fields the admin supplies.
        "config": {
            "sites": ["https://contoso.sharepoint.com/sites/kb"],
            "sharepoint_domain": "contoso.sharepoint.com",
        },
        "credentials_secret_arn": "arn:aws:secretsmanager:us-east-1:1:secret:quick-admin-buddy/sharepoint-kb-AbCdEf",
    }
    params = client._build_data_source_params("sharepoint", request)
    sp = params["DataSourceParameters"]["SharePointParameters"]
    assert sp["SharePointDomain"] == "contoso.sharepoint.com"
    assert sp["TenantId"] == "11111111-1111-1111-1111-111111111111"
    assert sp["ClientId"] == "22222222-2222-2222-2222-222222222222"


def test_onedrive_tenant_client_id_sourced_from_secret_not_config():
    client = _client()
    request = {
        "name": "onedrive",
        "auth_type": "admin_managed",
        "auth_params": {
            "client_id": "cid-od",
            "tenant_id": "tid-od",
            "kms_key_arn": "arn:aws:kms:us-east-1:1:key/x",
            "certificate_thumbprint": "CD34",
        },
        "config": {},
        "credentials_secret_arn": "arn:aws:secretsmanager:us-east-1:1:secret:quick-admin-buddy/onedrive-kb-AbCdEf",
    }
    params = client._build_data_source_params("onedrive", request)
    od = params["DataSourceParameters"]["OneDriveParameters"]
    assert od["TenantId"] == "tid-od"
    assert od["ClientId"] == "cid-od"


def test_config_tenant_client_id_overrides_secret():
    """If the admin also supplies tenant_id/client_id inline in config, config
    wins over the secret (explicit override)."""
    client = _client()
    request = {
        "name": "sharepoint",
        "auth_type": "admin_managed",
        "auth_params": {"client_id": "from-secret", "tenant_id": "from-secret"},
        "config": {
            "sites": ["https://contoso.sharepoint.com/sites/kb"],
            "sharepoint_domain": "contoso.sharepoint.com",
            "client_id": "from-config",
            "tenant_id": "from-config",
        },
    }
    params = client._build_data_source_params("sharepoint", request)
    sp = params["DataSourceParameters"]["SharePointParameters"]
    assert sp["TenantId"] == "from-config"
    assert sp["ClientId"] == "from-config"


@pytest.mark.parametrize("key", sorted(_CASES))
def test_create_knowledge_base_params_pass_botocore_validation(key, _models):
    _, ckb_shape = _models
    client = _client()
    request = {
        "name": key,
        "auth_type": "admin_managed",
        "auth_params": {"client_id": "x", "client_secret": "y"},
        "config": _CASES[key],
        "share_with": "workshopuser",
        # Threaded in by the service between the two create calls.
        "data_source_arn": f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:datasource/ds-{key}",
    }
    params = client._build_knowledge_base_params(key, request)
    # No obsolete SourceType; required DataSourceArn present; correctly nested.
    assert "SourceType" not in params
    assert params["DataSourceArn"].startswith("arn:aws:quicksight:")
    template = params["KnowledgeBaseConfiguration"]["templateConfiguration"]["template"]
    assert template["type"] in {
        "SHAREPOINTV3",
        "ONEDRIVEV3",
        "GOOGLEDRIVEV3",
        "CONFLUENCEV3",
    }
    # Does not raise.
    validate_parameters(params, ckb_shape)


def test_sharepoint_template_matches_real_shape():
    """The SharePoint template mirrors a real KB: siteUrls + crawlAcl + type."""
    client = _client()
    request = {
        "name": "sharepoint",
        "auth_type": "admin_managed",
        "config": _CASES["sharepoint"],
        "data_source_arn": "arn:aws:quicksight:us-east-1:123456789012:datasource/x",
    }
    params = client._build_knowledge_base_params("sharepoint", request)
    template = params["KnowledgeBaseConfiguration"]["templateConfiguration"]["template"]
    assert template["type"] == "SHAREPOINTV3"
    assert (
        template["dataEntityConfiguration"]["siteUrls"] == _CASES["sharepoint"]["sites"]
    )
    assert template["dataEntityConfiguration"]["crawlFiles"] is True
    assert template["dataEntityConfiguration"]["crawlPages"] is True
    # ACL dual-switch: template-level crawlAcl AND top-level isACLEnabled.
    assert template["accessControlConfiguration"]["crawlAcl"] is True
    assert params["AccessControlConfiguration"]["isACLEnabled"] is True


def test_admin_managed_sets_acl_user_managed_does_not():
    client = _client()
    admin = client._build_knowledge_base_params(
        "onedrive",
        {
            "auth_type": "admin_managed",
            "config": _CASES["onedrive"],
            "data_source_arn": "a",
        },
    )
    user = client._build_knowledge_base_params(
        "onedrive",
        {
            "auth_type": "user_managed",
            "config": _CASES["onedrive"],
            "data_source_arn": "a",
        },
    )
    assert admin["AccessControlConfiguration"]["isACLEnabled"] is True
    assert (
        admin["KnowledgeBaseConfiguration"]["templateConfiguration"]["template"][
            "accessControlConfiguration"
        ]["crawlAcl"]
        is True
    )
    assert user["AccessControlConfiguration"]["isACLEnabled"] is False
    assert (
        "accessControlConfiguration"
        not in user["KnowledgeBaseConfiguration"]["templateConfiguration"]["template"]
    )


def test_sharepoint_kb_template_acl_on_and_off():
    """SharePoint template includes the full filterConfiguration with
    inclusionItemPaths = the site list, and crawlIdentities + crawlAcl track the
    ACL flag (both on for admin_managed, both off otherwise)."""
    client = _client()
    sites = [
        "https://contoso.sharepoint.com/sites/eng",
        "https://contoso.sharepoint.com/sites/support",
    ]
    cfg = {"sites": sites}
    on = client._build_knowledge_base_params(
        "sharepoint",
        {"auth_type": "admin_managed", "config": cfg, "data_source_arn": "a"},
    )
    t = on["KnowledgeBaseConfiguration"]["templateConfiguration"]["template"]
    assert t["type"] == "SHAREPOINTV3"
    assert "connectionConfiguration" not in t  # SharePoint has none (unlike GDrive)
    assert t["crawlIdentities"] is True
    assert t["accessControlConfiguration"]["crawlAcl"] is True
    assert t["dataEntityConfiguration"]["siteUrls"] == sites
    assert t["filterConfiguration"]["inclusionItemPaths"] == sites
    assert t["filterConfiguration"]["maxFileSizeInMegaBytes"] == "500"
    assert on["AccessControlConfiguration"]["isACLEnabled"] is True

    off = client._build_knowledge_base_params(
        "sharepoint",
        {"auth_type": "user_managed", "config": cfg, "data_source_arn": "a"},
    )
    t2 = off["KnowledgeBaseConfiguration"]["templateConfiguration"]["template"]
    # ACL off: crawlIdentities AND crawlAcl must both be false, or the service
    # rejects with "Data source validation failed".
    assert t2["crawlIdentities"] is False
    assert t2["accessControlConfiguration"]["crawlAcl"] is False
    assert off["AccessControlConfiguration"]["isACLEnabled"] is False


def test_inline_credentials_map_to_oauth_client_credentials():
    client = _client()
    # Confluence (not certificate-based) may use inline OAuthClientCredentials.
    creds = client._data_source_credentials(
        "confluence", {"auth_params": {"client_id": "cid", "client_secret": "sec"}}
    )
    assert creds == {
        "OAuthClientCredentials": {"ClientId": "cid", "ClientSecret": "sec"}
    }


def test_sharepoint_uses_inline_sharepoint_client_credentials():
    """SharePoint admin-managed sends SharePointClientCredentials INLINE from the
    resolved secret (CertThumbprint + KmsKeyArn), never OAuthClientCredentials
    and never a SecretArn."""
    client = _client()
    creds = client._data_source_credentials(
        "sharepoint",
        {
            "auth_params": {
                "certificate_thumbprint": "AB12CD34",
                "kms_key_arn": "arn:aws:kms:eu-west-1:1:key/x",
                "tenant_id": "t",
                "client_id": "c",
                "domain_url": "https://contoso.sharepoint.com",
            }
        },
    )
    assert creds == {
        "SharePointClientCredentials": {
            "CertThumbprint": "AB12CD34",
            "KmsKeyArn": "arn:aws:kms:eu-west-1:1:key/x",
        }
    }
    # Missing a required field -> no credentials (defensive; domain layer gates).
    assert (
        client._data_source_credentials(
            "sharepoint", {"auth_params": {"certificate_thumbprint": "AB12"}}
        )
        is None
    )


def test_onedrive_never_uses_inline_oauth_credentials():
    """OneDrive (certificate-based, SecretArn channel) must NOT fall back to
    inline credentials - its KMS key ARN + thumbprint only travel in the secret."""
    client = _client()
    # Even with inline client_id/client_secret present, no inline credentials.
    assert (
        client._data_source_credentials(
            "onedrive", {"auth_params": {"client_id": "cid", "client_secret": "sec"}}
        )
        is None
    )
    # With a secret ARN, it uses SecretArn.
    creds = client._data_source_credentials(
        "onedrive",
        {"credentials_secret_arn": "arn:aws:secretsmanager:us-east-1:1:secret:x"},
    )
    assert creds == {"SecretArn": "arn:aws:secretsmanager:us-east-1:1:secret:x"}


def test_secret_arn_credentials_preferred_over_inline():
    client = _client()
    creds = client._data_source_credentials(
        "confluence",
        {
            "credentials_secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:kb-xyz",
            "auth_params": {"client_id": "cid", "client_secret": "sec"},
        },
    )
    assert creds == {
        "SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:kb-xyz"
    }


def test_no_credentials_omits_credentials_block():
    client = _client()
    assert client._data_source_credentials("confluence", {"auth_params": {}}) is None


class _ParamValidationBotoClient:
    """Fake boto3 client whose create_* raise botocore ParamValidationError."""

    def create_data_source(self, **kwargs):
        raise ParamValidationError(report="Missing required parameter: 'Type'")

    def create_knowledge_base(self, **kwargs):
        raise ParamValidationError(report="Unknown parameter 'SourceType'")


def test_param_validation_error_surfaced_as_api_error_not_internal():
    """A ParamValidationError must NOT be swallowed as a generic internal error.

    This is the regression guard for the original bug: a wrong request shape was
    raised by botocore before any network call, fell into the catch-all, and
    surfaced to the user as an opaque "unexpected internal error". It must now
    be a QuickAdminApiError (server_error) carrying the real validation detail.
    """
    client = BotoQuickAdminClient(
        client=_ParamValidationBotoClient(),
        region_name=_REGION,
        aws_account_id=_ACCOUNT,
    )
    with pytest.raises(QuickAdminApiError) as exc_info:
        client.create_data_source(
            "sharepoint",
            {"auth_type": "admin_managed", "config": _CASES["sharepoint"]},
        )
    assert exc_info.value.code == "ParamValidationError"
    assert "parameter validation failed" in str(exc_info.value).lower()


def test_create_data_source_returns_arn_from_response():
    """create_data_source maps the response Arn into DataSourceDetail.arn."""

    class _OkClient:
        def create_data_source(self, **kwargs):
            return {
                "Arn": "arn:aws:quicksight:us-east-1:123456789012:datasource/abc",
                "DataSourceId": "abc",
                "CreationStatus": "CREATION_IN_PROGRESS",
            }

    client = BotoQuickAdminClient(
        client=_OkClient(), region_name=_REGION, aws_account_id=_ACCOUNT
    )
    detail = client.create_data_source(
        "sharepoint", {"auth_type": "admin_managed", "config": _CASES["sharepoint"]}
    )
    assert isinstance(detail, DataSourceDetail)
    assert detail.arn == "arn:aws:quicksight:us-east-1:123456789012:datasource/abc"
    assert detail.data_source_id == "abc"


# ---------------------------------------------------------------------------
# Delete paths must send AwsAccountId (regression: the three Delete* actions
# previously omitted it, so botocore raised ParamValidationError -> the
# disable_* tools failed with an opaque server_error in production).
# These validate the actual params the client sends against the live
# Delete* input shapes, which require AwsAccountId + the resource id.
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Captures the kwargs passed to each delete method and returns {}."""

    def __init__(self):
        self.calls = []

    def delete_action_connector(self, **kwargs):
        self.calls.append(("delete_action_connector", kwargs))
        return {}

    def delete_knowledge_base(self, **kwargs):
        self.calls.append(("delete_knowledge_base", kwargs))
        return {}

    def delete_data_source(self, **kwargs):
        self.calls.append(("delete_data_source", kwargs))
        return {}


@pytest.fixture(scope="module")
def _delete_models():
    client = boto3.Session().client("quicksight", region_name=_REGION)
    model = client.meta.service_model
    return {
        "delete_action_connector": model.operation_model(
            "DeleteActionConnector"
        ).input_shape,
        "delete_knowledge_base": model.operation_model(
            "DeleteKnowledgeBase"
        ).input_shape,
        "delete_data_source": model.operation_model("DeleteDataSource").input_shape,
    }


def test_delete_action_connector_sends_aws_account_id(_delete_models):
    fake = _RecordingClient()
    client = BotoQuickAdminClient(
        client=fake, region_name=_REGION, aws_account_id=_ACCOUNT
    )
    client.delete_action_connector("sharepoint")
    action, params = fake.calls[0]
    assert action == "delete_action_connector"
    assert params["AwsAccountId"] == _ACCOUNT
    assert params["ActionConnectorId"] == "quickadminbuddy-sharepoint"
    # Passes botocore validation against the live DeleteActionConnector model.
    validate_parameters(params, _delete_models["delete_action_connector"])


def test_delete_knowledge_base_sends_aws_account_id(_delete_models):
    fake = _RecordingClient()
    client = BotoQuickAdminClient(
        client=fake, region_name=_REGION, aws_account_id=_ACCOUNT
    )
    client.delete_knowledge_base("sharepoint")
    action, params = fake.calls[0]
    assert action == "delete_knowledge_base"
    assert params["AwsAccountId"] == _ACCOUNT
    assert params["KnowledgeBaseId"] == "quickadminbuddy-sharepoint"
    validate_parameters(params, _delete_models["delete_knowledge_base"])


def test_delete_data_source_sends_aws_account_id(_delete_models):
    fake = _RecordingClient()
    client = BotoQuickAdminClient(
        client=fake, region_name=_REGION, aws_account_id=_ACCOUNT
    )
    client.delete_data_source("sharepoint")
    action, params = fake.calls[0]
    assert action == "delete_data_source"
    assert params["AwsAccountId"] == _ACCOUNT
    assert params["DataSourceId"] == "quickadminbuddy-sharepoint"
    validate_parameters(params, _delete_models["delete_data_source"])


# ---------------------------------------------------------------------------
# Google Drive admin-managed: ServiceAccountCredentials (inline) + template
# connectionConfiguration. Validates the data-source params against the live
# CreateDataSource model (botocore >= 1.43.117, which models
# Credentials.ServiceAccountCredentials), and asserts the KB template carries
# connectionConfiguration.authType=SERVICE_ACCOUNT.
# ---------------------------------------------------------------------------

_GDRIVE_AUTH = {
    "client_email": "svc@proj.iam.gserviceaccount.com",
    # PEM markers assembled from fragments so the literal key banner never
    # appears contiguously in source (avoids false-positive secret scanners).
    "private_key": (
        "-----BEGIN "
        + "PRIVATE KEY-----\n"
        + "\n".join(["A" * 64] * 30)
        + "\n-----END "
        + "PRIVATE KEY-----\n"
    ),
    "admin_account_email": "admin@example.com",
}


def test_gdrive_data_source_emits_service_account_credentials_inline(_models):
    cds_shape, _ = _models
    client = _client()
    request = {
        "name": "google_drive",
        "auth_type": "admin_managed",
        "auth_params": dict(_GDRIVE_AUTH),
        "config": {},
    }
    params = client._build_data_source_params("google_drive", request)
    assert params["Type"] == "GOOGLE_DRIVE"
    assert params["DataSourceParameters"]["GoogleDriveParameters"]["AuthType"] == (
        "SERVICE_ACCOUNT"
    )
    # Inline ServiceAccountCredentials, NOT a SecretArn.
    cred = params["Credentials"]
    assert "SecretArn" not in cred
    sac = cred["ServiceAccountCredentials"]
    assert sac["ClientEmail"] == _GDRIVE_AUTH["client_email"]
    assert sac["AdminAccountEmail"] == _GDRIVE_AUTH["admin_account_email"]
    assert sac["ServiceAccountPrivateKey"] == _GDRIVE_AUTH["private_key"]
    # Must pass botocore validation against the live CreateDataSource model
    # (requires botocore >= 1.43.117 for the ServiceAccountCredentials member).
    validate_parameters(params, cds_shape)


def test_gdrive_kb_template_has_connection_configuration():
    client = _client()
    request = {
        "name": "google_drive",
        "auth_type": "admin_managed",
        "auth_params": dict(_GDRIVE_AUTH),
        "config": {},
        "data_source_arn": "arn:aws:quicksight:us-east-1:123456789012:datasource/x",
    }
    params = client._build_knowledge_base_params("google_drive", request)
    template = params["KnowledgeBaseConfiguration"]["templateConfiguration"]["template"]
    assert template["type"] == "GOOGLEDRIVEV3"
    assert template["connectionConfiguration"]["authType"] == "SERVICE_ACCOUNT"
    dec = template["dataEntityConfiguration"]
    assert dec["crawlMyDrive"] is True
    assert dec["crawlSharedDrives"] is True
    assert dec["crawlSharedWithMe"] is True
    # admin-managed => ACL dual switch on
    assert template["accessControlConfiguration"]["crawlAcl"] is True
    assert params["AccessControlConfiguration"]["isACLEnabled"] is True


# ---------------------------------------------------------------------------
# SharePoint admin-managed: inline SharePointClientCredentials + bare domain,
# validated against the live CreateDataSource model (botocore >= 1.43.117).
# ---------------------------------------------------------------------------

_SP_AUTH = {
    "certificate_thumbprint": "AB12CD34EF",
    "kms_key_arn": "arn:aws:kms:eu-west-1:123456789012:key/11111111-2222-3333-4444-555555555555",
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "client_id": "22222222-2222-2222-2222-222222222222",
    "domain_url": "https://contoso.sharepoint.com",
}


def test_sharepoint_data_source_inline_creds_and_bare_domain(_models):
    cds_shape, _ = _models
    client = _client()
    # Mirror _build_data_source_params: auth_params (secret) merged under config.
    request = {
        "name": "sharepoint",
        "auth_type": "admin_managed",
        "auth_params": dict(_SP_AUTH),
        "config": {"sites": ["https://contoso.sharepoint.com/sites/engineering"]},
    }
    params = client._build_data_source_params("sharepoint", request)
    assert params["Type"] == "SHAREPOINT"
    sp = params["DataSourceParameters"]["SharePointParameters"]
    # Domain must be BARE (scheme stripped), or the service doubles https://.
    assert sp["SharePointDomain"] == "contoso.sharepoint.com"
    assert sp["TenantId"] == _SP_AUTH["tenant_id"]
    assert sp["ClientId"] == _SP_AUTH["client_id"]
    assert sp["AuthType"] == "TWO_LEGGED_OAUTH"
    # Inline SharePointClientCredentials, NOT a SecretArn.
    cred = params["Credentials"]
    assert "SecretArn" not in cred
    assert cred["SharePointClientCredentials"] == {
        "CertThumbprint": _SP_AUTH["certificate_thumbprint"],
        "KmsKeyArn": _SP_AUTH["kms_key_arn"],
    }
    # Passes botocore validation against the live model (needs 1.43.117+).
    validate_parameters(params, cds_shape)
