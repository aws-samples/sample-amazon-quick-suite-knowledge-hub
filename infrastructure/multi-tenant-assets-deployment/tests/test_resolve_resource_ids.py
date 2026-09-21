"""Unit tests for the resource-selection logic in server.py.

These cover resolve_resource_ids — the id / name / all selection model shared by
the migrate_resources and preview_migration MCP tools. No AWS calls are made:
the QuickSight client is a stub.

Run:  python -m unittest discover -s tests
The module import is skipped (not failed) if runtime-only deps (mcp, boto3) are
not installed in the current environment, so the suite is safe to run in CI
before the AgentCore bundle is built.
"""

import importlib.util
import os
import sys
import unittest

_SERVER = os.path.join(os.path.dirname(__file__), os.pardir, "src", "server.py")


def _load_server():
    sys.argv = ["server.py"]  # avoid triggering --cli branch
    spec = importlib.util.spec_from_file_location("server_under_test", _SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    server = _load_server()
    _IMPORT_ERROR = None
except Exception as e:  # pragma: no cover - environment without runtime deps
    server = None
    _IMPORT_ERROR = e


class FakeQS:
    """Minimal stub of the QuickSight client used by resolve_resource_ids."""

    def __init__(self, agents=None, connectors=None, kbs=None):
        self._agents = agents or []
        self._connectors = connectors or []
        self._kbs = kbs or []

    def list_agents(self, **_):
        return {"AgentSummaries": self._agents}

    def list_action_connectors(self, **_):
        return {"ActionConnectorSummaries": self._connectors}

    def list_knowledge_bases(self, **_):
        return {"KnowledgeBaseSummaries": self._kbs}


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class ResolveResourceIdsTest(unittest.TestCase):
    def setUp(self):
        self.qs = FakeQS(
            agents=[
                {"AgentId": "a1", "Name": "Sales"},
                {"AgentId": "a2", "Name": "sales"},  # case variant
                {"AgentId": "a3", "Name": "HR"},
            ],
            connectors=[{"ActionConnectorId": "c1", "Name": "Slack"}],
            kbs=[{"KnowledgeBaseId": "k1", "Name": "Docs"}],
        )

    def test_all_returns_every_id(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "agent", "all", "")
        self.assertIsNone(err)
        self.assertEqual(ids, ["a1", "a2", "a3"])

    def test_id_returns_value_directly(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "agent", "id", "a2")
        self.assertIsNone(err)
        self.assertEqual(ids, ["a2"])

    def test_name_is_case_insensitive_and_returns_all_matches(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "agent", "name", "sales")
        self.assertIsNone(err)
        self.assertEqual(sorted(ids), ["a1", "a2"])

    def test_name_no_match_returns_error(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "agent", "name", "nope")
        self.assertEqual(ids, [])
        self.assertIn("No agent found with name", err)

    def test_invalid_resource_type_rejected(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "space", "all", "")
        self.assertEqual(ids, [])
        self.assertIn("Invalid resource_type", err)

    def test_invalid_search_by_rejected(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "agent", "foo", "")
        self.assertEqual(ids, [])
        self.assertIn("Invalid search_by", err)

    def test_id_or_name_requires_value(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "agent", "name", "")
        self.assertEqual(ids, [])
        self.assertIn("requires a non-empty 'value'", err)

    def test_type_alias_kb_normalizes(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "kb", "all", "")
        self.assertIsNone(err)
        self.assertEqual(ids, ["k1"])

    def test_connector_all(self):
        ids, err = server.resolve_resource_ids(self.qs, "111", "connector", "all", "")
        self.assertIsNone(err)
        self.assertEqual(ids, ["c1"])


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class MigratableTypesTest(unittest.TestCase):
    def test_space_is_not_migratable(self):
        self.assertNotIn("space", server._MIGRATABLE_TYPES)
        self.assertEqual(
            server._MIGRATABLE_TYPES, {"agent", "connector", "knowledge_base"}
        )


if __name__ == "__main__":
    unittest.main()
