#!/usr/bin/env bash
# LaunchPad smoke test (macOS / Linux).
# Requires the server to already be running (./start.sh in another terminal).
# Checks each public endpoint responds with a reasonable status code.

set -u

PORT="${LAUNCHPAD_PORT:-7070}"
HOST="${LAUNCHPAD_HOST:-localhost}"
BASE="http://${HOST}:${PORT}"

pass=0
fail=0
results=()

# Args: name path expected_statuses (comma-separated)
check() {
    local name="$1"
    local path="$2"
    local expected="$3"

    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}${path}" || echo "000")
    if [[ ",${expected}," == *",${code},"* ]]; then
        printf "  \033[0;32m\xE2\x9C\x93\033[0m  %-46s %s\n" "${name}" "[${code}]"
        pass=$((pass + 1))
    else
        printf "  \033[0;31m\xE2\x9C\x97\033[0m  %-46s %s (expected %s)\n" "${name}" "[${code}]" "${expected}"
        fail=$((fail + 1))
        results+=("${name}:${code}")
    fi
}

echo ""
echo "LaunchPad smoke test against ${BASE}"
echo "--------------------------------------------"

# Public endpoints
check "Health check"                  "/api/health"              "200"
check "Network info"                  "/api/network"             "200"
check "Profiles list"                 "/api/profiles"            "200"
check "Static index.html"             "/"                        "200"
check "Static CSS"                    "/styles.css"              "200"

# Auth-protected endpoints (401 without session is correct)
check "Auth me (should reject)"       "/api/auth/me"             "401"
check "Listings (should reject)"      "/api/listings"            "401"
check "Settings (should reject)"      "/api/settings"            "401"
check "Gmail status (should reject)"  "/api/gmail/status"        "401"
check "Reminders (should reject)"     "/api/reminders"           "401"
check "Companies (should reject)"     "/api/companies"           "401"
check "History (should reject)"       "/api/history"             "401"
check "Interview prep (reject)"       "/api/interview-prep/stories"  "401"
check "Backup export (reject)"        "/api/backup/export"       "401"
check "Scanner companies (reject)"    "/api/scanner/companies"   "401"

# API docs (available by default)
check "OpenAPI schema"                "/openapi.json"            "200"

echo "--------------------------------------------"
echo "  Passed: ${pass}   Failed: ${fail}"
echo ""

if [[ ${fail} -gt 0 ]]; then
    echo "FAILED checks:"
    for r in "${results[@]}"; do echo "  - ${r}"; done
    exit 1
fi

echo "All smoke checks passed."
