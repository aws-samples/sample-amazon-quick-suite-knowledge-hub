"""
Utility Functions for HR MCP Server Workshop

Sets up complete Cognito authentication for AgentCore MCP Server + Q MCP Client:

1. User Pool           — container for users who can access the MCP server
2. Cognito Domain      — required for the OAuth2 token endpoint URL
3. Resource Server     — defines a scope (required for client_credentials grant)
4. App Client          — single client with secret, supports both:
                         - client_credentials flow (used by Q MCP Client)
                         - user_password flow (used for direct testing)
5. Test User           — for testing auth directly via user_password flow
"""

import base64
import hashlib
import hmac
import time

import boto3


def setup_cognito_user_pool() -> dict[str, str]:
    """
    Create and configure a Cognito User Pool for MCP server authentication.

    Why each piece is needed:
    - Domain:          Q MCP Client calls /oauth2/token to get a JWT — this endpoint
                       only works when a Cognito domain is configured.
    - Resource Server: Defines the scope 'hr-mcp/access'. Without a scope,
                       client_credentials grant returns 'invalid_scope'.
    - Client Secret:   Q MCP Client requires client_id + client_secret to authenticate.
    - client_credentials flow: How Q MCP Client gets a token (no user interaction).
    - user_password flow: How we test auth directly with username/password.

    Returns dict with all values needed for AgentCore deployment and Q MCP Client:
        pool_id, client_id, client_secret, token_url,
        discovery_url, bearer_token, refresh_token, username, password, region
    """
    cognito_client = boto3.client("cognito-idp")
    region = boto3.Session().region_name
    ts = int(time.time())

    # ── 1. Create User Pool ──────────────────────────────────────────────
    pool_name = f"mcp-hr-server-pool-{ts}"
    print(f"Creating Cognito User Pool: {pool_name}")

    pool_response = cognito_client.create_user_pool(
        PoolName=pool_name,
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 8,
                "RequireUppercase": False,
                "RequireLowercase": False,
                "RequireNumbers": False,
                "RequireSymbols": False,
            }
        },
        AutoVerifiedAttributes=["email"],
        Schema=[
            {
                "Name": "email",
                "AttributeDataType": "String",
                "Required": True,
                "Mutable": True,
            }
        ],
    )
    pool_id = pool_response["UserPool"]["Id"]
    print(f"✓ User Pool created: {pool_id}")

    # ── 2. Create Cognito Domain ─────────────────────────────────────────
    # Without a domain, the /oauth2/token endpoint does not exist.
    # The domain creates: https://{prefix}.auth.{region}.amazoncognito.com
    domain_prefix = f"hr-mcp-{ts}"
    cognito_client.create_user_pool_domain(UserPoolId=pool_id, Domain=domain_prefix)
    token_url = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/token"
    print(f"✓ Cognito domain created: {domain_prefix}")
    print(f"  Token URL: {token_url}")

    # ── 3. Create Resource Server with Scope ─────────────────────────────
    # client_credentials grant requires at least one scope.
    # We create a resource server 'hr-mcp' with scope 'access'.
    # The full scope name becomes 'hr-mcp/access'.
    cognito_client.create_resource_server(
        UserPoolId=pool_id,
        Identifier="hr-mcp",
        Name="HR MCP Server",
        Scopes=[{"ScopeName": "access", "ScopeDescription": "Access HR MCP Server"}],
    )
    print("✓ Resource server created with scope: hr-mcp/access")

    # ── 4. Create App Client ─────────────────────────────────────────────
    # Single client that supports both flows:
    # - client_credentials: Q MCP Client sends client_id + client_secret to get a token
    # - user_password: We can test auth directly with username + password
    # GenerateSecret=True: Q MCP Client requires a client secret
    client_response = cognito_client.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName="mcp-hr-server-client",
        GenerateSecret=True,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=["hr-mcp/access"],
        AllowedOAuthFlowsUserPoolClient=True,
    )
    client_id = client_response["UserPoolClient"]["ClientId"]
    client_secret = client_response["UserPoolClient"]["ClientSecret"]
    print(f"✓ App client created: {client_id}")
    print("  Flows: client_credentials + user_password")
    print(f"  Secret: {client_secret[:10]}...")

    # ── 5. Create Test User ──────────────────────────────────────────────
    username = "mcpuser"
    password = (
        base64.b64encode(hashlib.sha256(str(ts).encode()).digest()).decode()[:16]
        + "!A1"
    )

    try:
        cognito_client.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            TemporaryPassword=password,
            MessageAction="SUPPRESS",
        )
        cognito_client.admin_set_user_password(
            UserPoolId=pool_id, Username=username, Password=password, Permanent=True
        )
        print(f"✓ Test user created: {username}")
    except cognito_client.exceptions.UsernameExistsException:
        print(f"ℹ️  User {username} already exists")

    # ── 6. Authenticate Test User ────────────────────────────────────────
    # When a client has a secret, Cognito requires a SECRET_HASH
    # SECRET_HASH = Base64(HMAC_SHA256(client_secret, username + client_id))
    def _secret_hash(uname, cid, csecret):
        msg = uname + cid
        dig = hmac.new(
            csecret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.b64encode(dig).decode()

    auth_response = cognito_client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": username,
            "PASSWORD": password,
            "SECRET_HASH": _secret_hash(username, client_id, client_secret),
        },
    )
    access_token = auth_response["AuthenticationResult"]["AccessToken"]
    refresh_token = auth_response["AuthenticationResult"]["RefreshToken"]
    print("✓ Authentication tokens obtained")

    # ── Build URLs ───────────────────────────────────────────────────────
    # Discovery URL: AgentCore uses this to validate JWT tokens
    discovery_url = (
        f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json"
    )

    config = {
        "pool_id": pool_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_url": token_url,
        "discovery_url": discovery_url,
        "bearer_token": access_token,
        "refresh_token": refresh_token,
        "username": username,
        "password": password,
        "region": region,
    }

    print("\n✅ Cognito setup completed successfully!")
    return config
