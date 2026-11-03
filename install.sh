#!/usr/bin/env bash
# Installs keel2, the command that starts and manages keel v2 (one Docker container).
#
#   curl -fsSL https://raw.githubusercontent.com/MiladNalbandi/keel-v2/main/install.sh | bash
#
# Settings (environment): KEEL2_DIR (where keel2 goes; default /usr/local/bin when writable, else ~/.local/bin),
# KEEL2_REF (a release tag like v0.21.3, or a git branch, to install from; default: the newest release), KEEL2_PULL=0
# (do not download the image now).
# It changes nothing else, except one PATH line in your shell profile when you say yes.
set -euo pipefail

REPO="MiladNalbandi/keel-v2"
REF="${KEEL2_REF:-}"
# 0.21.3 keel2 comes from a release (the newest, or the tag KEEL2_REF names) with its minisign signature, checked with
# keel's release key below (docs/plugins/tools/setup-signing-keys.sh writes it). A branch, or a release from before
# signing, is installed from the repository with a warning: nothing checks that file.
RELEASE_KEY="${KEEL2_RELEASE_KEY:-}"
case "$REF" in
  "") REL="https://github.com/$REPO/releases/latest/download"; RAW="https://raw.githubusercontent.com/$REPO/main/keel2" ;;
  v[0-9]*) REL="https://github.com/$REPO/releases/download/$REF"; RAW="https://raw.githubusercontent.com/$REPO/$REF/keel2" ;;
  *) REL=""; RAW="https://raw.githubusercontent.com/$REPO/$REF/keel2" ;;
esac

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=""; G=""; Y=""; R=""; N=""; fi
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '%s✗ %s%s\n' "$R" "$1" "$N" >&2; shift; for l in "$@"; do printf '  %s\n' "$l" >&2; done; exit 1; }
# Piped into bash, stdin is this script: questions are read from the terminal itself.
ask_yes() { { : </dev/tty; } 2>/dev/null || return 1; local a; printf '  %s [y/N] ' "$1" >/dev/tty; read -r a </dev/tty || return 1; case "$a" in y|Y|yes) return 0 ;; *) return 1 ;; esac; }

printf '%sInstalling keel2%s\n' "$B" "$N"

case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux ;;
  MINGW*|MSYS*|CYGWIN*) die "Run this inside WSL 2 (Ubuntu), not in Git Bash or PowerShell." "Docker Desktop: Settings › Resources › WSL integration › turn on your distribution." ;;
  *) die "This system ($(uname -s)) is not supported. keel2 runs on macOS, Linux and Windows (WSL 2)." ;;
esac
command -v curl >/dev/null 2>&1 || die "curl is needed." "Install it with your package manager (for example: sudo apt install curl)."

# Where to put it
if [ -n "${KEEL2_DIR:-}" ]; then DIR="$KEEL2_DIR"
elif [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then DIR=/usr/local/bin
else DIR="$HOME/.local/bin"; fi
mkdir -p "$DIR" || die "Could not create $DIR." "Choose another folder: KEEL2_DIR=~/bin bash install.sh"

# Download, check, install (never a half-written file)
MINISIG_PY='import base64, hashlib, sys
p = 2**255 - 19; q = 2**252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, p - 2, p) % p; I = pow(2, (p - 1) // 4, p)
def add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p; B = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * d % p; D = 2 * P[2] * Q[2] % p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % p, G * H % p, F * G % p, E * H % p)
def mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1: Q = add(Q, P)
        P = add(P, P); s >>= 1
    return Q
def xof(y, sign):
    if y >= p: return None
    x2 = (y * y - 1) * pow(d * y * y + 1, p - 2, p)
    if x2 == 0: return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p: x = x * I % p
    if (x * x - x2) % p: return None
    return p - x if (x & 1) != sign else x
def point(b):
    y = int.from_bytes(b, "little"); sign = y >> 255; y &= (1 << 255) - 1
    x = xof(y, sign)
    return None if x is None else (x, y, 1, x * y % p)
gy = 4 * pow(5, p - 2, p) % p; gx = xof(gy, 0); BASE = (gx, gy, 1, gx * gy % p)
def ok(pub, msg, sig):
    A = point(pub); R = point(sig[:32]); s = int.from_bytes(sig[32:], "little")
    if A is None or R is None or s >= q: return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % q
    P = mul(s, BASE); Q = add(R, mul(h, A))
    return (P[0] * Q[2] - Q[0] * P[2]) % p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % p == 0
def die(why): print(why); sys.exit(1)
path, sigpath, keyline, name = sys.argv[1:5]
try:
    key = base64.b64decode(keyline.strip(), validate=True)
    lines = open(sigpath, encoding="utf-8").read().rstrip("\n").split("\n")
    raw = base64.b64decode(lines[1].strip(), validate=True); glob = base64.b64decode(lines[3].strip(), validate=True)
except Exception: die("the signature file is not in the minisign format")
if len(key) != 42 or key[:2] != b"Ed": die("the release key is not a minisign Ed25519 key")
if len(lines) != 4 or not lines[2].startswith("trusted comment: ") or len(raw) != 74 or len(glob) != 64:
    die("the signature file is not in the minisign format")
if raw[2:10] != key[2:10]: die("the file is signed with another key than keel'"'"'s")
data = open(path, "rb").read()
msg = hashlib.blake2b(data, digest_size=64).digest() if raw[:2] == b"ED" else data
if raw[:2] not in (b"ED", b"Ed") or not ok(key[10:], msg, raw[10:]): die("the signature does not match the file")
trusted = lines[2][len("trusted comment: "):]
if not ok(key[10:], raw[10:] + trusted.encode("utf-8"), glob): die("the signature'"'"'s trusted comment was changed")
if ("file:" + name) not in trusted.split("\t"): die("the signature is for another file (" + trusted + ")")
'
check_sig() {  # check_sig <file> <file.minisig> — 0 signed by keel's key; 1 a bad signature (why on stdout); 2 cannot check
  if command -v minisign >/dev/null 2>&1; then
    local out; out="$(minisign -V -P "$RELEASE_KEY" -m "$1" -x "$2" 2>&1)" || { echo "$out"; return 1; }
    printf '%s\n' "$out" | grep -qE 'file:keel2([[:space:]]|$)' || { echo "the signature is for another file"; return 1; }
    return 0
  fi
  python3 -c 'import sys' >/dev/null 2>&1 || return 2
  python3 -I -c "$MINISIG_PY" "$1" "$2" "$RELEASE_KEY" keel2
}
TMP="$(mktemp)"; SIG="$(mktemp)"; trap 'rm -f "$TMP" "$SIG"' EXIT
signed=0
if [ -n "$REL" ] && curl -fsSL "$REL/keel2" -o "$TMP" 2>/dev/null; then
  if [ -z "$RELEASE_KEY" ]; then
    warn "This install.sh has no release key yet: keel2 is not checked."
  elif curl -fsSL "$REL/keel2.minisig" -o "$SIG" 2>/dev/null; then
    rc=0; why="$(check_sig "$TMP" "$SIG")" || rc=$?
    case "$rc" in
      0) signed=1; ok "keel2's signature checked (keel's release key)" ;;
      2) warn "keel2 is signed, but this computer has neither minisign nor python3 to check it: it is installed unchecked." ;;
      *) die "The downloaded keel2 failed its signature check: $why" "Someone may have changed it. Try again later, or report it: https://github.com/$REPO/issues" ;;
    esac
  else
    warn "This release has no signature for keel2 (made before keel signed them): it is installed unchecked."
  fi
else
  [ -z "$REL" ] || warn "The release has no keel2 file (made before keel attached it): installing keel2 from the repository, unchecked."
  [ -n "$REL" ] || warn "Installing keel2 from the branch $REF: a branch is not signed, so nothing checks this file."
  curl -fsSL "$RAW" -o "$TMP" || die "Could not download $RAW." "Check your internet connection and try again."
fi
head -1 "$TMP" | grep -q '^#!/usr/bin/env bash' && bash -n "$TMP" || die "The downloaded keel2 is not a valid script." "Try again later, or install from a tag: KEEL2_REF=v0.3.0"
chmod 755 "$TMP"
mv "$TMP" "$DIR/keel2"; trap 'rm -f "$SIG"' EXIT
ok "keel2 installed: $DIR/keel2$([ "$signed" = 1 ] && echo " (signed)")"

# PATH
case ":$PATH:" in
  *":$DIR:"*) ok "$DIR is on your PATH" ;;
  *)
    case "${SHELL:-}" in */zsh) RC="$HOME/.zshrc" ;; */bash) RC="$HOME/.bashrc"; [ "$OS" = mac ] && RC="$HOME/.bash_profile" ;; *) RC="$HOME/.profile" ;; esac
    LINE="export PATH=\"$DIR:\$PATH\""
    if grep -qsF "$LINE" "$RC"; then ok "$RC already adds $DIR to PATH (open a new terminal)"
    elif ask_yes "Add $DIR to your PATH in $RC?"; then printf '\n# keel2\n%s\n' "$LINE" >> "$RC"; ok "added to $RC — open a new terminal, or run: source $RC"
    else warn "$DIR is not on your PATH. Add this line to $RC:  $LINE"; fi
    export PATH="$DIR:$PATH" ;;
esac

# Docker
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker is not installed yet. keel runs in Docker:"
  [ "$OS" = mac ] && warn "  https://docs.docker.com/desktop/setup/install/mac-install/" || warn "  https://docs.docker.com/engine/install/"
  printf '\nWhen Docker runs: %skeel2 start /path/to/your/project%s\n' "$B" "$N"
  exit 0
fi
if docker info >/dev/null 2>&1; then
  ok "Docker is running"
  if [ "${KEEL2_PULL:-1}" = 1 ]; then
    IMAGE="${KEEL_IMAGE:-ghcr.io/miladnalbandi/keel-v2:latest}"
    printf '  Getting the keel image (%s), a few minutes the first time…\n' "$IMAGE"
    if docker pull -q "$IMAGE" >/dev/null; then ok "image ready"; else warn "could not get the image now; keel2 start tries again"; fi
  fi
else
  warn "Docker is installed but not running. Start Docker Desktop (or: sudo systemctl start docker) before keel2 start."
fi

printf '\n%sReady.%s Start keel for a project:\n\n  %skeel2 start /path/to/your/project%s\n\n' "$G" "$N" "$B" "$N"
printf 'More: keel2 help · Check your setup: keel2 doctor\n'
