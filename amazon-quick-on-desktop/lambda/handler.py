"""API Gateway Lambda proxy that strips offline_access from Cognito OAuth requests."""

import base64
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from enum import Enum, StrEnum


class Route(StrEnum):
    AUTHORIZE = "/oauth2/authorize"
    TOKEN = "/oauth2/token"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"


class StatusCode(int, Enum):
    REDIRECT = 302
    NOT_FOUND = 404
    INTERNAL_ERROR = 500
    BAD_GATEWAY = 502


class ContentType(StrEnum):
    JSON = "application/json"
    FORM = "application/x-www-form-urlencoded"


@dataclass
class ProxyResponse:
    statusCode: int
    body: str = ""
    headers: dict | None = None

    def to_dict(self) -> dict:
        result = asdict(self)
        if self.headers is None:
            del result["headers"]
        return result


class AuthorizeHandler:
    def __init__(self, cognito_domain: str) -> None:
        self._cognito_domain = cognito_domain

    def handle(self, event: dict) -> ProxyResponse:
        params = event.get("queryStringParameters") or {}
        if "scope" in params:
            params["scope"] = self._strip_offline_access(params["scope"])
        qs = urllib.parse.urlencode(params)
        return ProxyResponse(
            statusCode=StatusCode.REDIRECT,
            headers={"Location": f"{self._cognito_domain}{Route.AUTHORIZE.value}?{qs}"},
        )

    def _strip_offline_access(self, scope_str: str) -> str:
        return " ".join(s for s in scope_str.split() if s != "offline_access")


class TokenHandler:
    def __init__(self, cognito_domain: str) -> None:
        self._cognito_domain = cognito_domain

    def handle(self, event: dict) -> ProxyResponse:
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode()

        headers = {"Content-Type": ContentType.FORM.value}
        req_headers = event.get("headers") or {}
        if "authorization" in req_headers:
            headers["Authorization"] = req_headers["authorization"]

        req = urllib.request.Request(
            f"{self._cognito_domain}{Route.TOKEN.value}",
            data=body.encode(),
            headers=headers,
            method=HttpMethod.POST.value,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return ProxyResponse(
                    statusCode=resp.status,
                    headers={"Content-Type": ContentType.JSON.value},
                    body=resp.read().decode(),
                )
        except urllib.error.HTTPError as e:
            return ProxyResponse(
                statusCode=e.code,
                headers={"Content-Type": ContentType.JSON.value},
                body=e.read().decode(),
            )
        except urllib.error.URLError as e:
            return ProxyResponse(
                statusCode=StatusCode.BAD_GATEWAY,
                headers={"Content-Type": ContentType.JSON.value},
                body=f'{{"error": "upstream_unreachable", "reason": "{e.reason}"}}',
            )


COGNITO_DOMAIN = os.environ["COGNITO_DOMAIN"]
authorize_handler = AuthorizeHandler(COGNITO_DOMAIN)
token_handler = TokenHandler(COGNITO_DOMAIN)


def handler(event: dict, _context: object) -> dict:
    path = event.get("path", "")
    method = event.get("httpMethod", "")
    print(f"REQUEST: {method} {path}")

    try:
        match (path, method):
            case (Route.AUTHORIZE, HttpMethod.GET):
                response = authorize_handler.handle(event)
            case (Route.TOKEN, HttpMethod.POST):
                response = token_handler.handle(event)
            case _:
                response = ProxyResponse(
                    statusCode=StatusCode.NOT_FOUND, body="Not found"
                )
    except Exception as e:
        print(f"ERROR: {e}")
        response = ProxyResponse(
            statusCode=StatusCode.INTERNAL_ERROR,
            headers={"Content-Type": ContentType.JSON.value},
            body='{"error": "internal_error"}',
        )

    print(f"RESPONSE: {response.statusCode} {response.headers}")
    return response.to_dict()
