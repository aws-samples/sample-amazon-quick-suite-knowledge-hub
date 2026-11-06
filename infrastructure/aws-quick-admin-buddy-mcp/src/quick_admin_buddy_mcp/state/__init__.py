"""State layer: durable persistence of Onboarding_State with tenant isolation.

Defines the StateRepository interface, an in-memory fake for tests, and the
DynamoDB-backed implementation used at runtime.
"""

from quick_admin_buddy_mcp.state.dynamodb import DynamoDbStateRepository
from quick_admin_buddy_mcp.state.repository import (
    InMemoryStateRepository,
    StateRepository,
    StateStoreError,
    TenantId,
)

__all__ = [
    "DynamoDbStateRepository",
    "InMemoryStateRepository",
    "StateRepository",
    "StateStoreError",
    "TenantId",
]
