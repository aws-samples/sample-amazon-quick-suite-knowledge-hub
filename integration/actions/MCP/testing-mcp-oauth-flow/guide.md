# Testing Remote MCP Server OAuth with a Jupyter Notebook

A hands-on walkthrough for verifying your remote MCP server's 3LO authorization flow before integrating with Amazon Quick or any other MCP client.

## Why This Matters

The [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization) layers several RFCs on top of each other — [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html) for Protected Resource Metadata, [RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html) for Authorization Server discovery, [RFC 7591](https://www.rfc-editor.org/rfc/rfc7591.html) for Dynamic Client Registration, and [OAuth 2.1](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-12) with [PKCE (RFC 7636)](https://www.rfc-editor.org/rfc/rfc7636.html) for the actual token exchange. Every MCP server implements a slightly different subset of these standards. Some advertise metadata in `WWW-Authenticate` headers, some don't. Some support Dynamic Client Registration, others require manual credentials. Some only expose [OpenID Connect discovery](https://openid.net/specs/openid-connect-discovery-1_0.html) instead of RFC 8414.

Rather than discovering these differences in production, the `mcp_test-3LO.ipynb` notebook lets you walk through each step interactively, see exactly what your server returns, and identify issues before they become integration blockers.

## What You Need

- Python 3.9+ with a virtual environment
- The `requests` library (`pip install requests`)
- A remote MCP server URL that requires user authorization (3LO)
- Client credentials if the server doesn't support Dynamic Client Registration

## The Flow at a Glance

The notebook follows the [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization) step by step:

1. **Probe the MCP endpoint** — Send a HEAD request, look for `WWW-Authenticate` with a `resource_metadata` URL ([MCP spec §2.1](https://modelcontextprotocol.io/specification/draft/basic/authorization#2-1-discovering-the-protected-resource-metadata))
2. **Fetch Protected Resource Metadata** — Discover which authorization server to use, what scopes are available, and the resource identifier ([RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html))
3. **Fetch Authorization Server Metadata** — Find the `authorization_endpoint`, `token_endpoint`, and `registration_endpoint` ([RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html))
4. **Register a client** — Attempt Dynamic Client Registration, fall back to manual credentials ([RFC 7591](https://www.rfc-editor.org/rfc/rfc7591.html))
5. **Authorize** — Open the browser, capture the callback with a local HTTP server, get an authorization code ([OAuth 2.1 §4.1](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-12#section-4.1))
6. **Exchange for a token** — Trade the code + [PKCE](https://www.rfc-editor.org/rfc/rfc7636.html) verifier for an access token
7. **Test the connection** — Send an MCP `initialize` [JSON-RPC](https://modelcontextprotocol.io/specification/draft/basic/lifecycle) request and list available tools

## Step-by-Step Walkthrough

### Set Your MCP Endpoint

The first cell contains the MCP server URL.

```python
mcpURL = "https://your-mcp-server.example.com/mcp"
```


### Configure the Redirect URI

The notebook spins up a local HTTP server to capture the OAuth callback. The default is:

```python
REDIRECT_URI = "http://localhost:8888/callback"
```

If your identity provider requires HTTPS redirects, see the `enable_ssl.md` file in the repo for setup instructions using `mkcert`.

### Discover Protected Resource Metadata ([RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html))

This is where things get interesting. The notebook sends a HEAD request to your MCP endpoint and looks for the `WWW-Authenticate` header:

```
Status: 401
WWW-Authenticate: Bearer resource_metadata="https://..."
```

If the header is missing or doesn't include `resource_metadata`, the notebook falls back to the path-suffixed well-known URL per [RFC 9728 Section 3](https://www.rfc-editor.org/rfc/rfc9728.html#section-3):

```
https://{host}/.well-known/oauth-protected-resource{path}
```

The path-suffixed pattern is critical. For an endpoint like `https://example.com/api/v1/mcp`, the metadata URL is `https://example.com/.well-known/oauth-protected-resource/api/v1/mcp` — not just the base domain. Servers like Cisco Webex rely on this path to identify which protected resource the request is for.

**What can go wrong here:**
- Server returns no `WWW-Authenticate` header — the fallback handles this
- Advertised URL 404s but the path-suffixed URL works — the fallback handles this
- Server returns 200 with HTML instead of JSON — the Content-Type validation catches this and gives a clear error
- Server doesn't implement [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html) at all — the notebook skips PRM gracefully, defaults `resource` to the MCP endpoint URL itself per [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html), and proceeds directly to AS metadata discovery

### Fetch Authorization Server Metadata ([RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html))

If PRM succeeded, the notebook uses the `authorization_servers` list from the PRM document. If PRM failed, the notebook falls back to the MCP endpoint's own origin as the AS base URL. This two-source approach means servers that skip RFC 9728 entirely but expose RFC 8414 AS metadata at their origin still work out of the box.

Per [RFC 8414 Section 3](https://www.rfc-editor.org/rfc/rfc8414.html#section-3), the well-known URL is formed by inserting `/.well-known/oauth-authorization-server` **between** the host and path components of the issuer. If the issuer is `https://example.com/oidc`, the metadata URL is:

```
https://example.com/.well-known/oauth-authorization-server/oidc
```

The notebook also falls back to [OpenID Connect discovery](https://openid.net/specs/openid-connect-discovery-1_0.html) (`/.well-known/openid-configuration`) for servers that only expose OIDC.

> **Note:** PRM and AS discovery are combined into a single notebook cell (Phase 1 / Phase 2) so that PRM failure flows seamlessly into AS discovery without manual intervention.

### Client Registration ([RFC 7591](https://www.rfc-editor.org/rfc/rfc7591.html))

If the authorization server exposes a `registration_endpoint`, the notebook attempts [Dynamic Client Registration](https://www.rfc-editor.org/rfc/rfc7591.html) automatically. If DCR isn't available or fails, it prompts for a `client_id` and `client_secret` that you've registered manually through the provider's developer console.

### Authorization Code Flow with [PKCE (RFC 7636)](https://www.rfc-editor.org/rfc/rfc7636.html)

The notebook generates a [PKCE](https://www.rfc-editor.org/rfc/rfc7636.html) challenge, builds the authorization URL, opens your browser, and starts a local callback server. After you authenticate and consent, the IDP redirects back to `localhost:8888/callback` with the authorization code, which the notebook captures and exchanges for an access token. The `resource` parameter ([RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html)) binds the token to the specific MCP server.

### Test the Connection

With a valid token, the notebook sends an MCP [`initialize`](https://modelcontextprotocol.io/specification/draft/basic/lifecycle#initialization) JSON-RPC request and then calls [`tools/list`](https://modelcontextprotocol.io/specification/draft/basic/servers/tools#listing-tools) to enumerate all available tools with their descriptions and input schemas.

## Common Issues and What They Mean

| Symptom | Likely Cause | What to Check |
|---|---|---|
| HEAD returns 404, no `WWW-Authenticate` | Server may not implement RFC 9728 discovery | Try the path-suffixed well-known URL manually; check if this is a 2LO endpoint |
| PRM returns 200 but Content-Type is `text/html` | Server has a catch-all route (e.g. Next.js SPA) | The server doesn't implement RFC 9728; contact the vendor |
| AS metadata 404 at `oauth-authorization-server` | Issuer path not preserved, or server uses OIDC only | The notebook tries OIDC discovery automatically; check the issuer URL in the PRM |
| DCR returns 400 or 405 | Server doesn't support Dynamic Client Registration | Register credentials manually via the provider's dashboard |
| Browser redirect fails or times out | Redirect URI mismatch or IDP requires HTTPS | Verify the redirect URI matches what's registered; see `enable_ssl.md` |
| Token exchange returns `invalid_grant` | Code expired, PKCE mismatch, or wrong redirect URI | Re-run the authorization cell; check that `REDIRECT_URI` matches exactly |

## 3LO vs 2LO

This notebook is for **3LO** (three-legged OAuth) — flows where a human user authenticates in a browser. If your MCP server uses **2LO** (client credentials / machine-to-machine), use the `mcp_test-2LO.ipynb` notebook instead. That flow follows the [MCP OAuth Client Credentials extension](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials) — it skips the browser entirely and just POSTs `grant_type=client_credentials` to the token endpoint.

## What's in the Repo

```
├── mcp_test-3LO.ipynb       # Interactive 3LO OAuth testing notebook
├── mcp_test-2LO.ipynb       # 2LO (client credentials) flow notebook
├── guide.md                  # This file — detailed walkthrough of the 3LO notebook
├── mcp_3LO_auth_flow.md     # Visual explanation of the 3LO auth flow
├── enable_ssl.md             # HTTPS setup for the local callback server
├── requirements.txt          # Python dependencies
└── .gitignore                # Git ignore rules
```
