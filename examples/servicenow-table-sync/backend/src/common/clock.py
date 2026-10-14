"""Shared UTC clock used by extraction activities."""

from datetime import UTC, datetime


class Clock:
    """Provide UTC timestamps and ingestion dates."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today_partition(self) -> str:
        return self.now().strftime("%Y-%m-%d")
