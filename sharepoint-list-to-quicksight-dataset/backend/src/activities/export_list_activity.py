"""Activity for exporting a SharePoint list to CSV in S3 with a Quick Sight manifest.

Each export overwrites the previous data so the Quick Sight dataset
always points to the same stable manifest URI.
"""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime

from aws_lambda_powertools.event_handler import Response
from pydantic import BaseModel

from activities.activity_utils import ActivityErrorHandler
from common.dao.s3_dao import S3Dao
from common.models import (
    ContentType,
    ExportListRequest,
    GetColumnsRequest,
    GetListItemsRequest,
    GetListRequest,
    SharePointListItem,
    StatusCode,
    UploadContentRequest,
)
from common.observability import logger, tracer
from common.services.graph_api_client import GraphApiClient


class ExportListResponse(BaseModel):
    s3_bucket: str
    csv_s3_key: str
    manifest_s3_key: str
    manifest_s3_uri: str
    row_count: int
    column_count: int
    columns: list[str]
    exported_at: datetime
    instructions: str


class ExportListActivity:
    """Exports a SharePoint list to CSV in S3 with a Quick Sight manifest."""

    def __init__(self, graph_client: GraphApiClient, s3_dao: S3Dao, error_handler: ActivityErrorHandler) -> None:
        self._graph_client = graph_client
        self._s3_dao = s3_dao
        self._error_handler = error_handler

    @tracer.capture_method
    def export(self, request: ExportListRequest) -> Response:
        """Export a SharePoint list to CSV in S3 with a Quick Sight manifest.

        S3 key structure (stable, overwritten on each export):
            exports/{site_id}/{list_id}/data.csv
            exports/{site_id}/{list_id}/manifest.json
        """
        try:
            logger.info("Exporting list", extra={"site_id": request.site_id, "list_id": request.list_id})

            list_info = self._graph_client.get_list(
                GetListRequest(site_id=request.site_id, list_id=request.list_id, access_token=request.access_token)
            )
            list_name = list_info.display_name or list_info.name or request.list_id

            columns_request = GetColumnsRequest(
                site_id=request.site_id, list_id=request.list_id, access_token=request.access_token
            )
            columns_response = self._graph_client.get_columns(columns_request)
            name_to_display = {col.name: col.display_name or col.name for col in columns_response.columns}
            column_names = list(name_to_display.values())

            items_request = GetListItemsRequest(
                site_id=request.site_id, list_id=request.list_id, access_token=request.access_token
            )
            items_response = self._graph_client.get_list_items(items_request)

            prefix = f"exports/{self._safe_key(request.site_id)}/{self._safe_key(request.list_id)}"
            csv_key = f"{prefix}/data.csv"
            manifest_key = f"{prefix}/manifest.json"

            csv_content = self._build_csv(name_to_display, items_response.items, request.site_id, request.list_id)
            self._s3_dao.upload(UploadContentRequest(key=csv_key, content=csv_content, content_type="text/csv"))

            manifest_content = self._build_manifest(self._s3_dao.bucket_name, csv_key)
            self._s3_dao.upload(
                UploadContentRequest(key=manifest_key, content=manifest_content, content_type="application/json")
            )

            manifest_uri = f"s3://{self._s3_dao.bucket_name}/{manifest_key}"

            logger.info(
                "Export complete",
                extra={"csv_key": csv_key, "manifest_key": manifest_key, "row_count": len(items_response.items)},
            )

            response_data = ExportListResponse(
                s3_bucket=self._s3_dao.bucket_name,
                csv_s3_key=csv_key,
                manifest_s3_key=manifest_key,
                manifest_s3_uri=manifest_uri,
                row_count=len(items_response.items),
                column_count=len(column_names),
                columns=column_names,
                exported_at=datetime.now(UTC),
                instructions=(
                    f"To create a Quick Sight dataset: "
                    f"1) Open Amazon Quick and choose Datasets. "
                    f"2) Choose Create dataset, then Create data source, then select Amazon S3. "
                    f"3) Choose Next. "
                    f"4) Enter a data source name (e.g. '{list_name}'). "
                    f"5) Enter this S3 URI to the manifest file: {manifest_uri} "
                    f"6) Choose Connect. "
                    f"7) Choose Edit/Preview data. Make any necessary configurations, "
                    f"preview the data, and/or add calculated fields. "
                    f"8) Choose Save & publish."
                ),
            )
            return Response(
                status_code=StatusCode.OK.value,
                content_type=ContentType.APPLICATION_JSON.value,
                body=response_data.model_dump_json(),
            )

        except Exception as e:
            return self._error_handler.handle(e)

    @staticmethod
    def _build_csv(name_to_display: dict[str, str], items: list[SharePointListItem], site_id: str, list_id: str) -> str:
        display_names = list(name_to_display.values())
        fieldnames = ["site_id", "list_id", *display_names]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            row = {name_to_display.get(k, k): ExportListActivity._flatten_value(v) for k, v in item.fields.items()}
            row["site_id"] = site_id
            row["list_id"] = list_id
            writer.writerow(row)
        return buffer.getvalue()

    @staticmethod
    def _build_manifest(bucket_name: str, csv_key: str) -> str:
        manifest = {
            "fileLocations": [{"URIs": [f"s3://{bucket_name}/{csv_key}"]}],
            "globalUploadSettings": {
                "format": "CSV",
                "delimiter": ",",
                "containsHeader": "true",
            },
        }
        return json.dumps(manifest, indent=2)

    @staticmethod
    def _safe_key(value: str) -> str:
        """Hash a value to a safe, fixed-length S3 key segment."""
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    @staticmethod
    def _flatten_value(value: object) -> str:
        """Flatten complex SharePoint field values to strings for CSV.

        Person/Lookup fields come as dicts with LookupValue,
        multi-value fields come as lists.
        """
        match value:
            case None:
                return ""
            case dict():
                return str(value.get("LookupValue", value.get("Email", json.dumps(value))))
            case list():
                return "; ".join(ExportListActivity._flatten_value(v) for v in value)
            case _:
                return str(value)
