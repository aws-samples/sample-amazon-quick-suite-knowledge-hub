"""Per-tool-call structured logging.

Every tool call writes one JSON log line to stdout, which AgentCore Runtime
delivers to the Runtime's CloudWatch log group. The line names the tool, the
connector or extension key, the result status, the AWS request IDs of the AWS
calls the tool made, and the duration. It never contains the result message,
tool arguments, or any secret value.

The AWS clients call :func:`record_request_id` after each AWS API call; the IDs
are collected per tool call in a context variable, so concurrent requests do
not mix their IDs.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

LOGGER_NAME = "quick_admin_buddy_mcp"
logger = logging.getLogger(LOGGER_NAME)

# AWS request IDs collected during the current tool call. ``None`` outside a
# tool call, so recording is a no-op there (for example in unit tests that call
# the clients directly).
_request_ids: ContextVar[list[str] | None] = ContextVar(
    "quick_admin_buddy_request_ids", default=None
)


def record_request_id(source: Any) -> None:
    """Record the AWS request ID carried by a boto3 response or error.

    ``source`` is a boto3 response ``dict`` or a botocore exception with a
    ``response`` attribute. Anything without a request ID is ignored.
    """
    ids = _request_ids.get()
    if ids is None:
        return
    response = source if isinstance(source, dict) else getattr(source, "response", None)
    if not isinstance(response, dict):
        return
    request_id = (response.get("ResponseMetadata") or {}).get("RequestId")
    if request_id and request_id not in ids:
        ids.append(request_id)


@contextmanager
def _collect_request_ids() -> Iterator[list[str]]:
    ids: list[str] = []
    token = _request_ids.set(ids)
    try:
        yield ids
    finally:
        _request_ids.reset(token)


def logged_tool_call(tool: str, call: Callable[[], dict], **keys: str | None) -> dict:
    """Run one tool call and write its structured log line.

    ``keys`` are the identifying, non-secret arguments to log (for example
    ``connector_key`` or ``extension_key``); ``None`` values are omitted.
    The tool result is returned unchanged. An exception that escapes ``call``
    is logged with status ``exception`` and re-raised.
    """
    started = time.monotonic()
    status = "exception"
    error_type = None
    with _collect_request_ids() as ids:
        try:
            result = call()
            status = str(result.get("status", "unknown"))
            data = result.get("data")
            if isinstance(data, dict):
                error_type = data.get("error_type")
                state_error = data.get("state_store_error")
                if isinstance(state_error, dict):
                    error_type = error_type or state_error.get("error_type")
            return result
        finally:
            record: dict[str, Any] = {"event": "tool_call", "tool": tool}
            record.update({k: v for k, v in keys.items() if v is not None})
            record["status"] = status
            if error_type:
                record["error_type"] = error_type
            record["aws_request_ids"] = list(ids)
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            logger.info(json.dumps(record, sort_keys=True))


def log_startup(settings: dict[str, Any]) -> None:
    """Write one log line with the server's resolved, non-secret settings."""
    logger.info(json.dumps({"event": "startup", "settings": settings}, sort_keys=True))


def configure_logging() -> None:
    """Send this package's log lines to stdout as bare JSON messages."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
