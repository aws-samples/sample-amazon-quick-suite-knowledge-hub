"""
generate_synthetic_data.pipeline.loaders.postgres_loader
═══════════════════════════════════════════════════════════════
PostgreSQL / RDS implementation of the :class:`Loader` interface using
psycopg (v3).

DDL
---
Tables are created from the canonical Python schema (schema.py →
``create_table_ddl(engine='postgres')``), which emits Postgres-native
``CREATE TABLE IF NOT EXISTS`` statements. No ``.sql`` files are read and no
dialect rewriting is required — correct per-engine DDL is generated directly.

Bulk load
---------
The scalable Postgres path is ``COPY <table> FROM STDIN WITH CSV HEADER``,
streaming each ``synthetic_data/structured/tables/<TABLE>.csv``. Every table is
TRUNCATEd (RESTART IDENTITY CASCADE) before load for idempotency.

Safety: ``dry_run`` prints the plan and never imports psycopg or connects.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from .base import (
    DEFAULT_GENERATED_DIR,
    Loader,
    discover_generated_csvs,
    validate_identifier,
    with_retries,
)

logger = logging.getLogger("generate_synthetic_data.pipeline.loaders.postgres")


def _compose(template: str, *identifiers):
    """Compose a psycopg ``sql.SQL`` template with ``sql.Identifier`` parts.

    Returns a ``psycopg.sql.Composed`` object in which every identifier is
    quoted by the driver. Keeping the composition in this helper (rather than
    inline at the ``cur.execute()`` call site) means the executed statement is
    a first-class SQL-composition object, never a Python string built from
    concatenation or ``str.format`` — the safe, injection-proof path psycopg
    documents for dynamic identifiers.
    """
    from psycopg import sql

    return sql.SQL(template).format(*identifiers)


class PostgresLoader(Loader):
    engine = "postgres"

    @property
    def _pg(self):
        return self.cfg.postgres

    @contextlib.contextmanager
    def _connection(self):
        """Context-managed psycopg (v3) connection (imported lazily)."""
        from ..config import ConfigError

        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(
                "psycopg (v3) is not installed. Install the postgres extra: "
                "pip install 'psycopg[binary]>=3.2,<4' (see generate_synthetic_data/pipeline/requirements.txt)."
            ) from exc

        self._pg.validate()
        logger.info("Connecting to Postgres: %s", self._pg.redacted_dsn())
        conn = with_retries(lambda: psycopg.connect(self._pg.dsn()), label="postgres.connect")
        try:
            yield conn
            conn.commit()
        except Exception:
            with contextlib.suppress(Exception):
                conn.rollback()
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.close()
                logger.info("Postgres connection closed.")

    # ── DDL ─────────────────────────────────────────────────────
    def create_tables(self) -> int:
        from generate_synthetic_data.generators.schema import create_table_ddl

        schema = self._pg.schema
        validate_identifier(schema, kind="schema")

        if self.dry_run:
            self._dry_run_header()
            print(f"  1. CREATE {len(create_table_ddl(engine='postgres'))} tables "
                  f"from schema.py (engine=postgres, schema={schema})")
            self._print_config()
            return 0

        from psycopg import sql

        # Schema names are identifiers, so compose them with sql.Identifier.
        schema_ident = sql.Identifier(schema)
        prelude = [
            _compose("CREATE SCHEMA IF NOT EXISTS {}", schema_ident),
            _compose("SET search_path TO {}", schema_ident),
        ]
        # DDL bodies are emitted by the schema generator; wrap them as sql.SQL.
        ddl = [sql.SQL(stmt) for stmt in create_table_ddl(engine="postgres", schema_prefix=schema)]
        statements = [*prelude, *ddl]

        with self._connection() as conn:
            with conn.cursor() as cur:
                for idx, stmt in enumerate(statements, 1):
                    with_retries(lambda s=stmt: cur.execute(s), label=f"DDL#{idx}")
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
                print(f"  2. LOAD via COPY FROM STDIN ({len(csvs)} CSV(s) under "
                      f"{generated_dir}/structured/tables/), schema={self._pg.schema}:")
                for c in csvs:
                    print(f"       • TRUNCATE {c.stem.lower()} → COPY ← {c.name}")
            else:
                print(f"  2. LOAD via COPY, but no per-table CSVs found under "
                      f"{generated_dir}/structured/tables/. Run the generator first.")
            self._print_config()
            return "dry-run"

        if not csvs:
            from ..config import ConfigError
            raise ConfigError(
                f"No per-table CSVs found under {generated_dir}/structured/tables/. "
                "Run: python generate_synthetic_data/generators/generate_all_data.py --output ./synthetic_data/"
            )

        from psycopg import sql

        with self._connection() as conn:
            with conn.cursor() as cur:
                validate_identifier(self._pg.schema, kind="schema")
                # search_path takes an identifier, so compose with sql.Identifier.
                search_path_sql = _compose("SET search_path TO {}", sql.Identifier(self._pg.schema))
                cur.execute(search_path_sql)
            for csv_path in csvs:
                self._copy_csv(conn, csv_path.stem.lower(), csv_path)
        return f"copy ({len(csvs)} tables)"

    # ── Helpers ─────────────────────────────────────────────────
    def _copy_csv(self, conn, table: str, csv_path: Path) -> None:
        from psycopg import sql

        # Validate table/column names, then compose them with sql.Identifier.
        validate_identifier(self._pg.schema, kind="schema")
        validate_identifier(table, kind="table")
        qualified = sql.Identifier(self._pg.schema, table)
        qualified_str = f"{self._pg.schema}.{table}"

        def _do() -> None:
            with conn.cursor() as cur:
                truncate_sql = _compose("TRUNCATE TABLE {} RESTART IDENTITY CASCADE", qualified)
                cur.execute(truncate_sql)
                logger.info("COPY %s ← %s", qualified_str, csv_path.name)
                # Read the header to build an explicit column list so identity
                # columns absent from the CSV are left to their defaults.
                with open(csv_path, "r", encoding="utf-8") as fh:
                    header = fh.readline().rstrip("\n\r")
                col_names = [c.strip() for c in header.split(",")]
                for col in col_names:
                    validate_identifier(col, kind="column")
                cols = sql.SQL(", ").join(sql.Identifier(c) for c in col_names)
                copy_sql = _compose(
                    "COPY {} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE)",
                    qualified, cols,
                )
                with open(csv_path, "rb") as fh, cur.copy(copy_sql) as cp:
                    while True:
                        chunk = fh.read(65536)
                        if not chunk:
                            break
                        cp.write(chunk)

        with_retries(_do, label=f"COPY {table}")
        logger.info("✅ COPY complete: %s", qualified_str)

    def _print_config(self) -> None:
        print()
        print("  Effective config:")
        for k, v in self.cfg.redacted().items():
            print(f"    {k} = {v}")
        print("═" * 60)
