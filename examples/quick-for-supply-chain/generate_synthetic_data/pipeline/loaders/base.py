"""
generate_synthetic_data.pipeline.loaders.base
═══════════════════════════════════════════════════════════════
Common interface + shared helpers for engine-specific structured-data
loaders, plus a :func:`get_loader` factory that returns the right loader
based on ``DB_ENGINE`` (via :class:`PipelineConfig`).

Design
------
Every loader implements the same two operations:

    create_tables()                  — create the schema/tables from the
                                        canonical Python schema (schema.py →
                                        create_table_ddl), no .sql files.
    load(tables_dir)                 — bulk-load data from per-table CSVs.

All loaders support a ``dry_run`` flag: when True they print a coherent plan
and NEVER open a network connection or import a DB driver. This lets
``--dry-run`` work even when psycopg / SQLAlchemy are not installed.

Retries with exponential backoff are provided via :func:`with_retries` for the
transient errors that matter (network blips, throttling, warehouse resume).
"""

from __future__ import annotations

import abc
import logging
import re
from pathlib import Path
from threading import Event

logger = logging.getLogger("generate_synthetic_data.pipeline.loaders")

_POLL_IDLE = Event()


def _poll_wait(seconds: float) -> None:
    """Wait `seconds` between polls."""
    _POLL_IDLE.wait(timeout=seconds)


# ── Project paths (repo root is four levels up: generate_synthetic_data/pipeline/loaders/) ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# Generated data root (per-table CSVs live under <root>/structured/tables/).
DEFAULT_GENERATED_DIR = PROJECT_ROOT / "synthetic_data"

# Transient error substrings worth retrying across engines.
_RETRYABLE_SUBSTRINGS = (
    "timeout",
    "connection",
    "temporarily",
    "try again",
    "could not connect",
    "server closed",
    "503",
    "429",
    "warehouse is being resumed",
)


# ── SQL identifier safety (shared) ──────────────────────────────
# SQL table/schema/column names cannot be passed as bind parameters — only
# *values* can. To keep dynamically-built DDL/COPY statements injection-safe,
# every identifier that is interpolated into a SQL string MUST first pass
# through validation here. Identifiers in this pipeline are derived from CSV
# filenames and config, but validating them closes the injection vector even
# if a hostile filename (e.g. "t; DROP TABLE x --") were ever introduced.

# A safe unquoted SQL identifier: starts with a letter/underscore, followed by
# letters, digits or underscores. Optionally a single schema-qualifier dot.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(name: str, *, kind: str = "identifier") -> str:
    """Return ``name`` if it is a safe SQL identifier, else raise ValueError.

    Rejects anything containing whitespace, quotes, semicolons, comment
    markers, or other characters that could break out of an identifier
    position and inject SQL. Use this on every table/schema/column name
    before interpolating it into a SQL statement.
    """
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(
            f"Unsafe SQL {kind}: {name!r}. Identifiers must match "
            f"[A-Za-z_][A-Za-z0-9_]* (no quotes, spaces, dots or semicolons)."
        )
    return name


def validate_qualified_name(name: str) -> str:
    """Validate a possibly schema-qualified name like ``schema.table``.

    Each dot-separated part must independently be a safe identifier.
    """
    parts = name.split(".")
    for part in parts:
        validate_identifier(part, kind="identifier part")
    return name


# ── SQL parsing (shared) ────────────────────────────────────────
def split_sql_statements(sql_text: str) -> list[str]:
    """Split a SQL script into individual statements on ';'.

    Handles single/double-quoted strings and line/block comments so a ';'
    inside a string literal or comment does not split a statement. Pragmatic
    (not a full parser) but sufficient for the DDL/DML scripts in this repo.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql_text)
    in_single = in_double = in_line_comment = in_block_comment = False

    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)

    cleaned = []
    for s in statements:
        stripped = "\n".join(
            line for line in s.splitlines() if not line.strip().startswith("--")
        ).strip()
        if stripped:
            cleaned.append(s)
    return cleaned


# ── Retry helper (shared) ───────────────────────────────────────
def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(sub in msg for sub in _RETRYABLE_SUBSTRINGS)


def with_retries(
    fn, *, attempts: int = 3, base_delay: float = 1.5, label: str = "operation"
):
    """Call ``fn`` with exponential backoff on transient errors."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            last_exc = exc
            if attempt >= attempts or not _is_retryable(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                label,
                attempt,
                attempts,
                exc,
                delay,
            )
            _poll_wait(delay)
    raise last_exc  # pragma: no cover - unreachable when attempts >= 1


def discover_generated_csvs(generated_dir: Path) -> list[Path]:
    """Find per-table CSVs under ``synthetic_data/structured/tables/<TABLE>.csv``.

    Returns an empty list when none are present.
    """
    tables_dir = Path(generated_dir) / "structured" / "tables"
    if not tables_dir.exists():
        return []
    return sorted(tables_dir.glob("*.csv"))


# ── Abstract loader interface ───────────────────────────────────
class Loader(abc.ABC):
    """Common interface for all structured-data loaders.

    Subclasses implement :meth:`create_tables` and :meth:`load`. When
    ``dry_run`` is True, implementations must print their plan and avoid
    opening any connection or importing a DB driver.
    """

    #: Human-readable engine name (e.g. "snowflake").
    engine: str = "base"

    def __init__(self, cfg, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run

    @abc.abstractmethod
    def create_tables(self) -> int:
        """Create schema/tables from schema.py DDL. Returns statements executed."""

    @abc.abstractmethod
    def load(
        self,
        tables_dir: Path = DEFAULT_GENERATED_DIR,
        *,
        use_copy: bool = False,
    ) -> str:
        """Load structured data from per-table CSVs. Returns a strategy string."""

    # Shared dry-run banner helper.
    def _dry_run_header(self) -> None:
        print("═" * 60)
        print(f"  DB Loader — DRY RUN (engine={self.engine}, no connection opened)")
        print("═" * 60)


# ── Factory ─────────────────────────────────────────────────────
def get_loader(cfg, *, dry_run: bool = False) -> Loader:
    """Return the loader implementation for ``cfg.db_engine``.

    Loader modules import their DB driver lazily (inside methods), so importing
    them here is safe even when a driver is not installed — essential for
    ``--dry-run``.
    """
    cfg.validate_engine()
    engine = cfg.db_engine

    if engine == "snowflake":
        from .snowflake_loader_engine import SnowflakeLoader

        return SnowflakeLoader(cfg, dry_run=dry_run)
    if engine == "postgres":
        from .postgres_loader import PostgresLoader

        return PostgresLoader(cfg, dry_run=dry_run)
    if engine == "sqlalchemy":
        from .sqlalchemy_loader import SQLAlchemyLoader

        return SQLAlchemyLoader(cfg, dry_run=dry_run)

    from ..config import ConfigError

    raise ConfigError(f"Unsupported DB_ENGINE '{engine}'.")
