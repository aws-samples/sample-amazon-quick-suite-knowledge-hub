"""Centralized result-sanitization boundary.

This module implements the single sanitization boundary described in the design
document's "Error Handling" section. Before any :class:`ToolResult` is
serialized and returned to a caller, its ``message`` and ``data`` are scrubbed
of two classes of sensitive content:

1. **Resolved secret values** — any known secret string (for example, a value
   resolved from a ``Secret_Reference``, or a raw credential field that reached
   the server) is replaced with a redaction marker wherever it appears in the
   ``message`` or recursively within ``data``.
2. **Stack-trace / exception-internal text** — Python traceback headers,
   ``File "...", line N`` frames, and exception reprs that expose module paths
   are stripped and replaced with a safe redaction marker.

Centralizing this logic here (rather than relying on each service branch to
scrub its own output) satisfies Requirement 8.4 and Property 25:
*no raw credentials or internal stack detail leak into any result*.

Requirements: 8.4, 3.9, 7.6, 7.7.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from .types import ToolResult

#: Marker substituted in place of a redacted secret value.
SECRET_REDACTION = "[REDACTED]"  # noqa: S105  # nosec B105 - redaction marker literal, not a credential

#: Marker substituted in place of stripped stack-trace / exception-internal text.
STACK_REDACTION = "[stack detail redacted]"

# ---------------------------------------------------------------------------
# Stack-trace / exception-internal text patterns
# ---------------------------------------------------------------------------
# These patterns match the internal detail that must never reach a caller:
#   - "Traceback (most recent call last):" headers
#   - 'File "/path/to/module.py", line 42, in func' frames
#   - Bare 'File "...", line N' fragments
#   - Dotted-module exception reprs such as
#     'quick_admin_buddy_mcp.domain.foo.SomeError: boom'
_STACK_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Full traceback block: header through the trailing exception line.
    re.compile(
        r"Traceback \(most recent call last\):.*?(?:\n\S.*|$)",
        re.DOTALL,
    ),
    # Individual 'File "...", line N, in name' frames (with optional source line).
    re.compile(
        r'File "[^"]+", line \d+(?:, in \S+)?(?:\n\s+.*)?',
    ),
    # Dotted module-qualified exception repr, e.g. 'pkg.mod.SomeError: detail'.
    re.compile(
        r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\b(?::[^\n]*)?",
    ),
)


def _strip_stack_text(text: str) -> str:
    """Return ``text`` with any stack-trace / exception-internal fragments removed."""
    scrubbed = text
    for pattern in _STACK_PATTERNS:
        scrubbed = pattern.sub(STACK_REDACTION, scrubbed)
    return scrubbed


def _redact_secrets(text: str, secrets: tuple[str, ...]) -> str:
    """Return ``text`` with every non-empty secret occurrence replaced."""
    scrubbed = text
    for secret in secrets:
        if secret:
            scrubbed = scrubbed.replace(secret, SECRET_REDACTION)
    return scrubbed


def _scrub_str(text: str, secrets: tuple[str, ...]) -> str:
    """Scrub a single string of secret values and stack-trace text."""
    return _strip_stack_text(_redact_secrets(text, secrets))


def _scrub_value(value: Any, secrets: tuple[str, ...]) -> Any:
    """Recursively scrub strings within an arbitrary JSON-like value.

    Dict keys are preserved as-is (keys are field names, not secret-bearing
    content); dict values, list/tuple elements, and strings are scrubbed.
    Non-string scalars (numbers, booleans, ``None``) pass through untouched.
    """
    if isinstance(value, str):
        return _scrub_str(value, secrets)
    if isinstance(value, dict):
        return {k: _scrub_value(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v, secrets) for v in value]
    return value


def sanitize(result: ToolResult, secrets: Iterable[str] = ()) -> ToolResult:
    """Return a sanitized copy of ``result`` fit for serialization.

    Scrubs the result's ``message`` and, recursively, its ``data`` of:

    * any of the provided ``secrets`` (resolved secret values / raw credentials),
      replacing each occurrence with :data:`SECRET_REDACTION`; and
    * stack-trace and exception-internal text, replacing it with
      :data:`STACK_REDACTION`.

    The input ``result`` is not mutated: a new :class:`ToolResult` is returned
    with the same ``status`` and scrubbed ``message``/``data``. This is the
    single boundary the tool layer applies to every returned result.

    Args:
        result: The tool result to sanitize.
        secrets: Known secret string values to redact wherever they appear.
            Empty strings are ignored.

    Returns:
        A new :class:`ToolResult` with scrubbed ``message`` and ``data``.
    """
    secret_tuple = tuple(secrets)
    clean_message = _scrub_str(result.message, secret_tuple)
    clean_data = _scrub_value(result.data, secret_tuple)
    return replace(result, message=clean_message, data=clean_data)
