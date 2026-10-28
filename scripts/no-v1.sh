#!/usr/bin/env bash
# keel v2 must not depend on keel v1 at run time (v1 is a separate project; v0.4.0 merged what v2 needs).
# Lists every place that still names v1's install, binary, dashboard or v1-format files.
#   scripts/no-v1.sh            report only (exit 0)
#   scripts/no-v1.sh --strict   fail when anything is found (CI, since v0.4.1)
set -euo pipefail
cd "$(dirname "$0")/.."
PATTERN='KEEL_HOME|keel_home|KEEL_DASHBOARD|KEEL_RE(F|PO)([^A-Z_]|$)|/opt/keel([^-]|$)|keel-v1/|bin/keel([^-]|$)|\.keel/state\.json|(^|[^_a-z])events\.jsonl|KeelDashboard|OpenKeelV1|keelDashboard|projects\.json|keel_v1_rules'
# Allowed: this script, the content test (it names the words content/ must not contain), history/changelog
# text, the optional external MCP entry for keel v1 (stage 4), and a line that says a v1 setting is
# "ignored since 0.4.1" (keel2 update warns about a KEEL_HOME left in the environment).
# 0.18.64 keel v1's leftover state file: v2 never runs on it, it only finds it (it blocks commits through v1's hooks) and
# archives it (runtime/v1_state.py, keel2 state): that module, its tests, and a line that says "left" or "archive".
ALLOW='^(scripts/no-v1\.sh|engine/tests/test_content\.py|CHANGELOG\.md|docs/CONTRACT\.md|docs/mockup\.html|docs/plans/field-report-clarify\.md|engine/keel_engine/runtime/v1_state\.py|engine/tests/test_v1_state\.py|api/src/test/kotlin/keel/api/KeelStateApiTest\.kt):|keel-v1-optional|ignored since 0\.4\.1|keel v1.{0,40}(left|leftover|state file)|(left|leftover|archiv).{0,60}\.keel/state\.json|\.keel/state\.json.{0,80}(left|leftover|archiv|blocks commits)'
hits="$(git grep -nE "$PATTERN" -- . ':!*.lock' ':!web/package-lock.json' 2>/dev/null | grep -vE "$ALLOW" || true)"
n=$(printf '%s' "$hits" | grep -c . || true)
if [ "$n" -eq 0 ]; then echo "no-v1: clean"; exit 0; fi
printf '%s\n' "$hits" | head -200
echo "no-v1: $n line(s) still depend on keel v1"
[ "${1:-}" = "--strict" ] && exit 1 || exit 0
