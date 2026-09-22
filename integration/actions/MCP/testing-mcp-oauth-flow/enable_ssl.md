# Enabling HTTPS for the Local OAuth Callback

Some identity providers refuse to redirect back to `http://` URIs, even on localhost.
This guide covers how to switch the callback server in `mcp_test-3LO.ipynb` to HTTPS.

## Prerequisites

You need a TLS certificate and private key for `localhost`. Two options:

### Option A: mkcert (recommended — no browser warnings)

```bash
brew install mkcert
mkcert -install          # one-time: installs a local CA into your system trust store
mkcert localhost         # creates localhost.pem + localhost-key.pem
```

This produces certs that your browser already trusts, so the OAuth redirect works seamlessly.

### Option B: openssl (self-signed — browser will warn)

```bash
openssl req -x509 -newkey rsa:2048 \
  -keyout localhost-key.pem -out localhost.pem \
  -days 365 -nodes -subj '/CN=localhost'
```

With self-signed certs, your browser will show a security warning on the callback.
You'll need to click through it (or manually add the cert to your keychain).

## Notebook Changes

Three things need to change in `mcp_test-3LO.ipynb`:

### 1. Update the redirect URI

In the Client Registration cell, change:

```python
REDIRECT_URI = "http://localhost:8888/callback"
```

to:

```python
REDIRECT_URI = "https://localhost:8888/callback"
```

### 2. Wrap the callback server socket with TLS

In the Authorization Code Flow cell, after creating the `HTTPServer`, add the SSL context:

```python
import ssl

server = HTTPServer(('localhost', 8888), CallbackHandler)

# Wrap with TLS
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain('localhost.pem', 'localhost-key.pem')
server.socket = ctx.wrap_socket(server.socket, server_side=True)
```

### 3. Update DCR payload (if applicable)

If the server supports Dynamic Client Registration, the `redirect_uris` in the
registration payload will automatically pick up the new `REDIRECT_URI` value since
it references the variable. No extra change needed there.

## File Layout

After setup, your project directory should look like:

```
.
├── .env
├── .venv/
├── localhost.pem            # TLS certificate
├── localhost-key.pem        # TLS private key
├── enable_ssl.md            # this file
├── mcp_test-2LO.ipynb
├── mcp_test-3LO.ipynb
├── mcp_scope_usage.md
└── requirements.txt
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Browser shows "Your connection is not private" | Self-signed cert (Option B) | Use mkcert (Option A) or click through the warning |
| `ssl.SSLError: [SSL] PEM lib` | Wrong cert/key file paths | Verify `localhost.pem` and `localhost-key.pem` exist in the working directory |
| IDP rejects `https://localhost:8888/callback` | Redirect URI not registered | Re-register the client with the `https://` URI, or update it in the IDP's developer console |
| `OSError: [Errno 48] Address already in use` | Port 8888 still bound from a previous run | Wait a few seconds or kill the process using the port: `lsof -ti:8888 \| xargs kill` |
