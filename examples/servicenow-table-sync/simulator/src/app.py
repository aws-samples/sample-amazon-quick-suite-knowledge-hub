"""ServiceNow OAuth and Table API simulator used by the optional test stack."""

from __future__ import annotations

from fastapi import FastAPI, Form, Query
from fastapi.responses import JSONResponse, Response
from mangum import Mangum

from encoded_query import EncodedQuery
from seed import Store, build_store

_ACCESS_TOKEN = "sim-access-token"  # noqa: S105
_DEFAULT_LIMIT = 10_000
_TOKEN_LIFETIME_SECONDS = 1800


def create_app(store: Store | None = None) -> FastAPI:
    """Create the simulator with deterministic data unless a store is supplied."""
    data = store if store is not None else build_store()
    app = FastAPI(title="ServiceNow Simulator", version="1.0.0")

    @app.post("/oauth_token.do")
    def issue_token(grant_type: str = Form(...)) -> JSONResponse:
        """Issue a synthetic OAuth client-credentials token."""
        if grant_type != "client_credentials":
            return JSONResponse(
                {"error": "unsupported_grant_type"},
                status_code=400,
            )
        return JSONResponse(
            {
                "access_token": _ACCESS_TOKEN,
                "token_type": "Bearer",
                "expires_in": _TOKEN_LIFETIME_SECONDS,
            }
        )

    @app.get("/api/now/table/{table_name}")
    def query_table(
        table_name: str,
        sysparm_query: str = Query(default=""),
        sysparm_fields: str = Query(default=""),
        sysparm_limit: int = Query(default=_DEFAULT_LIMIT, gt=0),
        sysparm_offset: int = Query(default=0, ge=0),
    ) -> Response:
        """Return one page of records and the matching record count."""
        records = EncodedQuery.parse(sysparm_query).apply(list(data.tables.get(table_name, [])))
        page = records[sysparm_offset : sysparm_offset + sysparm_limit]
        response = JSONResponse({"result": _select_fields(page, sysparm_fields)})
        response.headers["X-Total-Count"] = str(len(records))
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "tables": ",".join(sorted(data.tables))}

    return app


def _select_fields(records: list[dict[str, str]], fields: str) -> list[dict[str, str]]:
    if not fields:
        return records
    names = [name.strip() for name in fields.split(",") if name.strip()]
    return [{name: record.get(name, "") for name in names} for record in records]


handler = Mangum(create_app())
