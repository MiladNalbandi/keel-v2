#!/usr/bin/env bash
# Tests keel2's start, restart, update and status with a stub docker and curl (no Docker, no network): which image
# keel2 starts (the :core image for --core, KEEL_IMAGE always wins), that restart and update keep a core keel core,
# and that status says the edition the image was built as (its KEEL_EDITION).
#
#   docker/tests/keel2.test.sh        (a few seconds; prints one line per check, exits 1 when one fails)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KEEL2="${KEEL2:-$ROOT/keel2}"  # KEEL2: another copy to test
WORK="$(mktemp -d "${TMPDIR:-/tmp}/keel2-test.XXXXXX")"
BIN="$WORK/bin"
mkdir -p "$BIN" "$WORK/project/.git"
trap 'rm -rf "$WORK"' EXIT
failures=0

# docker: one container, its state in $KEEL_TEST_DIR/state.json; every call is logged to $KEEL_TEST_DIR/log. An image
# whose tag ends in "core" was built with EDITION=core, any other with EDITION=full (its KEEL_EDITION).
cat > "$BIN/docker" << 'EOF'
#!/usr/bin/env python3
import json, os, sys
t = os.environ["KEEL_TEST_DIR"]
args = sys.argv[1:]
with open(f"{t}/log", "a") as log:
    log.write("docker " + " ".join(args) + "\n")
path = f"{t}/state.json"
st = json.load(open(path)) if os.path.exists(path) else {}
def save():
    json.dump(st, open(path, "w"))
cmd = args[0] if args else ""
if cmd == "info" and "{{.Architecture}}" in args:   # KEEL_TEST_DOCKER_ARCH: the Docker engine's CPU
    print(os.environ.get("KEEL_TEST_DOCKER_ARCH", "")); sys.exit(0)
if cmd in ("info", "pull"):
    sys.exit(0)
if cmd == "image":                       # image inspect <image>: every image is there (KEEL_TEST_IMAGE_ARCH: its CPU)
    if "{{.Architecture}}" in args:
        print(os.environ.get("KEEL_TEST_IMAGE_ARCH", ""))
    sys.exit(0)
if cmd == "ps" and "{{.Names}} {{.Image}}" in args:  # KEEL_TEST_PS: the running containers, "name image" per line
    print(os.environ.get("KEEL_TEST_PS", "")); sys.exit(0)
if cmd == "port":
    print("127.0.0.1:" + st.get("port", "8080")) if st.get("running") else sys.exit(1)
    sys.exit(0)
if cmd == "rm":
    st.clear(); save(); sys.exit(0)
if cmd == "run":
    labels, env, i, port = {}, [], 1, "8080"
    while i < len(args) - 1:
        a = args[i]
        if a == "--label":
            k, v = args[i + 1].split("=", 1); labels[k] = v; i += 2; continue
        if a == "-e":
            env.append(args[i + 1] if "=" in args[i + 1] else args[i + 1] + "=x"); i += 2; continue
        if a == "-p":
            port = args[i + 1].split(":")[1]; i += 2; continue
        if a in ("--name", "--restart", "-v", "--group-add", "--add-host", "--entrypoint"):
            i += 2; continue
        i += 1
    image = args[-1]
    edition = "core" if image.endswith("core") else "full"
    st.update(exists=True, running=True, labels=labels, image=image, port=port, env=[f"KEEL_EDITION={edition}", *env])
    save(); print("c0ffee"); sys.exit(0)
if cmd == "inspect":
    if not st.get("exists"):
        sys.exit(1)
    fmt = args[2] if len(args) > 3 and args[1] == "-f" else ""
    if not fmt:
        print("[{}]"); sys.exit(0)
    if fmt == "{{.State.Running}}":
        print("true" if st.get("running") else "false")
    elif fmt.startswith("{{index .Config.Labels"):
        key = fmt.split('"')[1]
        print(st.get("labels", {}).get(key, ""))
    elif fmt.startswith("{{range .Config.Env}}"):
        print("\n".join(st.get("env", [])))
    elif fmt == "{{.Config.Image}}":
        print(st.get("image", ""))
    sys.exit(0)
sys.exit(0)
EOF

# curl: keel answers its health and has no running job
cat > "$BIN/curl" << 'EOF'
#!/bin/sh
for a in "$@"; do
  case "$a" in
    */api/health) echo '{"status":"ok","version":"0.16.1"}'; exit 0 ;;
    */api/jobs*) echo '[]'; exit 0 ;;
  esac
done
exit 0
EOF
chmod +x "$BIN"/*

export KEEL_TEST_DIR="$WORK" PATH="$BIN:$PATH" KEEL_OPEN=0 PORT=18997 NO_COLOR=1
# keel2 remembers its start options per keel (0.21.2): a test name and its own folders, never this computer's keel
export KEEL_NAME=keel-test-core XDG_CONFIG_HOME="$WORK/config" KEEL2_HOME="$WORK/keel2-home" HOME="$WORK/home"
mkdir -p "$XDG_CONFIG_HOME" "$KEEL2_HOME" "$HOME"
unset KEEL_IMAGE KEEL_PLUGINS KEEL_DOCKER KEEL_BIND

check() {  # check "what" <command…>
  local what="$1"; shift
  if "$@"; then echo "  ok   $what"; else echo "  FAIL $what"; failures=$((failures + 1)); fi
}
reset() { rm -f "$WORK/log" "$WORK/state.json" "$XDG_CONFIG_HOME/keel2/$KEEL_NAME.options"; }
ran_with() { grep '^docker run ' "$WORK/log" | tail -1 | grep -q -- "$1"; }
pulled() { grep -q "^docker pull $1\$" "$WORK/log"; }

echo "keel2 start"
reset
"$KEEL2" start "$WORK/project" > "$WORK/out" 2>&1 || true
check "start: the :latest image, not core" ran_with "keel2.core=0 .* ghcr.io/miladnalbandi/keel-v2:latest$"

reset
"$KEEL2" start --core "$WORK/project" > "$WORK/out" 2>&1 || true
check "start --core: the :core image" ran_with "ghcr.io/miladnalbandi/keel-v2:core$"
check "start --core: the container remembers it (label keel2.core=1)" ran_with "--label keel2.core=1"
check "start --core: it says how to add plugins" grep -q "Control › Plugins" "$WORK/out"

reset
KEEL_IMAGE=keel-v2:mine "$KEEL2" start --core "$WORK/project" > "$WORK/out" 2>&1 || true
check "start --core with KEEL_IMAGE: KEEL_IMAGE wins" ran_with " keel-v2:mine$"

echo "keel2 status"
reset
"$KEEL2" start --core "$WORK/project" > /dev/null 2>&1 || true
"$KEEL2" status > "$WORK/out" 2>&1 || true
check "status: a core image says edition core" grep -q "edition    core (keel's core only" "$WORK/out"
check "status: and its image" grep -q "image      ghcr.io/miladnalbandi/keel-v2:core" "$WORK/out"
reset
"$KEEL2" start "$WORK/project" > /dev/null 2>&1 || true
"$KEEL2" status > "$WORK/out" 2>&1 || true
check "status: a full image says edition full" grep -q "edition    full (every plugin inside)" "$WORK/out"
reset
KEEL_PLUGINS=off "$KEEL2" start "$WORK/project" > /dev/null 2>&1 || true
"$KEEL2" status > "$WORK/out" 2>&1 || true
check "status: a full image with KEEL_PLUGINS=off says so too" grep -q "plugins    off (KEEL_PLUGINS=off)" "$WORK/out"

echo "keel2 restart and update"
reset
"$KEEL2" start --core "$WORK/project" > /dev/null 2>&1 || true
: > "$WORK/log"
"$KEEL2" restart > "$WORK/out" 2>&1 || true
check "restart: a core keel stays core" ran_with "ghcr.io/miladnalbandi/keel-v2:core$"
: > "$WORK/log"
"$KEEL2" update > "$WORK/out" 2>&1 || true
check "update: a core keel gets the newest core image" pulled "ghcr.io/miladnalbandi/keel-v2:core"
check "update: and starts it again" ran_with "ghcr.io/miladnalbandi/keel-v2:core$"
reset
"$KEEL2" start "$WORK/project" > /dev/null 2>&1 || true
: > "$WORK/log"
"$KEEL2" update > "$WORK/out" 2>&1 || true
check "update: a full keel gets :latest" pulled "ghcr.io/miladnalbandi/keel-v2:latest"

echo "keel2 doctor"
reset
KEEL_TEST_DOCKER_ARCH=aarch64 KEEL_TEST_IMAGE_ARCH=amd64 "$KEEL2" doctor > "$WORK/out" 2>&1 || true
check "doctor: an amd64 image on an arm64 Docker is emulation" grep -q "built for amd64, but Docker runs on arm64: it runs under emulation" "$WORK/out"
check "doctor: and how to get the native one" grep -q "docker pull --platform linux/arm64" "$WORK/out"
reset
KEEL_TEST_DOCKER_ARCH=aarch64 KEEL_TEST_IMAGE_ARCH=arm64 KEEL_TEST_PS="keel-test-core ghcr.io/miladnalbandi/keel-v2:latest
keel-e2e ghcr.io/miladnalbandi/keel-v2:0.18.55
db postgres:16" "$KEEL2" doctor > "$WORK/out" 2>&1 || true
check "doctor: aarch64 and arm64 are the same CPU" grep -q "image runs natively (arm64)" "$WORK/out"
check "doctor: no emulation warning then" sh -c "! grep -q emulation '$WORK/out'"
check "doctor: names the other keels, not itself or other containers" grep -q "other keels run on this Docker too: keel-e2e\. " "$WORK/out"

echo "keel2 help"
check "help names start --core" sh -c "'$KEEL2' help | grep -q 'keel2 start --core'"

if [ "$failures" -gt 0 ]; then echo "keel2.test: $failures check(s) failed"; exit 1; fi
echo "keel2.test: all checks passed"
