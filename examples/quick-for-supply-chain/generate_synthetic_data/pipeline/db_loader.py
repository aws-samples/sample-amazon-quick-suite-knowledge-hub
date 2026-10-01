#!/usr/bin/env python3
"""
generate_synthetic_data.pipeline.db_loader
═══════════════════════════════════════════════════════════════
Generic, database-agnostic entrypoint for creating tables and loading the
structured supply-chain data. The target engine is chosen by the ``DB_ENGINE``
environment variable (default: ``snowflake``):

    DB_ENGINE=snowflake   python -m generate_synthetic_data.pipeline.db_loader --use-copy
    DB_ENGINE=postgres    python -m generate_synthetic_data.pipeline.db_loader
    DB_ENGINE=sqlalchemy  SQLALCHEMY_URL=sqlite:///tmp.db python -m generate_synthetic_data.pipeline.db_loader

Flags (consistent across engines)
---------------------------------
  --generated-dir PATH  where per-table CSVs live (default: ./synthetic_data)
  --use-copy            use the bulk COPY path where the engine supports it
  --skip-create         skip DDL, load only
  --skip-load           create tables only
  --dry-run             print the plan; never connect or import a DB driver

Tables are created from the canonical Python schema
(:func:`generate_synthetic_data.generators.schema.create_table_ddl`) — there
are no ``.sql`` files. The factory (:func:`get_loader`) returns the
engine-specific loader; all loaders honor ``--dry-run`` without importing
their driver.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Support both "python -m generate_synthetic_data.pipeline.db_loader" and direct execution.
try:
    from .config import ConfigError, PipelineConfig
    from .loaders import (
        DEFAULT_GENERATED_DIR,
        get_loader,
    )
except ImportError:  # pragma: no cover - direct-run fallback
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    from generate_synthetic_data.pipeline.config import ConfigError, PipelineConfig
    from generate_synthetic_data.pipeline.loaders import (
        DEFAULT_GENERATED_DIR,
        get_loader,
    )

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("generate_synthetic_data.pipeline.db_loader")


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--generated-dir", default=str(DEFAULT_GENERATED_DIR))
    parser.add_argument(
        "--use-copy",
        action="store_true",
        help="Use the bulk COPY path where the engine supports it.",
    )
    parser.add_argument(
        "--skip-create", action="store_true", help="Skip DDL, load only."
    )
    parser.add_argument("--skip-load", action="store_true", help="Create tables only.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without connecting to any database.",
    )
    return parser


def run(
    argv: list[str] | None = None, *, description: str = "Database-agnostic loader."
) -> int:
    parser = build_parser(description)
    args = parser.parse_args(argv)

    # In dry-run we do NOT resolve any Secrets Manager ARN (no AWS call).
    try:
        cfg = PipelineConfig.load(resolve_secret=not args.dry_run)
        # Validate the selected engine's config up-front (skipped in dry-run so a
        # plan can print even with incomplete creds).
        if not args.dry_run:
            cfg.validate_active_db()
    except (ConfigError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 2

    logger.info("DB_ENGINE=%s", cfg.db_engine)

    try:
        loader = get_loader(cfg, dry_run=args.dry_run)
        if not args.skip_create:
            loader.create_tables()
        if not args.skip_load:
            strategy = loader.load(
                Path(args.generated_dir),
                use_copy=args.use_copy,
            )
            logger.info("Load strategy: %s", strategy)
    except (ConfigError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.error("%s load failed: %s", cfg.db_engine, exc)
        return 1

    if not args.dry_run:
        logger.info("✅ %s loader finished.", cfg.db_engine)
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(
        argv, description="Database-agnostic structured-data loader (honors DB_ENGINE)."
    )


if __name__ == "__main__":
    raise SystemExit(main())
