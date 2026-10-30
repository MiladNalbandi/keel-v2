#!/usr/bin/env bash
# Test keel2's start, update, stop, token and native update with a fake `docker` and `curl` (and fake native releases).
# Nothing real runs: no Docker, no network (native releases are file:// URLs). Needs bash, python3; the native part needs uv.
#   1. start: the running keel is removed only when the port is chosen and everything is ready; a failing `docker run`
#      says how to recover (the volume is untouched) and exits non-zero
#   2. update: no pull for a local image, no restart when the image did not change, another image is kept, a pinned version moves to its edition's newest
#   3. native update: no downgrade when GitHub does not answer; a failed download leaves keel running;
#      keel2 start keeps a version the person went back to
#   4. guard_busy: when keel does not say if agents work (403, no token), keel2 asks (or refuses); it does not guess "none"
#   5. token: no hang on a stdin that never ends; a token on the command line gets a warning; `-` reads stdin
#   6. docker stop and the container's stop timeout match keel-start's KEEL_STOP_TIMEOUT (20 s) plus a grace
# KEEL2_BIN=<file> runs another copy of keel2 (to see this test fail on the old one).
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd -P)"
keel2="${KEEL2_BIN:-$here/keel2}"
scratch="$(mktemp -d)"
listeners=""
cleanup() { [ -z "$listeners" ] || kill "$listeners" 2>/dev/null || true; rm -rf -- "$scratch"; }
trap cleanup EXIT

S="$scratch/state"
project="$scratch/project"
mkdir -p "$scratch/bin" "$S/img" "$project/.git" "$scratch/config" "$scratch/home" "$scratch/user"
export XDG_CONFIG_HOME="$scratch/config" KEEL2_HOME="$scratch/home" HOME="$scratch/user"
export KEEL_OPEN=0 NO_COLOR=1 KEEL_NATIVE_CLIS=0 KEEL_NAME=keel-test-launcher KEEL_VOLUME=test-launcher-data
export PORT=39650 FAKE_S="$S" FAKE_CALLS="$scratch/calls" FAKE_BODY="$scratch/body"
unset KEEL_IMAGE KEEL_FORCE KEEL_YES KEEL_PLUGINS KEEL_DOCKER KEEL_TAKE_NEW_IMAGE KEEL_STOP_TIMEOUT KEEL_HOME   # KEEL_HOME: ignored since 0.4.1, unset so keel2 prints no warning about it

cat > "$scratch/bin/docker" <<'FAKE'
#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_CALLS"
S="$FAKE_S"
fmt=""; [ "${2:-}" = -f ] && fmt="${3:-}"
imgfile() { echo "$S/img/$(printf '%s' "$1" | tr '/:' '__')"; }
case "$1" in
  info|volume|logs) exit 0 ;;
  ps) exit 0 ;;
  image)  # image inspect [-f fmt] <ref>
    shift; shift; f=""; [ "${1:-}" = -f ] && { f=1; shift 2; }
    [ -f "$(imgfile "$1")" ] || exit 1
    [ -z "$f" ] || cat "$(imgfile "$1")"
    exit 0 ;;
  pull)
    [ "${FAKE_PULL_FAIL:-0}" = 1 ] && { echo "pull access denied" >&2; exit 1; }
    if [ "${FAKE_PULL_NEW:-0}" = 1 ] || [ ! -f "$(imgfile "$2")" ]; then echo "sha256:new-$$-$RANDOM" > "$(imgfile "$2")"; fi
    exit 0 ;;
  inspect)
    [ -f "$S/exists" ] || exit 1
    case "$fmt" in
      *State.Running*) cat "$S/running" ;;
      "{{.Image}}") cat "$S/imgid" ;;
      "{{.Config.Image}}") cat "$S/cfgimage" ;;
      *keel2.project*) cat "$S/project" ;;
      *keel2.docker*) echo 0 ;;
    esac
    exit 0 ;;
  port) [ "$(cat "$S/running" 2>/dev/null)" = true ] && echo "127.0.0.1:${FAKE_RUNNING_PORT:-$PORT}"; exit 0 ;;
  stop) echo false > "$S/running"; exit 0 ;;
  start) echo true > "$S/running"; exit 0 ;;
  rm) rm -f "$S/exists" "$S/running" "$S/imgid" "$S/cfgimage" "$S/project"; exit 0 ;;
  run)
    [ "${FAKE_RUN_FAIL:-0}" = 1 ] && { echo "Bind for 127.0.0.1:$PORT failed: port is already allocated" >&2; exit 125; }
    img="${!#}"; proj=""
    for a in "$@"; do case "$a" in keel2.project=*) proj="${a#keel2.project=}" ;; esac; done
    case "$*" in *" --name "*) ;; *) exit 0 ;; esac   # a helper run (stat of the socket), not keel's container
    touch "$S/exists"; echo true > "$S/running"; echo "$img" > "$S/cfgimage"; echo "$proj" > "$S/project"
    cat "$(imgfile "$img")" > "$S/imgid"; echo c0ffee; exit 0 ;;
  exec)
    case "$*" in
      *internal-token*) [ "${FAKE_NO_TOKEN:-0}" = 1 ] && exit 1; printf 'faketoken' ;;
      *" python3 "*) shift; while [ "$1" != python3 ]; do shift; done; shift; exec python3 "$@" ;;
    esac
    exit 0 ;;
esac
exit 0
FAKE
cat > "$scratch/bin/curl" <<'FAKE'
#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_CALLS"
for a in "$@"; do   # keel2 gives the token header from a pipe/file (-H @file): an empty token is a 403
  case "$a" in @*) if [ "$(cat "${a#@}" 2>/dev/null)" = "X-Keel-Token: " ]; then exit 22; fi ;; esac
done
case "$*" in *--data-binary*) cat > "$FAKE_BODY" ;; esac
case "$*" in
  */health*) echo '{"status":"ok"}'; exit 0 ;;
  */jobs*)
    [ "${FAKE_JOBS_FAIL:-0}" = 1 ] && exit 22
    if [ "${FAKE_BUSY:-0}" = 1 ]; then echo '[{"agent":"coder"},{"agent":"reviewer"}]'; else echo '[]'; fi; exit 0 ;;
  */secrets/*) printf '{"hint":"…XYZ"}'; case "$*" in *http_code*) printf '\n200' ;; esac; exit 0 ;;
esac
exit 22
FAKE
chmod +x "$scratch/bin/docker" "$scratch/bin/curl"

fails=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }
check() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got '$2', want '$3')"; fi; }
called() { grep -q -- "$1" "$FAKE_CALLS"; }
yes_if() { if "${@:2}"; then pass "$1"; else fail "$1"; fi; }   # yes_if "what" <command…>
no_if() { if "${@:2}"; then fail "$1"; else pass "$1"; fi; }
has() { if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"; else fail "$1 (no '$3' in: $2)"; fi; }
hasnt() { if printf '%s' "$2" | grep -qF -- "$3"; then fail "$1 ('$3' in: $2)"; else pass "$1"; fi; }
exists() { [ -f "$S/exists" ]; }
newimg() { echo "$2" > "$S/img/$(printf '%s' "$1" | tr '/:' '__')"; }
seed() {  # seed <image ref> <image id>: keel runs now, from that image
  newimg "$1" "$2"; touch "$S/exists"; echo true > "$S/running"; echo "$1" > "$S/cfgimage"; echo "$2" > "$S/imgid"; echo "$project" > "$S/project"
}
reset() { rm -f "$S"/exists "$S"/running "$S"/imgid "$S"/cfgimage "$S"/project "$S"/img/*; : > "$FAKE_CALLS"; }
LATEST=ghcr.io/miladnalbandi/keel-v2:latest
orig_path="$PATH"
fakebin="$scratch/bin"   # the fake docker and curl; the native part uses the real curl (file:// releases): fakebin is empty there
run_k() {  # run_k <env…> -- <args…>: keel2 with the fakes first on PATH; sets rc and out
  local envs=(); while [ "$1" != -- ]; do envs+=("$1"); shift; done; shift
  rc=0; out="$(env PATH="${fakebin:+$fakebin:}$orig_path" ${envs[@]+"${envs[@]}"} "$keel2" "$@" 2>&1 < /dev/null)" || rc=$?
}

# ------------------------------------------------------------------ 1 + 6: start, stop timeout
echo "start: the running keel is kept until the new one can start"
reset; seed "$LATEST" sha256:old; newimg "$LATEST" sha256:old
# all of 39700..39720 are busy, so no port can be picked; the running keel is on another port
python3 - <<'PY' &
import socket, time
socks = []
for p in range(39700, 39721):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", p)); s.listen(1); socks.append(s)
time.sleep(120)
PY
listeners=$!
sleep 1
run_k PORT=39700 FAKE_RUNNING_PORT=39800 -- start "$project"
no_if "no free port: start fails" test "$rc" = 0
has "no free port: it says so" "$out" "No free port"
yes_if "no free port: the running keel is still there" exists
no_if "no free port: it was not stopped or removed" called "docker rm"
kill "$listeners" 2>/dev/null || true; listeners=""

reset; seed "$LATEST" sha256:old
run_k FAKE_RUN_FAIL=1 -- start "$project"
no_if "docker run fails: start exits non-zero" test "$rc" = 0
has "docker run fails: the usual message stays" "$out" "Docker could not start the container."
has "docker run fails: it says the data is safe" "$out" "untouched"
has "docker run fails: it says how to start again" "$out" "keel2 start"

echo "start/stop: docker waits as long as keel-start does (KEEL_STOP_TIMEOUT 20 s + 5)"
reset; seed "$LATEST" sha256:old
run_k -- start "$project"
check "start replaces the keel" "$rc" 0
yes_if "stop -t 25 before the removal" called "docker stop -t 25 $KEEL_NAME"
yes_if "host.docker.internal reaches this computer (local models on Linux)" called "--add-host host.docker.internal:host-gateway"
yes_if "the new container has --stop-timeout 25" called "docker run .*--stop-timeout 25"
reset; seed "$LATEST" sha256:old
run_k KEEL_STOP_TIMEOUT=40 -- stop
yes_if "KEEL_STOP_TIMEOUT=40 gives docker stop -t 45" called "docker stop -t 45 $KEEL_NAME"
no_if "stop removes the container" exists

# ------------------------------------------------------------------ 2: update
echo "update: local image, same image, other image"
reset; seed keel-v2:dev sha256:dev1
run_k KEEL_IMAGE=keel-v2:dev -- update
check "local image: update succeeds" "$rc" 0
no_if "local image: nothing is pulled" called "docker pull"
has "local image: it says why" "$out" "local image"
no_if "local image, unchanged: no restart" called "docker run"
reset; seed keel-v2:dev sha256:dev1; newimg keel-v2:dev sha256:dev2
run_k KEEL_IMAGE=keel-v2:dev -- update
no_if "local image: nothing is pulled (rebuilt)" called "docker pull"
yes_if "local image rebuilt: keel restarts on the new build" called "docker run .*keel-v2:dev$"
reset; seed keel-v2:dev sha256:dev1
run_k KEEL_IMAGE=keel-v2:dev KEEL_TAKE_NEW_IMAGE=1 FAKE_PULL_FAIL=1 -- update
has "KEEL_TAKE_NEW_IMAGE=1 pulls a local tag anyway" "$out" "Could not download"
reset; seed "$LATEST" sha256:same
run_k -- update
check "same image: update succeeds" "$rc" 0
yes_if "same image: it is pulled" called "docker pull $LATEST"
no_if "same image: keel is not restarted" called "docker run"
no_if "same image: keel is not removed" called "docker rm"
has "same image: it says so" "$out" "newest image already"
reset; seed "$LATEST" sha256:old
run_k FAKE_PULL_NEW=1 -- update
yes_if "new image: keel restarts" called "docker run .*$LATEST$"
reset; seed ghcr.io/miladnalbandi/keel-v2-product:1.2 sha256:prod
run_k -- update
no_if "other image: :latest is not pulled" called "docker pull $LATEST"
yes_if "other image: its own image is pulled" called "docker pull ghcr.io/miladnalbandi/keel-v2-product:1.2"
# 0.19.2 a pinned version of keel's own image moves to its edition's newest release (the same tag again never updated)
R=ghcr.io/miladnalbandi/keel-v2
for pin in "0.18.74-product-beta:product-beta" "0.18.68:latest" "0.18:latest" "0.18.74-core:core"; do
  reset; seed "$R:${pin%:*}" sha256:old
  run_k -- update
  yes_if "pinned :${pin%:*}: the newest :${pin##*:} is pulled" called "docker pull $R:${pin##*:}$"
  no_if "pinned :${pin%:*}: the pinned tag is not pulled again" called "docker pull $R:${pin%:*}$"
  yes_if "pinned :${pin%:*}: keel restarts on :${pin##*:}" called "docker run .*$R:${pin##*:}$"
done
reset; seed "$R:product-beta" sha256:old
run_k -- update
yes_if "product-beta stays product-beta" called "docker pull $R:product-beta$"
reset; seed "$R:0.18.74-product-beta" sha256:old
run_k KEEL_IMAGE="$R:0.18.74-product-beta" -- update
yes_if "KEEL_IMAGE pins a version on purpose" called "docker pull $R:0.18.74-product-beta$"
reset; seed "$R:stage-abc-full-amd64" sha256:old
run_k -- update
yes_if "a staging tag is kept" called "docker pull $R:stage-abc-full-amd64$"

# ------------------------------------------------------------------ 4: guard_busy
echo "guard_busy: an unknown answer is not 'no agents'"
reset; seed "$LATEST" sha256:old
run_k FAKE_JOBS_FAIL=1 -- stop
no_if "403 from jobs: stop is refused" test "$rc" = 0
yes_if "403 from jobs: keel keeps running" exists
has "403 from jobs: it says it cannot tell" "$out" "cannot tell"
reset; seed "$LATEST" sha256:old
run_k FAKE_NO_TOKEN=1 -- stop
no_if "no token (403): stop is refused" test "$rc" = 0
yes_if "no token: keel keeps running" exists
reset; seed "$LATEST" sha256:old
run_k FAKE_JOBS_FAIL=1 -- stop --force
check "unknown + --force: stop works" "$rc" 0
reset; seed "$LATEST" sha256:old
run_k FAKE_JOBS_FAIL=1 KEEL_YES=1 -- stop
check "unknown + KEEL_YES=1: stop works" "$rc" 0
reset; seed "$LATEST" sha256:old
run_k FAKE_BUSY=1 -- stop
no_if "agents work: stop is refused" test "$rc" = 0
yes_if "agents work: keel keeps running" exists
reset; seed "$LATEST" sha256:old
run_k -- stop
check "no agents: stop works without a question" "$rc" 0

# ------------------------------------------------------------------ 5: token
echo "token: no hang, a warning for a value on the command line"
deadline() {  # deadline <seconds> <bash command…>: output in $out; rc 124 when it did not end in time
  local s="$1"; shift
  rc=0; out="$(python3 -c '
import subprocess, sys, os, signal, tempfile
# output goes to a file, not a pipe: a background helper (the stdin that never ends) must not hold the harness
with tempfile.TemporaryFile("w+") as f:
    p = subprocess.Popen(["bash", "-c", sys.argv[2]], stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        p.wait(timeout=float(sys.argv[1]))
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = 124
        f.write("TIMEOUT\n")
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    f.seek(0); print(f.read(), end="")
    sys.exit(rc)
' "$s" "$*")" || rc=$?
}
reset; seed "$LATEST" sha256:old
export PATH="$scratch/bin:$orig_path"
deadline 20 "KEEL_TOKEN_STDIN_TIMEOUT=2 '$keel2' token claude < <(sleep 60 2>/dev/null)"
no_if "a stdin that never ends does not hang" test "$rc" = 124
has "a stdin that never ends: it says nothing arrived" "$out" "Nothing arrived"
rm -f "$FAKE_BODY"
deadline 20 "printf 'sk-ant-oat01-test_ABC\n' | '$keel2' token claude"
check "a pipe still saves the token" "$rc" 0
has "a pipe: saved" "$out" "saved CLAUDE_CODE_OAUTH_TOKEN"
rm -f "$FAKE_BODY"
deadline 20 "printf 'sk-ant-oat01-test_DEF\n' | '$keel2' token claude -"
check "'-' reads stdin" "$rc" 0
has "'-' : saved" "$out" "saved CLAUDE_CODE_OAUTH_TOKEN"
deadline 20 "'$keel2' token claude sk-ant-oat01-test_GHI < /dev/null"
check "a token on the command line is still saved" "$rc" 0
has "a token on the command line: warning" "$out" "shows in 'ps'"
deadline 20 "printf 'sk-ant-oat01-test_JKL\n' | '$keel2' token claude"
hasnt "a pipe gets no command-line warning" "$out" "shows in 'ps'"

# ------------------------------------------------------------------ 3: native update
echo "native update: no downgrade, download before stop, rollback is kept"
if ! command -v uv >/dev/null 2>&1; then
  echo "  skip uv is not installed (the native checks need it)"
else
  fakebin=""; export PATH="$orig_path"
  script_v="$(sed -n 's/^KEEL2_VERSION="\(.*\)"/\1/p' "$keel2" | head -1)"
  export KEEL_NAME=keel-test-launcher-nat KEEL_RELEASE_URL="file://$scratch/releases" KEEL_YES=1 KEEL_FORCE=1
  mkdir -p "$scratch/releases"
  ND="$KEEL2_HOME/$KEEL_NAME"
  make_release() {
    local v="$1" st="$scratch/build/keel-v2-$1" out="$scratch/releases/v$1" name="keel-v2-$1-native.tar.gz"
    mkdir -p "$st/engine" "$st/content" "$out"
    printf 'fake jar %s\n' "$v" > "$st/api.jar"; printf '%s\n' "$v" > "$st/VERSION"
    printf '#!/bin/sh\nexit 0\n' > "$st/keel-native-start"; chmod +x "$st/keel-native-start"
    printf '[project]\nname = "fake-engine"\nversion = "%s"\nrequires-python = ">=3.8"\ndependencies = []\n\n[tool.uv]\npackage = false\n' "$v" > "$st/engine/pyproject.toml"
    (cd "$st/engine" && uv lock -q --offline)
    tar -C "$scratch/build" -czf "$out/$name" "keel-v2-$v"
    if command -v sha256sum >/dev/null 2>&1; then (cd "$out" && sha256sum "$name" > "$name.sha256"); else (cd "$out" && shasum -a 256 "$name" > "$name.sha256"); fi
  }
  make_release 99.0.0; make_release "$script_v"
  mkdir -p "$ND" "$scratch/project2"; git -C "$scratch/project2" init -q; echo native > "$ND/mode"; printf '%s\n' "$scratch/project2" > "$ND/project"
  # an installed release newer than this script, and a GitHub that does not answer
  echo '{"tag_name": "v99.0.0"}' > "$scratch/api.json"
  run_k KEEL_RELEASES_API="file://$scratch/api.json" -- update
  check "installed 99.0.0" "$(cat "$ND/current/VERSION" 2>/dev/null || true)" 99.0.0
  run_k KEEL_RELEASES_API="file://$scratch/missing.json" -- update
  check "GitHub does not answer: no downgrade to the script's version" "$(cat "$ND/current/VERSION")" 99.0.0
  # a failed download must not stop a running keel
  printf '#!/bin/bash\nsleep 300\n' > "$scratch/keel-native-start"; chmod +x "$scratch/keel-native-start"
  "$scratch/keel-native-start" & fakepid=$!
  mkdir -p "$ND/run"; echo "$fakepid" > "$ND/run/keel.pid"
  echo '{"tag_name": "v99.5.0"}' > "$scratch/api.json"   # no such release: the download fails
  run_k KEEL_RELEASES_API="file://$scratch/api.json" -- update
  no_if "download fails: update exits non-zero" test "$rc" = 0
  yes_if "download fails: keel is still running" kill -0 "$fakepid"
  kill "$fakepid" 2>/dev/null || true; rm -f "$ND/run/keel.pid"
  # a rollback is kept by keel2 start: the script ($script_v) is older than 99.x but newer than a rolled-back 0.0.1
  make_release 0.0.1
  echo '{"tag_name": "v0.0.1"}' > "$scratch/api.json"
  rm -rf "$ND/app" "$ND/current" "$ND/previous"
  run_k KEEL_RELEASES_API="file://$scratch/api.json" -- update   # installs 0.0.1
  ln -sfn "$ND/app/0.0.1" "$ND/current"
  mkdir -p "$scratch/fakejava"; printf '#!/bin/sh\necho "openjdk version \\"21.0.1\\"" >&2\n' > "$scratch/fakejava/java"; chmod +x "$scratch/fakejava/java"
  # (not --force for the rollback itself: nothing runs)
  : > "$ND/rolled-back"
  run_k PATH="$scratch/fakejava:$orig_path" -- start --native "$scratch/project2"
  check "after a rollback keel2 start keeps the version" "$(cat "$ND/current/VERSION")" 0.0.1
  rm -f "$ND/rolled-back" "$ND/run/keel.pid"
  run_k PATH="$scratch/fakejava:$orig_path" -- start --native "$scratch/project2"
  check "without a rollback keel2 start follows the script" "$(cat "$ND/current/VERSION")" "$script_v" || true
  [ "$(cat "$ND/current/VERSION")" = "$script_v" ] || printf '%s\n' "$out" | tail -8
fi

echo
if [ "$fails" = 0 ]; then echo "all launcher checks passed"; else echo "$fails check(s) failed"; exit 1; fi
