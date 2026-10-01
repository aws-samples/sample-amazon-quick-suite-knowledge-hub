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

_SRC = os.path.join(os.path.dirname(__file__), os.pardir, "src")
_SERVER = os.path.join(_SRC, "server.py")


def _load_server():
    sys.argv = ["server.py"]  # avoid triggering --cli branch
    # server.py imports its flat sibling modules (common, backups, resources,
    # migrate_*) by bare name; the AgentCore bundle flattens them next to
    # server.py, so mirror that here by putting src/ on sys.path.
    src = os.path.abspath(_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)
    spec = importlib.util.spec_from_file_location("server_under_test", _SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    server = _load_server()
    _IMPORT_ERROR = None
    # After the refactor the migration helpers live in flat sibling modules.
    # Tests that monkeypatch a global read INSIDE a helper must patch it on the
    # module that actually defines/binds it (patching server.X would not affect
    # the helper's own module-level binding).
    import backups as backups_mod
    import common as common_mod
    import migrate_knowledge_bases as kb_mod
except Exception as e:  # pragma: no cover - environment without runtime deps
    server = None
    _IMPORT_ERROR = e
    backups_mod = common_mod = kb_mod = None


class FakeQS:
    """Minimal stub of the QuickSight client used by resolve_resource_ids."""

    def __init__(self, agents=None, connectors=None, kbs=None, spaces=None):
        self._agents = agents or []
        self._connectors = connectors or []
        self._kbs = kbs or []
        self._spaces = spaces or []

    def list_agents(self, **_):
        return {"AgentSummaries": self._agents}

    def list_action_connectors(self, **_):
        return {"ActionConnectorSummaries": self._connectors}

    def list_knowledge_bases(self, **_):
        return {"KnowledgeBaseSummaries": self._kbs}

    def list_spaces(self, **_):
        return {"SpaceSummaries": self._spaces}

    def list_flows(self, **_):
        return {"FlowSummaryList": getattr(self, "_flows", [])}


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
        ids, err = server.resolve_resource_ids(self.qs, "111", "dashboard", "all", "")
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

    # ── search_by=id value coercion (JSON / wrapped / list / malformed) ──
    def test_id_accepts_bare_id(self):
        ids, err = server.resolve_resource_ids(
            self.qs, "111", "knowledge_base", "id", "k1"
        )
        self.assertIsNone(err)
        self.assertEqual(ids, ["k1"])

    def test_id_accepts_json_wrapped_object(self):
        ids, err = server.resolve_resource_ids(
            self.qs, "111", "knowledge_base", "id", '{"knowledge_base_id":"k1"}'
        )
        self.assertIsNone(err)
        self.assertEqual(ids, ["k1"])

    def test_id_accepts_json_array(self):
        ids, err = server.resolve_resource_ids(
            self.qs, "111", "knowledge_base", "id", '["k1","k2"]'
        )
        self.assertIsNone(err)
        self.assertEqual(ids, ["k1", "k2"])

    def test_id_accepts_comma_separated(self):
        ids, err = server.resolve_resource_ids(
            self.qs, "111", "knowledge_base", "id", "k1, k2 ,k3"
        )
        self.assertIsNone(err)
        self.assertEqual(ids, ["k1", "k2", "k3"])

    def test_id_dedupes(self):
        ids, err = server.resolve_resource_ids(
            self.qs, "111", "knowledge_base", "id", "k1,k1,k2"
        )
        self.assertIsNone(err)
        self.assertEqual(ids, ["k1", "k2"])

    def test_id_rejects_truncated_json_value(self):
        # The exact malformed value observed in a real migration failure.
        ids, err = server.resolve_resource_ids(
            self.qs,
            "111",
            "knowledge_base",
            "id",
            '{"knowledge_base_id":"3d874e85-fe37-41ac',
        )
        self.assertEqual(ids, [])
        self.assertIn("Invalid resource id", err)

    def test_id_rejects_object_without_id_key(self):
        ids, err = server.resolve_resource_ids(
            self.qs, "111", "knowledge_base", "id", '{"foo":"bar"}'
        )
        self.assertEqual(ids, [])
        self.assertIn("Could not find a resource id", err)


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class MigratableTypesTest(unittest.TestCase):
    def test_migratable_types(self):
        # agent/connector/knowledge_base/space/flow are all migratable.
        self.assertEqual(
            server._MIGRATABLE_TYPES,
            {"agent", "connector", "knowledge_base", "space", "flow"},
        )

    def test_flow_registered_in_all_maps(self):
        # Flow must be wired into every per-type map used by preview/migrate.
        self.assertIn("flow", server._RESOURCE_LISTERS)
        self.assertIn("flow", server._RESOURCE_DESCRIBERS)
        self.assertIn("flow", server._RESOURCE_DESCRIBE_KEYS)
        self.assertIn("flow", server._RESOURCE_PERMISSION_DESCRIBERS)
        # Flows read permissions via get_flow_permissions (not describe_*).
        self.assertEqual(
            server._RESOURCE_PERMISSION_DESCRIBERS["flow"][0], "get_flow_permissions"
        )


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class TargetResourceExistsTest(unittest.TestCase):
    """target_resource_exists: existence check used for CREATE/UPDATE mapping."""

    def _client_error(self, code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": code}}, "Describe")

    def _fake_target(self, *, raises=None):
        outer = self

        class FakeTarget:
            def describe_agent(self, **_):
                if raises:
                    raise outer._client_error(raises)
                return {"Agent": {"AgentId": "x"}}

            describe_action_connector = describe_knowledge_base = describe_space = (
                describe_agent
            )

        return FakeTarget()

    def test_exists_returns_true(self):
        exists, err = server.target_resource_exists(
            self._fake_target(), "222", "agent", "a1"
        )
        self.assertTrue(exists)
        self.assertIsNone(err)

    def test_not_found_returns_false_no_error(self):
        exists, err = server.target_resource_exists(
            self._fake_target(raises="ResourceNotFoundException"), "222", "agent", "a1"
        )
        self.assertFalse(exists)
        self.assertIsNone(err)

    def test_other_error_returns_none_with_error(self):
        exists, err = server.target_resource_exists(
            self._fake_target(raises="AccessDeniedException"), "222", "agent", "a1"
        )
        self.assertIsNone(exists)
        self.assertIsNotNone(err)


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class DescribeTargetResourceTest(unittest.TestCase):
    """describe_target_resource: fetch a matched target asset (scoped to source ids)."""

    def _client_error(self, code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": code}}, "Describe")

    def _target(self, *, raises=None):
        outer = self

        class FakeTarget:
            def describe_knowledge_base(self, **_):
                if raises:
                    raise outer._client_error(raises)
                return {
                    "KnowledgeBase": {"Name": "Docs", "Type": "S3", "Status": "ACTIVE"}
                }

        return FakeTarget()

    def test_existing_returns_obj(self):
        obj, exists, err = server.describe_target_resource(
            self._target(), "222", "knowledge_base", "k1"
        )
        self.assertTrue(exists)
        self.assertIsNone(err)
        self.assertEqual(obj["knowledge_base_id"], "k1")
        self.assertEqual(obj["name"], "Docs")

    def test_absent_returns_none_false(self):
        obj, exists, err = server.describe_target_resource(
            self._target(raises="ResourceNotFoundException"),
            "222",
            "knowledge_base",
            "k1",
        )
        self.assertIsNone(obj)
        self.assertFalse(exists)
        self.assertIsNone(err)

    def test_error_surfaced(self):
        obj, exists, err = server.describe_target_resource(
            self._target(raises="AccessDeniedException"), "222", "knowledge_base", "k1"
        )
        self.assertIsNone(obj)
        self.assertIsNone(exists)
        self.assertIsNotNone(err)


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class DescribeResourcePermissionsTest(unittest.TestCase):
    """describe_resource_permissions: normalized source/target permission read."""

    def _client_error(self, code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": code}}, "Describe")

    def _qs(self, *, perms=None, raises=None):
        outer = self

        class FakeQS:
            def describe_knowledge_base_permissions(self, **_):
                if raises:
                    raise outer._client_error(raises)
                return {"Permissions": perms or []}

        return FakeQS()

    def test_normalizes_permissions(self):
        raw = [
            {
                "Principal": "arn:...:user/default/alice",
                "Actions": ["quicksight:DescribeKnowledgeBase"],
            }
        ]
        perms, err = server.describe_resource_permissions(
            self._qs(perms=raw), "111", "knowledge_base", "k1"
        )
        self.assertIsNone(err)
        self.assertEqual(
            perms,
            [
                {
                    "principal": "arn:...:user/default/alice",
                    "actions": ["quicksight:DescribeKnowledgeBase"],
                }
            ],
        )

    def test_empty_permissions(self):
        perms, err = server.describe_resource_permissions(
            self._qs(perms=[]), "111", "knowledge_base", "k1"
        )
        self.assertEqual(perms, [])
        self.assertIsNone(err)

    def test_error_returns_empty_with_error(self):
        perms, err = server.describe_resource_permissions(
            self._qs(raises="AccessDeniedException"), "111", "knowledge_base", "k1"
        )
        self.assertEqual(perms, [])
        self.assertIsNotNone(err)


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class UpsertBackupTest(unittest.TestCase):
    """do_migrate_resources upsert + pre-update backup behavior (connector path)."""

    def _client_error(self, code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": code}}, "Op")

    def setUp(self):
        outer = self

        class FakeSource:
            def describe_action_connector(self, **_):
                return {
                    "ActionConnector": {
                        "ActionConnectorId": "c1",
                        "Name": "Slack",
                        "Type": "GENERIC_HTTP",
                        "AuthenticationConfig": {
                            "AuthenticationType": "NONE",
                            "AuthenticationMetadata": {
                                "NoneConnectionMetadata": {
                                    "BaseEndpoint": "https://example.com"
                                }
                            },
                        },
                    }
                }

            def describe_action_connector_permissions(self, **_):
                return {"Permissions": []}

        # Target: create raises ResourceExists when the resource "exists",
        # forcing the update path (which triggers a pre-update backup).
        class FakeTarget:
            def __init__(self, exists):
                self._exists = exists
                self.update_called = False

            def describe_action_connector(self, **_):
                return {"ActionConnector": {"ActionConnectorId": "c1", "Name": "Slack"}}

            def create_action_connector(self, **_):
                if self._exists:
                    raise outer._client_error("ResourceExistsException")

            def update_action_connector(self, **_):
                self.update_called = True

            def describe_action_connector_permissions(self, **_):
                return {"Permissions": []}

        # Backup S3 client: records puts; can be told to fail.
        class FakeBackupS3:
            def __init__(self, fail=False):
                self.fail = fail
                self.puts = []

            def list_objects_v2(self, **_):
                return {"Contents": [], "IsTruncated": False}

            def put_object(self, **kw):
                if self.fail:
                    raise outer._client_error("AccessDenied")
                self.puts.append(kw["Key"])

        self.FakeSource = FakeSource
        self.FakeTarget = FakeTarget
        self.FakeBackupS3 = FakeBackupS3

        self._orig_assume = server.assume_role_client
        self._orig_src = server.SOURCE_ROLE_ARN
        self._orig_tgt = server.TARGET_ROLE_ARN
        self._orig_boto = server.boto3.client
        self._orig_bucket = server.BACKUP_BUCKET
        self._orig_common_bucket = common_mod.BACKUP_BUCKET
        server.SOURCE_ROLE_ARN = "arn:aws:iam::111:role/src"
        server.TARGET_ROLE_ARN = "arn:aws:iam::222:role/tgt"
        server.BACKUP_BUCKET = "quick-migrator-backups-999"
        # The backup/snapshot helpers live in backups.py and read
        # common.BACKUP_BUCKET, so set it there too.
        common_mod.BACKUP_BUCKET = "quick-migrator-backups-999"

    def tearDown(self):
        server.assume_role_client = self._orig_assume
        server.SOURCE_ROLE_ARN = self._orig_src
        server.TARGET_ROLE_ARN = self._orig_tgt
        server.boto3.client = self._orig_boto
        server.BACKUP_BUCKET = self._orig_bucket
        common_mod.BACKUP_BUCKET = self._orig_common_bucket

    def _patch(self, target, backup_s3):
        src = self.FakeSource()

        def fake_assume(service, role_arn, region):
            return src if role_arn == server.SOURCE_ROLE_ARN else target

        def fake_boto_client(service, **_):
            return backup_s3  # the runner-account S3 client for backups

        server.assume_role_client = fake_assume
        server.boto3.client = fake_boto_client

    def test_update_takes_backup_first(self):
        target = self.FakeTarget(exists=True)
        backup = self.FakeBackupS3(fail=False)
        self._patch(target, backup)
        report = server.do_migrate_resources(
            "111",
            "222",
            "connector",
            ["c1"],
            "us-east-1",
            "dev",
            "prod",
            server.DEFAULT_QS_SERVICE_ROLE,
        )
        # A pre-update backup is taken BEFORE the update, and a post-migration
        # snapshot after — both land in the asset's folder.
        self.assertTrue(all(k.startswith("Slack (c1)/") for k in backup.puts))
        self.assertGreaterEqual(len(backup.puts), 1)
        self.assertTrue(target.update_called)
        self.assertIn("backups", report)

    def test_update_aborts_when_backup_fails(self):
        target = self.FakeTarget(exists=True)
        backup = self.FakeBackupS3(fail=True)
        self._patch(target, backup)
        report = server.do_migrate_resources(
            "111",
            "222",
            "connector",
            ["c1"],
            "us-east-1",
            "dev",
            "prod",
            server.DEFAULT_QS_SERVICE_ROLE,
        )
        # Update must NOT run when the backup could not be written.
        self.assertFalse(target.update_called)
        statuses = [c["status"] for c in report["migrated"]["connectors"]]
        self.assertTrue(
            any("backup before update failed" in s for s in statuses), statuses
        )

    def test_create_writes_snapshot(self):
        # A successful CREATE (resource absent in target) must still land a
        # snapshot in the bucket (v1) so it shows up in list_backups.
        target = self.FakeTarget(exists=False)
        backup = self.FakeBackupS3(fail=False)
        self._patch(target, backup)
        report = server.do_migrate_resources(
            "111",
            "222",
            "connector",
            ["c1"],
            "us-east-1",
            "dev",
            "prod",
            server.DEFAULT_QS_SERVICE_ROLE,
        )
        # No pre-update backup (nothing to overwrite) but a post-migration snapshot.
        self.assertTrue(any(k.startswith("Slack (c1)/") for k in backup.puts))
        self.assertIn("snapshots", report)
        self.assertEqual(report["snapshots"][0]["action"], "CREATED")


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class FlowMigrationTest(unittest.TestCase):
    """_migrate_flows: NAME-matched upsert (CreateFlow assigns a new id)."""

    def _client_error(self, code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": code}}, "Op")

    def setUp(self):
        class FakeSource:
            def describe_flow(self, **_):
                return {
                    "Flow": {
                        "FlowId": "srcflow1",
                        "Name": "MyFlow",
                        "FlowDefinition": {"steps": []},
                        "Description": "d",
                    }
                }

            def get_flow_permissions(self, **_):
                return {"Permissions": []}

        class FakeTarget:
            def __init__(self, existing_names):
                # existing_names: list of (flow_id, name) already in target
                self._existing = existing_names
                self.created = False
                self.updated_id = None

            def list_flows(self, **_):
                return {
                    "FlowSummaryList": [
                        {"FlowId": fid, "Name": nm} for fid, nm in self._existing
                    ]
                }

            def describe_flow(self, **kw):
                fid = kw["FlowId"]
                nm = next((n for i, n in self._existing if i == fid), "MyFlow")
                return {"Flow": {"FlowId": fid, "Name": nm}}

            def get_flow_permissions(self, **_):
                return {"Permissions": []}

            def create_flow(self, **_):
                self.created = True
                return {"FlowId": "newtgtflow"}

            def update_flow(self, **kw):
                self.updated_id = kw["FlowId"]

        class FakeBackupS3:
            def list_objects_v2(self, **_):
                return {"Contents": [], "IsTruncated": False}

            def put_object(self, **_):
                pass

        self.FakeSource = FakeSource
        self.FakeTarget = FakeTarget
        self.FakeBackupS3 = FakeBackupS3

        self._orig_assume = server.assume_role_client
        self._orig_boto = server.boto3.client
        self._orig_src = server.SOURCE_ROLE_ARN
        self._orig_tgt = server.TARGET_ROLE_ARN
        self._orig_bucket = server.BACKUP_BUCKET
        self._orig_common_bucket = common_mod.BACKUP_BUCKET
        server.SOURCE_ROLE_ARN = "arn:aws:iam::111:role/src"
        server.TARGET_ROLE_ARN = "arn:aws:iam::222:role/tgt"
        server.BACKUP_BUCKET = "backups-999"
        common_mod.BACKUP_BUCKET = "backups-999"

    def tearDown(self):
        server.assume_role_client = self._orig_assume
        server.boto3.client = self._orig_boto
        server.SOURCE_ROLE_ARN = self._orig_src
        server.TARGET_ROLE_ARN = self._orig_tgt
        server.BACKUP_BUCKET = self._orig_bucket
        common_mod.BACKUP_BUCKET = self._orig_common_bucket

    def _run(self, target):
        src = self.FakeSource()
        backup = self.FakeBackupS3()
        server.assume_role_client = lambda service, arn, region: (
            src if arn == server.SOURCE_ROLE_ARN else target
        )
        server.boto3.client = lambda *a, **k: backup
        return server.do_migrate_resources(
            "111",
            "222",
            "flow",
            ["srcflow1"],
            "us-east-1",
            "dev",
            "prod",
            server.DEFAULT_QS_SERVICE_ROLE,
        )

    def test_no_name_match_creates(self):
        target = self.FakeTarget(existing_names=[])
        report = self._run(target)
        self.assertTrue(target.created)
        self.assertIsNone(target.updated_id)
        st = report["migrated"]["flows"][0]["status"]
        self.assertEqual(st, "CREATED")

    def test_name_match_updates_target_id(self):
        target = self.FakeTarget(existing_names=[("tgtflowX", "MyFlow")])
        report = self._run(target)
        self.assertFalse(target.created)
        self.assertEqual(target.updated_id, "tgtflowX")  # updated the TARGET id
        self.assertEqual(report["migrated"]["flows"][0]["status"], "UPDATED")

    def test_ambiguous_name_skips(self):
        target = self.FakeTarget(existing_names=[("f1", "MyFlow"), ("f2", "MyFlow")])
        report = self._run(target)
        self.assertFalse(target.created)
        self.assertIsNone(target.updated_id)
        self.assertIn("SKIPPED", report["migrated"]["flows"][0]["status"])

    def test_describe_flow_passes_publish_state(self):
        # DescribeFlow REQUIRES PublishState — helper must always send it.
        seen = {}

        class T:
            def describe_flow(self, **kw):
                seen.update(kw)
                return {"Flow": {"FlowId": "f1", "Name": "MyFlow"}}

        server._describe_flow(T(), "111", "f1")
        self.assertEqual(seen.get("PublishState"), "PUBLISHED")
        self.assertEqual(seen.get("FlowId"), "f1")


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class BackupCatalogTest(unittest.TestCase):
    """list_backup_catalog + _parse_backup_folder + restore helpers."""

    def _client_error(self, code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": code}}, "Op")

    def test_parse_backup_folder(self):
        self.assertEqual(
            server._parse_backup_folder("Alembic (356cca57-abc)"),
            ("Alembic", "356cca57-abc"),
        )
        self.assertEqual(server._parse_backup_folder("no-parens"), (None, None))

    def _s3_with(self, keys):
        # keys: list of (Key, Size)
        class FakeS3:
            def list_objects_v2(self, **_):
                return {
                    "Contents": [
                        {"Key": k, "Size": sz, "LastModified": "t"} for k, sz in keys
                    ],
                    "IsTruncated": False,
                }

        return FakeS3()

    def test_catalog_groups_versions_and_filters(self):
        s3 = self._s3_with(
            [
                ("Alembic (a1)/a1_v1.json", 10),
                ("Alembic (a1)/a1_v2.json", 12),
                ("Teams (c9)/json_v1.json", 8),  # legacy filename form still parsed
                ("Alembic (a1)/notes.txt", 3),  # ignored (not *_v<N>.json)
            ]
        )
        assets, err = server.list_backup_catalog(s3, "bkt", "")
        self.assertIsNone(err)
        by_id = {a["asset_id"]: a for a in assets}
        self.assertEqual(sorted(by_id), ["a1", "c9"])
        self.assertEqual([v["version"] for v in by_id["a1"]["versions"]], [1, 2])
        self.assertEqual(by_id["a1"]["latest_version"], 2)

        # filter by name substring
        assets, _ = server.list_backup_catalog(s3, "bkt", "teams")
        self.assertEqual([a["asset_id"] for a in assets], ["c9"])
        # filter by id substring
        assets, _ = server.list_backup_catalog(s3, "bkt", "a1")
        self.assertEqual([a["asset_id"] for a in assets], ["a1"])

    def test_catalog_no_bucket(self):
        assets, err = server.list_backup_catalog(self._s3_with([]), "", "")
        self.assertEqual(assets, [])
        self.assertIsNotNone(err)

    def test_collect_dependencies_agent(self):
        class T:
            def describe_action_connector(self, **_):
                return {"ActionConnector": {"Name": "Teams", "Type": "MICROSOFT_TEAMS"}}

        obj = {
            "ActionConnectors": ["arn:aws:quicksight:us-east-1:2:action-connector/c9"]
        }
        deps = server._collect_dependencies(T(), "2", "agent", obj)
        self.assertEqual(len(deps["action_connectors"]), 1)
        self.assertEqual(deps["action_connectors"][0]["action_connector_id"], "c9")
        self.assertEqual(deps["action_connectors"][0]["name"], "Teams")

    def test_collect_dependencies_space(self):
        obj = {
            "resources": [
                {
                    "resourceType": "AGENT",
                    "resourceDetails": {
                        "resourceArn": "arn:aws:quicksight:us-east-1:2:agent/a1"
                    },
                }
            ]
        }
        deps = server._collect_dependencies(None, "2", "space", obj)
        self.assertEqual(deps["linked_resources"][0]["resource_id"], "a1")

    def test_restore_resource_agent_updates(self):
        calls = {}

        class T:
            def update_agent(self, **kw):
                calls["update_agent"] = kw

        env = {
            "resource_type": "agent",
            "resource_id": "a1",
            "name": "Alembic",
            "resource": {"Name": "Alembic", "Description": "d"},
        }
        status = server._restore_resource(T(), "2", "us-east-1", env, {"errors": []})
        self.assertEqual(status, "RESTORED")
        self.assertEqual(calls["update_agent"]["AgentId"], "a1")

    def test_restore_resource_flow_needs_definition(self):
        env = {
            "resource_type": "flow",
            "resource_id": "f1",
            "name": "F",
            "resource": {"Name": "F"},
        }  # no FlowDefinition
        status = server._restore_resource(
            object(), "2", "us-east-1", env, {"errors": []}
        )
        self.assertIn("FAILED", status)

    def test_restore_agent_reattaches_connectors_only(self):
        # Agent restore re-attaches connector LINKS but never updates connectors.
        calls = {}

        class T:
            def describe_agent(self, **_):
                return {"Agent": {"ActionConnectors": []}}  # none currently linked

            def update_agent(self, **kw):
                calls["update_agent"] = kw

            # If restore ever called update_action_connector, this would record it.
            def update_action_connector(self, **kw):
                calls["update_action_connector"] = kw

        env = {
            "resource_type": "agent",
            "resource_id": "a1",
            "name": "Alembic",
            "resource": {
                "Name": "Alembic",
                "ActionConnectors": [
                    "arn:aws:quicksight:us-east-1:111:action-connector/c9"
                ],
                "CustomPromptInterface": {"CustomInstructions": "be helpful"},
            },
        }
        status = server._restore_resource(T(), "222", "us-east-1", env, {"errors": []})
        self.assertEqual(status, "RESTORED")
        # Connector re-attached as a LINK (remapped to target account 222)...
        self.assertIn("ActionConnectorsToAdd", calls["update_agent"])
        self.assertIn("222", calls["update_agent"]["ActionConnectorsToAdd"][0])
        # ...and the instructions restored...
        self.assertIn("CustomPromptInput", calls["update_agent"])
        # ...but the connector RESOURCE itself was never updated.
        self.assertNotIn("update_action_connector", calls)


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class KbBucketNameTest(unittest.TestCase):
    def test_prefixes_and_appends_account(self):
        n = server._derive_kb_bucket_name("my-docs", "014498642076")
        self.assertTrue(n.startswith("knowledge-base-"))
        self.assertTrue(n.endswith("-014498642076"))
        self.assertLessEqual(len(n), 63)

    def test_reuses_existing_prefix(self):
        n = server._derive_kb_bucket_name("knowledge-base-foo", "014498642076")
        self.assertEqual(n, "knowledge-base-foo-014498642076")

    def test_sanitizes_and_truncates(self):
        n = server._derive_kb_bucket_name("Weird_Name!" + "x" * 80, "014498642076")
        self.assertLessEqual(len(n), 63)
        self.assertNotIn("_", n)
        self.assertTrue(n.startswith("knowledge-base-"))


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class KnowledgeBaseRoutingTest(unittest.TestCase):
    """_migrate_knowledge_bases routes by source KB type."""

    def _client_error(self, code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": code}}, "Op")

    def _src(self, kb_type, *, bucket=None, ds_type=None, ds_params=None):
        cfg = {}
        if kb_type == "S3_KNOWLEDGE_BASE":
            cfg = {
                "templateConfiguration": {
                    "template": {
                        "type": "S3V2",
                        # A non-empty filterConfiguration the migration must
                        # PRESERVE from the source (not clobber with an empty one).
                        "filterConfiguration": {
                            "inclusionPatterns": ["*.pdf"],
                            "inclusionPrefixes": ["docs/"],
                            "exclusionPatterns": ["*.tmp"],
                            "exclusionPrefixes": ["drafts/"],
                            "maxFileSizeInMegaBytes": "500",
                        },
                        "connectionConfiguration": {
                            "bucketName": bucket or "src-bucket"
                        },
                    }
                }
            }
        else:
            cfg = {"templateConfiguration": {"template": {"type": kb_type + "V3"}}}

        class Src:
            def describe_knowledge_base(self, **_):
                return {
                    "KnowledgeBase": {
                        "Name": "KB",
                        "Type": kb_type,
                        "KnowledgeBaseConfiguration": cfg,
                        "DataSourceArn": "arn:aws:quicksight:us-east-1:111:datasource/srcds",
                    }
                }

            def describe_data_source(self, **_):
                return {
                    "DataSource": {
                        "Type": ds_type or kb_type,
                        "DataSourceParameters": ds_params or {},
                    }
                }

            def describe_knowledge_base_permissions(self, **_):
                return {"Permissions": []}

        return Src()

    def _tgt(self):
        from botocore.exceptions import ClientError

        rec = {"created_ds": [], "created_kb": [], "kb_config": None}

        class Tgt:
            def describe_knowledge_base(self, **_):
                raise ClientError(
                    {"Error": {"Code": "ResourceNotFoundException", "Message": "nf"}},
                    "DescribeKnowledgeBase",
                )

            def create_data_source(self, **kw):
                rec["created_ds"].append(kw)
                return {
                    "Arn": f"arn:aws:quicksight:us-east-1:222:datasource/{kw['DataSourceId']}"
                }

            def create_knowledge_base(self, **kw):
                rec["created_kb"].append(kw)
                rec["kb_config"] = kw.get("KnowledgeBaseConfiguration")

            def describe_knowledge_base_permissions(self, **_):
                return {"Permissions": []}

        t = Tgt()
        t.rec = rec
        return t

    def _s3(self):
        class S3:
            def create_bucket(self, **_):
                pass

            def put_bucket_policy(self, **_):
                pass

            def head_bucket(self, **_):
                pass

        return S3()

    def _run(self, src, tgt, *, kb_bucket="", ds_arn=""):
        report = {
            "errors": [],
            "steps": [],
            "migrated": {"knowledge_bases": [], "buckets": []},
        }
        # Patch the module helpers that touch AWS/wait. These are bound in the
        # migrate_knowledge_bases module (where _migrate_knowledge_bases calls
        # them), so patch them there — not on server.
        orig_verify = kb_mod.verify_qs_service_role
        orig_bucket = kb_mod.create_kb_bucket
        orig_wait = kb_mod.wait_for_kb_active
        kb_mod.verify_qs_service_role = lambda *a, **k: True
        kb_mod.create_kb_bucket = lambda *a, **k: None
        kb_mod.wait_for_kb_active = lambda *a, **k: None
        try:
            server._migrate_knowledge_bases(
                src,
                tgt,
                self._s3(),
                object(),
                object(),
                "111",
                "222",
                "us-east-1",
                ["kb1"],
                "prod",
                server.DEFAULT_QS_SERVICE_ROLE,
                kb_bucket,
                ds_arn,
                report,
            )
        finally:
            kb_mod.verify_qs_service_role = orig_verify
            kb_mod.create_kb_bucket = orig_bucket
            kb_mod.wait_for_kb_active = orig_wait
        return report, tgt.rec

    def test_s3_creates_with_derived_bucket(self):
        report, rec = self._run(
            self._src("S3_KNOWLEDGE_BASE", bucket="docs"), self._tgt()
        )
        st = report["migrated"]["knowledge_bases"][0]["status"]
        self.assertEqual(st, "CREATED")
        # S3V2 config sent, bucket derived + prefixed
        bkt = rec["kb_config"]["templateConfiguration"]["template"][
            "connectionConfiguration"
        ]["bucketName"]
        self.assertTrue(bkt.startswith("knowledge-base-") and bkt.endswith("-222"))

    def test_s3_preserves_source_filter_configuration(self):
        # The source KB's filterConfiguration (inclusion/exclusion patterns,
        # prefixes, max file size) must survive migration, not be clobbered with
        # an empty one. Only the connectionConfiguration is retargeted.
        report, rec = self._run(
            self._src("S3_KNOWLEDGE_BASE", bucket="docs"), self._tgt()
        )
        self.assertEqual(report["migrated"]["knowledge_bases"][0]["status"], "CREATED")
        tmpl = rec["kb_config"]["templateConfiguration"]["template"]
        fc = tmpl["filterConfiguration"]
        self.assertEqual(fc["inclusionPatterns"], ["*.pdf"])
        self.assertEqual(fc["inclusionPrefixes"], ["docs/"])
        self.assertEqual(fc["exclusionPatterns"], ["*.tmp"])
        self.assertEqual(fc["exclusionPrefixes"], ["drafts/"])
        self.assertEqual(fc["maxFileSizeInMegaBytes"], "500")
        # Connection is retargeted to the derived target bucket/account.
        self.assertTrue(tmpl["connectionConfiguration"]["bucketName"].endswith("-222"))
        self.assertEqual(tmpl["connectionConfiguration"]["bucketOwnerAccountId"], "222")
        src = self._src(
            "WEB_CRAWLER",
            ds_type="WEB_CRAWLER",
            ds_params={"WebCrawlerParameters": {"WebCrawlerAuthType": "NO_AUTH"}},
        )
        report, rec = self._run(src, self._tgt())
        self.assertEqual(report["migrated"]["knowledge_bases"][0]["status"], "CREATED")
        self.assertTrue(rec["created_ds"])  # replayed data source created

    def test_credentialed_skips_without_arn(self):
        report, rec = self._run(self._src("SHAREPOINT"), self._tgt())
        st = report["migrated"]["knowledge_bases"][0]["status"]
        self.assertIn("SKIPPED", st)
        self.assertFalse(rec["created_kb"])

    def test_credentialed_uses_provided_arn(self):
        report, rec = self._run(
            self._src("CONFLUENCE"),
            self._tgt(),
            ds_arn="arn:aws:quicksight:us-east-1:222:datasource/preexisting",
        )
        self.assertEqual(report["migrated"]["knowledge_bases"][0]["status"], "CREATED")
        self.assertEqual(
            rec["created_kb"][0]["DataSourceArn"],
            "arn:aws:quicksight:us-east-1:222:datasource/preexisting",
        )

    def test_s3_rejects_bad_user_bucket(self):
        report, rec = self._run(
            self._src("S3_KNOWLEDGE_BASE"), self._tgt(), kb_bucket="my-own-bucket"
        )
        st = report["migrated"]["knowledge_bases"][0]["status"]
        self.assertIn("SKIPPED", st)
        self.assertIn("knowledge-base-", st)


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class RemapFlowDefinitionTest(unittest.TestCase):
    def test_rewrites_source_account_arns(self):
        d = {
            "steps": [{"target": "arn:aws:quicksight:us-east-1:111111111111:space/abc"}]
        }
        out = server._remap_flow_definition(d, "111111111111", "222222222222")
        self.assertEqual(
            out["steps"][0]["target"],
            "arn:aws:quicksight:us-east-1:222222222222:space/abc",
        )

    def test_noop_when_no_source_arns(self):
        d = {"steps": [{"target": "arn:aws:quicksight:us-east-1:999999999999:space/x"}]}
        out = server._remap_flow_definition(d, "111111111111", "222222222222")
        self.assertEqual(out, d)

    def test_noop_when_accounts_equal(self):
        d = {"x": ":111111111111:"}
        self.assertIs(
            server._remap_flow_definition(d, "111111111111", "111111111111"), d
        )

    def test_handles_none(self):
        self.assertIsNone(server._remap_flow_definition(None, "111", "222"))


@unittest.skipIf(server is None, f"runtime deps unavailable: {_IMPORT_ERROR}")
class ExtractFlowLinkedResourcesTest(unittest.TestCase):
    def test_extracts_and_maps_types(self):
        d = {
            "steps": [
                {"t": "arn:aws:quicksight:us-east-1:111:space/sp1"},
                {"t": "arn:aws:quicksight:us-east-1:111:action-connector/c1"},
                {"t": "arn:aws:quicksight:us-east-1:111:space/sp1"},  # dup
            ]
        }
        out = server._extract_flow_linked_resources(d)
        types = {(r["resource_type"], r["resource_id"]) for r in out}
        self.assertIn(("space", "sp1"), types)
        self.assertIn(("connector", "c1"), types)  # action-connector -> connector
        self.assertEqual(len(out), 2)  # deduped

    def test_empty_when_no_arns(self):
        self.assertEqual(server._extract_flow_linked_resources({"steps": []}), [])

    def test_none(self):
        self.assertEqual(server._extract_flow_linked_resources(None), [])


if __name__ == "__main__":
    unittest.main()
