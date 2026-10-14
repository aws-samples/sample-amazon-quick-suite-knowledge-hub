"""Parses the subset of ServiceNow encoded-query syntax the ingestion pipeline emits.

The extractor builds queries like
`state=In Progress^sys_updated_on>=2026-08-31 00:00:00^ORDERBYsys_updated_on`.
This parser turns such a string into predicates plus an optional order field, then filters
and sorts records to honour incremental extraction (`>=` on `sys_updated_on`), equality
filters, and `ORDERBY`. Unsupported operators are ignored rather than erroring.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

_ORDER_BY = "ORDERBY"
_ORDER_BY_DESC = "ORDERBYDESC"


@dataclass(frozen=True)
class Predicate:
    """One `field<op>value` clause."""

    field: str
    operator: str
    value: str

    def matches(self, record: dict[str, str]) -> bool:
        actual = record.get(self.field, "")
        comparators: dict[str, Callable[[str, str], bool]] = {
            "=": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
            ">=": lambda a, b: a >= b,
            "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b,
            "<": lambda a, b: a < b,
        }
        comparator = comparators.get(self.operator)
        return comparator(actual, self.value) if comparator is not None else True


@dataclass(frozen=True)
class EncodedQuery:
    """A parsed encoded query: its predicates and an optional (field, descending) order."""

    predicates: tuple[Predicate, ...]
    order_field: str | None
    order_desc: bool

    def apply(self, records: list[dict[str, str]]) -> list[dict[str, str]]:
        """Filter records by every predicate, then sort by the order field if set."""
        filtered = [r for r in records if all(p.matches(r) for p in self.predicates)]
        if self.order_field is not None:
            filtered.sort(key=lambda r: r.get(self.order_field or "", ""), reverse=self.order_desc)
        return filtered

    @classmethod
    def parse(cls, encoded: str) -> EncodedQuery:
        """Parse a `^`-delimited encoded query into predicates and order."""
        predicates: list[Predicate] = []
        order_field: str | None = None
        order_desc = False
        for clause in (c for c in encoded.split("^") if c):
            if clause.startswith(_ORDER_BY_DESC):
                order_field, order_desc = clause[len(_ORDER_BY_DESC) :], True
            elif clause.startswith(_ORDER_BY):
                order_field, order_desc = clause[len(_ORDER_BY) :], False
            else:
                predicate = _parse_predicate(clause)
                if predicate is not None:
                    predicates.append(predicate)
        return cls(predicates=tuple(predicates), order_field=order_field, order_desc=order_desc)


def _parse_predicate(clause: str) -> Predicate | None:
    """Parse one clause into a predicate; longest operators first so `>=` beats `>`."""
    for operator in (">=", "<=", "!=", "=", ">", "<"):
        marker = clause.find(operator)
        if marker > 0:
            return Predicate(
                field=clause[:marker], operator=operator, value=clause[marker + len(operator) :]
            )
    return None
