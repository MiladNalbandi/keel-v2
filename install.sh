#!/usr/bin/env bash
# Installs keel2, the command that starts and manages keel v2 (one Docker container).
#
#   curl -fsSL https://raw.githubusercontent.com/MiladNalbandi/keel-v2/main/install.sh | bash
#
# Settings (environment): KEEL2_DIR (where keel2 goes; default /usr/local/bin when writable, else ~/.local/bin),
# KEEL2_REF (git branch or tag to install from; default main), KEEL2_PULL=0 (do not download the image now).
# It changes nothing else, except one PATH line in your shell profile when you say yes.
set -euo pipefail

REPO="MiladNalbandi/keel-v2"
REF="${KEEL2_REF:-main}"
URL="https://raw.githubusercontent.com/$REPO/$REF/keel2"

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
TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
curl -fsSL "$URL" -o "$TMP" || die "Could not download $URL." "Check your internet connection and try again."
head -1 "$TMP" | grep -q '^#!/usr/bin/env bash' && bash -n "$TMP" || die "The downloaded keel2 is not a valid script." "Try again later, or install from a tag: KEEL2_REF=v0.3.0"
chmod 755 "$TMP"
mv "$TMP" "$DIR/keel2"; trap - EXIT
ok "keel2 installed: $DIR/keel2"

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
