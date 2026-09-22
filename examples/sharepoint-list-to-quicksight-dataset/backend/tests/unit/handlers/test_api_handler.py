"""Tests for the Lambda API handler routes."""

import json
from unittest.mock import MagicMock, patch

import pytest
from aws_lambda_powertools.event_handler import Response


def _lambda_context() -> MagicMock:
    ctx = MagicMock()
    ctx.function_name = "QuickSpExportApiHandler"
    ctx.memory_limit_in_mb = 512
    ctx.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:QuickSpExportApiHandler"
    ctx.aws_request_id = "test-request-id"
    return ctx


def _apigw_event(method: str, path: str, path_params: dict | None = None, body: dict | None = None) -> dict:
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"authorization": "Bearer test-token"},
        "requestContext": {
            "http": {"method": method, "path": path},
            "accountId": "123456789012",
            "stage": "$default",
        },
        "pathParameters": path_params or {},
        "body": json.dumps(body) if body else None,
        "isBase64Encoded": False,
    }


@pytest.fixture(autouse=True, scope="module")
def mock_boto3_secrets():
    with patch.dict("os.environ", {"POWERTOOLS_METRICS_NAMESPACE": "quick-sp-export"}):
        with patch("boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.get_secret_value.return_value = {"SecretString": "test-secret"}
            mock_boto.return_value = mock_client
            import api_handler  # noqa: F401

            yield


class TestSearchSitesRoute:
    @patch("api_handler.search_sites_activity")
    @patch("api_handler.token_extractor")
    def test_returns_200(self, mock_extractor: MagicMock, mock_activity: MagicMock) -> None:
        mock_extractor.extract.return_value = MagicMock(access_token="graph-token")
        mock_activity.search.return_value = Response(
            status_code=200, content_type="application/json", body='{"sites": []}'
        )
        import api_handler

        event = _apigw_event("GET", "/sites")
        event["queryStringParameters"] = {"query": "test"}
        result = api_handler.handler(event, _lambda_context())
        assert result["statusCode"] == 200


class TestListListsRoute:
    @patch("api_handler.list_lists_activity")
    @patch("api_handler.token_extractor")
    def test_returns_200(self, mock_extractor: MagicMock, mock_activity: MagicMock) -> None:
        mock_extractor.extract.return_value = MagicMock(access_token="graph-token")
        mock_activity.list_lists.return_value = Response(
            status_code=200, content_type="application/json", body='{"lists": []}'
        )
        import api_handler

        event = _apigw_event("GET", "/sites/site-1/lists", path_params={"site_id": "site-1"})
        result = api_handler.handler(event, _lambda_context())
        assert result["statusCode"] == 200


class TestExportListRoute:
    @patch("api_handler.export_list_activity")
    @patch("api_handler.token_extractor")
    def test_returns_200(self, mock_extractor: MagicMock, mock_activity: MagicMock) -> None:
        mock_extractor.extract.return_value = MagicMock(access_token="graph-token")
        mock_activity.export.return_value = Response(
            status_code=200, content_type="application/json", body='{"s3_bucket": "bucket"}'
        )
        import api_handler

        event = _apigw_event(
            "POST",
            "/sites/site-1/lists/list-1/export",
            path_params={"site_id": "site-1", "list_id": "list-1"},
            body={"confirm": True},
        )
        result = api_handler.handler(event, _lambda_context())
        assert result["statusCode"] == 200
