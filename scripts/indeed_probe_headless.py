#!/usr/bin/env python3
"""Run just discovery + dynamic client registration (no browser needed).

This proves (or disproves) that Indeed's MCP OAuth flow accepts public-client
registration without partner approval. If these two steps succeed, the only
thing left that requires a human is the actual login in a browser.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

from indeed_probe import discover_auth_server, register_client  # noqa: E402

try:
    meta = discover_auth_server()
    reg = register_client(meta["registration_endpoint"])
    print("\n\x1b[32m\x1b[1m✓ Discovery + DCR succeeded.\x1b[0m")
    print(f"\n  client_id: {reg.get('client_id')}")
    print(f"  client_secret: {'<issued>' if reg.get('client_secret') else '<none — public client>'}")
    print(f"  scopes granted: {reg.get('scope')}")
    print(f"  token auth method: {reg.get('token_endpoint_auth_method')}")
    print(f"\n  Next step requires a browser login. Run:")
    print(f"    python3 launchpad/scripts/indeed_probe.py 'product manager' 'Seattle, WA'")
except SystemExit as e:
    sys.exit(e.code)
