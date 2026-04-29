#!/usr/bin/env python3
"""Scrappy standalone prober for the Indeed MCP + OAuth flow.

Not wired into LaunchPad. Runs end-to-end against Indeed's real endpoints:
  1. Discover the auth server via OAuth Protected Resource metadata.
  2. Dynamically register as a public OAuth client (RFC 7591).
  3. Do authorization_code + PKCE with a tiny local callback server.
  4. Hit mcp.indeed.com/claude/mcp with the bearer token and run:
     - initialize
     - tools/list
     - tools/call search_jobs with a caller-supplied query

Usage:
    python3 launchpad/scripts/indeed_probe.py "product manager" "Seattle, WA"

Exits 0 on success, non-zero with a readable error otherwise.
Writes token details to /tmp/indeed_probe_token.json for manual inspection.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

MCP_ENDPOINT = "https://mcp.indeed.com/claude/mcp"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
CLIENT_NAME = "LaunchPad Probe (local test)"
SCOPES = "job_seeker.jobs.search offline_access job_seeker.company.details.read"


# ---------------------------------------------------------------------------
# small HTTP helper — uses stdlib so no extra deps needed for the probe
# ---------------------------------------------------------------------------
def http_json(method: str, url: str, *, headers: Optional[dict] = None, body: Optional[dict] = None,
              form: Optional[dict] = None) -> tuple[int, dict, str]:
    data: Optional[bytes] = None
    hdr = {
        "Accept": "application/json",
        # Cloudflare in front of Indeed blocks the default Python-urllib UA.
        # Use a modern browser UA so discovery + DCR go through.
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    if headers:
        hdr.update(headers)
    if body is not None:
        data = json.dumps(body).encode()
        hdr["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode()
            try:
                return r.status, json.loads(text), text
            except json.JSONDecodeError:
                return r.status, {}, text
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text), text
        except json.JSONDecodeError:
            return e.code, {}, text


def log(step: str, detail: str = "") -> None:
    line = f"\x1b[36m▸\x1b[0m \x1b[1m{step}\x1b[0m"
    if detail:
        line += f"  {detail}"
    print(line, flush=True)


def ok(msg: str) -> None:
    print(f"  \x1b[32m✓\x1b[0m {msg}", flush=True)


def die(msg: str) -> None:
    print(f"\x1b[31m✗ {msg}\x1b[0m", flush=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Step 1: discover the auth server from the MCP resource metadata
# ---------------------------------------------------------------------------
def discover_auth_server() -> dict:
    log("Step 1/5", "Discover OAuth auth server from MCP resource metadata")
    # The 401 from the MCP endpoint points at a well-known protected-resource URL.
    status, data, raw = http_json("GET", "https://mcp.indeed.com/.well-known/oauth-protected-resource/claude/mcp")
    if status != 200:
        die(f"Protected-resource metadata fetch failed: HTTP {status}\n{raw[:400]}")
    auth_servers = data.get("authorization_servers") or []
    if not auth_servers:
        die("No authorization_servers listed in metadata")
    auth_server = auth_servers[0].rstrip("/")
    ok(f"Auth server: {auth_server}")
    ok(f"Scopes supported: {', '.join(data.get('scopes_supported', []))}")

    # Grab auth-server metadata for endpoints
    status, meta, raw = http_json("GET", f"{auth_server}/.well-known/oauth-authorization-server")
    if status != 200:
        die(f"Auth-server metadata fetch failed: HTTP {status}\n{raw[:400]}")
    needed = ["authorization_endpoint", "token_endpoint", "registration_endpoint"]
    for k in needed:
        if k not in meta:
            die(f"Auth server metadata missing '{k}'")
    ok(f"Registration endpoint: {meta['registration_endpoint']}")
    ok(f"Authorization endpoint: {meta['authorization_endpoint']}")
    ok(f"Token endpoint: {meta['token_endpoint']}")
    return meta


# ---------------------------------------------------------------------------
# Step 2: Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------
def register_client(registration_endpoint: str) -> dict:
    log("Step 2/5", "Dynamic client registration (RFC 7591)")
    # Note: Indeed rejects a `scope` field at registration time —
    # scopes are declared per-authorization-request, not at DCR.
    body = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",   # public client with PKCE
        "application_type": "native",
    }
    status, data, raw = http_json("POST", registration_endpoint, body=body)
    if status not in (200, 201):
        die(f"Registration failed: HTTP {status}\n{raw[:800]}")
    client_id = data.get("client_id")
    if not client_id:
        die(f"Registration succeeded but no client_id in response: {raw[:400]}")
    ok(f"client_id: {client_id}")
    if data.get("client_secret"):
        ok("client_secret issued (will use client_secret_post for token exchange)")
    else:
        ok("No client_secret (public client) — using PKCE only")
    return data


# ---------------------------------------------------------------------------
# Step 3: Authorization code + PKCE with a tiny local callback server
# ---------------------------------------------------------------------------
def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    captured: dict = {}

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.captured["code"] = (qs.get("code") or [None])[0]
        _CallbackHandler.captured["state"] = (qs.get("state") or [None])[0]
        _CallbackHandler.captured["error"] = (qs.get("error") or [None])[0]
        _CallbackHandler.captured["error_description"] = (qs.get("error_description") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:system-ui;padding:40px;text-align:center'>"
            b"<h2>Indeed auth received</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, fmt, *args):  # silence default stderr spam
        return


def run_callback_server_once() -> dict:
    srv = http.server.HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    srv.timeout = 120
    thread = threading.Thread(target=srv.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=180)
    srv.server_close()
    return dict(_CallbackHandler.captured)


def authorize(meta: dict, client_id: str) -> tuple[str, str]:
    log("Step 3/5", "Open browser for Indeed login + consent")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # RFC 8707 Resource Indicators — tell the auth server we want a token
        # scoped to the MCP resource (not the default Indeed API audience).
        "resource": MCP_ENDPOINT,
    }
    auth_url = f"{meta['authorization_endpoint']}?{urllib.parse.urlencode(params)}"
    print(f"  Opening: {auth_url}", flush=True)
    try:
        webbrowser.open(auth_url)
    except Exception:
        print("  (could not open browser automatically — paste the URL above into your browser)", flush=True)
    print("  Waiting for redirect to localhost:{}...".format(REDIRECT_PORT), flush=True)
    captured = run_callback_server_once()
    if captured.get("error"):
        die(f"OAuth returned error: {captured['error']} — {captured.get('error_description')}")
    if not captured.get("code"):
        die("Did not receive an auth code within 3 minutes")
    if captured.get("state") != state:
        die(f"State mismatch — CSRF risk: got '{captured.get('state')}' expected '{state}'")
    ok("Authorization code received")
    return captured["code"], verifier


def exchange_code(meta: dict, client_id: str, client_secret: Optional[str], code: str, verifier: str) -> dict:
    log("Step 4/5", "Exchange auth code for access token")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
        # RFC 8707: repeat the resource indicator at token exchange so the
        # issued token's aud = https://mcp.indeed.com/claude/mcp
        "resource": MCP_ENDPOINT,
    }
    headers = {}
    if client_secret:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    status, data, raw = http_json("POST", meta["token_endpoint"], form=form, headers=headers)
    if status != 200:
        die(f"Token exchange failed: HTTP {status}\n{raw[:800]}")
    if not data.get("access_token"):
        die(f"Token response missing access_token: {raw[:400]}")
    ok(f"access_token acquired ({len(data['access_token'])} chars)")
    # Decode the JWT payload just to show aud/scope — helpful for debugging
    parts = data["access_token"].split(".")
    if len(parts) == 3:
        try:
            pad = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(pad).decode())
            ok(f"token aud={payload.get('aud')!r}  scope={payload.get('scope')!r}")
        except Exception:
            pass
    if data.get("refresh_token"):
        ok("refresh_token acquired")
    if data.get("expires_in"):
        ok(f"expires in {data['expires_in']} seconds")
    # Persist for inspection
    with open("/tmp/indeed_probe_token.json", "w") as f:
        json.dump(data, f, indent=2)
    ok("Token saved to /tmp/indeed_probe_token.json")
    return data


# ---------------------------------------------------------------------------
# Step 5: Call the MCP server — JSON-RPC over HTTP with SSE response
# ---------------------------------------------------------------------------
def parse_mcp_response(raw: str) -> dict:
    """MCP endpoint returns either application/json or an SSE stream.

    Parse either shape and return the first response object we find.
    """
    text = raw.lstrip()
    if text.startswith("{"):
        return json.loads(text)
    # SSE: one or more `event:` + `data:` frames
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
    raise ValueError(f"Could not parse MCP response:\n{raw[:400]}")


def mcp_call(access_token: str, method: str, params: Optional[dict] = None, *, rid: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json, text/event-stream",
    }
    status, _, raw = http_json("POST", MCP_ENDPOINT, body=body, headers=headers)
    if status >= 400:
        die(f"MCP {method} failed: HTTP {status}\n{raw[:800]}")
    return parse_mcp_response(raw)


def run_mcp(access_token: str, search_query: str, location: str) -> None:
    log("Step 5/5", "Call MCP server with bearer token")

    # initialize (required by the MCP spec before any tools/* call)
    init = mcp_call(access_token, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "launchpad-probe", "version": "0.1"},
    }, rid=1)
    server_info = (init.get("result") or {}).get("serverInfo") or {}
    ok(f"MCP initialized — server: {server_info.get('name')} v{server_info.get('version')}")

    # tools/list
    tools_resp = mcp_call(access_token, "tools/list", {}, rid=2)
    tools = (tools_resp.get("result") or {}).get("tools") or []
    if not tools:
        die(f"tools/list returned no tools: {json.dumps(tools_resp)[:400]}")
    ok(f"Discovered {len(tools)} tools:")
    for t in tools:
        print(f"    - \x1b[33m{t.get('name')}\x1b[0m — {(t.get('description') or '')[:90]}")

    # tools/call — search_jobs
    if not any(t.get("name") == "search_jobs" for t in tools):
        die("search_jobs tool not present on this MCP server")
    log("", f"Calling search_jobs query='{search_query}' location='{location}'")
    call_resp = mcp_call(access_token, "tools/call", {
        "name": "search_jobs",
        "arguments": {"query": search_query, "location": location, "limit": 5},
    }, rid=3)
    result = call_resp.get("result") or {}
    if "content" not in result:
        print(json.dumps(call_resp, indent=2)[:1500])
        die("search_jobs response missing 'content'")
    # MCP text content is a list of {type: 'text', text: '...'}
    for block in result.get("content", []):
        if block.get("type") == "text":
            txt = block.get("text", "")
            print("\n\x1b[1mSearch result preview\x1b[0m:")
            print(txt[:2000])
            if len(txt) > 2000:
                print(f"\n  ... truncated ({len(txt)} chars total)")
    ok("search_jobs succeeded — end-to-end flow works")


# ---------------------------------------------------------------------------
def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "product manager"
    location = sys.argv[2] if len(sys.argv) > 2 else "Remote"
    print(f"\n\x1b[1mIndeed MCP end-to-end probe\x1b[0m — query='{query}', location='{location}'\n")

    meta = discover_auth_server()
    reg = register_client(meta["registration_endpoint"])
    code, verifier = authorize(meta, reg["client_id"])
    token = exchange_code(meta, reg["client_id"], reg.get("client_secret"), code, verifier)
    run_mcp(token["access_token"], query, location)

    print("\n\x1b[32m\x1b[1mAll 5 steps passed.\x1b[0m  Token cached at /tmp/indeed_probe_token.json\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
