# MCP Remote Auth Flow: 3-Legged OAuth (3LO) Handshake

## Overview of the Players

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────────┐
│  MCP Client │     │  MCP Server     │     │  Authorization      │
│  (e.g. IDE) │     │  (Remote)       │     │  Server (AS)        │
└─────────────┘     └─────────────────┘     └─────────────────────┘
```

---

## Step 1: Initial Connection & Discovery

The client first hits the MCP server to discover its capabilities and auth requirements.

```
GET https://mcp-server.example.com/.well-known/oauth-authorization-server
```

The server responds with metadata:

```json
{
  "issuer": "https://mcp-server.example.com",
  "authorization_endpoint": "https://auth.example.com/oauth/authorize",
  "token_endpoint": "https://auth.example.com/oauth/token",
  "scopes_supported": [
    "mcp:read",
    "mcp:write",
    "mcp:tools:execute",
    "mcp:resources:read",
    "mcp:prompts:read"
  ],
  "response_types_supported": ["code"],
  "code_challenge_methods_supported": ["S256"]
}
```

> The client now knows **what scopes exist** and **where to send the user**

---

## Step 2: Client Registration (Dynamic, if needed)

If the client hasn't been pre-registered, MCP spec allows **dynamic client registration**:

```
POST https://auth.example.com/oauth/register
Content-Type: application/json

{
  "client_name": "My MCP Client",
  "redirect_uris": ["http://localhost:3000/callback"],
  "grant_types": ["authorization_code"],
  "response_types": ["code"],
  "scope": "mcp:read mcp:tools:execute"   // <-- desired scopes declared here
}
```

Response:
```json
{
  "client_id": "abc123",
  "client_secret": "xyz789",    // only if confidential client
  "client_id_issued_at": 1234567890
}
```

---

## Step 3: PKCE Setup (Client Side)

Before redirecting the user, the client generates PKCE values:

```javascript
// Client generates these locally
const codeVerifier = generateRandomString(64)
// "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

const codeChallenge = base64url(sha256(codeVerifier))
// "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
```

---

## Step 4: Authorization Request (The "3" in 3LO)

Client redirects the **user's browser** to the AS with requested scopes:

```
GET https://auth.example.com/oauth/authorize?
  response_type=code
  &client_id=abc123
  &redirect_uri=http://localhost:3000/callback
  &scope=mcp:read%20mcp:tools:execute     // <-- scopes requested HERE
  &state=randomCSRFtoken
  &code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM
  &code_challenge_method=S256
```

**The user then:**
1. Logs in (if not already)
2. Sees a consent screen showing exactly what scopes are being requested
3. Approves or denies

---

## Step 5: Authorization Code Returned

After user consent, AS redirects back:

```
GET http://localhost:3000/callback?
  code=SplxlOBeZQQYbYS6WxSbIA
  &state=randomCSRFtoken     // client verifies this matches
```

---

## Step 6: Token Exchange

Client exchanges the code for tokens:

```
POST https://auth.example.com/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=SplxlOBeZQQYbYS6WxSbIA
&redirect_uri=http://localhost:3000/callback
&client_id=abc123
&code_verifier=dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk
```

AS responds with tokens:

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "8xLOxBtZp8",
  "scope": "mcp:read mcp:tools:execute"   // <-- AS confirms GRANTED scopes
                                           // may differ from what was requested!
}
```

> ⚠️ **Important:** The AS may grant fewer scopes than requested. The client **must** check what was actually granted.

---

## Step 7: MCP Connection with Token

Now the client connects to the MCP server using the token:

```
// SSE or WebSocket connection
GET https://mcp-server.example.com/sse
Authorization: Bearer eyJhbGciOiJSUzI1NiJ9...
```

---

## Step 8: MCP Initialize Handshake

Over the established connection:

```json
// Client → Server
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "roots": { "listChanged": true },
      "sampling": {}
    },
    "clientInfo": {
      "name": "MyMCPClient",
      "version": "1.0.0"
    }
  }
}
```

```json
// Server → Client
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": {},
      "resources": { "subscribe": true },
      "prompts": {}
    },
    "serverInfo": {
      "name": "ExampleMCPServer",
      "version": "2.0.0"
    }
  }
}
```

---

## How Scopes Gate MCP Operations

This is where it gets interesting — scopes map to MCP primitives:

```
┌─────────────────────┬──────────────────────────────────────┐
│  Scope              │  Allowed MCP Operations              │
├─────────────────────┼──────────────────────────────────────┤
│ mcp:read            │  tools/list, resources/list,         │
│                     │  prompts/list                        │
├─────────────────────┼──────────────────────────────────────┤
│ mcp:tools:execute   │  tools/call                          │
├─────────────────────┼──────────────────────────────────────┤
│ mcp:resources:read  │  resources/read, resources/subscribe │
├─────────────────────┼──────────────────────────────────────┤
│ mcp:prompts:read    │  prompts/get                         │
├─────────────────────┼──────────────────────────────────────┤
│ mcp:write           │  resources/write (if supported)      │
└─────────────────────┴──────────────────────────────────────┘
```

The MCP server validates on every request:

```javascript
// Pseudocode on the MCP server
function handleToolCall(request, token) {
  const scopes = introspectToken(token)  // or decode JWT
  
  if (!scopes.includes("mcp:tools:execute")) {
    return {
      jsonrpc: "2.0",
      error: {
        code: -32001,   // or HTTP 403
        message: "Insufficient scope: requires mcp:tools:execute"
      }
    }
  }
  
  // proceed with tool execution
}
```

---

## Token Refresh Flow

```
POST https://auth.example.com/oauth/token

grant_type=refresh_token
&refresh_token=8xLOxBtZp8
&client_id=abc123
&scope=mcp:read mcp:tools:execute   // can request same or FEWER scopes
```

---

## Full Flow Diagram

```
Client          Browser         Auth Server      MCP Server
  │                │                │                │
  │──discover─────────────────────────────────────►  │
  │◄──scopes_supported──────────────────────────────  │
  │                │                │                │
  │──redirect user►│                │                │
  │                │──authz request─►                │
  │                │   (w/ scopes)  │                │
  │                │◄──login+consent│                │
  │                │──approve──────►│                │
  │                │◄──auth code────│                │
  │◄──callback─────│                │                │
  │                │                │                │
  │──token exchange────────────────►│                │
  │◄──access_token + granted scopes─│                │
  │                │                │                │
  │──connect w/ Bearer token───────────────────────►│
  │──initialize────────────────────────────────────►│
  │◄──capabilities──────────────────────────────────│
  │                │                │                │
  │──tools/call────────────────────────────────────►│
  │             (server checks scopes on token)      │
  │◄──result or 403─────────────────────────────────│
```

---

## Key Gotchas

**1. Scope downscoping at the AS**
```
Requested:  mcp:read mcp:tools:execute mcp:write
Granted:    mcp:read mcp:tools:execute
// Client must handle this gracefully
```

**2. The MCP server might not be the AS**
- MCP server could delegate to an external IdP (GitHub, Google, etc.)
- Token introspection or JWT validation happens server-side

**3. Per-tool scope granularity**
Some implementations get more granular:
```
mcp:tools:execute:calculator
mcp:tools:execute:file-system    // more sensitive, requires explicit grant
```

**4. State parameter is critical**
Must validate `state` on callback to prevent CSRF attacks during the auth flow