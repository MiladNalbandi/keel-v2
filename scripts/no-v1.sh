#!/usr/bin/env bash
# keel v2 must not depend on keel v1 at run time (v1 is a separate project; v0.4.0 merged what v2 needs).
# Lists every place that still names v1's install, binary, dashboard or v1-format files.
#   scripts/no-v1.sh            report only (exit 0)
#   scripts/no-v1.sh --strict   fail when anything is found (CI, from v0.4.0)
set -euo pipefail
cd "$(dirname "$0")/.."
PATTERN='KEEL_HOME|keel_home|/opt/keel([^-]|$)|keel-v1/|bin/keel|\.keel/state\.json|events\.jsonl|KeelDashboard|OpenKeelV1|keelDashboard|projects\.json|keel_v1_rules'
# Allowed: this script, history/changelog text, and the optional external MCP entry for keel v1 (stage 4).
ALLOW='^(scripts/no-v1\.sh|CHANGELOG\.md|docs/CONTRACT\.md|docs/mockup\.html):|keel-v1-optional'
hits="$(git grep -nE "$PATTERN" -- . ':!*.lock' ':!web/package-lock.json' 2>/dev/null | grep -vE "$ALLOW" || true)"
n=$(printf '%s' "$hits" | grep -c . || true)
if [ "$n" -eq 0 ]; then echo "no-v1: clean"; exit 0; fi
printf '%s\n' "$hits" | head -200
echo "no-v1: $n line(s) still depend on keel v1"
[ "${1:-}" = "--strict" ] && exit 1 || exit 0
