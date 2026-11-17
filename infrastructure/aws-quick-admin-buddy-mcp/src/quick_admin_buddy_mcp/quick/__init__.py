"""Quick Admin API layer: wraps boto3 calls to the AWS Quick admin actions
(action connectors and knowledge bases), signs with the Agent_IAM_Identity, and
maps API errors to Tool_Result statuses while stripping credentials and stack
detail. Also provides Secrets Manager resolution for auth parameters.

Exposes the :class:`QuickAdminClient` protocol, its detail value objects and
failure taxonomy, the :class:`SecretResolver` protocol and implementations, and
the configurable mocks used by the domain tests.
"""

from .boto_client import BotoQuickAdminClient
from .client import (
    ConnectorDetail,
    DataSourceDetail,
    KnowledgeBaseDetail,
    PermissionDetail,
    QuickAdminApiError,
    QuickAdminClient,
    QuickAdminInternalError,
)
from .mock import ACTIONS, MockBehavior, MockQuickAdminClient, Mode, RecordedCall
from .secrets import (
    BotoSecretResolver,
    InMemorySecretResolver,
    SecretAccessError,
    SecretResolutionError,
    SecretResolver,
    is_secret_arn,
)

__all__ = [
    "ConnectorDetail",
    "DataSourceDetail",
    "KnowledgeBaseDetail",
    "PermissionDetail",
    "QuickAdminApiError",
    "QuickAdminInternalError",
    "QuickAdminClient",
    "BotoQuickAdminClient",
    "ACTIONS",
    "Mode",
    "MockBehavior",
    "RecordedCall",
    "MockQuickAdminClient",
    "SecretResolver",
    "SecretResolutionError",
    "SecretAccessError",
    "BotoSecretResolver",
    "InMemorySecretResolver",
    "is_secret_arn",
]
