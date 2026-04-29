#!/usr/bin/env python3
"""Quick diagnostic — reuse the cached token and probe a few MCP endpoint variants
+ header combos to see if the 403 is a simple missing-header issue or a genuine
client allowlist.
"""
import json
import urllib.parse
import urllib.request
import urllib.error

TOKEN = json.load(open("/tmp/indeed_probe_token.json"))["access_token"]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Try several permutations
permutations = [
    ("POST", "https://mcp.indeed.com/claude/mcp", {}),
    ("POST", "https://mcp.indeed.com/claude/mcp", {"MCP-Protocol-Version": "2024-11-05"}),
    ("POST", "https://mcp.indeed.com/mcp", {}),
    ("POST", "https://mcp.indeed.com/", {}),
    ("GET", "https://mcp.indeed.com/claude/mcp", {}),  # some SSE-only servers want GET for init
]

body = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "launchpad-probe", "version": "0.1"},
    },
}

for method, url, extra in permutations:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    headers.update(extra)
    data = json.dumps(body).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = r.read().decode()
            print(f"[{r.status}] {method} {url}  extra={extra}")
            print(f"    -> {resp[:400]}\n")
    except urllib.error.HTTPError as e:
        resp = e.read().decode()
        print(f"[{e.code}] {method} {url}  extra={extra}")
        print(f"    -> {resp[:400]}")
        # also dump response headers for 401/403 — we may see hints there
        if e.code in (401, 403):
            for k, v in e.headers.items():
                if k.lower() in ("www-authenticate", "mcp-protocol-version", "x-request-id"):
                    print(f"    hdr: {k}: {v}")
        print()
    except Exception as e:
        print(f"[ERR] {method} {url}: {e}\n")
