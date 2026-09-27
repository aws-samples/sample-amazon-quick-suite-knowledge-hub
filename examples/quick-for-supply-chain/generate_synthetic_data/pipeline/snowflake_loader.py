#!/usr/bin/env python3
"""
generate_synthetic_data.pipeline.snowflake_loader
═══════════════════════════════════════════════════════════════
Thin, BACKWARD-COMPATIBLE entrypoint for the Snowflake load path.

    python -m generate_synthetic_data.pipeline.snowflake_loader [--use-copy] [--skip-create]
                                              [--skip-load] [--dry-run] …

still works exactly as before. It now delegates to the shared, engine-aware
:mod:`generate_synthetic_data.pipeline.db_loader` factory. To keep prior behavior unchanged, this
module forces ``DB_ENGINE=snowflake`` unless the caller has already set it —
so existing scripts and docs keep working, while the new
:mod:`generate_synthetic_data.pipeline.db_loader` entrypoint (``python -m generate_synthetic_data.pipeline.db_loader``)
targets ANY engine via ``DB_ENGINE``.

All the actual Snowflake logic (create tables from schema.py, write_pandas,
PUT+COPY INTO) now lives in
:mod:`generate_synthetic_data.pipeline.loaders.snowflake_loader_engine`.
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

# Support both "python -m generate_synthetic_data.pipeline.snowflake_loader" and direct execution.
try:
    from .db_loader import run
except ImportError:  # pragma: no cover - direct-run fallback
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from generate_synthetic_data.pipeline.db_loader import run


def main(argv: Optional[List[str]] = None) -> int:
    # Preserve the original behavior: this entrypoint defaults to Snowflake.
    # Only set DB_ENGINE when the caller has not chosen one explicitly.
    os.environ.setdefault("DB_ENGINE", "snowflake")
    return run(argv, description="Create + load Snowflake structured data (DB_ENGINE=snowflake).")


if __name__ == "__main__":
    raise SystemExit(main())
