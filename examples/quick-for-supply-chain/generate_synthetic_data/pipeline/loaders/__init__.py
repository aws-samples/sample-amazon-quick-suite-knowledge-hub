"""Engine-specific structured-data loaders for the supply-chain pipeline.

A common :class:`Loader` interface (``create_tables`` + ``load`` + ``dry_run``)
is implemented by one module per engine. Use :func:`get_loader` to obtain the
right loader for the configured ``DB_ENGINE``::

    from generate_synthetic_data.pipeline.config import PipelineConfig
    from generate_synthetic_data.pipeline.loaders import get_loader

    cfg = PipelineConfig.load(resolve_secret=False)
    loader = get_loader(cfg, dry_run=True)
    loader.create_tables()
    loader.load()

Driver imports (snowflake-connector, psycopg, SQLAlchemy) are lazy — done
inside methods — so ``--dry-run`` works even when a driver is not installed.
"""
from .base import (
    DEFAULT_GENERATED_DIR,
    Loader,
    discover_generated_csvs,
    get_loader,
    split_sql_statements,
    with_retries,
)

__all__ = [
    "Loader",
    "get_loader",
    "split_sql_statements",
    "with_retries",
    "discover_generated_csvs",
    "DEFAULT_GENERATED_DIR",
]
