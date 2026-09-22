"""Environment variable access for configuration."""

import os

SERVICE = os.getenv("SERVICE")

EXPORT_BUCKET = os.getenv("EXPORT_BUCKET")

GRAPH_API_BASE_URL = os.getenv("GRAPH_API_BASE_URL", "https://graph.microsoft.com/v1.0")
GRAPH_API_TIMEOUT_SECONDS = int(os.getenv("GRAPH_API_TIMEOUT_SECONDS", "30"))
MAX_LIST_ITEMS_PER_PAGE = int(os.getenv("MAX_LIST_ITEMS_PER_PAGE", "5000"))

ENTRA_TOKEN_URL = os.getenv("ENTRA_TOKEN_URL")
ENTRA_CLIENT_ID = os.getenv("ENTRA_CLIENT_ID")
ENTRA_CLIENT_SECRET_ARN = os.getenv("ENTRA_CLIENT_SECRET_ARN")
