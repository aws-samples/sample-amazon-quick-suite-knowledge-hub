"""Tests for the ServiceNow record value object."""

from common.services.service_now_record import ServiceNowRecord


def test_from_serialized_coerces_values_to_strings() -> None:
    record = ServiceNowRecord.from_serialized({"sys_id": "abc", "priority": 1, "empty": None})
    assert record.fields == {"sys_id": "abc", "priority": "1", "empty": ""}


def test_sys_id_and_updated_on_accessors() -> None:
    record = ServiceNowRecord.from_serialized(
        {"sys_id": "abc", "sys_updated_on": "2026-09-01 10:00:00"}
    )
    assert record.sys_id == "abc"
    assert record.updated_on == "2026-09-01 10:00:00"


def test_missing_values_are_none() -> None:
    record = ServiceNowRecord.from_serialized({"sys_id": "", "sys_updated_on": ""})
    assert record.sys_id is None
    assert record.updated_on is None
