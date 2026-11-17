"""Tests for the environment-driven settings and the server's startup wiring."""

from __future__ import annotations

import json
import logging

import pytest

from quick_admin_buddy_mcp import config, server
from quick_admin_buddy_mcp.observability import LOGGER_NAME

_ALL_ENV = (config.STATE_TABLE_ENV, config.TENANT_ENV, config.BIND_HOST_ENV)


@pytest.fixture
def clean_env(monkeypatch):
    """Start every test with none of the server's variables set.

    A Region is set (main() creates the DynamoDB resource, which needs one, but
    makes no AWS call), and the user's AWS config file is hidden.
    """
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent/aws-config")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    # main() calls configure_logging(), which replaces the package logger's
    # handlers and turns off propagation. Restore both for later tests.
    logger = logging.getLogger(LOGGER_NAME)
    saved = (list(logger.handlers), logger.propagate, logger.level)
    yield monkeypatch
    logger.handlers[:] = saved[0]
    logger.propagate = saved[1]
    logger.setLevel(saved[2])


@pytest.fixture
def started(clean_env):
    """Capture what main() builds instead of serving it.

    Returns a dict that main() fills with the OnboardingTools it built and the
    keyword arguments it passed to MCPServer.run().
    """
    seen: dict = {}
    real_build_server = server.build_server
    real_repository = server.DynamoDbStateRepository

    def _build_server(tools):
        seen["tools"] = tools
        return real_build_server(tools)

    def _repository(table_name):
        seen["table_name"] = table_name
        return real_repository(table_name)

    clean_env.setattr(server, "build_server", _build_server)
    clean_env.setattr(server, "DynamoDbStateRepository", _repository)
    clean_env.setattr(
        server.MCPServer, "run", lambda _self, **kwargs: seen.update(run=kwargs)
    )
    return seen


def test_defaults_when_nothing_is_set(clean_env) -> None:
    settings = config.load_settings()
    assert settings.onboarding_state_table == "OnboardingState"
    assert settings.onboarding_tenant == "shared"
    assert settings.mcp_bind_host == "127.0.0.1"


def test_values_come_from_the_environment(clean_env) -> None:
    clean_env.setenv(config.STATE_TABLE_ENV, "MyTable")
    clean_env.setenv(config.TENANT_ENV, "acme")
    clean_env.setenv(config.BIND_HOST_ENV, "0.0.0.0")  # noqa: S104  # nosec B104 - test value only
    settings = config.load_settings()
    assert settings.log_view() == {
        "ONBOARDING_STATE_TABLE": "MyTable",
        "ONBOARDING_TENANT": "acme",
        "MCP_BIND_HOST": "0.0.0.0",  # noqa: S104  # nosec B104 - test value only
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (config.STATE_TABLE_ENV, ""),
        (config.STATE_TABLE_ENV, "ab"),
        (config.STATE_TABLE_ENV, "bad table/name"),
        (config.TENANT_ENV, ""),
        (config.TENANT_ENV, "tenant with spaces"),
        (config.BIND_HOST_ENV, ""),
    ],
)
def test_invalid_value_names_the_variable_not_the_value(clean_env, name, value) -> None:
    clean_env.setenv(name, value)
    with pytest.raises(config.SettingsError) as excinfo:
        config.load_settings()
    message = str(excinfo.value)
    assert name in message
    if value:
        assert value not in message


def test_settings_are_immutable(clean_env) -> None:
    settings = config.load_settings()
    with pytest.raises(ValueError):
        settings.onboarding_tenant = "other"


def test_unrelated_variables_are_ignored(clean_env) -> None:
    clean_env.setenv("ONBOARDING_SOMETHING_ELSE", "x")
    assert config.load_settings().onboarding_tenant == "shared"


def test_main_stops_before_listening_on_invalid_config(clean_env, started) -> None:
    clean_env.setenv(config.STATE_TABLE_ENV, "")
    with pytest.raises(SystemExit) as excinfo:
        server.main()
    assert config.STATE_TABLE_ENV in str(excinfo.value.code)
    assert "tools" not in started and "run" not in started


def test_main_stops_before_listening_without_a_region(clean_env, started) -> None:
    clean_env.delenv("AWS_DEFAULT_REGION")
    clean_env.delenv("AWS_REGION", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        server.main()
    assert "AWS_DEFAULT_REGION" in str(excinfo.value.code)
    assert "tools" not in started and "run" not in started


def test_main_wires_the_startup_settings(clean_env, started) -> None:
    clean_env.setenv(config.STATE_TABLE_ENV, "StartupTable")
    clean_env.setenv(config.TENANT_ENV, "startup-tenant")

    server.main()

    assert started["run"] == {
        "transport": "streamable-http",
        "host": "127.0.0.1",
        "stateless_http": True,
    }
    assert started["tools"].tenant == "startup-tenant"
    assert started["table_name"] == "StartupTable"


def test_startup_log_line(clean_env, started, capsys) -> None:
    clean_env.setenv(config.STATE_TABLE_ENV, "StartupTable")
    server.main()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    startup = [line for line in lines if line.get("event") == "startup"]
    assert startup == [
        {
            "event": "startup",
            "settings": {
                "MCP_BIND_HOST": "127.0.0.1",
                "ONBOARDING_STATE_TABLE": "StartupTable",
                "ONBOARDING_TENANT": "shared",
            },
        }
    ]
