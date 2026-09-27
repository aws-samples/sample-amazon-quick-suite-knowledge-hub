"""
Shared authentication helper for all Supply Chain components.
All components use the SAME Cognito User Pool.
"""

import os
from functools import lru_cache

import requests
from jose import JWTError, jwt

# ─── Configuration ───────────────────────────────────────────
REGION = os.getenv("AWS_REGION", "us-east-1")
USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN")
RESOURCE_SERVER_ID = os.getenv("COGNITO_RESOURCE_SERVER_ID", "supply-chain-api")

JWKS_URL = (
    f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json"
)
TOKEN_URL = f"https://{COGNITO_DOMAIN}/oauth2/token"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"

# Dev mode flag — bypasses auth for local testing
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"


@lru_cache(maxsize=1)
def get_jwks():
    """Fetch and cache JWKS keys from Cognito."""
    response = requests.get(JWKS_URL, timeout=10)
    response.raise_for_status()
    return response.json()["keys"]


def validate_token(token: str, required_scopes: list[str] = None) -> dict:
    """
    Validate a Cognito JWT token.

    Args:
        token: The JWT access token
        required_scopes: List of required scopes (e.g., ["supply-chain-api/demand.read"])

    Returns:
        Decoded token claims

    Raises:
        ValueError: If token is invalid or missing required scopes
    """
    if DEV_MODE:
        return {
            "sub": "dev-user",
            "scope": "supply-chain-api/orchestrator.full",
            "dev_mode": True,
        }

    try:
        # Decode header to get key ID
        header = jwt.get_unverified_header(token)
        kid = header["kid"]

        # Find matching key
        keys = get_jwks()
        key = next((k for k in keys if k["kid"] == kid), None)
        if not key:
            raise ValueError("Token key ID not found in JWKS")

        # Verify and decode
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=None,  # Cognito access tokens don't have aud
            issuer=ISSUER,
        )

        # Check token_use
        if claims.get("token_use") != "access":
            raise ValueError("Token is not an access token")

        # Check scopes
        if required_scopes:
            token_scopes = claims.get("scope", "").split()
            for scope in required_scopes:
                if scope not in token_scopes:
                    raise ValueError(f"Missing required scope: {scope}")

        return claims

    except JWTError as e:
        raise ValueError(f"Token validation failed: {e}") from e


def get_m2m_token(client_id: str, client_secret: str, scopes: list[str]) -> str:
    """
    Get a machine-to-machine token using client_credentials grant.
    Used by the orchestrator agent to call MCP servers.

    Args:
        client_id: Cognito app client ID
        client_secret: Cognito app client secret
        scopes: List of scopes to request

    Returns:
        Access token string
    """
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": " ".join(scopes),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]
