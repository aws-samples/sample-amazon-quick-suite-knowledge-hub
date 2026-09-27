#!/usr/bin/env python3
"""
generate_synthetic_data.pipeline.salesforce_prep
═══════════════════════════════════════════════════════════════
Prepare Salesforce CRM import files and validate them.

Default behavior (no live call)
-------------------------------
1. Ensure Salesforce CSVs exist under ``synthetic_data/salesforce/``; if missing,
   invoke ``generate_synthetic_data/generators/generate_all_data.py --salesforce-only``.
2. Validate required columns per object and the referential-integrity load
   order (Accounts → Contacts → Opportunities).
3. Print the exact Dataloader load order + instructions.

Optional live load
-------------------
If ``simple-salesforce`` is installed AND ``SF_USERNAME`` / ``SF_PASSWORD`` /
``SF_TOKEN`` are set, :func:`load_salesforce` performs a Bulk API upsert on the
``External_ID__c`` field. This is opt-in via ``--load``; default is prep only.

``--dry-run`` prints the plan and never calls out.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

_run = subprocess.run

try:
    from .config import ConfigError
except ImportError:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from generate_synthetic_data.pipeline.config import ConfigError

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("generate_synthetic_data.pipeline.salesforce_prep")

# Module now lives at generate_synthetic_data/pipeline/, so repo root is three levels up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GENERATOR = PROJECT_ROOT / "generate_synthetic_data" / "generators" / "generate_all_data.py"
DEFAULT_SF_DIR = PROJECT_ROOT / "synthetic_data" / "salesforce"

# FK-safe load order + required columns per object.
LOAD_ORDER: List[str] = ["Accounts", "Contacts", "Opportunities"]
REQUIRED_COLUMNS: Dict[str, List[str]] = {
    "Accounts": ["External_ID__c", "Name"],
    "Contacts": ["Account_External_ID__c", "LastName"],
    "Opportunities": [
        "External_ID__c",
        "Account_External_ID__c",
        "Name",
        "StageName",
        "CloseDate",
    ],
}
# Object → Salesforce API name for the live Bulk upsert.
SF_OBJECT_API = {
    "Accounts": "Account",
    "Contacts": "Contact",
    "Opportunities": "Opportunity",
}


def ensure_csvs(sf_dir: Path = DEFAULT_SF_DIR, dry_run: bool = False) -> Path:
    """Ensure Salesforce CSVs exist; generate them if any are missing."""
    sf_dir = Path(sf_dir)
    expected = [sf_dir / f"{obj}.csv" for obj in LOAD_ORDER]
    missing = [p for p in expected if not p.exists()]

    if not missing:
        logger.info("Salesforce CSVs present in %s", sf_dir)
        return sf_dir

    output_root = sf_dir.parent  # generate_all_data writes <output>/salesforce/
    cmd = [sys.executable, str(GENERATOR), "--salesforce-only", "--output", str(output_root)]
    if dry_run:
        logger.info("[dry-run] Missing CSVs %s — would run: %s",
                    [p.name for p in missing], " ".join(cmd))
        return sf_dir

    logger.info("Missing CSVs %s — generating via %s",
                [p.name for p in missing], GENERATOR.name)
    result = _run(
        cmd, capture_output=True, text=True, shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"generate_all_data.py --salesforce-only failed: {result.stderr[:500]}"
        )
    logger.info("✅ Generated Salesforce CSVs in %s", sf_dir)
    return sf_dir


def validate_csvs(sf_dir: Path = DEFAULT_SF_DIR) -> Dict[str, int]:
    """Validate required columns + FK order. Returns {object: row_count}.

    Raises ConfigError on the first structural problem.
    """
    sf_dir = Path(sf_dir)
    counts: Dict[str, int] = {}
    account_ids = set()

    for obj in LOAD_ORDER:
        path = sf_dir / f"{obj}.csv"
        if not path.exists():
            raise ConfigError(f"Missing required CSV: {path}")
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            missing_cols = [c for c in REQUIRED_COLUMNS[obj] if c not in headers]
            if missing_cols:
                raise ConfigError(
                    f"{obj}.csv is missing required column(s): {missing_cols}. "
                    f"Found: {headers}"
                )
            rows = list(reader)
        counts[obj] = len(rows)

        # Referential-integrity checks across the FK chain.
        if obj == "Accounts":
            account_ids = {r["External_ID__c"] for r in rows if r.get("External_ID__c")}
        elif obj == "Contacts":
            orphans = [
                r.get("Account_External_ID__c")
                for r in rows
                if r.get("Account_External_ID__c") not in account_ids
            ]
            if orphans:
                raise ConfigError(
                    f"Contacts.csv references unknown Account_External_ID__c "
                    f"(load Accounts first): {sorted(set(orphans))[:5]}"
                )
        elif obj == "Opportunities":
            orphans = [
                r.get("Account_External_ID__c")
                for r in rows
                if r.get("Account_External_ID__c") not in account_ids
            ]
            if orphans:
                raise ConfigError(
                    f"Opportunities.csv references unknown Account_External_ID__c "
                    f"(load Accounts first): {sorted(set(orphans))[:5]}"
                )
        logger.info("✅ %s.csv valid — %d row(s)", obj, counts[obj])

    return counts


def print_load_instructions(sf_dir: Path, counts: Dict[str, int]) -> None:
    print("═" * 60)
    print("  Salesforce Import Plan")
    print("═" * 60)
    print(f"  CSV directory: {sf_dir}")
    print("  Load order (respect FK dependencies):")
    for i, obj in enumerate(LOAD_ORDER, 1):
        n = counts.get(obj, "?")
        print(f"    {i}. {obj}.csv  ({n} rows)  → upsert on External_ID__c")
    print()
    print("  Salesforce Dataloader instructions:")
    print("    • Operation: Upsert")
    print("    • External ID field: External_ID__c (Accounts/Opportunities)")
    print("    • Contacts: match Account via Account_External_ID__c relationship")
    print("    • Load in the exact order above so lookups resolve.")
    print("═" * 60)


def _sf_env() -> Optional[Dict[str, str]]:
    u = os.environ.get("SF_USERNAME")
    p = os.environ.get("SF_PASSWORD")
    t = os.environ.get("SF_TOKEN")
    if u and p and t:
        return {"username": u, "password": p, "security_token": t}
    return None


def load_salesforce(sf_dir: Path = DEFAULT_SF_DIR, dry_run: bool = False) -> dict:
    """Live Bulk API upsert on External_ID__c. Opt-in; requires simple-salesforce.

    Returns a per-object summary dict. Raises ConfigError if prerequisites are
    not met.
    """
    creds = _sf_env()
    if not creds:
        raise ConfigError(
            "Live load requires SF_USERNAME, SF_PASSWORD, SF_TOKEN env vars."
        )
    try:
        from simple_salesforce import Salesforce
    except ImportError as exc:
        raise ConfigError(
            "simple-salesforce is not installed (optional). "
            "pip install 'simple-salesforce>=1.12,<2'."
        ) from exc

    sf_dir = Path(sf_dir)
    summary: Dict[str, int] = {}

    if dry_run:
        for obj in LOAD_ORDER:
            logger.info("[dry-run] Would bulk-upsert %s from %s.csv on External_ID__c",
                        SF_OBJECT_API[obj], obj)
        return summary

    logger.info("Connecting to Salesforce as %s", creds["username"])
    sf = Salesforce(**creds)

    for obj in LOAD_ORDER:
        path = sf_dir / f"{obj}.csv"
        with path.open(newline="", encoding="utf-8") as f:
            records = list(csv.DictReader(f))
        api_name = SF_OBJECT_API[obj]
        # Contacts upsert on the parent external id relationship uses a
        # different external field; Account/Opportunity upsert on External_ID__c.
        ext_field = "Account_External_ID__c" if obj == "Contacts" else "External_ID__c"
        logger.info("Bulk upsert %s: %d record(s) on %s", api_name, len(records), ext_field)
        handler = getattr(sf.bulk, api_name)
        handler.upsert(records, ext_field)
        summary[obj] = len(records)

    logger.info("✅ Salesforce live load complete: %s", summary)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare/validate Salesforce import CSVs.")
    parser.add_argument("--sf-dir", default=str(DEFAULT_SF_DIR))
    parser.add_argument("--load", action="store_true",
                        help="Perform live Bulk API upsert (requires simple-salesforce "
                             "and SF_* env vars). Default: prep + validate only.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sf_dir = Path(args.sf_dir)
    try:
        ensure_csvs(sf_dir, dry_run=args.dry_run)
        if args.dry_run:
            logger.info("[dry-run] Would validate CSVs and print load instructions.")
            if args.load:
                load_salesforce(sf_dir, dry_run=True)
            return 0

        counts = validate_csvs(sf_dir)
        print_load_instructions(sf_dir, counts)

        if args.load:
            load_salesforce(sf_dir, dry_run=False)
        else:
            creds = _sf_env()
            if creds:
                logger.info("SF_* creds detected. Re-run with --load to perform a live "
                            "Bulk upsert. (Default stays offline.)")
            else:
                logger.info("No live load requested. Use Dataloader with the plan above, "
                            "or set SF_USERNAME/SF_PASSWORD/SF_TOKEN and pass --load.")
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.error("Salesforce prep failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
