"""Domain layer: connector catalog, suite gating, validation, idempotency,
stage grouping, and Tool_Result construction. Deterministic, pure business
logic — the focus of property-based testing.
"""

from quick_admin_buddy_mcp.domain.types import (
    CatalogEntry,
    ConnectorRecord,
    ConnectorStatus,
    OnboardingState,
    ResultStatus,
    Stage,
    Suite,
    ToolResult,
)

__all__ = [
    "CatalogEntry",
    "ConnectorRecord",
    "ConnectorStatus",
    "OnboardingState",
    "ResultStatus",
    "Stage",
    "Suite",
    "ToolResult",
]
