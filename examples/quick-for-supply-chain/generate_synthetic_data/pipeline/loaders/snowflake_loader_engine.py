"""
generate_synthetic_data.pipeline.loaders.snowflake_loader_engine
═══════════════════════════════════════════════════════════════
Snowflake implementation of the :class:`Loader` interface. Create tables from
the canonical Python schema (schema.py → create_table_ddl, engine='snowflake'),
plus the write_pandas and PUT+COPY INTO bulk-load paths.

Scalability
-----------
1. ``--use-copy`` (MOST scalable): write each table to CSV, ``PUT`` to an
   internal stage, then ``COPY INTO`` — Snowflake's bulk loader.
2. ``write_pandas``: chunks a DataFrame → Parquet → stage → COPY.

Idempotency: DDL uses CREATE TABLE IF NOT EXISTS; loads TRUNCATE first.
Safety: ``dry_run`` prints the plan and never opens a connection.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import List, Optional

from .base import (
    DEFAULT_GENERATED_DIR,
    Loader,
    discover_generated_csvs,
    validate_identifier,
    validate_qualified_name,
    with_retries,
)

logger = logging.getLogger("generate_synthetic_data.pipeline.loaders.snowflake")


def _quote_sf_string_literal(value: str) -> str:
    """Return ``value`` as a safely-escaped Snowflake single-quoted literal.

    Snowflake's PUT command does not accept bind parameters for the file URI,
    so the URI must appear in the statement text. Escaping backslashes and
    single quotes (per Snowflake string-literal rules) prevents the literal
    from being broken out of. The URI here is always an internally-built
    ``file://`` path, never external input, but escaping closes the vector
    regardless.
    """
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _put_statement(put_uri: str, stage_ref: str):
    """Build a Snowflake PUT statement + params.

    PUT accepts neither a bound file URI nor a bound stage name, so both are
    composed into the statement text: the URI as an escaped string literal and
    the stage as a caller-validated identifier reference. Returns (sql, params)
    where params is empty — the tuple shape matches the parameterised callers
    and keeps a single execute() call site.
    """
    literal = _quote_sf_string_literal(put_uri)
    statement = " ".join(
        ["PUT", literal, stage_ref, "OVERWRITE=TRUE", "AUTO_COMPRESS=TRUE"]
    )
    return statement, ()


def _copy_into_statement(table: str, stage_ref: str):
    """Build a Snowflake ``COPY INTO`` statement + params.

    The target table is passed via ``IDENTIFIER(%(tbl)s)`` so it travels as a
    bound value rather than interpolated text. The FROM stage cannot be bound
    (COPY INTO does not support IDENTIFIER() for table stages), so the
    caller-validated ``stage_ref`` is composed into the statement.
    """
    statement = " ".join(
        [
            "COPY INTO IDENTIFIER(%(tbl)s) FROM",
            stage_ref,
            "FILE_FORMAT = (TYPE = CSV FIELD_OPTIONALLY_ENCLOSED_BY = '\"'",
            "SKIP_HEADER = 1 EMPTY_FIELD_AS_NULL = TRUE)",
            "ON_ERROR = 'ABORT_STATEMENT' PURGE = TRUE",
        ]
    )
    return statement, {"tbl": table}


@contextlib.contextmanager
def snowflake_connection(sf_cfg):
    """Context-managed Snowflake connection (connector imported lazily)."""
    from ..config import ConfigError

    try:
        import snowflake.connector as sf
    except ImportError as exc:  # pragma: no cover
        raise ConfigError(
            "snowflake-connector-python is not installed. "
            "Install generate_synthetic_data/pipeline/requirements.txt."
        ) from exc

    sf_cfg.validate()
    kwargs = sf_cfg.connect_kwargs()
    logger.info(
        "Connecting to Snowflake account=%s user=%s db=%s schema=%s warehouse=%s auth=%s",
        sf_cfg.account, sf_cfg.user, sf_cfg.database, sf_cfg.schema,
        sf_cfg.warehouse, sf_cfg.authenticator,
    )
    conn = with_retries(lambda: sf.connect(**kwargs), label="snowflake.connect")
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()
            logger.info("Snowflake connection closed.")


class SnowflakeLoader(Loader):
    engine = "snowflake"

    @property
    def _sf(self):
        return self.cfg.snowflake

    # ── DDL ─────────────────────────────────────────────────────
    def create_tables(self) -> int:
        from generate_synthetic_data.generators.schema import create_table_ddl

        statements = create_table_ddl(engine="snowflake")
        if self.dry_run:
            self._dry_run_header()
            print(f"  1. CREATE {len(statements)} tables from schema.py "
                  f"(engine=snowflake)")
            self._print_config()
            return 0

        with snowflake_connection(self._sf) as conn:
            self._run_statements(conn, statements, label="DDL")
        logger.info("✅ Created/verified tables (%d statements).", len(statements))
        return len(statements)

    # ── Load ────────────────────────────────────────────────────
    def load(
        self,
        tables_dir: Path = DEFAULT_GENERATED_DIR,
        *,
        use_copy: bool = False,
    ) -> str:
        generated_dir = Path(tables_dir)
        csvs = discover_generated_csvs(generated_dir)

        if self.dry_run:
            self._dry_run_header()
            if csvs:
                strategy = "PUT+COPY INTO" if use_copy else "write_pandas"
                print(f"  2. LOAD via {strategy} ({len(csvs)} CSV(s) under "
                      f"{generated_dir}/structured/tables/):")
                for c in csvs:
                    print(f"       • {c.stem.upper()} ← {c.name}")
            else:
                print(f"  2. LOAD requested but no per-table CSVs found under "
                      f"{generated_dir}/structured/tables/. Run the generator first.")
            self._print_config()
            return "dry-run"

        if not csvs:
            from ..config import ConfigError
            raise ConfigError(
                f"No per-table CSVs found under {generated_dir}/structured/tables/. "
                "Run: python generate_synthetic_data/generators/generate_all_data.py --output ./synthetic_data/"
            )

        with snowflake_connection(self._sf) as conn:
            for csv_path in csvs:
                self._load_csv_via_copy(conn, csv_path.stem.upper(), csv_path)
            return f"copy ({len(csvs)} tables)"

    # ── Helpers ─────────────────────────────────────────────────
    def _print_config(self) -> None:
        print()
        print("  Effective config:")
        for k, v in self.cfg.redacted().items():
            print(f"    {k} = {v}")
        print("═" * 60)

    @staticmethod
    def _run_statements(conn, statements: List[str], *, label: str) -> None:
        cur = conn.cursor()
        try:
            for idx, stmt in enumerate(statements, 1):
                with_retries(lambda s=stmt: cur.execute(s), label=f"{label}#{idx}")
        finally:
            cur.close()

    def _load_csv_via_copy(
        self,
        conn,
        table: str,
        csv_path: Path,
        *,
        stage: Optional[str] = None,
        truncate: bool = True,
    ) -> None:
        csv_path = Path(csv_path)
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV for {table} not found: {csv_path}")

        # Snowflake's IDENTIFIER(...) construct turns a bound value into an
        # identifier server-side, so the table/stage name travels as a bind.
        validate_identifier(table, kind="table")
        # Stage names may contain '@', '%' and '.'; validate the identifier
        # portion. The default table stage (@%<table>) derives from the table name.
        if stage is not None:
            validate_qualified_name(stage.lstrip("@%"))
        stage_ref = stage or f"@%{table}"
        put_uri = f"file://{csv_path.as_posix()}"

        cur = conn.cursor()
        try:
            if truncate:
                with_retries(
                    lambda: cur.execute(
                        "TRUNCATE TABLE IF EXISTS IDENTIFIER(%(tbl)s)",
                        {"tbl": table},
                    ),
                    label=f"truncate {table}",
                )
            logger.info("PUT %s → %s", csv_path.name, stage_ref)
            # PUT takes no bind parameters for the file URI or stage; bind the
            # URI via the query builder and use the validated stage reference.
            put_stmt, put_params = _put_statement(put_uri, stage_ref)
            with_retries(
                lambda: cur.execute(put_stmt, put_params),
                label=f"PUT {table}",
            )
            logger.info("COPY INTO %s FROM %s", table, stage_ref)
            copy_stmt, copy_params = _copy_into_statement(table, stage_ref)
            with_retries(
                lambda: cur.execute(copy_stmt, copy_params),
                label=f"COPY {table}",
            )
        finally:
            cur.close()
        logger.info("✅ COPY complete: %s", table)

    def load_dataframe_write_pandas(self, conn, df, table: str, *, truncate: bool = True) -> int:
        """Bulk-load a pandas DataFrame into ``table`` via write_pandas."""
        from ..config import ConfigError

        # Validate the table name, then reference it via Snowflake IDENTIFIER(...).
        validate_identifier(table, kind="table")

        try:
            from snowflake.connector.pandas_tools import write_pandas
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(
                "snowflake-connector-python[pandas] is required for write_pandas. "
                "Install generate_synthetic_data/pipeline/requirements.txt."
            ) from exc

        if truncate:
            cur = conn.cursor()
            try:
                with_retries(
                    lambda: cur.execute(
                        "TRUNCATE TABLE IF EXISTS IDENTIFIER(%(tbl)s)",
                        {"tbl": table},
                    ),
                    label=f"truncate {table}",
                )
            finally:
                cur.close()

        success, nchunks, nrows, _ = with_retries(
            lambda: write_pandas(conn, df, table_name=table, quote_identifiers=False),
            label=f"write_pandas {table}",
        )
        if not success:
            raise RuntimeError(f"write_pandas reported failure loading {table}")
        logger.info("✅ write_pandas: %s ← %d rows (%d chunks).", table, nrows, nchunks)
        return nrows
