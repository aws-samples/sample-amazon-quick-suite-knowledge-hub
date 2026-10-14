"""Glue Spark job: merge one table's newly-staged ServiceNow Parquet into S3 Tables (Iceberg).

One job run normalizes one ServiceNow table (`--table_name`). Runs on AWS Glue 5.0 (required
for S3 Tables), which bundles Iceberg; the CDK configures the Iceberg Spark catalog via job
`--conf` arguments, so this script references it by `namespace.table`. The `awsglue`/`pyspark`
imports are guarded because those modules exist only inside the Glue image; guarding lets the
module import in the unit environment so `build_delete_statements` can be unit-tested without a
Spark runtime.

Incremental correctness: the staged Parquet is read through a Glue job bookmark, so a file
already merged by a prior successful run is never read again. The increment read is scoped to
`staging/{table}/ingested_utc_date=` so the sibling `staging/{table}/_deletes/` manifest is
never read as Parquet.

Deletes (design of record: `.kiro/specs/deletion-propagation/`): after the upsert MERGE and
before clearing staging, this job reads any deletes manifest under `staging/{table}/_deletes/`,
applies the deleted `sys_id`s to the Iceberg target in batch-safe chunks, then deletes the
manifest objects. Deleting a `sys_id` absent from the target is a no-op, and the manifest is
cleared only after the apply succeeds, so a failed apply is retried next run.

Glue job arguments:
  --raw_data_lake_bucket   the S3 bucket holding staging/{table}/ Parquet
  --s3_tables_namespace    the Iceberg namespace (database) the target table lives in
  --table_name             the single ServiceNow table this run normalizes
"""

import sys

import awswrangler as wr  # type: ignore[import-not-found]

try:  # awsglue/pyspark exist only in the Glue image; guarded so the module imports for tests.
    from awsglue.context import GlueContext  # type: ignore[import-not-found]
    from awsglue.job import Job  # type: ignore[import-not-found]
    from awsglue.utils import getResolvedOptions  # type: ignore[import-not-found]
    from pyspark.context import SparkContext  # type: ignore[import-not-found]
    from pyspark.sql.functions import input_file_name  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - only importable inside the AWS Glue image
    GlueContext = Job = getResolvedOptions = SparkContext = input_file_name = None  # type: ignore[assignment,misc]

_MERGE_KEY = "sys_id"
_PATH_COLUMN = "__staged_path"
_DELETE_BATCH_SIZE = 500
_STAGING_PREFIX = "staging"
_DELETES_PREFIX = "_deletes"
_ARGS = ["JOB_NAME", "raw_data_lake_bucket", "s3_tables_namespace", "table_name"]


def build_delete_statements(target, sys_ids, batch_size=_DELETE_BATCH_SIZE):
    """Build batch-safe `DELETE FROM {target} WHERE sys_id IN (...)` statements.

    Pure and Spark-free so it can be unit-tested. De-duplicates and sorts the ids for
    determinism, drops empties, single-quote-escapes each id, and splits the IN-list into
    `batch_size` chunks so a large deletion set stays within a single statement's limits.
    """
    unique = sorted({sys_id for sys_id in sys_ids if sys_id})
    statements = []
    for start in range(0, len(unique), batch_size):
        batch = unique[start : start + batch_size]
        in_list = ", ".join("'" + sys_id.replace("'", "''") + "'" for sys_id in batch)
        statements.append(f"DELETE FROM {target} WHERE {_MERGE_KEY} IN ({in_list})")
    return statements


class ContentNormalizer:
    """Merges one table's newly-staged Parquet into its Iceberg target, applies deletes, clears."""

    def __init__(self, glue_context, namespace: str, bucket: str) -> None:
        self._glue_context = glue_context
        self._spark = glue_context.spark_session
        self._namespace = namespace
        self._bucket = bucket

    def normalize(self, table_name: str) -> None:
        """MERGE the new increment, apply audited deletes, then clear staging.

        Applies the upsert MERGE on `sys_id` when there is a new increment, then applies any
        deletes manifest (which can exist with or without a new increment), then deletes the
        merged staging objects.
        """
        target = f"{self._namespace}.{table_name}"
        frame = self._read_new_staged(table_name)
        # Iceberg rejects a non-deterministic MERGE source (INVALID_NON_DETERMINISTIC_EXPRESSIONS),
        # and input_file_name() taints the whole logical plan even after its column is dropped, so
        # this source carries none; cleanup paths come from a separate DataFrame in _delete_staged.
        staged = frame.toDF()
        has_increment = not staged.isEmpty()
        if has_increment:
            staged.createOrReplaceTempView("staged")
            self._ensure_table(target, staged)
            self._spark.sql(
                f"MERGE INTO {target} AS t USING staged AS s "
                f"ON t.{_MERGE_KEY} = s.{_MERGE_KEY} "
                f"WHEN MATCHED THEN UPDATE SET * "
                f"WHEN NOT MATCHED THEN INSERT *"
            )
        self._apply_deletes(table_name, target)
        if has_increment:
            self._delete_staged(frame)

    def _apply_deletes(self, table_name: str, target: str) -> None:
        """Apply the deletes manifest to the target, then clear the manifest on success.

        Reads the `sys_id`s from `staging/{table}/_deletes/`, runs the batch-safe deletes, and
        clears the manifest objects only after they succeed. No manifest is a no-op, and a
        `sys_id` absent from the target is a no-op, so re-applying the same manifest is idempotent.
        """
        deletes_path = f"s3://{self._bucket}/{_STAGING_PREFIX}/{table_name}/{_DELETES_PREFIX}/"
        manifest_keys = wr.s3.list_objects(path=deletes_path)
        if not manifest_keys:
            return
        sys_ids = self._read_manifest_sys_ids(deletes_path)
        for statement in build_delete_statements(target, sys_ids):
            self._spark.sql(statement)
        wr.s3.delete_objects(path=manifest_keys)

    def _read_manifest_sys_ids(self, deletes_path: str):
        """Read the newline-delimited `sys_id`s across every manifest object under the prefix."""
        frame = self._spark.read.text(deletes_path)
        return [row["value"] for row in frame.collect() if row["value"]]

    def _read_new_staged(self, table_name: str):
        """Read only new Parquet partitions (via the bookmark); never the `_deletes/` manifest."""
        return self._glue_context.create_dynamic_frame.from_options(
            connection_type="s3",
            connection_options={
                "paths": [f"s3://{self._bucket}/{_STAGING_PREFIX}/{table_name}/ingested_utc_date="],
                "recurse": True,
            },
            format="parquet",
            transformation_ctx=f"read_{table_name}",
        )

    def _ensure_table(self, target: str, staged) -> None:
        """Create the Iceberg target table from the staged schema on first run (all STRING)."""
        columns = ", ".join(f"`{field.name}` STRING" for field in staged.schema.fields)
        self._spark.sql(f"CREATE TABLE IF NOT EXISTS {target} ({columns}) USING iceberg")

    @staticmethod
    def _delete_staged(frame) -> None:
        """Delete the exact staging objects merged this run (the bookmark is the reprocess guard).

        Builds a separate cleanup DataFrame carrying input_file_name(), kept off the MERGE source
        so that plan stays deterministic. Called only after a successful MERGE, so files are
        removed only once their rows are safely in the target.
        """
        cleanup = frame.toDF().withColumn(_PATH_COLUMN, input_file_name())
        paths = [row[_PATH_COLUMN] for row in cleanup.select(_PATH_COLUMN).distinct().collect()]
        if paths:
            wr.s3.delete_objects(path=paths)


def main() -> None:
    """Merge the one configured table's new increment, apply deletes, then commit the bookmark."""
    args = getResolvedOptions(sys.argv, _ARGS)
    glue_context = GlueContext(SparkContext())
    job = Job(glue_context)
    job.init(args["JOB_NAME"], args)

    ContentNormalizer(
        glue_context,
        namespace=args["s3_tables_namespace"],
        bucket=args["raw_data_lake_bucket"],
    ).normalize(args["table_name"])

    job.commit()


if __name__ == "__main__":
    main()
