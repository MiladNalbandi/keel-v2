#!/usr/bin/env bash
# Test keel2 add (0.18.64): a folder keel sees already (inside the main project, or inside a folder keel has mounted)
# becomes a project with no restart, at the path keel's container sees it at; a folder keel must mount restarts keel once,
# and keel2 says so first, with what it does to open MCP sessions. A fake `docker` and `curl` on PATH stand in and record
# every call; nothing real runs (no container, no image, no network).
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd -P)"
keel2="$here/keel2"
scratch="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf -- "$scratch"' EXIT
S="$scratch/state"
mkdir -p "$scratch/bin" "$S" "$scratch/config" "$scratch/home" "$scratch/user"
export XDG_CONFIG_HOME="$scratch/config" KEEL2_HOME="$scratch/home" HOME="$scratch/user" KEEL_OPEN=0 NO_COLOR=1 \
  KEEL_NAME=keel-add-test KEEL_VOLUME=keel-add-test-data PORT=39671 KEEL_NATIVE_CLIS=0 \
  FAKE_S="$S" FAKE_CALLS="$scratch/calls" FAKE_POSTS="$scratch/posts"
unset KEEL_IMAGE KEEL_FORCE KEEL_YES KEEL_PLUGINS KEEL_DOCKER KEEL_TAKE_NEW_IMAGE

cat > "$scratch/bin/docker" <<'FAKE'
#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_CALLS"
S="$FAKE_S"
fmt=""; [ "${2:-}" = -f ] && fmt="${3:-}"
case "$1" in
  info|volume|logs|ps|image|pull) exit 0 ;;
  inspect)
    [ -f "$S/exists" ] || exit 1
    case "$fmt" in
      *State.Running*) echo true ;;
      "{{.Config.Image}}") echo ghcr.io/miladnalbandi/keel-v2:test ;;
      *keel2.project*) cat "$S/project" ;;
      *keel2.docker*) echo 0 ;;
      *Config.Env*) printf 'KEEL_FOLDERS=%s\n' "$(cat "$S/folders")" ;;
    esac
    exit 0 ;;
  port) echo "127.0.0.1:$PORT"; exit 0 ;;
  rm) rm -f "$S/exists"; exit 0 ;;
  run)
    case "$*" in *" --name "*) ;; *) exit 0 ;; esac
    for a in "$@"; do case "$a" in KEEL_FOLDERS=*) echo "${a#KEEL_FOLDERS=}" > "$S/folders" ;; esac; done
    touch "$S/exists"; echo c0ffee; exit 0 ;;
  exec) case "$*" in *internal-token*) printf 'faketoken' ;; esac; exit 0 ;;
esac
exit 0
FAKE
cat > "$scratch/bin/curl" <<'FAKE'
#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_CALLS"
body=""; post=0; prev=""
for a in "$@"; do
  [ "$prev" = --data-binary ] && body="$a"
  [ "$prev" = -X ] && [ "$a" = POST ] && post=1
  prev="$a"
done
case "$*" in
  */health*) echo '{"status":"ok"}'; exit 0 ;;
  */jobs*) echo '[]'; exit 0 ;;
  */projects*) if [ "$post" = 1 ]; then echo "$body" >> "$FAKE_POSTS"; echo '{"id":"x"}'; else echo '[]'; fi; exit 0 ;;
esac
exit 22
FAKE
chmod +x "$scratch/bin/docker" "$scratch/bin/curl"

fails=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }
has() { if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"; else fail "$1 (no '$3' in: $2)"; fi; }
hasnt() { if printf '%s' "$2" | grep -qF -- "$3"; then fail "$1 ('$3' in: $2)"; else pass "$1"; fi; }
before() { local a b; a="$(printf '%s\n' "$2" | grep -nF -- "$3" | head -1 | cut -d: -f1)"; b="$(printf '%s\n' "$2" | grep -nF -- "$4" | head -1 | cut -d: -f1)"
  if [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]; then pass "$1"; else fail "$1 ('$3' at ${a:-none}, '$4' at ${b:-none})"; fi; }
repo() { mkdir -p "$1/.git"; }
run_k() { rc=0; out="$(env PATH="$scratch/bin:$PATH" "$keel2" "$@" 2>&1 < /dev/null)" || rc=$?; }
reset() { : > "$FAKE_CALLS"; : > "$FAKE_POSTS"; }

main="$scratch/work/main"; shared="$scratch/work/shared"; outside="$scratch/work/outside"
repo "$main"; repo "$main/libs/pay"; repo "$shared/api"; repo "$outside"
touch "$S/exists"; echo "$main" > "$S/project"; echo "/workspace:$shared" > "$S/folders"
mkdir -p "$XDG_CONFIG_HOME/keel2"; echo "$shared" > "$XDG_CONFIG_HOME/keel2/keel-add-test.folders"

echo "keel2 add: a folder keel sees already"
reset; run_k add "$main/libs/pay"
[ "$rc" = 0 ] && pass "inside the main project: done" || fail "inside the main project: exit $rc ($out)"
has "it says no restart is needed" "$out" "no restart needed"
hasnt "no restart" "$(cat "$FAKE_CALLS")" "docker run"
has "registered where keel's container sees it (/workspace/…)" "$(cat "$FAKE_POSTS")" '"root":"/workspace/libs/pay"'
reset; run_k add "$shared/api"
has "inside a mounted folder: no restart needed" "$out" "no restart needed"
hasnt "inside a mounted folder: no restart" "$(cat "$FAKE_CALLS")" "docker run"
has "registered at its own path" "$(cat "$FAKE_POSTS")" "\"root\":\"$shared/api\""

echo "keel2 add: a folder keel must mount"
reset; run_k add "$outside"
[ "$rc" = 0 ] && pass "a new folder: done" || fail "a new folder: exit $rc ($out)"
has "it says keel restarts" "$out" "keel restarts once now"
has "it says what happens to MCP sessions" "$out" "Open MCP sessions (keel2 mcp) reconnect by themselves"
before "it says so before the restart" "$out" "keel restarts once now" "Starting keel for"
has "keel was restarted" "$(cat "$FAKE_CALLS")" "docker run"
has "the new folder is mounted" "$(cat "$S/folders")" "$outside"

echo "keel2 mcp: the client's folder and time zone go to keel's MCP server"
reset; (cd "$main/libs/pay" && env PATH="$scratch/bin:$PATH" TZ=Europe/Berlin KEEL_MCP_RELAY=0 "$keel2" mcp < /dev/null >/dev/null 2>&1) || true
has "the folder as keel sees it" "$(grep 'docker exec -i' "$FAKE_CALLS")" "KEEL_CLIENT_CWD=/workspace/libs/pay"
has "the time zone" "$(grep 'docker exec -i' "$FAKE_CALLS")" "TZ=Europe/Berlin"
reset; (cd "$scratch" && env PATH="$scratch/bin:$PATH" KEEL_MCP_RELAY=0 "$keel2" mcp < /dev/null >/dev/null 2>&1) || true
has "a folder keel cannot see: empty, so no project is guessed" "$(grep 'docker exec -i' "$FAKE_CALLS")" "KEEL_CLIENT_CWD= "

[ "$fails" = 0 ] || { echo "$fails check(s) failed"; exit 1; }
echo "all keel2 add checks passed"
