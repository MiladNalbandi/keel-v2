#!/usr/bin/env bash
# Puts the released plugins of plugins.lock into a folder, each one checked (docs/plugins/16-steps5-7-contract.md,
# step 5). The Dockerfile's plugins stage runs it when the image is built with PLUGINS=lock; CI runs --check.
#
#   scripts/fetch-locked-plugins.sh <lock> <out-dir> --pub KEY [--skip a,b]
#   scripts/fetch-locked-plugins.sh --check <lock>
#
# The lock has one line per plugin: "<name> <version> <url> sha256:<hex>" (a line that starts with # is a comment).
# For each plugin:
#   1. download <url> and <url>.minisig: https only (http only to 127.0.0.1, localhost or host.docker.internal, and only
#      with PLUGINS_LOCK_ALLOW_HTTP=1: tests), 200 MB at most
#   2. its sha256 must be the lock's, and its signature must verify with KEY (keel-plugin verify; KEY is a public key
#      line RW… or a .pub file: the Dockerfile gives content/trust/keel.pub)
#   3. keel-plugin lint (keel's checks: no link or path outside the folder, the manifest, files.sha256), and the
#      manifest's name and version must be the lock's
#   4. unpacked into <out-dir>/<name>/<version>/: the layout scripts/build-plugin.sh makes
# A plugin that a locked plugin requires must be in the lock too, in a version that fits. All or nothing: at the first
# problem it stops, and <out-dir> gets nothing.
#
#   --skip a,b   leave these out (the Dockerfile leaves nothing out since 0.31: keel Product is in every full image)
#   --check      only check the lock's lines (no download)
# KEEL_PLUGIN: a keel-plugin command (default: keel-plugin on the PATH, else uv run --project tools/keel-plugin).
# Needs curl, tar, python3 and keel-plugin (python3 with cryptography).
set -euo pipefail

die() { echo "fetch-locked-plugins: $*" >&2; exit 1; }
usage="usage: fetch-locked-plugins.sh <lock> <out-dir> --pub KEY [--skip a,b] | fetch-locked-plugins.sh --check <lock>"

root="$(cd "$(dirname "$0")/.." && pwd)"
lock="" out="" pub="" skip="" check=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) check=1 ;;
    --pub) [ $# -ge 2 ] || die "--pub needs a key"; pub="$2"; shift ;;
    --skip) [ $# -ge 2 ] || die "--skip needs names"; skip="$2"; shift ;;
    -h | --help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1 ($usage)" ;;
    *) if [ -z "$lock" ]; then lock="$1"; elif [ -z "$out" ] && [ "$check" = 0 ]; then out="$1"; else die "$usage"; fi ;;
  esac
  shift
done
[ -n "$lock" ] || die "$usage"
[ -f "$lock" ] || die "there is no lock file $lock"
[ "$check" = 1 ] || [ -n "$out" ] || die "$usage"

# ---- the lock's lines: check every one, then keep the ones not skipped
name_re='^[a-z][a-z0-9-]{0,31}$'
version_re='^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$'
sum_re='^sha256:[0-9a-f]{64}$'
allow_http="${PLUGINS_LOCK_ALLOW_HTTP:-0}"
url_problem() {
  case "$1" in
    https://?*) return 1 ;;
    http://*)
      local host="${1#http://}"
      host="${host%%/*}"
      host="${host%%:*}"
      if [ "$allow_http" = 1 ]; then
        case "$host" in 127.0.0.1 | localhost | host.docker.internal) return 1 ;; esac
        echo "http:// is allowed only to 127.0.0.1, localhost and host.docker.internal (tests)"
      else
        echo "it must be https:// (http:// only for tests, with PLUGINS_LOCK_ALLOW_HTTP=1)"
      fi ;;
    *) echo "it must be an https:// URL" ;;
  esac
  return 0
}
errors=() entries=() names=" " no=0
while IFS= read -r raw || [ -n "$raw" ]; do
  no=$((no + 1))
  line="$(printf '%s' "$raw" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$line" in "" | "#"*) continue ;; esac
  read -r n v u s extra <<< "$line" || true
  where="$lock:$no"
  if [ -n "${extra:-}" ] || [ -z "${s:-}" ]; then
    errors+=("$where: write four fields, '<name> <version> <url> sha256:<hex>' (not '$line')"); continue
  fi
  [[ "$n" =~ $name_re ]] || errors+=("$where: '$n' is not a plugin name")
  [[ "$v" =~ $version_re ]] || errors+=("$where: '$v' is not a version like 1.2.3")
  [[ "$s" =~ $sum_re ]] || errors+=("$where: '$s' is not sha256: and 64 lowercase hex characters")
  if why="$(url_problem "$u")"; then errors+=("$where: the url '$u': $why"); fi
  case "$names" in *" $n "*) errors+=("$where: $n is locked twice") ;; esac
  names="$names$n "
  case ",$skip," in *",$n,"*) continue ;; esac
  entries+=("$n $v $u ${s#sha256:}")
done < "$lock"
if [ ${#errors[@]} -gt 0 ]; then
  printf 'fetch-locked-plugins: %s\n' "${errors[@]}" >&2
  exit 1
fi
if [ "$check" = 1 ]; then
  echo "$lock: ok ($(($(printf '%s' "$names" | wc -w))) plugins locked)"
  exit 0
fi
[ ${#entries[@]} -gt 0 ] ||
  die "$lock locks no plugin${skip:+ (besides $skip)}: an image built with PLUGINS=lock would have none. Build with PLUGINS=source until the plugins are released (scripts/update-plugins-lock.sh writes the lines)"

# ---- the key and the tools
[ -n "$pub" ] || die "give the publisher's public key with --pub (a key line RW… or a .pub file)"
case "$pub" in
  RW*) ;;
  *) [ -f "$pub" ] || die "the public key $pub is missing, so no signature can be checked. keel's is content/trust/keel.pub" \
       "(docs/plugins/tools/setup-signing-keys.sh writes it): until it is there, build with PLUGINS=source" ;;
esac
if [ -n "${KEEL_PLUGIN:-}" ]; then
  kp=("$KEEL_PLUGIN")
elif command -v keel-plugin > /dev/null 2>&1; then
  kp=(keel-plugin)
elif command -v uv > /dev/null 2>&1 && [ -d "$root/tools/keel-plugin" ]; then
  kp=(uv run --quiet --project "$root/tools/keel-plugin" keel-plugin)
else
  die "keel-plugin is needed (tools/keel-plugin; or set KEEL_PLUGIN)"
fi
command -v curl > /dev/null 2>&1 || die "curl is needed"
command -v python3 > /dev/null 2>&1 || die "python3 is needed"
sha256() { if command -v sha256sum > /dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
top() { awk -v k="$2" 'index($0, k ":") == 1 { sub("^" k ":[ \t]*", ""); sub(/\r$/, ""); sub(/[ \t]+#.*$/, ""); print; exit }' "$1" | tr -d "\"' "; }

mkdir -p "$out"
out="$(cd "$out" && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/fetch-locked-plugins.XXXXXX")"
trap 'rm -rf "$work"' EXIT
protos="=https"
[ "$allow_http" != 1 ] || protos="=http,https"

# ---- 1-4 for each plugin, into the work folder
locked=""
for entry in "${entries[@]}"; do
  read -r n v u s <<< "$entry"
  [ ! -e "$out/$n/$v" ] || die "$out/$n/$v is there already"
  file="$work/$n-$v.kplug"
  for target in "$u" "$u.minisig"; do
    dest="$file"
    [ "$target" = "$u" ] || dest="$file.minisig"
    curl -fsSL --proto "$protos" --proto-redir "$protos" --retry 2 --connect-timeout 20 --max-time 600 \
      --max-filesize 209715200 -o "$dest" "$target" 2> "$work/curl.err" ||
      die "$n $v: cannot download $target ($(tr '\n' ' ' < "$work/curl.err"))"
  done
  got="$(sha256 "$file")"
  [ "$got" = "$s" ] || die "$n $v: the sha256 of $u is $got, not the lock's $s: refused"
  if ! report="$("${kp[@]}" verify "$file" --pub "$pub" 2>&1)"; then
    die "$n $v: the signature does not verify with the publisher's key: refused ($(printf '%s' "$report" | tr '\n' ' '))"
  fi
  if ! report="$("${kp[@]}" lint "$file" 2>&1)"; then
    die "$n $v: keel-plugin lint refuses the package:"$'\n'"$report"
  fi
  mkdir -p "$work/out/$n/$v"
  tar -xzf "$file" -C "$work/out/$n/$v"
  m="$work/out/$n/$v/keel-plugin.yml"
  [ "$(top "$m" name)/$(top "$m" version)" = "$n/$v" ] ||
    die "$n $v: the package's manifest is $(top "$m" name) $(top "$m" version), not the lock's: refused"
  locked="$locked$n $v"$'\n'
  echo "$n $v: sha256 ok, signature ok, lint ok"
done

# ---- every plugin a locked one requires is locked too, in a version that fits
for entry in "${entries[@]}"; do
  read -r n v _ _ <<< "$entry"
  needs="$(python3 "$root/scripts/plugin_versions.py" needs "$work/out/$n/$v/keel-plugin.yml")" ||
    die "$n $v: its requires.plugins cannot be read"
  while IFS=$'\t' read -r dep spec; do
    [ -n "$dep" ] || continue
    have="$(printf '%s' "$locked" | sed -n "s/^$dep //p")"
    [ -n "$have" ] || die "$n $v needs $dep ${spec:-(any version)}, which plugins.lock does not lock${skip:+ (or --skip leaves out)}"
    python3 "$root/scripts/plugin_versions.py" fits "$have" "$spec" || die "$n $v needs $dep $spec, and the lock has $dep $have"
  done <<< "$needs"
done

for entry in "${entries[@]}"; do
  read -r n v _ _ <<< "$entry"
  mkdir -p "$out/$n"
  mv "$work/out/$n/$v" "$out/$n/$v"
done
echo "fetch-locked-plugins: ${#entries[@]} plugins into $out: $(printf '%s' "$locked" | awk '{ printf "%s%s %s", (NR > 1 ? ", " : ""), $1, $2 }')"
