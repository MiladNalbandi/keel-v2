#!/usr/bin/env bash
# Tests docker/keel-start, keel's supervisor, without Docker, Java or the real engine: small stub scripts stand in for
# the resolver, the engine and the api, and the health urls are file:// urls that the stubs create and remove.
#
#   docker/tests/keel-start.test.sh        (about 20 s; prints one line per check, exits 1 when one fails)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
START="${KEEL_START:-$ROOT/docker/keel-start}"  # KEEL_START: another copy to test
WORK="$(mktemp -d "${TMPDIR:-/tmp}/keel-start-test.XXXXXX")"
BIN="$WORK/bin"
mkdir -p "$BIN"
trap 'rm -rf "$WORK"' EXIT
failures=0

# ---------------------------------------------------------------- stubs (they log to $KEEL_TEST_DIR/log)

# The resolver: writes run/resolved.json and run/env for the set in $KEEL_TEST_DIR/set (a JSON list of
# [name, version]), or with --only <file> for the plugins in that file ($KEEL_TEST_DIR/only.set overrides them).
# It fails with the code in $KEEL_TEST_DIR/resolve.fail when that file exists.
cat > "$BIN/resolve" << 'EOF'
#!/usr/bin/env python3
import json, os, sys
t = os.environ["KEEL_TEST_DIR"]
with open(f"{t}/log", "a") as log:
    log.write(" ".join(["resolve", *sys.argv[1:]]) + "\n")
if os.path.exists(f"{t}/resolve.fail"):
    sys.exit(int(open(f"{t}/resolve.fail").read()))
if sys.argv[1:2] == ["--only"]:
    only = f"{t}/only.set"
    pairs = json.load(open(only)) if os.path.exists(only) else \
        [[p["name"], p["version"]] for p in json.load(open(sys.argv[2]))["plugins"]]
else:
    pairs = json.load(open(f"{t}/set"))
run = os.path.join(os.environ["KEEL_DATA"], "plugins", "run")
os.makedirs(run, exist_ok=True)
plugins = [{"name": n, "version": v, "dir": f"/p/{n}/{v}"} for n, v in pairs]
json.dump({"sdk": 1, "mode": "on", "plugins": plugins, "problems": []}, open(f"{run}/resolved.json", "w"))
with open(f"{run}/env", "w") as env:
    env.write("KEEL_PLUGIN_PATHS='%s'\n" % ":".join(f"/p/{n}/{v}/engine" for n, v in pairs))
    env.write("KEEL_PLUGIN_ADDONS='%s'\n" % ",".join(f"keel_{n}" for n, v in pairs))
    env.write("KEEL_PLUGIN_LOADER_PATH='%s'\n" % ",".join(f"/p/{n}/{v}/api/keel-plugin-{n}.jar" for n, v in pairs))
EOF

# The engine: healthy at once, runs until it is stopped.
cat > "$BIN/engine" << 'EOF'
#!/bin/sh
t="$KEEL_TEST_DIR"
echo "engine start paths=$KEEL_PLUGIN_PATHS addons=$KEEL_PLUGIN_ADDONS port=$KEEL_ENGINE_PORT" >> "$t/log"
trap 'rm -f "$t/engine.up"; echo "engine stop" >> "$t/log"; kill $s 2>/dev/null; exit 143' TERM
touch "$t/engine.up"
sleep 30 & s=$!
wait $s
EOF

# The api: start N does what line N of $KEEL_TEST_DIR/api.plan says:
#   up          healthy, runs until it is stopped (also when there is no line N)
#   up <code>   healthy, then exits with <code> a moment later
#   exit <code> exits with <code> at once, never healthy
#   down        runs, never healthy
cat > "$BIN/api" << 'EOF'
#!/bin/sh
t="$KEEL_TEST_DIR"
n=$(( $(cat "$t/api.count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$t/api.count"
echo "api start $n supervised=$KEEL_SUPERVISED loader=$KEEL_PLUGIN_LOADER_PATH" >> "$t/log"
set -- $(sed -n "${n}p" "$t/api.plan")
trap 'rm -f "$t/api.up"; echo "api stop" >> "$t/log"; kill $s 2>/dev/null; exit 143' TERM
case "${1:-up}" in
  exit) echo "api exit $2" >> "$t/log"; exit "$2" ;;
  up) touch "$t/api.up"
      if [ -n "${2:-}" ]; then sleep 3; rm -f "$t/api.up"; echo "api exit $2" >> "$t/log"; exit "$2"; fi ;;
esac
sleep 30 & s=$!
wait $s
EOF

# keel-start asks a mounted Docker for its version; these tests never talk to a real one.
printf '#!/bin/sh\necho "docker: stub"\n' > "$BIN/docker"
chmod +x "$BIN"/*

# ---------------------------------------------------------------- helpers

# new_case <title>: a fresh KEEL_DATA and log; the plugin set is product 0.1.0-beta.1, the api plan is empty
new_case() {
  echo "· $1"
  T="$(mktemp -d "$WORK/case.XXXXXX")"
  mkdir -p "$T/data/plugins/run" "$T/home"
  : > "$T/log"
  : > "$T/api.plan"
  echo '[["product", "0.1.0-beta.1"]]' > "$T/set"
}

plan() { printf '%s\n' "$@" > "$T/api.plan"; }
last_good() { printf '{"plugins": [%s]}\n' "$1" > "$T/data/plugins/last-good.json"; }

# start_bg [VAR=value ...]: keel-start in the background with the stubs; its pid in $pid, its output in $T/out
start_bg() {
  env HOME="$T/home" KEEL_DATA="$T/data" KEEL_WORKSPACE="$T" KEEL_CONTENT="$T" KEEL_TEST_DIR="$T" \
    PATH="$BIN:$PATH" KEEL_RESOLVE_CMD="$BIN/resolve" KEEL_ENGINE_CMD="$BIN/engine" KEEL_API_CMD="$BIN/api" \
    KEEL_ENGINE_HEALTH_URL="file://$T/engine.up" KEEL_API_HEALTH_URL="file://$T/api.up" \
    KEEL_ENGINE_START_TIMEOUT=5 KEEL_API_START_TIMEOUT=3 KEEL_STOP_TIMEOUT=3 KEEL_START_POLL=0.1 \
    "$@" bash "$START" > "$T/out" 2>&1 &
  pid=$!
}

# wait_exit [seconds]: waits for keel-start to stop; its exit code in $code ("timeout" when it did not stop)
wait_exit() {
  local end=$((SECONDS + ${1:-15}))
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$SECONDS" -ge "$end" ]; then
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      code=timeout
      return
    fi
    sleep 0.1
  done
  code=0
  wait "$pid" || code=$?
}

# wait_file <file> [seconds]
wait_file() {
  local end=$((SECONDS + ${2:-10}))
  until [ -f "$1" ] || [ "$SECONDS" -ge "$end" ]; do sleep 0.1; done
}

# check <what> <command...>: one line ok or FAIL (with keel-start's output and the stubs' log)
check() {
  local what="$1"
  shift
  if "$@"; then
    echo "  ok    $what"
  else
    echo "  FAIL  $what"
    failures=$((failures + 1))
    sed 's/^/        out | /' "$T/out"
    sed 's/^/        log | /' "$T/log"
  fi
}

count() { grep -c -- "$1" "$T/log" || true; }
logs() { grep -q -- "$1" "$T/log"; }
says() { grep -q -- "$1" "$T/out"; }
is() { [ "$1" = "$2" ]; }
missing() { [ ! -e "$1" ]; }

# ---------------------------------------------------------------- cases

new_case "a normal start writes last-good.json; TERM stops both"
start_bg
wait_file "$T/data/plugins/last-good.json"
check "last-good.json has product 0.1.0-beta.1" \
  grep -q '"name": "product"' "$T/data/plugins/last-good.json"
check "... and its version" grep -q '"version": "0.1.0-beta.1"' "$T/data/plugins/last-good.json"
check "the engine gets the plugin env" \
  logs "engine start paths=/p/product/0.1.0-beta.1/engine addons=keel_product port=8090"
check "the api is supervised, with the plugin jar on loader.path" \
  logs "api start 1 supervised=1 loader=/p/product/0.1.0-beta.1/api/keel-plugin-product.jar"
check "the resolver ran once, without --only" is "$(grep -c '^resolve$' "$T/log")" 1
kill -TERM "$pid"
wait_exit
check "keel-start exits 143 on TERM" is "$code" 143
check "TERM stopped the api and the engine" is "$(count 'stop$')" 2

new_case "api exit 75: both stop and start again, plugins resolved again"
plan "up 75" "up 3"
start_bg
wait_exit
check "keel-start exits with the second api's code (3)" is "$code" 3
check "the resolver ran twice, without --only" is "$(grep -c '^resolve$' "$T/log")" 2
check "the engine started twice" is "$(count 'engine start')" 2
check "the api started twice" is "$(count 'api start')" 2
check "the engine was stopped before the second start" logs "engine stop"

new_case "api exit 3 after a healthy start: keel-start exits 3"
plan "up 3"
start_bg
wait_exit
check "exit code 3" is "$code" 3
check "one api start" is "$(count 'api start')" 1
check "the engine was stopped" logs "engine stop"

new_case "api not healthy in time with a new plugin set: start again with --only last-good.json"
last_good ""
plan "down" "up 3"
start_bg
wait_exit
check "exit code 3 (from the second start)" is "$code" 3
check "the second resolve used --only last-good.json" logs "resolve --only $T/data/plugins/last-good.json"
check "the second api start has no plugin jar" logs "api start 2 supervised=1 loader=$"
check "the first api was stopped" is "$(count 'api stop')" 1
check "it says why" says "keel did not start with these plugins"

new_case "api exits at once with a new plugin set (a broken jar): start again with --only last-good.json"
last_good '{"name": "product", "version": "0.0.9"}'
plan "exit 1" "up 3"
start_bg
wait_exit
check "exit code 3 (from the second start)" is "$code" 3
check "the second resolve used --only last-good.json" logs "resolve --only $T/data/plugins/last-good.json"
check "the second api start loads the last good version" \
  logs "api start 2 supervised=1 loader=/p/product/0.0.9/api/keel-plugin-product.jar"

new_case "api exits at once with the last good plugin set: keel-start exits with its code"
last_good '{"name": "product", "version": "0.1.0-beta.1"}'
plan "exit 4"
start_bg
wait_exit
check "exit code 4" is "$code" 4
check "one api start, no --only" is "$(count 'api start'),$(count '--only')" "1,0"

new_case "api exits at once and there is no last-good.json: keel-start exits with its code"
plan "exit 5"
start_bg
wait_exit
check "exit code 5" is "$code" 5
check "one api start" is "$(count 'api start')" 1

new_case "the fallback start fails too: no third start"
last_good '{"name": "product", "version": "0.0.9"}'
echo '[]' > "$T/only.set" # the last good version is gone, so --only gives a set that differs again
plan "exit 1" "exit 2"
start_bg
wait_exit
check "exit code 2 (from the fallback start)" is "$code" 2
check "two api starts" is "$(count 'api start')" 2

new_case "the resolver fails: keel still starts, with no plugins"
echo 2 > "$T/resolve.fail"
echo "KEEL_PLUGIN_PATHS='/stale/engine'" > "$T/data/plugins/run/env"
echo '{"plugins": [{"name": "stale", "version": "1.0.0"}]}' > "$T/data/plugins/run/resolved.json"
plan "up 3"
start_bg
wait_exit
check "exit code 3 (the api ran)" is "$code" 3
check "it says why" says "plugins: the resolver failed (exit 2), so keel starts with no plugins"
check "the engine gets no plugins (not the old run/env)" logs "engine start paths= addons= port=8090"
check "the api gets no plugin jar" logs "api start 1 supervised=1 loader=$"
check "the old resolved.json is gone (the api sees no plugins)" missing "$T/data/plugins/run/resolved.json"
check "no last-good.json from a start without the resolver" missing "$T/data/plugins/last-good.json"

new_case "the engine stops before it is healthy: keel-start exits 1"
start_bg KEEL_ENGINE_CMD=false
wait_exit
check "exit code 1" is "$code" 1
check "it says so" says "engine failed to start"
check "the api never started" is "$(count 'api start')" 0

echo
if [ "$failures" != 0 ]; then
  echo "keel-start: $failures check(s) failed"
  exit 1
fi
echo "keel-start: all checks passed"
