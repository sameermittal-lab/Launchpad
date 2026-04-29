# LaunchPad Helper Scripts

Optional scripts to run LaunchPad as a background service.

## macOS — launchd

1. Edit `launchpad.plist.template`, replacing `/PATH/TO/launchpad` with your actual path.
2. Copy it to `~/Library/LaunchAgents/com.launchpad.server.plist`.
3. Load the agent:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.launchpad.server.plist
   ```
4. It will now start automatically at login. To stop it:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.launchpad.server.plist
   ```
5. Logs are written to `/tmp/launchpad.out.log` and `/tmp/launchpad.err.log`.

## Windows — Task Scheduler

1. Open Task Scheduler → Create Basic Task.
2. Name: `LaunchPad`. Trigger: **When I log on** (or **When the computer starts** for always-on).
3. Action: **Start a program**. Program: `C:\PATH\TO\launchpad\start.bat`.
4. In the task's Properties → General, check **Run whether user is logged on or not** if you want it to survive reboots without your login.
5. (Optional) Conditions tab → uncheck **Start the task only if the computer is on AC power** if running on a laptop.

## Linux — systemd

1. Edit `launchpad.service.template`, replacing `/PATH/TO/launchpad` and `YOUR_USERNAME`.
2. Copy it to `/etc/systemd/system/launchpad.service` (or `~/.config/systemd/user/` for user-level).
3. Enable and start:
   ```bash
   sudo systemctl enable launchpad
   sudo systemctl start launchpad
   sudo systemctl status launchpad
   ```
4. Logs:
   ```bash
   sudo journalctl -u launchpad -f
   ```

## Smoke test

After a fresh install or restore, run `smoke_test.sh` (macOS/Linux) or `smoke_test.bat` (Windows) to verify the server starts and responds to all the key endpoints.


## Indeed MCP probe (diagnostic)

Standalone OAuth 2.1 flow validator against Indeed's Model Context Protocol (MCP) server. Not wired into LaunchPad — kept as a one-shot test so we can detect the day Indeed opens its MCP client allowlist.

Three scripts:

| Script | What it does | Human needed? |
|---|---|---|
| `indeed_probe_headless.py` | Steps 1-2 only — discovery + dynamic client registration (RFC 7591). Proves the OAuth infrastructure is correct without requiring a browser. | No |
| `indeed_probe.py` | Full 5-step flow — discovery → DCR → browser auth (PKCE + RFC 8707 resource indicator) → token exchange → MCP `initialize` + `tools/list` + `search_jobs`. | Yes (browser login) |
| `indeed_probe_diag.py` | After running `indeed_probe.py`, tries alternate MCP endpoint paths and header combinations against the cached token. Used to diagnose whether a failure is a path/header issue or a true client-allowlist block. | No |

### Run the full probe

```bash
python3 launchpad/scripts/indeed_probe.py "product manager" "Seattle, WA"
```

A browser tab will open to `secure.indeed.com/oauth/v2/authorize`. Log in with your own Indeed account and approve. The terminal continues automatically once the callback lands. Token is cached to `/tmp/indeed_probe_token.json` for inspection.

### What the results mean

- **Steps 1-4 succeed, step 5 returns `403 invalid_client: Client not allowed`** — expected as of 2026-Q2. Indeed's MCP server allowlists clients separately from OAuth; only Anthropic's Claude client is currently permitted. Our OAuth flow is fully correct.
- **Step 5 succeeds** — Indeed has opened MCP access. Time to wire the probe's logic into `app/services/ai_company_monitor.py` as a real scanner adapter.

No user secrets are stored by these scripts. The cached token at `/tmp/indeed_probe_token.json` expires after 1 hour; delete it manually when done.


## Demo profile seed

If you want a "Jane Doe" profile with a sample resume so the UI has something to show on a fresh install (rather than an empty login screen), run:

```bash
python3 launchpad/scripts/seed_demo_profile.py
```

Idempotent — safe to run multiple times. Creates a profile named "Jane Doe" (Staff Product Manager / AI Platforms) with target roles, locations, and a sample `cv.md`. No API keys or real data — you add those via Settings. Delete the demo from Settings → Danger Zone once you have your own profile set up.
