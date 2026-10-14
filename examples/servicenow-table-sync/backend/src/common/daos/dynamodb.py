"""Generic DynamoDB DAO base shared by the table-specific DAOs.

Provides key get/put/delete and a bounded scan over typed pydantic models. `table` is typed with
the `mypy_boto3_dynamodb` `Table` stub under `TYPE_CHECKING`, so it stays a type-check-only
dependency the runtime never imports. The concrete DAOs inject the boto3 `Table`; this base
constructs nothing.
"""

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from common.errors import NotFoundError

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table

T = TypeVar("T", bound=BaseModel)


class DynamoDbKey(BaseModel):
    """A DynamoDB item key (partition key and optional sort key)."""

    key: dict[str, str]


class DynamoDbDao(Generic[T]):
    """Key get/put/delete and a bounded scan over a single table's typed items."""

    def __init__(self, table: "Table", model_class: type[T]) -> None:
        self.table = table
        self.model_class = model_class

    def get(self, key: DynamoDbKey) -> T:
        """Retrieve an item by key, raising `NotFoundError` if absent."""
        response = self.table.get_item(Key=key.key)
        if "Item" not in response:
            raise NotFoundError(self.model_class.__name__, str(key.key))
        return self.model_class.model_validate(response["Item"])

    def get_or_none(self, key: DynamoDbKey) -> T | None:
        """Retrieve an item by key, returning None if it does not exist."""
        response = self.table.get_item(Key=key.key)
        if "Item" not in response:
            return None
        return self.model_class.model_validate(response["Item"])

    def put(self, item: T) -> None:
        """Persist (create or overwrite) an item."""
        self.table.put_item(Item=item.model_dump(mode="json", by_alias=True, exclude_none=True))

    def delete(self, key: DynamoDbKey) -> None:
        """Remove an item by key. No-op if it does not exist."""
        self.table.delete_item(Key=key.key)

    def scan_all(self) -> list[T]:
        """Return every item in the table, following pagination.

        The registry and checkpoint tables hold at most a few dozen rows (one per configured
        ServiceNow table), so a full scan is the cheapest correct read; no key or index would
        serve a useful access pattern here.
        """
        items: list[T] = []
        scan_kwargs: dict[str, Any] = {}
        while True:
            response = self.table.scan(**scan_kwargs)
            items.extend(
                self.model_class.model_validate(item) for item in response.get("Items", [])
            )
            last_key = response.get("LastEvaluatedKey")
            if last_key is None:
                return items
            scan_kwargs["ExclusiveStartKey"] = last_key
