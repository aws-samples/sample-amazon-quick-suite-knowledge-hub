"""Supply-chain unified data home.

Subpackages / dirs:
    pipeline    — the DATA pipeline package (config, snowflake_loader,
                  s3_uploader, salesforce_prep). Run steps with
                  ``python -m generate_synthetic_data.pipeline.<module>`` from the repo root.
    generators  — data generators (generate_all_data.py, generate_documents.py).
                  schema.py is the single source of truth for both the
                  generated CSVs and the DB CREATE TABLE DDL (create_table_ddl).
    mock        — mock Salesforce JSON fixtures.
"""
