"""Tests for the Glue normalizer's pure delete-SQL construction.

Covers only the Spark-free build_delete_statements helper (determinism, de-duplication,
empty-input handling, quote escaping, and batch chunking). The Iceberg DELETE runs only inside
the Glue/Spark image and is verified by the E2E/Glue path. awsglue and pyspark are import-guarded
so the module imports here without a Spark runtime.
"""

from glue.normalize_content_job import build_delete_statements


def test_build_delete_statements_is_empty_when_no_sys_ids() -> None:
    assert build_delete_statements("db.incident", []) == []


def test_build_delete_statements_drops_empty_ids() -> None:
    assert build_delete_statements("db.incident", ["", "inc-1"]) == [
        "DELETE FROM db.incident WHERE sys_id IN ('inc-1')"
    ]


def test_build_delete_statements_dedupes_and_sorts_for_determinism() -> None:
    statements = build_delete_statements("db.incident", ["inc-b", "inc-a", "inc-b"])
    assert statements == ["DELETE FROM db.incident WHERE sys_id IN ('inc-a', 'inc-b')"]


def test_build_delete_statements_escapes_single_quotes() -> None:
    statements = build_delete_statements("db.incident", ["o'brien"])
    assert statements == ["DELETE FROM db.incident WHERE sys_id IN ('o''brien')"]


def test_build_delete_statements_chunks_into_batches() -> None:
    sys_ids = [f"id-{index:03d}" for index in range(5)]
    statements = build_delete_statements("db.incident", sys_ids, batch_size=2)
    assert len(statements) == 3
    assert statements[0] == "DELETE FROM db.incident WHERE sys_id IN ('id-000', 'id-001')"
    assert statements[2] == "DELETE FROM db.incident WHERE sys_id IN ('id-004')"
