"""Server configuration, read from environment variables.

:class:`Settings` collects every value the server reads from its environment,
with the same defaults the server always had, so a container started without
them behaves as before. A value that is set but invalid (for example an empty
``ONBOARDING_STATE_TABLE``) fails at startup with the variable's name, instead
of failing later on the first tool call.

The CloudFormation template sets ``ONBOARDING_STATE_TABLE`` and
``ONBOARDING_TENANT`` on the AgentCore Runtime, and the Dockerfile sets
``MCP_BIND_HOST``. Reading settings needs no AWS credentials or network.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environment variable names. Each matches a Settings field name, upper-cased.
STATE_TABLE_ENV = "ONBOARDING_STATE_TABLE"
TENANT_ENV = "ONBOARDING_TENANT"
BIND_HOST_ENV = "MCP_BIND_HOST"

DEFAULT_STATE_TABLE = "OnboardingState"
DEFAULT_TENANT = "shared"
# The server has no authentication of its own, so it binds to loopback unless
# told otherwise. The container image sets MCP_BIND_HOST (see the Dockerfile).
DEFAULT_BIND_HOST = "127.0.0.1"

# Same character set as the template's AllowedPattern for both parameters.
_NAME_PATTERN = r"^[A-Za-z0-9_.-]+$"

_ENV_BY_FIELD = {
    "onboarding_state_table": STATE_TABLE_ENV,
    "onboarding_tenant": TENANT_ENV,
    "mcp_bind_host": BIND_HOST_ENV,
}


class Settings(BaseSettings):
    """Every value the server reads from its environment."""

    # Read only the process environment (no .env file), ignore unrelated
    # variables, and match variable names case-insensitively to field names.
    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    # The DynamoDB table that stores the onboarding state. DynamoDB table names
    # are 3-255 characters.
    onboarding_state_table: str = Field(
        default=DEFAULT_STATE_TABLE,
        min_length=3,
        max_length=255,
        pattern=_NAME_PATTERN,
    )
    # The single shared tenant every authenticated caller maps to.
    onboarding_tenant: str = Field(
        default=DEFAULT_TENANT, min_length=1, pattern=_NAME_PATTERN
    )
    # The interface the MCP server listens on.
    mcp_bind_host: str = Field(default=DEFAULT_BIND_HOST, min_length=1)

    def log_view(self) -> dict[str, Any]:
        """Return the settings keyed by environment variable name, for logging.

        None of these values is secret.
        """
        return {_ENV_BY_FIELD[name]: value for name, value in self.model_dump().items()}


class SettingsError(ValueError):
    """The environment holds an invalid configuration value."""


def load_settings() -> Settings:
    """Read and validate the settings from the environment.

    Raises :class:`SettingsError` naming each invalid environment variable and
    the rule it breaks. The message omits the values themselves.
    """
    try:
        return Settings()
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_url=False):
            field = str(error["loc"][0]) if error["loc"] else ""
            problems.append(f"{_ENV_BY_FIELD.get(field, field)}: {error['msg']}")
        raise SettingsError(
            "Invalid server configuration. " + "; ".join(problems)
        ) from None
