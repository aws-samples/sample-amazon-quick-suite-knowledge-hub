#!/usr/bin/env python3
"""
generate_synthetic_data.pipeline.s3_uploader
═══════════════════════════════════════════════════════════════
Upload unstructured documents (invoices, contracts) to S3 for RAG /
downstream ingestion.

Features
--------
* Uploads from the generated docs dir
  (``synthetic_data/documents/``) to ``s3://$S3_BUCKET/$S3_PREFIX`` preserving
  filenames.
* Creates the bucket if missing, region-aware (us-east-1 must NOT send a
  ``LocationConstraint``; every other region must).
* Server-side encryption ``AES256`` on every object.
* Idempotent: skips objects whose size already matches; otherwise overwrites.
* ``--dry-run`` prints planned actions without touching AWS.
* Adaptive retries via a botocore ``Config``.

Credentials come from the default boto3 chain — never hard-coded.
"""

from __future__ import annotations

import argparse
import logging
import mimetypes
import os
import sys
from pathlib import Path

try:
    from .config import AWSConfig, ConfigError, PipelineConfig
except ImportError:  # pragma: no cover
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    from generate_synthetic_data.pipeline.config import (
        AWSConfig,
        ConfigError,
        PipelineConfig,
    )

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("generate_synthetic_data.pipeline.s3_uploader")

# Module now lives at generate_synthetic_data/pipeline/, so repo root is three levels up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCE_DIRS = [
    PROJECT_ROOT / "synthetic_data" / "documents",
]

# Extensions treated as unstructured documents to upload.
DOC_EXTENSIONS = {".pdf", ".txt", ".docx", ".doc", ".md", ".csv", ".json", ".html"}


def _make_s3_client(region: str):
    """Build an S3 client with adaptive retries."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover
        raise ConfigError(
            "boto3 is required for S3 upload. Install generate_synthetic_data/pipeline/requirements.txt."
        ) from exc

    cfg = Config(retries={"max_attempts": 10, "mode": "adaptive"}, region_name=region)
    return boto3.client("s3", config=cfg)


def discover_documents(source_dirs: list[Path]) -> list[Path]:
    """Return all uploadable document files across the given source dirs."""
    files: list[Path] = []
    for d in source_dirs:
        d = Path(d)
        if not d.exists():
            logger.debug("Source dir missing, skipping: %s", d)
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix.lower() in DOC_EXTENSIONS:
                files.append(p)
    return files


def bucket_exists(s3, bucket: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            return False
        if code in ("403", "301"):
            # Exists but not owned by us / different region — surface clearly.
            raise ConfigError(
                f"Bucket '{bucket}' exists but is not accessible from this account "
                f"(HTTP {code}). Choose a different S3_BUCKET name."
            ) from exc
        raise


def ensure_bucket(s3, bucket: str, region: str, dry_run: bool = False) -> None:
    """Create the bucket if missing (region-aware)."""
    if bucket_exists(s3, bucket):
        logger.info("Bucket exists: s3://%s", bucket)
        return
    if dry_run:
        logger.info("[dry-run] Would CREATE bucket s3://%s in %s", bucket, region)
        return

    logger.info("Creating bucket s3://%s in %s", bucket, region)
    if region == "us-east-1":
        # us-east-1 rejects a LocationConstraint.
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    # Enforce bucket-level default encryption too.
    with_suppressed_errors(
        lambda: s3.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            },
        ),
        label="put_bucket_encryption",
    )
    logger.info("✅ Bucket created: s3://%s", bucket)


def with_suppressed_errors(fn, *, label: str):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s failed (non-fatal): %s", label, exc)
        return None


def _remote_size(s3, bucket: str, key: str) -> int | None:
    from botocore.exceptions import ClientError

    try:
        resp = s3.head_object(Bucket=bucket, Key=key)
        return int(resp["ContentLength"])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def upload_documents(
    aws: AWSConfig,
    source_dirs: list[Path] | None = None,
    dry_run: bool = False,
) -> dict:
    """Upload all discovered documents. Returns a summary dict."""
    aws.validate_for_s3()
    source_dirs = source_dirs or DEFAULT_SOURCE_DIRS
    docs = discover_documents(source_dirs)

    summary = {"uploaded": 0, "skipped": 0, "planned": 0, "total": len(docs)}
    if not docs:
        logger.warning(
            "No documents found in: %s", ", ".join(str(d) for d in source_dirs)
        )
        return summary

    s3 = None if dry_run else _make_s3_client(aws.region)
    if dry_run:
        logger.info(
            "[dry-run] Target: s3://%s/%s (region %s)",
            aws.s3_bucket,
            aws.s3_prefix,
            aws.region,
        )
        ensure_bucket(_MockS3(), aws.s3_bucket, aws.region, dry_run=True)
    else:
        ensure_bucket(s3, aws.s3_bucket, aws.region, dry_run=False)

    for path in docs:
        key = f"{aws.s3_prefix}{path.name}"
        local_size = path.stat().st_size

        if dry_run:
            logger.info(
                "[dry-run] Would upload %s → s3://%s/%s (%d bytes, AES256)",
                path.name,
                aws.s3_bucket,
                key,
                local_size,
            )
            summary["planned"] += 1
            continue

        remote_size = _remote_size(s3, aws.s3_bucket, key)
        if remote_size is not None and remote_size == local_size:
            logger.info("Skip (size match): s3://%s/%s", aws.s3_bucket, key)
            summary["skipped"] += 1
            continue

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        logger.info(
            "Upload %s → s3://%s/%s (%d bytes)",
            path.name,
            aws.s3_bucket,
            key,
            local_size,
        )
        s3.upload_file(
            Filename=str(path),
            Bucket=aws.s3_bucket,
            Key=key,
            ExtraArgs={"ServerSideEncryption": "AES256", "ContentType": content_type},
        )
        summary["uploaded"] += 1

    logger.info(
        "✅ S3 upload done: uploaded=%d skipped=%d planned=%d total=%d",
        summary["uploaded"],
        summary["skipped"],
        summary["planned"],
        summary["total"],
    )
    return summary


class _MockS3:
    """Minimal stand-in so ensure_bucket() dry-run needs no real client."""

    def head_bucket(self, **_):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload unstructured docs to S3.")
    parser.add_argument(
        "--source-dir",
        action="append",
        default=None,
        help="Override source dir(s). Repeatable. "
        "Defaults to synthetic_data/documents/.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned uploads without touching AWS.",
    )
    args = parser.parse_args(argv)

    cfg = PipelineConfig.load(resolve_secret=False)  # S3 step needs no Snowflake secret
    source_dirs = [Path(s) for s in args.source_dir] if args.source_dir else None

    try:
        upload_documents(cfg.aws, source_dirs=source_dirs, dry_run=args.dry_run)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.error("S3 upload failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
