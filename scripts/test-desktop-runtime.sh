#!/usr/bin/env bash
# Test keel desktop's bundle without Electron: start keel from <dir> (scripts/build-desktop-runtime.sh's out dir, or a
# built app's resources/) the way the app does (desktop/src/keelenv.ts), from a copy in a folder with a space in its
# name (writable, with new file times: keel must still write nothing into it), with no Java, no uv and no keel Python
# on PATH. Then: healthy, a sign-in code, a flow on the fake model up to its first gate, a clean stop with nothing
# left running.
#
#   scripts/test-desktop-runtime.sh <dir> [--in-place]      (--in-place: no copy, e.g. a read-only AppImage mount)
#
# Ports 18180+; everything lives in one scratch folder that is removed at the end. Needs: bash, curl, git, python3.
set -euo pipefail
dir="${1:?usage: scripts/test-desktop-runtime.sh <dir with runtime/ and keel/> [--in-place]}"
dir="$(cd "$dir" && pwd -P)"
[ -x "$dir/runtime/bin/keel-engine" ] && [ -f "$dir/keel/keel-native-start" ] || { echo "$dir has no runtime/ and keel/" >&2; exit 1; }

scratch="$(mktemp -d)"
pid=""
cleanup() {
  if [ -n "$pid" ]; then kill -TERM "$pid" 2> /dev/null || true; sleep 3; fi
  pkill -KILL -f "$scratch" 2> /dev/null || true
  chmod -R u+w "$scratch" 2> /dev/null || true
  rm -rf -- "$scratch"
}
trap cleanup EXIT

if [ "${2:-}" = --in-place ]; then app="$dir"
else
  app="$scratch/keel app copy"
  mkdir -p "$app"
  # new file times (as a copy into the app gets) and writable: keel must still write nothing into its own files
  cp -R "$dir/runtime" "$dir/keel" "$app/"
fi
data="$scratch/data" repo="$scratch/my project"
mkdir -p "$repo" "$scratch/logs"
git -C "$repo" init -q -b main
printf '# demo\n' > "$repo/README.md"
git -C "$repo" add -A && git -C "$repo" -c user.name=ci -c user.email=ci@localhost commit -qm init

free() { local p="$1"; while (echo > "/dev/tcp/127.0.0.1/$p") 2> /dev/null; do p=$((p + 1)); done; echo "$p"; }
port="$(free 18180)"; eport="$(free $((port + 1)))"; bport="$(free $((eport + 1)))"
tok="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
# the PATH an app gets from the Dock: no Java, no uv, no Python of keel's; rg from the app, last
path="/usr/bin:/bin:/usr/sbin:/sbin:$app/runtime/bin"
for t in java uv keel-engine; do
  if PATH="$path" command -v "$t" > /dev/null 2>&1 && [ "$t" != keel-engine ]; then echo "  note: $t is on the base PATH ($(PATH="$path" command -v "$t"))"; fi
done

fails=0
ok() { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }

echo "starting keel from $app (port $port)…"
t0=$SECONDS
env -i HOME="$scratch/home" USER="${USER:-keel}" LANG=en_US.UTF-8 PATH="$path" TMPDIR="${TMPDIR:-/tmp}" \
  KEEL_APP="$app/keel" KEEL_DATA="$data" KEEL_WORKSPACE="$repo" KEEL_HOST_WORKSPACE="$repo" KEEL_PROJECT_NAME="my project" \
  KEEL_FOLDERS="$repo" KEEL_PORT="$port" KEEL_ENGINE_PORT="$eport" KEEL_BACK_PORT="$bport" KEEL_INTERNAL_TOKEN="$tok" \
  KEEL_PLUGINS=on KEEL_BIND=127.0.0.1 KEEL_NAME=keel-desktop-test KEEL_DESKTOP=1 \
  KEEL_ENGINE_EXE="$app/runtime/bin/keel-engine" KEEL_FRONT=1 KEEL_FRONT_EXE="$app/runtime/bin/keel-api" \
  KEEL_PYTHON="$app/runtime/python/bin/python3" KEEL_MCP_PYTHON="$app/runtime/python/bin/python3" \
  KEEL_API_JAR="$app/keel/api.jar" KEEL_JAVA="$app/runtime/jre/bin/java" PYTHONDONTWRITEBYTECODE=1 \
  KEEL_FAKEONREALPROJECTS=true \
  /bin/bash "$app/keel/keel-native-start" > "$scratch/logs/keel.log" 2>&1 < /dev/null &
pid=$!
mkdir -p "$scratch/home"

up=0
for _ in $(seq 1 180); do
  h="$(curl -fs "http://127.0.0.1:$port/api/health" 2> /dev/null || true)"
  case "$h" in *'"engine":true'*) up=1; break ;; esac
  kill -0 "$pid" 2> /dev/null || break
  sleep 1
done
if [ "$up" = 1 ]; then ok "healthy after $((SECONDS - t0)) s: $h"; else bad "not healthy"; tail -40 "$scratch/logs/keel.log"; exit 1; fi
case "$h" in *'"native":true'*) ok "it says native" ;; *) bad "health has no native:true" ;; esac

code="$(curl -fs -X POST -H "X-Keel-Token: $tok" "http://127.0.0.1:$port/api/web/code" || true)"
case "$code" in *'"path":"/web/login?code='*) ok "a sign-in code for the window" ;; *) bad "no sign-in code: $code" ;; esac
st="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/api/projects")"
[ "$st" = 401 ] && ok "no sign-in: 401" || bad "no sign-in gave $st"
st="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/")"
[ "$st" = 200 ] && ok "the web page: 200" || bad "the web page gave $st"
st="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$bport/api/health")"
[ "$st" = 200 ] && ok "the Python api in front, the Kotlin api behind it on $bport" || bad "no Kotlin api on the back port ($st)"

key="$(cat "$data/run/web-key" 2> /dev/null || true)"
auth="Authorization: Bearer $key"
pj="$(curl -fs -H "$auth" "http://127.0.0.1:$port/api/projects" || true)"
id="$(printf '%s' "$pj" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])' 2> /dev/null || true)"
if [ -n "$id" ]; then
  ok "the project folder is a keel project"
  curl -fs -X POST "http://127.0.0.1:$port/api/projects/$id/flows" -H "$auth" -H 'content-type: application/json' \
    -d '{"workflow_id":"feature","title":"Scores","request":"Save a score."}' > /dev/null || bad "could not start a flow"
  s=""
  for _ in $(seq 1 90); do
    s="$(curl -fs -H "$auth" "http://127.0.0.1:$port/api/projects/$id/flow" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("thread") or {}).get("status"))' 2> /dev/null || true)"
    [ "$s" = waiting ] && break
    sleep 2
  done
  [ "$s" = waiting ] && ok "a flow on the fake model reached its first gate" || bad "the flow is $s, not waiting at its gate"
  ls "$repo/.keel/specs"/*.md > /dev/null 2>&1 && ok "it wrote its spec into the project" || bad "no spec in the project"
else
  bad "no project: $pj"
fi

n="$(find "$app" -name '*.pyc' -newer "$repo/README.md" 2> /dev/null | wc -l | tr -d ' ')"
[ "$n" = 0 ] && ok "nothing written into the app's files" || { bad "$n new .pyc files in the app"; find "$app" -name '*.pyc' -newer "$repo/README.md" | head -5; }

kill -TERM "$pid"
t1=$SECONDS
for _ in $(seq 1 60); do kill -0 "$pid" 2> /dev/null || break; sleep 0.5; done
if kill -0 "$pid" 2> /dev/null; then bad "still running 30 s after TERM"; else ok "stopped $((SECONDS - t1)) s after TERM"; fi
pid=""
sleep 1
left="$(pgrep -f "$app/(runtime|keel)/" || true)"
[ -z "$left" ] && ok "no process left" || { bad "processes left: $left"; ps -o pid,command -p "$(echo "$left" | tr '\n' ',' | sed 's/,$//')" || true; }

if [ "$fails" = 0 ]; then echo "desktop runtime: all checks passed"; else echo "desktop runtime: $fails check(s) failed"; tail -30 "$scratch/logs/keel.log"; exit 1; fi
