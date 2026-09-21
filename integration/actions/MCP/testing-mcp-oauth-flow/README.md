# MCP OAuth Tester

Interactive Jupyter notebooks for testing OAuth authorization flows on remote [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers before integrating with Amazon QuickSight or any other MCP client.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Usage](#usage)
  - [3LO (Three-Legged OAuth)](#3lo-three-legged-oauth)
  - [2LO (Two-Legged OAuth / Client Credentials)](#2lo-two-legged-oauth--client-credentials)
- [Tested MCP Servers](#tested-mcp-servers)
- [Troubleshooting](#troubleshooting)
- [Repository Structure](#repository-structure)
- [Security](#security)
- [License](#license)

## Overview

The [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization) layers several RFCs on top of each other:

- [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html) — Protected Resource Metadata
- [RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html) — Authorization Server discovery
- [RFC 7591](https://www.rfc-editor.org/rfc/rfc7591.html) — Dynamic Client Registration
- [OAuth 2.1](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-12) with [PKCE (RFC 7636)](https://www.rfc-editor.org/rfc/rfc7636.html) — Token exchange

Every MCP server implements a slightly different subset of these standards. Some advertise metadata in `WWW-Authenticate` headers, some don't. Some support Dynamic Client Registration, others require manual credentials. Some only expose [OpenID Connect discovery](https://openid.net/specs/openid-connect-discovery-1_0.html) instead of RFC 8414.

Rather than discovering these differences in production, these notebooks let you walk through each step interactively, see exactly what your server returns, and identify issues before they become integration blockers.

## Prerequisites

- Python 3.9+
- A remote MCP server URL
- Client credentials if the server doesn't support Dynamic Client Registration (3LO)
- Client ID and secret for machine-to-machine flows (2LO)

## Getting Started

1. Clone the repository:

   ```bash
   git clone <repository-url>
   cd sample-amazon-quick-suite-knowledge-hub
   ```

2. Navigate to the testing-mcp-oauth-flow directory, create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Open the appropriate notebook:

   ```bash
   jupyter notebook mcp_test-3LO.ipynb   # For user-facing OAuth flows
   jupyter notebook mcp_test-2LO.ipynb   # For machine-to-machine flows
   ```

## Usage

### For details on the 3LO notebook tester see [MCP 3LO auth flow test notebook guide](guide.md)

### 3LO (Three-Legged OAuth)

Use `mcp_test-3LO.ipynb` for flows where a human user authenticates in a browser. The notebook walks through the full [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization) step by step:

1. **Probe the MCP endpoint** — Send a HEAD request, look for `WWW-Authenticate` with a `resource_metadata` URL
2. **Fetch Protected Resource Metadata** — Discover the authorization server, available scopes, and resource identifier ([RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html))
3. **Fetch Authorization Server Metadata** — Find the `authorization_endpoint`, `token_endpoint`, and `registration_endpoint` ([RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html))
4. **Register a client** — Attempt Dynamic Client Registration, fall back to manual credentials ([RFC 7591](https://www.rfc-editor.org/rfc/rfc7591.html))
5. **Authorize** — Open the browser, capture the callback with a local HTTP server, get an authorization code
6. **Exchange for a token** — Trade the code + PKCE verifier for an access token
7. **Test the connection** — Send MCP `initialize` and `tools/list` JSON-RPC requests

Set your MCP endpoint in the first cell:

```python
mcpURL = "https://your-mcp-server.example.com/mcp"
```

The default OAuth redirect URI is `http://localhost:8888/callback`. If your identity provider requires HTTPS redirects, see [enable_ssl.md](enable_ssl.md) for setup instructions.

For a detailed explanation of the 3LO auth flow, see [mcp_3LO_auth_flow.md](mcp_3LO_auth_flow.md).

### 2LO (Two-Legged OAuth / Client Credentials)

Use `mcp_test-2LO.ipynb` for server-to-server integrations, background services, CI/CD pipelines, and daemon processes where no end user is present. This flow follows the [MCP OAuth Client Credentials extension](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials):

1. Discover the token endpoint (via RFC 9728 PRM → RFC 8414 AS metadata, or manual config)
2. POST `grant_type=client_credentials` with your `client_id` and `client_secret`
3. Use the returned access token to call the MCP endpoint



## Troubleshooting

| Symptom | Likely Cause | What to Check |
|---|---|---|
| HEAD returns 404, no `WWW-Authenticate` | Server may not implement RFC 9728 discovery | Try the path-suffixed well-known URL manually; check if this is a 2LO endpoint |
| PRM returns 200 but Content-Type is `text/html` | Server has a catch-all route (e.g., Next.js SPA) | The server doesn't implement RFC 9728; contact the vendor |
| AS metadata 404 at `oauth-authorization-server` | Issuer path not preserved, or server uses OIDC only | The notebook tries OIDC discovery automatically; check the issuer URL in the PRM |
| DCR returns 400 or 405 | Server doesn't support Dynamic Client Registration | Register credentials manually via the provider's dashboard |
| Browser redirect fails or times out | Redirect URI mismatch or IDP requires HTTPS | Verify the redirect URI matches what's registered; see [enable_ssl.md](enable_ssl.md) |
| Token exchange returns `invalid_grant` | Code expired, PKCE mismatch, or wrong redirect URI | Re-run the authorization cell; check that `REDIRECT_URI` matches exactly |

## Repository Structure

```
├── mcp_test-3LO.ipynb       # Interactive 3LO OAuth testing notebook
├── mcp_test-2LO.ipynb       # 2LO (client credentials) flow notebook
├── guide.md                  # Detailed walkthrough of the 3LO notebook
├── mcp_3LO_auth_flow.md     # Visual explanation of the 3LO auth flow
├── enable_ssl.md             # HTTPS setup for the local callback server
├── requirements.txt          # Python dependencies
└── .gitignore                # Git ignore rules
```

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This project is licensed under the Amazon Software License.
