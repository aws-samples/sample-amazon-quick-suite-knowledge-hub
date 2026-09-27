"""
generate_synthetic_data.pipeline.config
═══════════════════════════════════════════════════════════════
Centralized, dataclass-based configuration for the supply-chain
DATA pipeline. Everything is loaded from environment variables with
sane defaults. NO secrets are hard-coded here.

Production guidance
-------------------
In production, Snowflake credentials should NOT be passed via plain
environment variables. Instead, store them in AWS Secrets Manager and
set ``SNOWFLAKE_SECRET_ARN``. The :func:`get_secret` helper (boto3
``secretsmanager``) will pull a JSON secret and override the matching
config fields at load time. S3 credentials should come from the default
boto3 credential chain (instance role, SSO, env, shared config) — never
be embedded in code.

Expected Secrets Manager JSON shape (all keys optional; present keys
override the corresponding env values)::

    {
      "account":       "abc-xy12345",
      "user":          "SVC_LOADER",
      "password":      "••••••",
      "role":          "SYSADMIN",
      "warehouse":     "COMPUTE_WH",
      "database":      "SUPPLY_CHAIN",
      "schema":        "SCM",
      "authenticator": "snowflake"
    }
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields

logger = logging.getLogger("generate_synthetic_data.pipeline.config")

# ── Defaults ────────────────────────────────────────────────────
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_S3_PREFIX = "unstructured/"
DEFAULT_WAREHOUSE = "COMPUTE_WH"
DEFAULT_DATABASE = "SUPPLY_CHAIN"
DEFAULT_SCHEMA = "SCM"
DEFAULT_AUTHENTICATOR = "snowflake"
VALID_AUTHENTICATORS = ("snowflake", "externalbrowser")

# ── Database engine selection ───────────────────────────────────
# DB_ENGINE picks which loader/config the pipeline uses. Default is
# "snowflake" so existing behavior is unchanged when the var is unset.
DEFAULT_DB_ENGINE = "snowflake"
VALID_DB_ENGINES = ("snowflake", "postgres", "sqlalchemy")

# Postgres defaults.
DEFAULT_PG_PORT = "5432"
DEFAULT_PG_DATABASE = "supply_chain"
DEFAULT_PG_SCHEMA = "scm"


def _env(name: str, default: str | None = None) -> str | None:
    """Read an env var, treating empty strings as unset."""
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    val = _env(name)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


# ── AWS / S3 config ─────────────────────────────────────────────
@dataclass
class AWSConfig:
    """AWS + S3 settings. Credentials come from the default boto3 chain."""

    region: str = DEFAULT_AWS_REGION
    s3_bucket: str | None = None
    s3_prefix: str = DEFAULT_S3_PREFIX

    @classmethod
    def from_env(cls) -> AWSConfig:
        prefix = _env("S3_PREFIX", DEFAULT_S3_PREFIX) or DEFAULT_S3_PREFIX
        # Normalize to a single trailing slash, no leading slash.
        prefix = prefix.lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return cls(
            region=_env("AWS_REGION", DEFAULT_AWS_REGION) or DEFAULT_AWS_REGION,
            s3_bucket=_env("S3_BUCKET"),
            s3_prefix=prefix,
        )

    def validate_for_s3(self) -> None:
        if not self.s3_bucket:
            raise ConfigError(
                "S3_BUCKET is required for the S3 upload step. "
                "Set S3_BUCKET to the target bucket for unstructured docs."
            )
        if not self.region:
            raise ConfigError("AWS_REGION is required.")


# ── Snowflake config ────────────────────────────────────────────
@dataclass
class SnowflakeConfig:
    """Snowflake connection settings.

    ``password`` is only required when ``authenticator == 'snowflake'``.
    With ``externalbrowser`` SSO, no password is needed.
    """

    account: str | None = None
    user: str | None = None
    password: str | None = None
    role: str | None = None
    warehouse: str = DEFAULT_WAREHOUSE
    database: str = DEFAULT_DATABASE
    schema: str = DEFAULT_SCHEMA
    authenticator: str = DEFAULT_AUTHENTICATOR
    secret_arn: str | None = None

    @classmethod
    def from_env(cls) -> SnowflakeConfig:
        cfg = cls(
            account=_env("SNOWFLAKE_ACCOUNT"),
            user=_env("SNOWFLAKE_USER"),
            password=_env("SNOWFLAKE_PASSWORD"),
            role=_env("SNOWFLAKE_ROLE"),
            warehouse=_env("SNOWFLAKE_WAREHOUSE", DEFAULT_WAREHOUSE)
            or DEFAULT_WAREHOUSE,
            database=_env("SNOWFLAKE_DATABASE", DEFAULT_DATABASE) or DEFAULT_DATABASE,
            schema=_env("SNOWFLAKE_SCHEMA", DEFAULT_SCHEMA) or DEFAULT_SCHEMA,
            authenticator=_env("SNOWFLAKE_AUTHENTICATOR", DEFAULT_AUTHENTICATOR)
            or DEFAULT_AUTHENTICATOR,
            secret_arn=_env("SNOWFLAKE_SECRET_ARN"),
        )
        return cfg

    def apply_secret_overrides(self, secret: dict) -> None:
        """Override fields from a Secrets Manager JSON payload (in place)."""
        mapping = {
            "account": "account",
            "user": "user",
            "password": "password",
            "role": "role",
            "warehouse": "warehouse",
            "database": "database",
            "schema": "schema",
            "authenticator": "authenticator",
        }
        for secret_key, attr in mapping.items():
            if secret_key in secret and secret[secret_key] not in (None, ""):
                setattr(self, attr, str(secret[secret_key]))

    def validate(self) -> None:
        if self.authenticator not in VALID_AUTHENTICATORS:
            raise ConfigError(
                f"SNOWFLAKE_AUTHENTICATOR must be one of {VALID_AUTHENTICATORS}, "
                f"got '{self.authenticator}'."
            )
        missing = []
        if not self.account:
            missing.append("SNOWFLAKE_ACCOUNT")
        if not self.user:
            missing.append("SNOWFLAKE_USER")
        if self.authenticator == "snowflake" and not self.password:
            missing.append(
                "SNOWFLAKE_PASSWORD (required unless authenticator=externalbrowser)"
            )
        if missing:
            raise ConfigError(
                "Missing required Snowflake configuration: "
                + ", ".join(missing)
                + ". Provide them via environment variables or a Secrets Manager "
                "secret referenced by SNOWFLAKE_SECRET_ARN."
            )

    def connect_kwargs(self) -> dict:
        """Build kwargs for snowflake.connector.connect(). Excludes None values."""
        kwargs = {
            "account": self.account,
            "user": self.user,
            "role": self.role,
            "warehouse": self.warehouse,
            "database": self.database,
            "schema": self.schema,
            "authenticator": self.authenticator,
        }
        if self.authenticator == "snowflake":
            kwargs["password"] = self.password
        return {k: v for k, v in kwargs.items() if v is not None}


# ── Postgres config ─────────────────────────────────────────────
@dataclass
class PostgresConfig:
    """PostgreSQL / RDS connection settings.

    Either provide the discrete PG* fields or a single ``DATABASE_URL``
    (libpq/SQLAlchemy-style URL) which, when present, takes precedence for
    connecting. ``schema`` is always used to qualify table names.
    """

    host: str | None = None
    port: str = DEFAULT_PG_PORT
    database: str = DEFAULT_PG_DATABASE
    user: str | None = None
    password: str | None = None
    schema: str = DEFAULT_PG_SCHEMA
    database_url: str | None = None

    @classmethod
    def from_env(cls) -> PostgresConfig:
        return cls(
            host=_env("PGHOST"),
            port=_env("PGPORT", DEFAULT_PG_PORT) or DEFAULT_PG_PORT,
            database=_env("PGDATABASE", DEFAULT_PG_DATABASE) or DEFAULT_PG_DATABASE,
            user=_env("PGUSER"),
            password=_env("PGPASSWORD"),
            schema=_env("PGSCHEMA", DEFAULT_PG_SCHEMA) or DEFAULT_PG_SCHEMA,
            database_url=_env("DATABASE_URL"),
        )

    def apply_secret_overrides(self, secret: dict) -> None:
        """Override fields from a Secrets Manager JSON payload (in place)."""
        mapping = {
            "host": "host",
            "port": "port",
            "database": "database",
            "dbname": "database",
            "user": "user",
            "username": "user",
            "password": "password",
            "schema": "schema",
            "database_url": "database_url",
            "url": "database_url",
        }
        for secret_key, attr in mapping.items():
            if secret_key in secret and secret[secret_key] not in (None, ""):
                setattr(self, attr, str(secret[secret_key]))

    def validate(self) -> None:
        if self.database_url:
            # A full URL is self-contained; only schema is still needed.
            if not self.schema:
                raise ConfigError("PGSCHEMA is required.")
            return
        missing = []
        if not self.host:
            missing.append("PGHOST")
        if not self.user:
            missing.append("PGUSER")
        if not self.database:
            missing.append("PGDATABASE")
        if missing:
            raise ConfigError(
                "Missing required Postgres configuration: "
                + ", ".join(missing)
                + ". Provide PGHOST/PGUSER/PGDATABASE (+ PGPASSWORD/PGPORT/PGSCHEMA) "
                "or a single DATABASE_URL, or reference a JSON secret via DB_SECRET_ARN."
            )

    def connect_kwargs(self) -> dict:
        """Build kwargs for psycopg.connect(). Excludes None values."""
        kwargs = {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
        }
        return {k: v for k, v in kwargs.items() if v is not None}

    def dsn(self) -> str:
        """Return a connection string for psycopg (URL if provided)."""
        if self.database_url:
            return self.database_url
        parts = [f"{k}={v}" for k, v in self.connect_kwargs().items()]
        return " ".join(parts)

    def redacted_dsn(self) -> str:
        if self.database_url:
            return _redact_url(self.database_url)
        return f"host={self.host} port={self.port} dbname={self.database} user={self.user} password=***"


# ── Generic SQLAlchemy config ───────────────────────────────────
@dataclass
class SQLAlchemyConfig:
    """Generic SQLAlchemy escape hatch — works for ANY SQLAlchemy-supported DB.

    Requires a single ``SQLALCHEMY_URL`` such as::

        postgresql+psycopg://user:pass@host:5432/dbname
        mysql+pymysql://user:pass@host/dbname
        sqlite:///local.db

    ``schema`` is optional (used by df.to_sql when the backend supports it).
    """

    url: str | None = None
    schema: str | None = None

    @classmethod
    def from_env(cls) -> SQLAlchemyConfig:
        return cls(
            url=_env("SQLALCHEMY_URL"),
            # Reuse PGSCHEMA as an optional schema hint; may be None.
            schema=_env("SQLALCHEMY_SCHEMA") or _env("PGSCHEMA"),
        )

    def apply_secret_overrides(self, secret: dict) -> None:
        mapping = {"url": "url", "sqlalchemy_url": "url", "schema": "schema"}
        for secret_key, attr in mapping.items():
            if secret_key in secret and secret[secret_key] not in (None, ""):
                setattr(self, attr, str(secret[secret_key]))

    def validate(self) -> None:
        if not self.url:
            raise ConfigError(
                "SQLALCHEMY_URL is required for DB_ENGINE=sqlalchemy. Example: "
                "postgresql+psycopg://user:pass@host:5432/db, mysql+pymysql://…, "
                "or sqlite:///local.db. May also be supplied via DB_SECRET_ARN."
            )

    def redacted_url(self) -> str:
        return _redact_url(self.url) if self.url else None


def _redact_url(url: str) -> str:
    """Mask the password portion of a DB URL for safe logging."""
    if not url:
        return url
    # Format: scheme://user:password@host/…
    try:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" in rest and ":" in rest.split("@", 1)[0]:
            creds, tail = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{tail}"
        return url
    except Exception:  # pragma: no cover - defensive
        return url


def _redact_arn(arn: str) -> str:
    """Mask the account ID and resource name of an ARN for safe logging.

    CodeQL flags secret ARNs as sensitive because the resource segment can
    reveal the secret's name. We keep the service/region for debuggability
    and mask the account id and the resource identifier.
    """
    if not arn:
        return arn
    parts = arn.split(":")
    # arn:partition:service:region:account-id:resource(...)
    if len(parts) < 6 or parts[0] != "arn":
        return "***"
    parts[4] = "***"  # account id
    parts[5] = "***"  # resource type / name (e.g. secret:my-secret-AbCdEf)
    return ":".join(parts[:6]) + ("..." if len(parts) > 6 else "")


# ── Secrets Manager helper ──────────────────────────────────────
def get_secret(secret_arn: str, region: str | None = None) -> dict:
    """Fetch a JSON secret from AWS Secrets Manager.

    Parameters
    ----------
    secret_arn:
        The ARN (or name) of the secret to retrieve.
    region:
        AWS region. Falls back to the default boto3 resolution when None.

    Returns
    -------
    dict
        Parsed JSON secret payload.

    Raises
    ------
    ConfigError
        If boto3 is unavailable, the fetch fails, or the payload is not JSON.
    """
    try:
        import boto3  # imported lazily so config import stays cheap
    except ImportError as exc:  # pragma: no cover - defensive
        raise ConfigError(
            "boto3 is required to resolve SNOWFLAKE_SECRET_ARN. "
            "Install generate_synthetic_data/pipeline/requirements.txt."
        ) from exc

    client_kwargs = {"region_name": region} if region else {}
    client = boto3.client("secretsmanager", **client_kwargs)
    try:
        resp = client.get_secret_value(SecretId=secret_arn)
    except Exception as exc:  # botocore.ClientError and friends
        raise ConfigError(f"Failed to fetch secret '{secret_arn}': {exc}") from exc

    raw = resp.get("SecretString")
    if raw is None:
        raise ConfigError(f"Secret '{secret_arn}' has no SecretString payload.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Secret '{secret_arn}' is not valid JSON. Expected a JSON object "
            "with Snowflake connection keys."
        ) from exc


# ── Top-level pipeline config ───────────────────────────────────
@dataclass
class PipelineConfig:
    """Aggregate config for the whole data pipeline.

    ``db_engine`` (from env ``DB_ENGINE``) selects which structured-data
    loader is used: ``snowflake`` (default), ``postgres``, or ``sqlalchemy``.
    The matching sub-config is populated from the environment and, optionally,
    from a Secrets Manager JSON payload referenced by ``DB_SECRET_ARN`` (or, for
    Snowflake, the legacy ``SNOWFLAKE_SECRET_ARN``).
    """

    aws: AWSConfig = field(default_factory=AWSConfig)
    snowflake: SnowflakeConfig = field(default_factory=SnowflakeConfig)
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    sqlalchemy: SQLAlchemyConfig = field(default_factory=SQLAlchemyConfig)
    db_engine: str = DEFAULT_DB_ENGINE
    db_secret_arn: str | None = None

    @classmethod
    def load(cls, resolve_secret: bool = True) -> PipelineConfig:
        """Load config from the environment.

        Secrets are resolved only when ``resolve_secret`` is True (so --dry-run
        makes no AWS call):
          * Snowflake: ``SNOWFLAKE_SECRET_ARN`` overrides Snowflake fields.
          * Any engine: ``DB_SECRET_ARN`` overrides the selected engine's fields.
        """
        aws = AWSConfig.from_env()
        snowflake = SnowflakeConfig.from_env()
        postgres = PostgresConfig.from_env()
        sqlalchemy = SQLAlchemyConfig.from_env()

        engine = (_env("DB_ENGINE", DEFAULT_DB_ENGINE) or DEFAULT_DB_ENGINE).lower()
        db_secret_arn = _env("DB_SECRET_ARN")

        cfg = cls(
            aws=aws,
            snowflake=snowflake,
            postgres=postgres,
            sqlalchemy=sqlalchemy,
            db_engine=engine,
            db_secret_arn=db_secret_arn,
        )
        cfg.validate_engine()

        if resolve_secret:
            # Legacy Snowflake secret path (unchanged behavior).
            if snowflake.secret_arn:
                logger.info(
                    "Resolving Snowflake credentials from Secrets Manager: %s",
                    _redact_arn(snowflake.secret_arn),
                )
                secret = get_secret(snowflake.secret_arn, region=aws.region)
                snowflake.apply_secret_overrides(secret)

            # Generic per-engine secret path.
            if db_secret_arn:
                logger.info(
                    "Resolving %s credentials from Secrets Manager: %s",
                    engine,
                    _redact_arn(db_secret_arn),
                )
                secret = get_secret(db_secret_arn, region=aws.region)
                cfg.active_db_config().apply_secret_overrides(secret)

        return cfg

    def validate_engine(self) -> None:
        if self.db_engine not in VALID_DB_ENGINES:
            raise ConfigError(
                f"DB_ENGINE must be one of {VALID_DB_ENGINES}, got '{self.db_engine}'."
            )

    def active_db_config(self):
        """Return the sub-config object for the selected engine."""
        if self.db_engine == "snowflake":
            return self.snowflake
        if self.db_engine == "postgres":
            return self.postgres
        if self.db_engine == "sqlalchemy":
            return self.sqlalchemy
        raise ConfigError(f"Unknown DB_ENGINE '{self.db_engine}'.")

    def validate_active_db(self) -> None:
        """Validate required fields for the selected engine."""
        self.validate_engine()
        self.active_db_config().validate()

    def redacted(self) -> dict:
        """Return a dict of config suitable for logging (secrets masked)."""
        out = {
            "db_engine": self.db_engine,
            "db_secret_arn": _redact_arn(self.db_secret_arn),
        }
        for f in fields(self.aws):
            out[f"aws.{f.name}"] = getattr(self.aws, f.name)

        if self.db_engine == "snowflake":
            for f in fields(self.snowflake):
                val = getattr(self.snowflake, f.name)
                if f.name == "password" and val:
                    val = "***REDACTED***"
                if f.name == "secret_arn" and val:
                    val = _redact_arn(val)
                out[f"snowflake.{f.name}"] = val
        elif self.db_engine == "postgres":
            for f in fields(self.postgres):
                val = getattr(self.postgres, f.name)
                if f.name in ("password",) and val:
                    val = "***REDACTED***"
                if f.name == "database_url" and val:
                    val = _redact_url(val)
                out[f"postgres.{f.name}"] = val
        elif self.db_engine == "sqlalchemy":
            for f in fields(self.sqlalchemy):
                val = getattr(self.sqlalchemy, f.name)
                if f.name == "url" and val:
                    val = _redact_url(val)
                out[f"sqlalchemy.{f.name}"] = val
        return out
