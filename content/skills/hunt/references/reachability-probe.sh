#!/usr/bin/env bash
# reachability-probe.sh — one happy-path call per target, timed, no retries.
#
# The `reachability` lens's whole method: hit every frontend route and every backend endpoint
# once, live, and report which ones did not answer. Not an edge-case matrix — that is
# `exploration`'s job, with its own recipe run twice per target for a 3/3 count. This script
# runs once, on purpose, because a second pass would double the time budget for no more proof
# than "it was slow, or it wasn't" already gives.
#
# Usage:
#   reachability-probe.sh <targets-file> [timeout-seconds]
#
#   targets-file   one "METHOD URL" per line, e.g.:
#                    GET  http://localhost:8080/api/bookmarks
#                    GET  http://localhost:3000/bookmarks
#                  Blank lines and lines starting with # are skipped. Build this list from the
#                  contract (backend paths) and the route/page inventory (frontend paths) —
#                  never from a controller or a component; that is implementation source, and
#                  this lens does not read it, same rule `exploration` and `contract-drift` use.
#   timeout-seconds  per-call timeout, default 3.
#
# The 30-second budget is not enforced by this script — keel has no wall-clock timer on a
# hunter — it is a property of what you put in the targets file: N targets at T seconds each is
# at most N*T seconds if every one of them hangs. Keep the list to what the math allows for
# whatever budget you were given; do not pad it with edge cases, that is not this lens.
#
# Exit code: 0 if every target answered without a 5xx or a timeout, 1 otherwise.

set -uo pipefail

FILE="${1:?usage: reachability-probe.sh <targets-file> [timeout-seconds]}"
TIMEOUT="${2:-3}"

if [[ ! -f "$FILE" ]]; then
  echo "no such targets file: $FILE" >&2
  exit 2
fi

fail=0
start=$(date +%s)

while IFS= read -r line; do
  line="${line%%$'\r'}"
  [[ -z "$line" || "$line" == \#* ]] && continue
  method="${line%% *}"
  url="${line#* }"
  body="$(mktemp)"
  code=$(curl -s -o "$body" -w '%{http_code}' --max-time "$TIMEOUT" -X "$method" "$url" 2>/dev/null)
  status=$?
  if [[ $status -ne 0 || -z "$code" ]]; then
    code="000"
  fi
  if [[ "$code" == "000" || "$code" =~ ^5[0-9][0-9]$ ]]; then
    fail=1
    printf 'FAIL  %-6s %-60s %s\n' "$method" "$url" "$code"
  else
    printf 'ok    %-6s %-60s %s\n' "$method" "$url" "$code"
  fi
  rm -f "$body"
done < "$FILE"

total=$(( $(date +%s) - start ))
echo "---"
echo "${total}s elapsed, timeout ${TIMEOUT}s/call"
exit $fail
