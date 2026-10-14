"""Typed view of a ServiceNow record returned by PySNC."""

from pydantic import BaseModel, ConfigDict, Field

_SYS_ID_FIELD = "sys_id"
_UPDATED_ON_FIELD = "sys_updated_on"


class ServiceNowRecord(BaseModel):
    """One display-resolved ServiceNow row."""

    model_config = ConfigDict(frozen=True)

    fields: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_serialized(cls, row: dict[str, object]) -> "ServiceNowRecord":
        """Convert a PySNC serialized row into string-valued fields."""
        return cls(fields={key: "" if value is None else str(value) for key, value in row.items()})

    @property
    def sys_id(self) -> str | None:
        """Return the record identifier when present."""
        return self.fields.get(_SYS_ID_FIELD) or None

    @property
    def updated_on(self) -> str | None:
        """Return the incremental update timestamp when present."""
        return self.fields.get(_UPDATED_ON_FIELD) or None
