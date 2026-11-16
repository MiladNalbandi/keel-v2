#!/usr/bin/env bash
# keel v2 daily-use e2e: one command.   e2e/daily/run.sh [--real] [--keep] [--only <playwright grep>] [-- <playwright args>]
#
# Starts a throw-away NATIVE keel (name keel-e2e-daily, volume keel-e2e-daily-data, its own port, config dir and home,
# all under one temp folder), seeds a sample git project, runs the journeys in a real Chromium (Playwright), writes
#   $OUT/html/index.html   Playwright's HTML report        $OUT/summary.md   pass/fail per journey + the bugs seen
# and stops and removes ONLY what it made. The model is the FAKE one (KEEL_FAKE=1: deterministic, real tool reads and
# writes, no cost).
#
#   --real   NOT for routine runs: the real model (the person says when, after the weekly usage reset). It does not turn
#            the fake model off by itself yet: it sets KEEL_E2E_REAL=1 (journeys then start flows without allow_fake,
#            a Claude login must exist on this computer) and drops KEEL_FAKE / KEEL_FAKEONREALPROJECTS. The safe gate
#            driver's caps stay: it never approves "Token cap reached" and stops on a gate that comes back 4 times.
#   --keep   leave the work folder (logs, tui captures) and do not delete the dist cache; the keel is still stopped.
#   --only   a Playwright --grep, e.g. --only "J5"
#
# Needs on this computer (omarchy has them via mise: Node 20, Java 21; uv): git, python3, curl, uv. Playwright is installed
# here in e2e/daily (npm install; npx playwright install chromium) - on the test server only, never on the Mac.
# The native build (scripts/build-native.sh, ~5 min) is cached in $DIST (rebuilt when a source file is newer).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd -P)"
src="$(cd "$here/../.." && pwd -P)"

# node 20 / java 21 from mise when there is one (Node 26 breaks jsdom; the build needs JDK 21)
if [ -z "${KEEL_E2E_IN_MISE:-}" ] && command -v mise >/dev/null 2>&1; then
  exec env KEEL_E2E_IN_MISE=1 mise exec node@20 java@21 -- "$0" "$@"
fi

real=0 keep=0 only="${KEEL_E2E_ONLY:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --real) real=1 ;;
    --keep) keep=1 ;;
    --only) only="$2"; shift ;;
    --) shift; break ;;
    *) echo "usage: run.sh [--real] [--keep] [--only <grep>] [-- playwright args]" >&2; exit 2 ;;
  esac
  shift
done

NAME="keel-e2e-daily"            # only names starting with keel-e2e are ever touched
VOLUME="keel-e2e-daily-data"
work="$(mktemp -d "${TMPDIR:-/tmp}/keel-e2e-daily.XXXXXX")"
OUT="${KEEL_E2E_OUT:-$here/report}"
DIST="${KEEL_E2E_DIST:-$HOME/.cache/keel-e2e-daily-dist}"
mkdir -p "$OUT" "$DIST"

free_port() { python3 - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}
export KEEL_NAME="$NAME" KEEL_VOLUME="$VOLUME" KEEL2_HOME="$work/k2" XDG_CONFIG_HOME="$work/cfg"
export PORT="$(free_port)"
[ "$PORT" != 8088 ] || PORT="$(free_port)"
export KEEL_OPEN=0 KEEL_YES=1 KEEL_NATIVE_CLIS=0 NO_COLOR=1
if [ "$real" = 1 ]; then export KEEL_E2E_REAL=1; unset KEEL_FAKE KEEL_FAKEONREALPROJECTS
else export KEEL_FAKE=1 KEEL_FAKEONREALPROJECTS=true; fi
export KEEL_GITHUB_LOGIN_GH="$here/bin/gh-fake"
export KEEL2_BIN="$src/keel2"
mkdir -p "$KEEL2_HOME" "$XDG_CONFIG_HOME/keel2"

stopped=0
cleanup() {
  local rc=$?
  if [ "$stopped" = 0 ]; then
    "$src/keel2" stop >/dev/null 2>&1 || true
    "$src/keel2" uninstall --all >/dev/null 2>&1 || true   # only $KEEL2_HOME/$KEEL_NAME: the env above points at our own folder
    stopped=1
  fi
  # anything still running from our own temp folder (the pattern's [k] keeps pkill from matching itself)
  pkill -f "[${work:0:1}]${work:1}/k2" >/dev/null 2>&1 || true
  if [ "$keep" = 0 ]; then rm -rf -- "$work"; else echo "kept: $work"; fi
  exit $rc
}
trap cleanup EXIT

rm -f "$here/bin/gh.signed-out"   # the fake gh starts signed in
echo "== seed the sample projects"
projects="$work/projects"
python3 "$here/seed.py" "$projects" >/dev/null
export KEEL_E2E_SHOP="$projects/shop" KEEL_E2E_NOTES="$projects/notes" KEEL_E2E_WORK="$work"

echo "== native build (cached in $DIST)"
tgz="$(ls -t "$DIST"/keel-v2-*-native.tar.gz 2>/dev/null | head -1 || true)"
stale=0
if [ -z "$tgz" ] || [ -n "${REBUILD:-}" ]; then stale=1
elif [ -n "$(find "$src/api/src" "$src/web/src" "$src/plugins" "$src/engine/keel_engine" "$src/cli/src" "$src/docker" "$src/keel2" -type f -newer "$tgz" -not -path '*/node_modules/*' -not -path '*/build/*' -not -path '*/dist/*' -print -quit 2>/dev/null)" ]; then stale=1; fi
if [ "$stale" = 1 ]; then rm -f "$DIST"/keel-v2-*; "$src/scripts/build-native.sh" "$DIST" > "$work/build.log" 2>&1 || { tail -40 "$work/build.log" >&2; exit 1; }; tgz="$(ls -t "$DIST"/keel-v2-*-native.tar.gz | head -1)"; fi

echo "== start the throw-away keel: $NAME on port $PORT (fake model: $([ $real = 1 ] && echo NO || echo yes))"
"$src/keel2" start --native --from-tarball "$tgz" "$projects/shop" > "$work/start.log" 2>&1 || { tail -30 "$work/start.log" >&2; exit 1; }
st="$("$src/keel2" status 2>&1 || true)"   # (not "| grep -q": pipefail + SIGPIPE)
case "$st" in *"running natively"*) ;; *) echo "keel is not running natively: $st" >&2; exit 1 ;; esac
export KEEL_URL="http://127.0.0.1:$PORT"
export KEEL_KEY="$("$src/keel2" auth key)"
export KEEL_TOKEN="$(cat "$KEEL2_HOME/$NAME/run/token" 2>/dev/null || true)"   # keel's internal token: only keel2 may make sign-in links
for _ in $(seq 1 60); do curl -fs "$KEEL_URL/api/health" >/dev/null 2>&1 && break; sleep 2; done
curl -fs "$KEEL_URL/api/health" >/dev/null || { echo "keel does not answer on $KEEL_URL" >&2; exit 1; }

echo "== journeys"
cd "$here"
[ -d node_modules ] || npm install --no-audit --no-fund
export KEEL_E2E_OUT="$OUT"
rc=0
npx playwright test ${only:+--grep "$only"} "$@" || rc=$?

echo "== stop and remove our keel"
"$src/keel2" stop >/dev/null 2>&1 || true
"$src/keel2" uninstall --all >/dev/null 2>&1 || true
stopped=1
if pgrep -f "[${work:0:1}]${work:1}/k2" >/dev/null 2>&1; then echo "a process of ours is still running" >&2; rc=1; fi
cat "$OUT/summary.md" 2>/dev/null | head -40 || true
echo "report: $OUT/html/index.html   summary: $OUT/summary.md"
exit $rc
