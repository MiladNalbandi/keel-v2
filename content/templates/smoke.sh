#!/usr/bin/env bash
# Smoke checks. Runs locally and after a deploy: BASE_URL and API_URL decide where.
set -euo pipefail
API="${API_URL:-{{API_URL}}}"
APP="${BASE_URL:-{{WEB_URL}}}"

check() { printf '%-40s' "$1"; shift; if "$@" >/dev/null 2>&1; then echo ok; else echo FAIL; exit 1; fi; }

check "api responds" bash -c "curl -fsS '{{API_HEALTH_PATH}}'"
# The app-shell assertion is deliberately weak: the root element differs by framework
# (#root, #__next, #app, none at all for server-rendered markup). Tighten it to something
# only YOUR app renders — a heading, a data-testid — once you know what that is.
check "app shell loads" bash -c "curl -fsS \"\$APP\" | grep -qi '<html'"
# Add one critical write and read back, for example:
# check "create and read X" bash -c "id=\$(curl -fsS -XPOST \"\$API/things\" -H 'content-type: application/json' -d '{}' | jq -r .id) && curl -fsS \"\$API/things/\$id\" | jq -e .id"
echo "smoke ok"
