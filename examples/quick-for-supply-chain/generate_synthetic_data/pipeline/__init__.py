"""Supply-chain DATA pipeline package.

Modules:
    config           — dataclass-based env config + Secrets Manager helper.
                       Selects the DB engine via DB_ENGINE (snowflake|postgres|sqlalchemy).
    db_loader        — database-AGNOSTIC entrypoint (honors DB_ENGINE).
    snowflake_loader — thin backward-compatible Snowflake entrypoint (delegates
                       to db_loader with DB_ENGINE=snowflake).
    loaders/         — per-engine Loader implementations + get_loader() factory.
    s3_uploader      — upload unstructured docs to S3.
    salesforce_prep  — prep/validate Salesforce CSVs (+ optional bulk load).

Run individual steps with ``python -m generate_synthetic_data.pipeline.<module>`` or use the
orchestrator ``generate_synthetic_data/load_data.sh``.
"""

__version__ = "1.0.0"
