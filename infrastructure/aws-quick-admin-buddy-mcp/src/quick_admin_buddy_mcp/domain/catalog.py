"""The fixed connector catalog and its query surface.

The connector catalog is a compile-time constant: exactly fourteen entries, each
identified by a stable :class:`~quick_admin_buddy_mcp.domain.types.CatalogEntry`
``key``. It is the source of truth for which connectors exist, which productivity
suite gates them, which summary stage they belong to, which configuration fields
they require, and whether they need a third-party authorization grant.

Microsoft Calendar is intentionally absent: it is not a standalone AWS Quick
integration (the Microsoft ``outlook`` connector covers email, calendar, and
contacts), unlike Google Calendar, which is a standalone integration and does
have its own ``google_calendar`` entry.

Google Drive is a single dual-capability entry (``google_drive``, both an action
connector and a knowledge base), mirroring ``sharepoint`` / ``onedrive`` /
``confluence``. There is no separate ``google_drive_kb`` key; the knowledge base
is enabled on ``google_drive`` via :func:`enable_kb_integration`.

``required_fields`` and ``requires_authorization`` follow the data-model notes.
Admin-managed knowledge-base connectors require the source-connection fields
needed to create their backing data source (``CreateDataSource``): ``sharepoint``
requires ``sites`` (the list of SharePoint site URLs to index); its
``sharepoint_domain``/``tenant_id``/``client_id`` come from the mandatory secret
(``domain_url`` etc.), not tool config. ``onedrive`` and ``google_drive`` require
no extra config. For the certificate-based connectors (``sharepoint``,
``onedrive``) the service credentials (``client_id``, ``tenant_id``,
``kms_key_arn``, ``certificate_thumbprint``) are NOT tool config — they are
carried in a mandatory Secrets Manager secret (see the service layer's step 7b/7c;
SharePoint sends them INLINE as SharePointClientCredentials, OneDrive via
SecretArn). ``confluence`` requires ``confluence_url``. None require an
OAuth-style authorization grant by default.
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.types import (
    CatalogEntry,
    ExtensionEntry,
    Stage,
    Suite,
)

# Suite-gate string constants. ``any`` matches every productivity suite.
_GATE_MICROSOFT_365 = "microsoft_365"
_GATE_GOOGLE_WORKSPACE = "google_workspace"
_GATE_ANY = "any"

# The fixed fourteen catalog entries. Order is preserved so ``all()`` returns a
# stable, deterministic list.
_ENTRIES: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        key="outlook",
        display_name="Outlook",
        category="Productivity - email",
        suite_gate=_GATE_MICROSOFT_365,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="ms_teams",
        display_name="Microsoft Teams",
        category="Productivity - messaging",
        suite_gate=_GATE_MICROSOFT_365,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="onedrive",
        display_name="OneDrive",
        category="Productivity - cloud storage",
        suite_gate=_GATE_MICROSOFT_365,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
        supports_action=True,
        supports_kb=True,
        kb_required_fields=(),
    ),
    # NOTE: Microsoft Calendar is intentionally NOT a standalone catalog entry.
    # Unlike Google Calendar (a standalone AWS Quick integration), Microsoft
    # does not expose a separate "Microsoft Calendar" connector — the Microsoft
    # Outlook connector (`outlook`) covers email, calendar, and contacts. Enable
    # `outlook` to get calendar.
    CatalogEntry(
        key="gmail",
        display_name="Gmail",
        category="Productivity - email",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="google_chat",
        display_name="Google Chat",
        category="Productivity - messaging",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="google_drive",
        display_name="Google Drive",
        category="Productivity - cloud storage",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
        supports_action=True,
        supports_kb=True,
    ),
    CatalogEntry(
        key="google_calendar",
        display_name="Google Calendar",
        category="Productivity - calendar",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="google_docs",
        display_name="Google Docs",
        category="Productivity - documents",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="google_meet",
        display_name="Google Meet",
        category="Productivity - meetings",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="google_sheets",
        display_name="Google Sheets",
        category="Productivity - spreadsheets",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="google_slides",
        display_name="Google Slides",
        category="Productivity - presentations",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        stage=Stage.PRODUCTIVITY_CONNECTORS.value,
    ),
    CatalogEntry(
        key="sharepoint",
        display_name="SharePoint",
        category="Enterprise knowledge",
        suite_gate=_GATE_MICROSOFT_365,
        stage=Stage.ENTERPRISE_KNOWLEDGE.value,
        kb_required_fields=("sites",),
        supports_action=True,
        supports_kb=True,
    ),
    CatalogEntry(
        key="confluence",
        display_name="Confluence",
        category="Enterprise knowledge",
        suite_gate=_GATE_ANY,
        stage=Stage.ENTERPRISE_KNOWLEDGE.value,
        kb_required_fields=("confluence_url",),
        supports_action=True,
        supports_kb=True,
    ),
    CatalogEntry(
        key="atlassian",
        display_name="Jira",
        category="Other",
        suite_gate=_GATE_ANY,
        stage=Stage.OTHER_CONNECTORS.value,
    ),
)


class ConnectorCatalog:
    """Read-only query surface over the fixed fourteen-entry connector catalog.

    All methods are pure and deterministic. Instances hold no mutable state; the
    catalog contents are a module-level constant shared across instances.
    """

    def __init__(self, entries: tuple[CatalogEntry, ...] = _ENTRIES) -> None:
        self._entries = entries
        self._by_key = {entry.key: entry for entry in entries}

    def all(self) -> list[CatalogEntry]:
        """Return every catalog entry in stable definition order."""
        return list(self._entries)

    def filter_by_suite(self, suite: Suite) -> list[CatalogEntry]:
        """Return entries valid for ``suite``: gate equals the suite or is ``any``.

        Preserves definition order. ``suite`` is a :class:`Suite`; its ``value``
        is compared against each entry's ``suite_gate``.
        """
        return [
            entry
            for entry in self._entries
            if entry.suite_gate == suite.value or entry.suite_gate == _GATE_ANY
        ]

    def get(self, key: str) -> CatalogEntry | None:
        """Return the entry for ``key``, or ``None`` if the key is unknown."""
        return self._by_key.get(key)

    def required_fields(self, key: str) -> list[str]:
        """Return the required configuration fields for ``key``.

        Returns an empty list for an unknown key or a connector that declares no
        required fields.
        """
        entry = self._by_key.get(key)
        if entry is None:
            return []
        return list(entry.required_fields)

    def requires_authorization(self, key: str) -> bool:
        """Return whether ``key`` needs a third-party authorization grant.

        Returns ``False`` for an unknown key.
        """
        entry = self._by_key.get(key)
        if entry is None:
            return False
        return entry.requires_authorization

    def supports_action(self, key: str) -> bool:
        """Return whether ``key`` can be set up as an action connector.

        Returns ``False`` for an unknown key.
        """
        entry = self._by_key.get(key)
        if entry is None:
            return False
        return entry.supports_action

    def supports_kb(self, key: str) -> bool:
        """Return whether ``key`` can be set up as a knowledge base.

        Returns ``False`` for an unknown key.
        """
        entry = self._by_key.get(key)
        if entry is None:
            return False
        return entry.supports_kb

    def stage_of(self, key: str) -> Stage | None:
        """Return the summary :class:`Stage` for ``key``, or ``None`` if unknown."""
        entry = self._by_key.get(key)
        if entry is None:
            return None
        return Stage(entry.stage)


# ---------------------------------------------------------------------------
# Extension catalog
# ---------------------------------------------------------------------------
# AWS Quick extensions are client-side add-ins the user installs in their
# Office app or browser. They have NO AWS Quick admin API, so the backend
# only records an admin-attested status (see ExtensionStatus) — it never creates
# or verifies them. The set is suite-gated: Microsoft 365 offers the four Office
# add-ins; Google Workspace offers the browser extension.
_EXTENSION_ENTRIES: tuple[ExtensionEntry, ...] = (
    ExtensionEntry(
        key="outlook_addin",
        display_name="Microsoft Outlook extension",
        suite_gate=_GATE_MICROSOFT_365,
        docs_url="https://docs.aws.amazon.com/quick/latest/userguide/outlook-extension.html",
        description="AWS Quick inside Outlook (email/calendar context).",
    ),
    ExtensionEntry(
        key="excel_addin",
        display_name="Microsoft Excel extension",
        suite_gate=_GATE_MICROSOFT_365,
        docs_url="https://docs.aws.amazon.com/quick/latest/userguide/excel-extension.html",
        description="AWS Quick inside Excel workbooks.",
    ),
    ExtensionEntry(
        key="word_addin",
        display_name="Microsoft Word extension",
        suite_gate=_GATE_MICROSOFT_365,
        docs_url="https://docs.aws.amazon.com/quick/latest/userguide/word-extension.html",
        description="AWS Quick inside Word documents.",
    ),
    ExtensionEntry(
        key="powerpoint_addin",
        display_name="Microsoft PowerPoint extension",
        suite_gate=_GATE_MICROSOFT_365,
        docs_url="https://docs.aws.amazon.com/quick/latest/userguide/powerpoint-extension.html",
        description="AWS Quick inside PowerPoint decks.",
    ),
    ExtensionEntry(
        key="browser_extension",
        display_name="Browser extension",
        suite_gate=_GATE_GOOGLE_WORKSPACE,
        docs_url="https://docs.aws.amazon.com/quick/latest/userguide/browser-extension.html",
        description="AWS Quick in the browser alongside Google Workspace web apps.",
    ),
)


class ExtensionCatalog:
    """Read-only query surface over the fixed extension catalog.

    All methods are pure and deterministic. The catalog contents are a
    module-level constant shared across instances.
    """

    def __init__(
        self, entries: tuple[ExtensionEntry, ...] = _EXTENSION_ENTRIES
    ) -> None:
        self._entries = entries
        self._by_key = {entry.key: entry for entry in entries}

    def all(self) -> list[ExtensionEntry]:
        """Return every extension entry in stable definition order."""
        return list(self._entries)

    def filter_by_suite(self, suite: Suite) -> list[ExtensionEntry]:
        """Return extensions valid for ``suite`` (gate equals the suite or ``any``).

        Preserves definition order.
        """
        return [
            entry
            for entry in self._entries
            if entry.suite_gate == suite.value or entry.suite_gate == _GATE_ANY
        ]

    def get(self, key: str) -> ExtensionEntry | None:
        """Return the extension entry for ``key``, or ``None`` if unknown."""
        return self._by_key.get(key)
