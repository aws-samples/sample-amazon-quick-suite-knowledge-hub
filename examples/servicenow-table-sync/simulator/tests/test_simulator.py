"""Behavior tests for the local ServiceNow API simulator."""

from fastapi.testclient import TestClient

from app import create_app
from encoded_query import EncodedQuery
from seed import build_store


def _client() -> TestClient:
    return TestClient(create_app(build_store()))


def test_health_lists_seeded_tables():
    response = _client().get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "incident" in response.json()["tables"]


def test_oauth_endpoint_issues_client_credentials_token():
    response = _client().post("/oauth_token.do", data={"grant_type": "client_credentials"})

    assert response.status_code == 200
    assert response.json()["token_type"] == "Bearer"
    assert response.json()["access_token"]


def test_oauth_endpoint_rejects_other_grants():
    response = _client().post("/oauth_token.do", data={"grant_type": "password"})

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"


def test_table_api_paginates_and_reports_total_count():
    response = _client().get(
        "/api/now/table/incident",
        params={"sysparm_limit": 2, "sysparm_offset": 1},
    )

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "4"
    assert len(response.json()["result"]) == 2


def test_table_api_selects_requested_fields():
    response = _client().get(
        "/api/now/table/incident",
        params={"sysparm_limit": 1, "sysparm_fields": "sys_id,number"},
    )

    assert set(response.json()["result"][0]) == {"sys_id", "number"}


def test_table_api_applies_incremental_query():
    response = _client().get(
        "/api/now/table/incident",
        params={"sysparm_query": "sys_updated_on>=2026-08-01 10:00:00"},
    )

    records = response.json()["result"]
    assert records
    assert all(record["sys_updated_on"] >= "2026-08-01 10:00:00" for record in records)


def test_encoded_query_orders_descending():
    records = [
        {"sys_id": "a", "sys_updated_on": "2026-08-01 00:00:00"},
        {"sys_id": "b", "sys_updated_on": "2026-08-02 00:00:00"},
    ]

    ordered = EncodedQuery.parse("ORDERBYDESCsys_updated_on").apply(records)

    assert [record["sys_id"] for record in ordered] == ["b", "a"]
