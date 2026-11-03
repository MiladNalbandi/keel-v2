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
#   0.21.3 a native release's .minisig is checked; limits Settings asked that docker would refuse are ignored; the
#      start-wanted question says the request came from inside keel; a .git file mounts only a repository that lists
#      the folder; git on this computer runs nothing a project's .git/config names; a linked .keel/archive is refused
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
  info)
    case "$*" in *MemTotal*) echo "${FAKE_MEM:-}" ;; *NCPU*) echo "${FAKE_CPUS:-}" ;; esac
    exit 0 ;;
  update) [ "${FAKE_UPDATE_FAIL:-0}" = 1 ] && { echo "Minimum memoryswap limit" >&2; exit 1; }; exit 0 ;;
  volume|logs) exit 0 ;;
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
      *keel2.docker*) echo "${FAKE_LABEL_DOCKER:-0}" ;;
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
      *"cat /data/limits-wanted"*) printf '%b' "${FAKE_WANTED:-}"; exit 0 ;;
      *"cat /data/start-wanted"*) printf '%b' "${FAKE_START_WANTED:-}"; exit 0 ;;
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

# ------------------------------------------------------------------ 0.20.0: limits
echo "limits: half of what Docker has by default, kept, changed at once"
G=1073741824
limits_reset() { reset; rm -f "$XDG_CONFIG_HOME/keel2/$KEEL_NAME.limits"; seed "$LATEST" sha256:old; }
limits_reset
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 -- update
yes_if "default: half the memory, with swap as much again" called "docker run .*--memory 8g --memory-swap $((16 * G)) "
yes_if "default: half the CPUs" called "docker run .*--cpus 4 "
limits_reset
run_k FAKE_MEM=$((64 * G)) FAKE_CPUS=1 FAKE_PULL_NEW=1 -- update
yes_if "default: at most 8g, at least 2 CPUs" called "docker run .*--memory 8g .*--cpus 2 "
limits_reset
run_k FAKE_MEM=$((2 * G)) FAKE_CPUS=4 FAKE_PULL_NEW=1 -- update
yes_if "default: at least 2g" called "docker run .*--memory 2g "
limits_reset
run_k FAKE_PULL_NEW=1 -- update
no_if "Docker says nothing: no limit is made up" called "docker run .*--memory "
limits_reset
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 KEEL_MEMORY=off KEEL_CPUS=1.5 FAKE_PULL_NEW=1 -- update
no_if "KEEL_MEMORY=off: no memory limit" called "docker run .*--memory "
yes_if "KEEL_CPUS: its own value" called "docker run .*--cpus 1.5 "
limits_reset
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 -- limits --memory 6g --cpus 3
check "limits: succeeds" "$rc" 0
yes_if "limits: changes the running container with no restart" called "docker update --memory 6g --memory-swap $((12 * G)) --cpus 3 $KEEL_NAME"
no_if "limits: nothing is restarted" called "docker run"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 -- update
yes_if "update keeps what limits saved" called "docker run .*--memory 6g .*--cpus 3 "
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 -- limits --cpus 2
yes_if "limits: one value keeps the other" called "docker update --memory 6g --memory-swap $((12 * G)) --cpus 2 $KEEL_NAME"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 -- limits --memory off --cpus off
# docker update ignores --memory 0 and --cpus 0 (seen on a real container): off is all of what Docker has, until a restart
yes_if "limits: off is all of what Docker has" called "docker update --memory $((16 * G)) --memory-swap $((32 * G)) --cpus 8 $KEEL_NAME"
: > "$FAKE_CALLS"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 -- update
yes_if "off is kept: keel starts again" called "docker run "
no_if "off is kept: the default does not come back" called "docker run .*--memory [0-9]"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 -- limits --auto
yes_if "limits --auto: back to half" called "docker update --memory 8g --memory-swap $((16 * G)) --cpus 4 $KEEL_NAME"
for bad in "--memory 6" "--memory lots" "--memory 500m" "--cpus many" "--cpus 1.2.3"; do
  # shellcheck disable=SC2086  # the option and its value
  run_k -- limits $bad
  no_if "limits $bad: refused" test "$rc" = 0
done
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_UPDATE_FAIL=1 -- limits --memory 2g
no_if "Docker refuses: it says so" test "$rc" = 0
has "Docker refuses: why" "$out" "could not change the limits"
# 0.20.1 what Settings › Resources saved in the container: a container cannot change its own limits, keel2 takes it
limits_reset; : > "$FAKE_CALLS"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_WANTED='memory=5g\ncpus=3\n' -- limits
check "Settings: limits succeeds" "$rc" 0
has "Settings: it says so" "$out" "Settings › Resources"
yes_if "Settings: keel2 limits applies it at once" called "docker update --memory 5g --memory-swap $((10 * G)) --cpus 3 $KEEL_NAME"
yes_if "Settings: the wish is taken once" called "docker exec $KEEL_NAME rm -f /data/limits-wanted"
limits_reset; : > "$FAKE_CALLS"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 FAKE_WANTED='memory=5g\n' -- update
yes_if "Settings: an update takes it" called "docker run .*--memory 5g .*--cpus 4 "
limits_reset; : > "$FAKE_CALLS"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 FAKE_WANTED='memory=lots\ncpus=many\n' -- update
check "Settings: a bad value does not stop keel" "$rc" 0
yes_if "Settings: a bad value keeps the default" called "docker run .*--memory 8g .*--cpus 4 "
# 0.21.3 values that passed the check but broke every start: 08g (octal in bash), a size with 20 digits, more CPUs than
# Docker has (docker run refuses it)
for wanted in 'memory=08g\n' 'memory=99999999999999999999g\n' 'cpus=64\n' 'cpus=0.001\n' 'cpus=99999\n'; do
  limits_reset; : > "$FAKE_CALLS"
  run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 FAKE_WANTED="$wanted" -- update
  check "Settings asked ${wanted%\\n}: keel still starts" "$rc" 0
  yes_if "Settings asked ${wanted%\\n}: a container is started" called "docker run .* --name "
  run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 -- update
  check "Settings asked ${wanted%\\n}: the next start works too" "$rc" 0
done
limits_reset; : > "$FAKE_CALLS"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 FAKE_WANTED='memory=08g\n' -- update
yes_if "Settings asked 08g: docker gets 8g's swap" called "docker run .*--memory 08g --memory-swap $((16 * G)) "
limits_reset; : > "$FAKE_CALLS"
run_k FAKE_MEM=$((16 * G)) FAKE_CPUS=8 FAKE_PULL_NEW=1 FAKE_WANTED='cpus=64\n' -- update
no_if "Settings asked 64 CPUs of 8: not passed to docker" called "docker run .*--cpus 64 "
has "...and it says it ignored it" "$out" "ignored"
for bad in "--memory 08" "--memory 99999999999999999999g" "--cpus 0.001" "--cpus 99999"; do
  # shellcheck disable=SC2086  # the option and its value
  run_k -- limits $bad
  no_if "limits $bad: refused" test "$rc" = 0
done
rm -f "$XDG_CONFIG_HOME/keel2/$KEEL_NAME.limits"

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
  # 0.21.3 a release's .minisig is checked with keel's release key before anything is unpacked
  echo "native update: the release's signature"
  kp=(uv run --quiet --project "$here/tools/keel-plugin" keel-plugin)
  if "${kp[@]}" keygen keel --dir "$scratch/keys" >/dev/null 2>&1; then
    pub="$(sed -n 2p "$scratch/keys/keel.pub")"
    sign_release() { KEEL_PUBLISHER_KEY="$(cat "$scratch/keys/keel.key")" "${kp[@]}" sign "$scratch/releases/v$1/keel-v2-$1-native.tar.gz" --key-env KEEL_PUBLISHER_KEY >/dev/null; }
    make_release 98.0.0; sign_release 98.0.0
    echo '{"tag_name": "v98.0.0"}' > "$scratch/api.json"
    run_k KEEL_RELEASES_API="file://$scratch/api.json" KEEL2_RELEASE_KEY="$pub" -- update
    check "a signed release is installed" "$(cat "$ND/current/VERSION")" 98.0.0
    has "...and keel2 says the signature was checked" "$out" "signature checked"
    # a signature of another release's file (a swapped tarball) and a changed tarball are refused
    make_release 98.1.0; sign_release 98.1.0; make_release 98.2.0
    cp "$scratch/releases/v98.1.0/keel-v2-98.1.0-native.tar.gz.minisig" "$scratch/releases/v98.2.0/keel-v2-98.2.0-native.tar.gz.minisig"
    echo '{"tag_name": "v98.2.0"}' > "$scratch/api.json"
    run_k KEEL_RELEASES_API="file://$scratch/api.json" KEEL2_RELEASE_KEY="$pub" -- update
    no_if "a signature of another file: update fails" test "$rc" = 0
    has "...it says why" "$out" "failed the signature check"
    check "...and nothing was installed" "$(cat "$ND/current/VERSION")" 98.0.0
    make_release 98.3.0; sign_release 98.3.0
    tgz3="$scratch/releases/v98.3.0/keel-v2-98.3.0-native.tar.gz"; echo extra >> "$tgz3"
    (cd "$(dirname "$tgz3")" && if command -v sha256sum >/dev/null 2>&1; then sha256sum "$(basename "$tgz3")"; else shasum -a 256 "$(basename "$tgz3")"; fi > "$(basename "$tgz3").sha256")
    echo '{"tag_name": "v98.3.0"}' > "$scratch/api.json"
    run_k KEEL_RELEASES_API="file://$scratch/api.json" KEEL2_RELEASE_KEY="$pub" -- update
    no_if "a changed tarball (its sha256 changed too): update fails" test "$rc" = 0
    check "...and nothing was installed" "$(cat "$ND/current/VERSION")" 98.0.0
    # a release from before signing: a warning, still installed; from RELEASE_SIGNED_SINCE on: refused
    make_release 98.4.0
    echo '{"tag_name": "v98.4.0"}' > "$scratch/api.json"
    run_k KEEL_RELEASES_API="file://$scratch/api.json" KEEL2_RELEASE_KEY="$pub" KEEL2_RELEASE_SIGNED_SINCE=98.4.0 -- update
    no_if "an unsigned release from RELEASE_SIGNED_SINCE on: refused" test "$rc" = 0
    check "...and nothing was installed" "$(cat "$ND/current/VERSION")" 98.0.0
    run_k KEEL_RELEASES_API="file://$scratch/api.json" KEEL2_RELEASE_KEY="$pub" -- update
    check "an unsigned (older) release: still installed" "$(cat "$ND/current/VERSION")" 98.4.0
    has "...with a warning" "$out" "has no signature"
  else
    echo "  skip keel-plugin could not make a key (uv could not get its packages)"
  fi
fi

# ------------------------------------------------------------------ 0.21.2 start remembers its choices
echo "start: keel2 remembers Docker access and the address"
fakebin="$scratch/bin"
opts_file="$XDG_CONFIG_HOME/keel2/$KEEL_NAME.options"
sock="$scratch/docker.sock"
python3 -c "import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])" "$sock"
run_line() { grep 'docker run' "$FAKE_CALLS" | grep -- ' --name ' | tail -1; }
reset; rm -f "$opts_file"
run_k DOCKER_HOST="unix://$sock" -- start --docker --wifi "$project"
check "start --docker --wifi: it starts" "$rc" 0
has "the choices are saved: Docker access" "$(cat "$opts_file" 2>/dev/null)" "docker=1"
has "the choices are saved: the address" "$(cat "$opts_file" 2>/dev/null)" "bind=0.0.0.0"
: > "$FAKE_CALLS"
run_k DOCKER_HOST="unix://$sock" -- start "$project"
has "a start without flags keeps Docker access" "$(run_line)" "$sock:/var/run/docker.sock"
has "a start without flags keeps --wifi" "$(run_line)" "0.0.0.0:"
has "it says Docker access is on and remembered" "$out" "keel2 remembers it"
: > "$FAKE_CALLS"
run_k DOCKER_HOST="unix://$sock" -- start --no-docker --no-wifi "$project"
hasnt "--no-docker turns Docker access off" "$(run_line)" "docker.sock"
has "--no-wifi goes back to this computer only" "$(run_line)" "127.0.0.1:"
has "and the off is saved" "$(cat "$opts_file" 2>/dev/null)" "docker=0"
# a keel started by an older keel2 has no file: its container's labels say what it had
reset; seed "$LATEST" sha256:old; rm -f "$opts_file"
run_k DOCKER_HOST="unix://$sock" FAKE_LABEL_DOCKER=1 -- start "$project"
has "no file yet: Docker access comes from the running keel's label" "$(run_line)" "$sock:/var/run/docker.sock"
has "and is saved from then on" "$(cat "$opts_file" 2>/dev/null)" "docker=1"
# what Settings › Resources asked: an "off" is taken at once, an "on" waits for a terminal to say yes in
: > "$FAKE_CALLS"
run_k DOCKER_HOST="unix://$sock" FAKE_START_WANTED='docker=0\nwifi=1\n' -- restart
hasnt "Settings asked Docker access off: restart takes it" "$(run_line)" "docker.sock"
has "Settings asked the Wi-Fi on: with no terminal to ask in, it waits" "$out" "Run keel2 restart in a terminal to confirm"
has "...and keel stays on this computer only" "$(run_line)" "127.0.0.1:"
no_if "...and the request stays for the next start" called "rm -f /data/start-wanted"
: > "$FAKE_CALLS"
run_k DOCKER_HOST="unix://$sock" FAKE_START_WANTED='docker=1\n' -- restart
hasnt "Settings asked Docker access on: never without a yes on this computer" "$(run_line)" "docker.sock"
has "...it says how to confirm" "$out" "asks to turn Docker access on"

# with a terminal (keel2 asks): the question says the request came from inside keel; an N keeps Docker access off
tty_k() {  # tty_k <answer> <args…>: keel2 on a pseudo terminal, the answer typed at the first [y/N]; sets out
  out="$(PATH="$fakebin:$orig_path" DOCKER_HOST="unix://$sock" FAKE_START_WANTED="${TTY_WANTED:-}" python3 - "$@" <<'PY'
import os, pty, select, sys
answer, args = sys.argv[1], sys.argv[2:]
pid, fd = pty.fork()
if pid == 0:
    os.execvp(args[0], args)
out, sent = b"", False
while True:
    r, _, _ = select.select([fd], [], [], 60)
    if not r:
        break
    try:
        data = os.read(fd, 4096)
    except OSError:
        break
    if not data:
        break
    out += data
    if not sent and b"[y/N]" in out:
        os.write(fd, (answer + "\n").encode()); sent = True
os.waitpid(pid, 0)
sys.stdout.write(out.decode("utf-8", "replace"))
PY
)"
}
: > "$FAKE_CALLS"
TTY_WANTED='docker=1\n' tty_k n "$keel2" restart
has "0.21.3 the question says the request came from inside keel" "$out" "A request from inside keel asks to turn Docker access on"
has "...names where it can come from" "$out" "an MCP admin tool"
has "...and says: not asked for, answer N" "$out" "If you did not ask for this yourself just now, answer N."
hasnt "...an N keeps Docker access off" "$(run_line)" "docker.sock"
: > "$FAKE_CALLS"
TTY_WANTED='wifi=1\n' tty_k n "$keel2" restart
has "the Wi-Fi question says so too" "$out" "A request from inside keel asks to make keel reachable on your Wi-Fi"
has "...an N keeps keel on this computer" "$(run_line)" "127.0.0.1:"

# ------------------------------------------------------------------ 0.21.3 git dirs the container could name
echo "start: a .git file mounts only a repository that is the person's (not one the container named)"
g() { git -c user.name=t -c user.email=t@t -c init.defaultBranch=main "$@" >/dev/null 2>&1; }
gp="$scratch/gproj"; other="$scratch/other"; rm -rf "$gp" "$other" "$scratch/owt"
g init -q "$gp"; g init -q "$other"; echo x > "$other/f"; g -C "$other" add -A; g -C "$other" commit -qm one
g -C "$other" worktree add -q -b owt "$scratch/owt"
gp="$(cd "$gp" && pwd -P)"; other="$(cd "$other" && pwd -P)"
mkdir -p "$gp/evil" "$gp/evil2" "$gp/evil3" "$gp/fake"
printf 'gitdir: %s/.git\n' "$other" > "$gp/evil/.git"                     # a plain repository elsewhere
printf 'gitdir: %s/.git/worktrees/owt\n' "$other" > "$gp/evil2/.git"      # another folder's real worktree
printf 'gitdir: %s/fake\n' "$gp" > "$gp/evil3/.git"                       # a made-up worktree inside the project
printf '%s/.git\n' "$other" > "$gp/fake/commondir"; printf '%s/evil3/.git\n' "$gp" > "$gp/fake/gitdir"
reset; rm -f "$opts_file"
run_k -- start "$gp"
check "a project with planted .git files starts" "$rc" 0
hasnt "a .git file naming another repository: it is not mounted" "$(run_line)" "$other/.git"
has "...it says which, and how to add it on purpose" "$out" "keel2 add \"$other\""
has "...naming the folder" "$out" "$gp/evil/.git names the git folder $other/.git"
has "...also for another folder's worktree" "$out" "$gp/evil2/.git names the git folder"
has "...and for a made-up worktree" "$out" "$gp/evil3/.git names the git folder"
reset
run_k -- start "$scratch/owt"
has "a real worktree (its repository lists it): the repository is mounted" "$(run_line)" "-v $other/.git:$other/.git "
printf 'gitdir: %s/.git\n' "$other" > "$gp/.git.tmp"; rm -rf "$gp/.git.real"; mv "$gp/.git" "$gp/.git.real"; mv "$gp/.git.tmp" "$gp/.git"
reset
run_k -- start "$gp"
hasnt "the project's own .git replaced by a file: the other repository is not mounted" "$(run_line)" "$other/.git"
rm -f "$gp/.git"; mv "$gp/.git.real" "$gp/.git"
mkdir -p "$gp/evil4"; printf 'gitdir: %s/x\033[31m\n' "$other" > "$gp/evil4/.git"; mkdir -p "$other/x$(printf '\033')[31m"
reset
run_k -- start "$gp"
hasnt "a path with control characters is never printed raw" "$out" "$(printf '\033')"

echo "git on this computer never runs what a project's .git/config names"
trap_repo="$scratch/trap"; rm -rf "$trap_repo"; g init -q "$trap_repo"
echo a > "$trap_repo/a.txt"; echo '* filter=evil' > "$trap_repo/.gitattributes"
g -C "$trap_repo" add -A; g -C "$trap_repo" commit -qm one
git -C "$trap_repo" config core.fsmonitor "touch $scratch/pwned-fsmonitor; false"
git -C "$trap_repo" config filter.evil.clean "touch $scratch/pwned-filter; cat"
git -C "$trap_repo" config core.pager "touch $scratch/pwned-pager; cat"
echo b > "$trap_repo/a.txt"
# the trap is real: plain git runs it
git -C "$trap_repo" status --porcelain >/dev/null 2>&1 || true
yes_if "(the trap works with plain git)" test -e "$scratch/pwned-fsmonitor"
rm -f "$scratch"/pwned-*
run_k -- doctor "$trap_repo"
has "doctor still counts the change" "$out" "uncommitted change"
no_if "doctor: core.fsmonitor did not run" test -e "$scratch/pwned-fsmonitor"
no_if "doctor: the filter did not run" test -e "$scratch/pwned-filter"
no_if "doctor: the pager did not run" test -e "$scratch/pwned-pager"
mkdir -p "$trap_repo/.keel" "$scratch/elsewhere"; echo '{"flow":"x"}' > "$trap_repo/.keel/state.json"  # v1's left state file, for state close to archive
ln -s "$scratch/elsewhere" "$trap_repo/.keel/archive"
run_k -- state close "$trap_repo"
no_if "state close: a linked .keel/archive is refused" test "$rc" = 0
yes_if "...and the left state file stays" test -f "$trap_repo/.keel/state.json"
check "...and nothing lands where the link points" "$(ls -A "$scratch/elsewhere")" ""
no_if "keel2 state: git ran nothing the config names" test -e "$scratch/pwned-fsmonitor"
yes_if "every host git call in keel2 goes through hgit" test -z "$(grep -nE '(^|[;&|(]|\$\()[[:space:]]*git -' "$keel2" | grep -vE '^[0-9]+:[[:space:]]*(#|die |warn |info )' || true)"

echo
if [ "$fails" = 0 ]; then echo "all launcher checks passed"; else echo "$fails check(s) failed"; exit 1; fi
