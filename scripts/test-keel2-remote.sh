#!/usr/bin/env bash
# Test keel2's phone access with fakes only: `docker`, `curl`, `tailscale`, `cloudflared`, `ip`, `hostname`, `ipconfig`
# and `uname` (to walk the Linux and the macOS way on any computer). Nothing real runs: no Docker, no network, no
# Tailscale serve, no tunnel.
#   1. start: 127.0.0.1 by default; --wifi / KEEL_BIND publish on the network, print the Wi-Fi URL and the plain-http
#      warning, tell keel its address (PUT /api/devices/addresses); restart keeps it; a start without --wifi drops it
#   2. the Wi-Fi address: `ip -4 route get` (Linux), `hostname -I` when that has none, `ipconfig getifaddr` (macOS)
#   3. share tailscale: missing / logged out says so; `tailscale serve --bg --https=443`, or keel's own port when
#      another serve of yours has 443; share off turns off only keel's https port
#   4. share tunnel: asks first (KEEL_YES=1 skips it); a host cloudflared runs in the background, else the pinned docker
#      image (--network host on Linux, host.docker.internal on macOS); the trycloudflare URL is printed and sent to keel;
#      share off stops it; share shows the status
# KEEL2_BIN=<file> runs another copy of keel2.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd -P)"
keel2="${KEEL2_BIN:-$here/keel2}"
scratch="$(mktemp -d)"
cleanup() {
  # a fake cloudflared a failed check left running (only ours: its pid file is in the scratch folder)
  local p; for p in "$scratch"/config/keel2/*.share/tunnel.pid; do [ -f "$p" ] && kill "$(cat "$p")" 2>/dev/null; done
  rm -rf -- "$scratch"
}
trap cleanup EXIT

S="$scratch/state"
project="$scratch/project"
mkdir -p "$scratch/bin" "$S" "$project/.git" "$scratch/config" "$scratch/home" "$scratch/user"
export XDG_CONFIG_HOME="$scratch/config" KEEL2_HOME="$scratch/home" HOME="$scratch/user"
export KEEL_OPEN=0 NO_COLOR=1 KEEL_NAME=keel-test-remote KEEL_VOLUME=test-remote-data KEEL_TUNNEL_WAIT=5
export PORT=39740 FAKE_S="$S" FAKE_CALLS="$scratch/calls"
# never a real tailscale or cloudflared, even when this computer has them: keel2 runs the ones these name
export KEEL_TAILSCALE_BIN="$scratch/bin/tailscale" KEEL_CLOUDFLARED_BIN="$scratch/bin/cloudflared"
unset KEEL_IMAGE KEEL_FORCE KEEL_YES KEEL_PLUGINS KEEL_DOCKER KEEL_BIND KEEL_LAN_IP KEEL_CLOUDFLARED_IMAGE KEEL_HOME   # KEEL_HOME: ignored since 0.4.1, unset so keel2 prints no warning about it

cat > "$scratch/bin/docker" <<'FAKE'
#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_CALLS"
S="$FAKE_S"
last="${!#}"
case "$1" in
  info|volume|ps|pull|stop|start) exit 0 ;;
  image) exit 0 ;;
  logs) [ -f "$S/tunnel" ] && [ "$last" = "$(cat "$S/tunnel")" ] && echo "INF |  https://fake-from-docker.trycloudflare.com  |"; exit 0 ;;
  inspect)
    if [ -f "$S/tunnel" ] && [ "$last" = "$(cat "$S/tunnel")" ]; then echo true; exit 0; fi
    [ -f "$S/exists" ] || exit 1
    case "${3:-}" in
      *State.Running*) echo true ;;
      *keel2.project*) cat "$S/project" ;;
      *keel2.bind*) cat "$S/bind" ;;
      *keel2.hosts*) cat "$S/hosts" 2>/dev/null || true ;;
      *keel2.docker*) echo 0 ;;
      "{{.Config.Image}}") echo ghcr.io/miladnalbandi/keel-v2:latest ;;
    esac
    exit 0 ;;
  port) [ -f "$S/exists" ] && echo "127.0.0.1:$PORT"; exit 0 ;;
  rm)
    if [ -f "$S/tunnel" ] && [ "$last" = "$(cat "$S/tunnel")" ]; then rm -f "$S/tunnel"; exit 0; fi
    case "$last" in *-tunnel) exit 0 ;; esac
    rm -f "$S/exists" "$S/project" "$S/bind" "$S/hosts"; exit 0 ;;
  run)
    name=""; prev=""; bind=""; proj=""; hosts=""
    for a in "$@"; do
      [ "$prev" = --name ] && name="$a"
      case "$a" in keel2.project=*) proj="${a#keel2.project=}" ;; keel2.bind=*) bind="${a#keel2.bind=}" ;; keel2.hosts=*) hosts="${a#keel2.hosts=}" ;; esac
      prev="$a"
    done
    case "$name" in
      "") exit 0 ;;
      *-tunnel) echo "$name" > "$S/tunnel"; echo c0ffee; exit 0 ;;
    esac
    touch "$S/exists"; echo "$proj" > "$S/project"; echo "$bind" > "$S/bind"; echo "$hosts" > "$S/hosts"; echo c0ffee; exit 0 ;;
  exec)
    case "$*" in
      *internal-token*) printf 'faketoken' ;;
      *" python3 "*) shift; while [ "$1" != python3 ]; do shift; done; shift; exec python3 "$@" ;;
    esac
    exit 0 ;;
esac
exit 0
FAKE
cat > "$scratch/bin/curl" <<'FAKE'
#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_CALLS"
case "$*" in
  */health*) echo '{"ok":true}'; exit 0 ;;
  */jobs*) echo '[]'; exit 0 ;;
  *"-X PUT"*/devices/addresses*) cat > "$FAKE_S/addresses"; echo '{}'; exit 0 ;;
esac
exit 22
FAKE
cat > "$scratch/bin/tailscale" <<'FAKE'
#!/usr/bin/env bash
echo "tailscale $*" >> "$FAKE_CALLS"
case "$*" in
  "status --json")
    [ "${FAKE_TS_DOWN:-0}" = 1 ] && { echo '{"BackendState":"NeedsLogin","Self":{"DNSName":""}}'; exit 0; }
    printf '{\n  "BackendState": "Running",\n  "Self": {\n    "DNSName": "mac.tail1234.ts.net."\n  },\n  "Peer": {"x": {"DNSName": "phone.tail1234.ts.net."}}\n}\n' ;;
  "serve status --json") printf '%s\n' "${FAKE_TS_SERVE:-{\}}" ;;
esac
exit 0
FAKE
cat > "$scratch/bin/cloudflared" <<'FAKE'
#!/usr/bin/env bash
echo "cloudflared $*" >> "$FAKE_CALLS"
echo "2026-10-23T10:00:00Z INF Requesting new quick Tunnel on trycloudflare.com..." >&2
echo "2026-10-23T10:00:01Z INF |  https://fake-host-tunnel.trycloudflare.com  |" >&2
trap 'exit 0' TERM
while :; do sleep 1; done
FAKE
cat > "$scratch/bin/uname" <<'FAKE'
#!/usr/bin/env bash
echo "${FAKE_OS:-Linux}"
FAKE
cat > "$scratch/bin/ip" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  "-4 route get 1.1.1.1")
    [ "${FAKE_NO_ROUTE:-0}" = 1 ] && exit 2
    echo "1.1.1.1 via 192.168.1.1 dev wlp3s0 src 192.168.1.20 uid 1000"; echo "    cache" ;;
  "-4 -o addr show scope global")
    [ "${FAKE_NO_ADDR:-0}" = 1 ] && exit 1
    echo "2: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\       valid_lft forever preferred_lft forever"
    echo "3: wlp3s0    inet 10.1.2.3/24 brd 10.1.2.255 scope global dynamic wlp3s0\       valid_lft 3600sec preferred_lft 3600sec" ;;
esac
FAKE
cat > "$scratch/bin/hostname" <<'FAKE'
#!/usr/bin/env bash
[ "${1:-}" = -I ] && echo "172.17.0.1 10.0.0.5 fd00::1 "
[ "${1:-}" = -s ] && echo "Server"
FAKE
cat > "$scratch/bin/ipconfig" <<'FAKE'
#!/usr/bin/env bash
[ "$1 $2" = "getifaddr en0" ] && { echo 192.168.1.20; exit 0; }
exit 1
FAKE
chmod +x "$scratch"/bin/*

fails=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }
check() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got '$2', want '$3')"; fi; }
called() { grep -q -- "$1" "$FAKE_CALLS"; }
yes_if() { if "${@:2}"; then pass "$1"; else fail "$1"; fi; }
no_if() { if "${@:2}"; then fail "$1"; else pass "$1"; fi; }
has() { if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"; else fail "$1 (no '$3' in: $2)"; fi; }
hasnt() { if printf '%s' "$2" | grep -qF -- "$3"; then fail "$1 ('$3' in: $2)"; else pass "$1"; fi; }
sent() { cat "$S/addresses" 2>/dev/null || true; }
reset() { rm -f "$S"/* "$XDG_CONFIG_HOME/keel2/$KEEL_NAME.options"; : > "$FAKE_CALLS"; }  # a fresh keel: nothing remembered
orig_path="$PATH"
fakebin="$scratch/bin"
run_k() {  # run_k <env…> -- <args…>: keel2 with the fakes first on PATH; sets rc and out
  local envs=(); while [ "$1" != -- ]; do envs+=("$1"); shift; done; shift
  rc=0; out="$(env PATH="$fakebin:$orig_path" ${envs[@]+"${envs[@]}"} "$keel2" "$@" 2>&1 < /dev/null)" || rc=$?
}

# ------------------------------------------------------------------ 1: start --wifi
echo "start: this computer only, unless --wifi"
reset
run_k -- start "$project"
check "plain start works" "$rc" 0
yes_if "plain start publishes on 127.0.0.1" called "docker run .*-p 127.0.0.1:$PORT:8080"
yes_if "the container knows its name (the phone shows it)" called "docker run .*KEEL_NAME=keel-test-remote"
hasnt "plain start prints no Wi-Fi URL" "$out" "On your Wi-Fi"
no_if "plain start adds no host names" called "docker run .*KEEL_ALLOWED_HOSTS"
check "keel gets an empty address list" "$(sent)" '{"urls":[]}'

reset
run_k -- start --wifi "$project"
check "start --wifi works" "$rc" 0
yes_if "--wifi publishes on every network" called "docker run .*-p 0.0.0.0:$PORT:8080"
no_if "--wifi does not publish on 127.0.0.1 too" called "docker run .*-p 127.0.0.1:$PORT:8080"
yes_if "--wifi is remembered on the container" called "docker run .*keel2.bind=0.0.0.0"
yes_if "--wifi lets keel answer on this computer's name (server.local)" called "docker run .*KEEL_ALLOWED_HOSTS=server.local,server"
has "--wifi prints this computer's name" "$out" "On your network: http://server.local:$PORT"
has "--wifi prints the Wi-Fi URL (Linux: ip route)" "$out" "On your Wi-Fi: http://192.168.1.20:$PORT"
has "--wifi warns about plain http" "$out" "plain http"
check "keel learns its Wi-Fi address" "$(sent)" "{\"urls\":[{\"kind\":\"wifi\",\"url\":\"http://192.168.1.20:$PORT\"}]}"

: > "$FAKE_CALLS"; rm -f "$S/addresses"
run_k -- restart
check "restart works" "$rc" 0
yes_if "restart keeps --wifi" called "docker run .*-p 0.0.0.0:$PORT:8080"
yes_if "restart keeps the names, once each" called "docker run .*KEEL_ALLOWED_HOSTS=server.local,server "
has "restart sends the address again" "$(sent)" "192.168.1.20"

: > "$FAKE_CALLS"
run_k -- start "$project"
yes_if "a start without flags keeps --wifi (0.21.2: keel2 remembers it)" called "docker run .*-p 0.0.0.0:$PORT:8080"
: > "$FAKE_CALLS"
run_k -- start --no-wifi "$project"
yes_if "a start --no-wifi is this computer only again" called "docker run .*-p 127.0.0.1:$PORT:8080"

: > "$FAKE_CALLS"
run_k KEEL_BIND=192.168.1.20 -- start "$project"
yes_if "KEEL_BIND=<address> publishes on that address" called "docker run .*-p 192.168.1.20:$PORT:8080"
run_k KEEL_BIND=nonsense -- start "$project"
no_if "KEEL_BIND must be an address" test "$rc" = 0

# ------------------------------------------------------------------ 2: the Wi-Fi address
echo "the Wi-Fi address on Linux and macOS"
reset
run_k FAKE_NO_ROUTE=1 -- start --wifi "$project"
has "Linux without a route: the real interface's address" "$out" "http://10.1.2.3:$PORT"
hasnt "Docker's bridge address is never offered" "$out" "172.17.0.1"
reset
run_k FAKE_NO_ROUTE=1 FAKE_NO_ADDR=1 -- start --wifi "$project"
has "Linux without ip's answers: hostname -I, without Docker's bridge" "$out" "http://10.0.0.5:$PORT"
hasnt "hostname -I: Docker's bridge address is never offered" "$out" "172.17.0.1"
reset
run_k FAKE_OS=Darwin -- start --wifi "$project"
has "macOS: ipconfig getifaddr en0" "$out" "On your Wi-Fi: http://192.168.1.20:$PORT"
reset
run_k KEEL_LAN_IP=10.9.8.7 -- start --wifi "$project"
has "KEEL_LAN_IP wins" "$out" "http://10.9.8.7:$PORT"

# ------------------------------------------------------------------ 3: share tailscale
echo "share tailscale"
reset
run_k -- start "$project"
run_k KEEL_TAILSCALE_BIN="$scratch/missing/tailscale" -- share tailscale
no_if "no tailscale: refused" test "$rc" = 0
has "no tailscale: says how to get it" "$out" "Tailscale is not installed"
run_k FAKE_TS_DOWN=1 -- share tailscale
no_if "logged out: refused" test "$rc" = 0
has "logged out: says so" "$out" "not signed in"
no_if "logged out: nothing is served" called "tailscale serve --bg"
: > "$FAKE_CALLS"
run_k -- share tailscale
check "share tailscale works" "$rc" 0
yes_if "serves https 443 to keel's port" called "tailscale serve --bg --https=443 http://127.0.0.1:$PORT"
has "prints the tailnet URL" "$out" "https://mac.tail1234.ts.net"
has "keel learns the Tailscale address" "$(sent)" '{"kind":"tailscale","url":"https://mac.tail1234.ts.net"}'
run_k -- share
has "share shows Tailscale" "$out" "Tailscale      https://mac.tail1234.ts.net"
: > "$FAKE_CALLS"
run_k -- share off tailscale
check "share off tailscale works" "$rc" 0
yes_if "only keel's https port is turned off" called "tailscale serve --https=443 off"
no_if "no tailscale serve reset" called "tailscale serve reset"
check "keel forgets the address" "$(sent)" '{"urls":[]}'
: > "$FAKE_CALLS"
run_k 'FAKE_TS_SERVE={"TCP":{"443":{"HTTPS":true}},"Web":{"mac.tail1234.ts.net:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:3000"}}}}}' -- share tailscale
yes_if "443 used by another serve: keel takes its own port" called "tailscale serve --bg --https=$PORT http://127.0.0.1:$PORT"
has "the URL carries that port" "$out" "https://mac.tail1234.ts.net:$PORT"
run_k -- share off
yes_if "share off turns off that port" called "tailscale serve --https=$PORT off"

# ------------------------------------------------------------------ 4: share tunnel
echo "share tunnel"
: > "$FAKE_CALLS"
run_k -- share tunnel
no_if "the first tunnel asks first (no terminal: refused)" test "$rc" = 0
has "it says why" "$out" "public"
no_if "nothing started without a yes" called "cloudflared"
run_k KEEL_YES=1 -- share tunnel
check "KEEL_YES=1: the tunnel opens" "$rc" 0
yes_if "a host cloudflared is used" called "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT"
has "prints the public URL" "$out" "https://fake-host-tunnel.trycloudflare.com"
has "says it is public" "$out" "public"
has "keel learns the tunnel address" "$(sent)" '{"kind":"tunnel","url":"https://fake-host-tunnel.trycloudflare.com"}'
pidf="$XDG_CONFIG_HOME/keel2/$KEEL_NAME.share/tunnel.pid"
tpid="$(cat "$pidf")"
yes_if "the tunnel runs in the background" kill -0 "$tpid"
run_k -- share
has "share shows the tunnel" "$out" "fake-host-tunnel.trycloudflare.com (public)"
run_k -- share tunnel
check "asked once: the second time needs no yes" "$rc" 0
tpid2="$(cat "$pidf")"
sleep 1
no_if "a second tunnel replaces the first" kill -0 "$tpid" 2>/dev/null
run_k -- share off tunnel
check "share off tunnel works" "$rc" 0
sleep 2
no_if "the tunnel is stopped" kill -0 "$tpid2" 2>/dev/null
check "keel forgets the tunnel" "$(sent)" '{"urls":[]}'

echo "share tunnel with Docker (no cloudflared installed)"
export KEEL_CLOUDFLARED_BIN="$scratch/missing/cloudflared"
: > "$FAKE_CALLS"
run_k -- share tunnel
check "the docker tunnel opens" "$rc" 0
yes_if "Linux: the pinned image on the host's network" called "docker run -d --name $KEEL_NAME-tunnel .*--network host cloudflare/cloudflared:[0-9][0-9.]* tunnel --no-autoupdate --url http://127.0.0.1:$PORT"
has "prints the URL from the container's log" "$out" "https://fake-from-docker.trycloudflare.com"
run_k -- share off
yes_if "share off removes keel's tunnel container" called "docker rm -f $KEEL_NAME-tunnel"
: > "$FAKE_CALLS"
run_k FAKE_OS=Darwin -- share tunnel
yes_if "macOS: host.docker.internal" called "docker run -d --name $KEEL_NAME-tunnel .*--add-host=host.docker.internal:host-gateway cloudflare/cloudflared:[0-9][0-9.]* tunnel --no-autoupdate --url http://host.docker.internal:$PORT"
run_k -- share off

# ------------------------------------------------------------------ 5: native mode
echo "native mode: --wifi reaches keel-native-start as KEEL_BIND"
export KEEL_CLOUDFLARED_BIN="$scratch/bin/cloudflared"
NDIR="$KEEL2_HOME/$KEEL_NAME"
mkdir -p "$NDIR/app/99.0.0"
ln -sfn "$NDIR/app/99.0.0" "$NDIR/current"
echo 99.0.0 > "$NDIR/app/99.0.0/VERSION"; : > "$NDIR/app/99.0.0/api.jar"; : > "$NDIR/.warned"
cat > "$NDIR/app/99.0.0/keel-native-start" <<'FAKE'
#!/usr/bin/env bash
echo "${KEEL_BIND:-unset} ${KEEL_NAME:-unset}" > "$FAKE_S/native-env"
trap 'exit 0' TERM
while :; do sleep 1; done
FAKE
chmod +x "$NDIR/app/99.0.0/keel-native-start"
cat > "$scratch/bin/java" <<'FAKE'
#!/bin/sh
echo 'openjdk version "21.0.1"' >&2
FAKE
chmod +x "$scratch/bin/java"
printf '#!/bin/sh\nexit 0\n' > "$scratch/bin/uv"; chmod +x "$scratch/bin/uv"
native_env() {  # what the fake keel-native-start got (it writes it right after it starts)
  local n=0; while [ ! -s "$S/native-env" ] && [ "$n" -lt 10 ]; do sleep 0.3; n=$((n + 1)); done
  cat "$S/native-env" 2>/dev/null || true
}
reset
run_k KEEL_NATIVE_CLIS=0 -- start --native --wifi "$project"
check "native start --wifi works" "$rc" 0
check "keel-native-start gets KEEL_BIND=0.0.0.0 and the name" "$(native_env)" "0.0.0.0 keel-test-remote"
has "native --wifi prints the Wi-Fi URL" "$out" "On your Wi-Fi: http://192.168.1.20:"
has "keel learns its Wi-Fi address (native)" "$(sent)" '"kind":"wifi"'
rm -f "$S/native-env"; run_k KEEL_NATIVE_CLIS=0 -- restart
check "native restart keeps --wifi" "$(native_env)" "0.0.0.0 keel-test-remote"
rm -f "$S/native-env"; run_k KEEL_NATIVE_CLIS=0 -- start --native "$project"
check "a native start without flags keeps --wifi" "$(native_env)" "0.0.0.0 keel-test-remote"
rm -f "$S/native-env"; run_k KEEL_NATIVE_CLIS=0 -- start --native --no-wifi "$project"
check "a native start --no-wifi is 127.0.0.1 again" "$(native_env)" "127.0.0.1 keel-test-remote"
run_k -- stop --force
check "native stop works" "$rc" 0

echo
if [ "$fails" = 0 ]; then echo "all phone access checks passed"; else echo "$fails check(s) failed"; exit 1; fi
