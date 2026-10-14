"""Deterministic generator of internally-consistent ServiceNow seed data.

Records reference each other by real `sys_id` the way a live instance does: an incident's
`assigned_to`, `cmdb_ci`, and `assignment_group` all point at real records. `sys_updated_on`
values are monotonic to exercise the pipeline's incremental extraction. Reference fields are
held as display strings, matching what the Table API returns with `sysparm_display_value=true`.

`build_store()` returns the fully populated in-memory store. The same call always yields the
same data, so tests and demo runs are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# Fixed epoch so sys_updated_on values are stable and strictly increasing across the dataset.
_EPOCH = datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC)


def _sys_id(prefix: str, index: int) -> str:
    """A 32-char sys_id, deterministic from a prefix and index."""
    return f"{prefix}{index:0>{32 - len(prefix)}}"


def _timestamp(offset_minutes: int) -> str:
    """A `YYYY-MM-DD HH:MM:SS` UTC timestamp offset from the epoch (ServiceNow format)."""
    return (_EPOCH + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class SeedCounter:
    """Hands out a strictly-increasing minute offset so every record's sys_updated_on advances."""

    minute: int = 0

    def next_offset(self) -> int:
        self.minute += 7
        return self.minute


@dataclass
class Store:
    """The in-memory table store: table name -> list of display-valued records."""

    tables: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def add(self, table: str, record: dict[str, str]) -> None:
        self.tables.setdefault(table, []).append(record)


def build_store() -> Store:
    """Build the fully-populated, internally-consistent seed store."""
    store = Store()
    counter = SeedCounter()
    users = _seed_users(store, counter)
    groups = _seed_groups(store, counter)
    servers = _seed_servers(store, counter)
    apps = _seed_applications(store, counter, servers)
    _seed_incidents(store, counter, users, groups, servers)
    _seed_change_requests(store, counter, users, groups, apps)
    _seed_problems(store, counter, users, groups)
    return store


def _seed_users(store: Store, counter: SeedCounter) -> list[dict[str, str]]:
    people = [
        ("Ava Chen", "ava.chen", "IT Operations", "San Francisco"),
        ("Marcus Reid", "marcus.reid", "Network Engineering", "Austin"),
        ("Priya Nair", "priya.nair", "Service Desk", "London"),
        ("Diego Santos", "diego.santos", "Database Administration", "São Paulo"),
    ]
    records: list[dict[str, str]] = []
    for index, (name, user_id, department, location) in enumerate(people):
        record = {
            "sys_id": _sys_id("user", index),
            "name": name,
            "user_name": user_id,
            "email": f"{user_id}@example.com",
            "department": department,
            "location": location,
            "sys_updated_on": _timestamp(counter.next_offset()),
        }
        store.add("sys_user", record)
        records.append(record)
    return records


def _seed_groups(store: Store, counter: SeedCounter) -> list[dict[str, str]]:
    names = ["Service Desk", "Network Engineering", "Database Administration", "Cloud Platform"]
    records: list[dict[str, str]] = []
    for index, name in enumerate(names):
        record = {
            "sys_id": _sys_id("grp", index),
            "name": name,
            "description": f"{name} assignment group",
            "sys_updated_on": _timestamp(counter.next_offset()),
        }
        store.add("sys_user_group", record)
        records.append(record)
    return records


def _seed_servers(store: Store, counter: SeedCounter) -> list[dict[str, str]]:
    servers = [
        ("web-prod-01", "Linux Red Hat", "Production", "us-east-1"),
        ("db-prod-01", "Linux Red Hat", "Production", "us-east-1"),
        ("app-prod-02", "Windows Server 2022", "Production", "eu-west-1"),
    ]
    records: list[dict[str, str]] = []
    for index, (name, os_name, environment, region) in enumerate(servers):
        record = {
            "sys_id": _sys_id("srv", index),
            "name": name,
            "os": os_name,
            "used_for": environment,
            "location": region,
            "operational_status": "Operational",
            "sys_updated_on": _timestamp(counter.next_offset()),
        }
        store.add("cmdb_ci_server", record)
        records.append(record)
    return records


def _seed_applications(
    store: Store, counter: SeedCounter, servers: list[dict[str, str]]
) -> list[dict[str, str]]:
    apps = [
        ("Customer Portal", servers[0]),
        ("Billing Service", servers[1]),
        ("Identity Provider", servers[2]),
    ]
    records: list[dict[str, str]] = []
    for index, (name, host) in enumerate(apps):
        record = {
            "sys_id": _sys_id("app", index),
            "name": name,
            "version": f"{index + 2}.4.1",
            "used_for": "Production",
            "host": host["name"],
            "operational_status": "Operational",
            "sys_updated_on": _timestamp(counter.next_offset()),
        }
        store.add("cmdb_ci_appl", record)
        records.append(record)
    return records


def _seed_incidents(
    store: Store,
    counter: SeedCounter,
    users: list[dict[str, str]],
    groups: list[dict[str, str]],
    servers: list[dict[str, str]],
) -> None:
    incidents = [
        ("VPN gateway dropping connections", "1", "Critical", 0, 1, 1),
        ("Customer Portal slow to load", "2", "High", 2, 0, 0),
        ("Password reset emails delayed", "3", "Moderate", 2, 0, 2),
        ("Disk space warning on db-prod-01", "2", "High", 3, 2, 1),
    ]
    for index, (summary, priority, impact, user_i, group_i, server_i) in enumerate(incidents):
        offset = counter.next_offset()
        store.add(
            "incident",
            {
                "sys_id": _sys_id("inc", index),
                "number": f"INC00{10001 + index}",
                "short_description": summary,
                "description": f"{summary}. Reported by monitoring and confirmed by the on-call engineer.",  # noqa: E501
                "priority": priority,
                "impact": impact,
                "state": "In Progress",
                "assigned_to": users[user_i]["name"],
                "assignment_group": groups[group_i]["name"],
                "cmdb_ci": servers[server_i]["name"],
                "opened_by": users[user_i]["name"],
                "opened_at": _timestamp(offset - 3),
                "sys_updated_on": _timestamp(offset),
            },
        )


def _seed_change_requests(
    store: Store,
    counter: SeedCounter,
    users: list[dict[str, str]],
    groups: list[dict[str, str]],
    apps: list[dict[str, str]],
) -> None:
    changes = [
        ("Upgrade Customer Portal to 3.4.1", "Normal", "Scheduled", 0, 3, 0),
        ("Patch Billing Service dependencies", "Standard", "Implement", 3, 2, 1),
    ]
    for index, (summary, change_type, state, user_i, group_i, app_i) in enumerate(changes):
        offset = counter.next_offset()
        store.add(
            "change_request",
            {
                "sys_id": _sys_id("chg", index),
                "number": f"CHG00{20001 + index}",
                "short_description": summary,
                "description": f"Planned change: {summary}.",
                "type": change_type,
                "state": state,
                "risk": "Moderate",
                "assigned_to": users[user_i]["name"],
                "assignment_group": groups[group_i]["name"],
                "cmdb_ci": apps[app_i]["name"],
                "opened_by": users[user_i]["name"],
                "sys_updated_on": _timestamp(offset),
            },
        )


def _seed_problems(
    store: Store,
    counter: SeedCounter,
    users: list[dict[str, str]],
    groups: list[dict[str, str]],
) -> None:
    problems = [
        ("Recurring VPN gateway instability", "1", 1, 1),
        ("Intermittent email delivery latency", "2", 2, 0),
    ]
    for index, (summary, priority, user_i, group_i) in enumerate(problems):
        offset = counter.next_offset()
        store.add(
            "problem",
            {
                "sys_id": _sys_id("prb", index),
                "number": f"PRB00{30001 + index}",
                "short_description": summary,
                "description": f"Root-cause investigation for: {summary}.",
                "priority": priority,
                "state": "Root Cause Analysis",
                "assigned_to": users[user_i]["name"],
                "assignment_group": groups[group_i]["name"],
                "sys_updated_on": _timestamp(offset),
            },
        )
