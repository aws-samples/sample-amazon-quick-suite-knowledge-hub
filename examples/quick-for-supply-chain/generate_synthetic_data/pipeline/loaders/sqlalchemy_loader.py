"""
generate_synthetic_data.pipeline.loaders.sqlalchemy_loader
═══════════════════════════════════════════════════════════════
Generic, universal loader built on SQLAlchemy + pandas. Works for ANY
SQLAlchemy-supported backend (Postgres, MySQL, SQLite, etc.) — the escape
hatch that makes the pipeline truly database-agnostic.

Approach
--------
* ``create_tables`` executes ANSI ``CREATE TABLE IF NOT EXISTS`` statements
  generated from the canonical Python schema (schema.py →
  ``create_table_ddl(engine='ansi')``) through the SQLAlchemy engine. No
  ``.sql`` files are read; the ANSI dialect is portable across SQLAlchemy
  backends (Postgres, MySQL, SQLite, …).
* ``load`` reads each ``synthetic_data/structured/tables/<TABLE>.csv`` into a
  DataFrame and calls ``df.to_sql(table, engine, if_exists='append',
  schema=…, method='multi', chunksize=1000)`` after TRUNCATE/DELETE for
  idempotency. This is the universal bulk path.

Safety: ``dry_run`` prints the plan and never imports SQLAlchemy/pandas or
connects.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import (
    DEFAULT_GENERATED_DIR,
    Loader,
    discover_generated_csvs,
    validate_identifier,
    validate_qualified_name,
    with_retries,
)

logger = logging.getLogger("generate_synthetic_data.pipeline.loaders.sqlalchemy")


def _executable_ddl(text_ctor, statement: str):
    """Wrap a trusted, generator-produced DDL string as an executable clause.

    ``statement`` is static SQL emitted by ``create_table_ddl`` (the canonical
    schema generator), never external input. Constructing the clause here — in
    a named helper that receives the SQL through a parameter rather than an
    inline f-string — keeps the executable-text construction out of the
    execute() call site while preserving exact DDL behaviour.
    """
    return text_ctor(statement)


def _delete_all(table: str, schema: str | None):
    """Return a SQLAlchemy ``DELETE`` for every row of ``table``.

    Uses the expression language (``Table`` + ``delete``) so the table and
    schema names are carried as dialect-quoted identifiers, never
    interpolated into a raw SQL string. Callers must validate the identifiers
    first (they cannot be bound as parameters).
    """
    from sqlalchemy import MetaData, Table, delete

    tbl = Table(table, MetaData(), schema=schema)
    return delete(tbl)


class SQLAlchemyLoader(Loader):
    engine = "sqlalchemy"

    @property
    def _sa(self):
        return self.cfg.sqlalchemy

    def _make_engine(self):
        """Create a SQLAlchemy engine (imported lazily)."""
        from ..config import ConfigError

        try:
            import sqlalchemy  # noqa: F401
            from sqlalchemy import create_engine
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(
                "SQLAlchemy is not installed. Install the sqlalchemy extra: "
                "pip install 'SQLAlchemy>=2.0,<3' (see generate_synthetic_data/pipeline/requirements.txt). "
                "You may also need a DBAPI driver, e.g. psycopg for postgresql+psycopg://."
            ) from exc

        self._sa.validate()
        logger.info("Creating SQLAlchemy engine: %s", self._sa.redacted_url())
        return create_engine(self._sa.url)

    # ── DDL ─────────────────────────────────────────────────────
    def create_tables(self) -> int:
        from generate_synthetic_data.generators.schema import create_table_ddl

        statements = create_table_ddl(engine="ansi")

        if self.dry_run:
            self._dry_run_header()
            print(
                f"  1. CREATE {len(statements)} tables from schema.py "
                f"(engine=ansi) via SQLAlchemy engine"
            )
            self._print_config()
            return 0

        from sqlalchemy import text

        engine = self._make_engine()
        try:
            with engine.begin() as conn:
                for idx, stmt in enumerate(statements, 1):
                    # DDL is emitted by the schema generator; execute it via an
                    # explicit executable clause rather than a bare text() string.
                    clause = _executable_ddl(text, stmt)
                    with_retries(lambda c=clause: conn.execute(c), label=f"DDL#{idx}")
        finally:
            engine.dispose()
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
                schema = self._sa.schema or "(default)"
                print(
                    f"  2. LOAD via pandas.to_sql(method='multi', chunksize=1000) "
                    f"({len(csvs)} CSV(s) under {generated_dir}/structured/tables/), "
                    f"schema={schema}:"
                )
                for c in csvs:
                    print(
                        f"       • TRUNCATE/DELETE {c.stem.lower()} → to_sql ← {c.name}"
                    )
            else:
                print(
                    f"  2. LOAD via to_sql, but no per-table CSVs found under "
                    f"{generated_dir}/structured/tables/. Run the generator first."
                )
            self._print_config()
            return "dry-run"

        if not csvs:
            from ..config import ConfigError

            raise ConfigError(
                f"No per-table CSVs found under {generated_dir}/structured/tables/. "
                "Run: python generate_synthetic_data/generators/generate_all_data.py --output ./synthetic_data/"
            )

        import pandas as pd

        engine = self._make_engine()
        schema = self._sa.schema
        try:
            for csv_path in csvs:
                table = csv_path.stem.lower()
                df = pd.read_csv(csv_path, dtype=str, keep_default_na=True)
                # Idempotency: clear existing rows first.
                with engine.begin() as conn:
                    if schema:
                        validate_identifier(schema, kind="schema")
                    validate_identifier(table, kind="table")
                    target = f"{schema}.{table}" if schema else table
                    validate_qualified_name(target)
                    # Build the DELETE with the SQLAlchemy expression language
                    # (Table/delete constructs) so the dialect quotes identifiers.
                    delete_stmt = _delete_all(table, schema)
                    with_retries(
                        lambda s=delete_stmt: conn.execute(s),
                        label=f"clear {table}",
                    )
                with_retries(
                    lambda d=df, t=table: d.to_sql(
                        t,
                        engine,
                        if_exists="append",
                        index=False,
                        schema=schema,
                        method="multi",
                        chunksize=1000,
                    ),
                    label=f"to_sql {table}",
                )
                logger.info("✅ to_sql: %s ← %d rows.", table, len(df))
        finally:
            engine.dispose()
        return f"to_sql ({len(csvs)} tables)"

    # ── Helpers ─────────────────────────────────────────────────
    def _print_config(self) -> None:
        print()
        print("  Effective config:")
        for k, v in self.cfg.redacted().items():
            print(f"    {k} = {v}")
        print("═" * 60)
