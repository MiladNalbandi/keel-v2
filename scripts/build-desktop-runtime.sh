#!/usr/bin/env bash
# Build what keel desktop carries inside (desktop/README.md), for the computer this runs on (Linux x64, macOS arm64/x64):
#
#   <out>/keel/              the native release, unpacked (api.jar, content/, plugins/, cli/, keel-start, keel-native-start)
#   <out>/runtime/python/    Python 3.12 (python-build-standalone: it can move to any folder) with keel's engine, keel's
#                            Python api and their locked packages in its own site-packages (no venv: a venv cannot move)
#   <out>/runtime/jre/       a small Java 21 for the Kotlin api, made with jlink from this computer's JDK 21
#   <out>/runtime/bin/       rg (ripgrep), and keel-engine and keel-api: two-line wrappers around the Python above
#
#   scripts/build-desktop-runtime.sh <keel-v2-<version>-native.tar.gz> [out dir, default desktop/build/stage]
#
# Needs: curl, tar, uv, and a JDK 21 (JAVA_HOME, or jlink on PATH). Every download is checked against
# desktop/runtimes.lock (sha256). The Python code is compiled here (the app's folder may be read-only, the macOS app
# must not change after it is signed), so keel never writes __pycache__ into the app.
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd -P)"
tgz="${1:?usage: scripts/build-desktop-runtime.sh <native tarball> [out dir]}"
out="${2:-$src/desktop/build/stage}"
[ -f "$tgz" ] || { echo "no file $tgz" >&2; exit 1; }
tgz="$(cd "$(dirname "$tgz")" && pwd -P)/$(basename "$tgz")"

case "$(uname -s)" in Linux) os=linux ;; Darwin) os=mac ;; *) echo "keel desktop builds on Linux or macOS only" >&2; exit 1 ;; esac
case "$(uname -m)" in x86_64 | amd64) arch=x64 ;; arm64 | aarch64) arch=arm64 ;; *) echo "unknown CPU $(uname -m)" >&2; exit 1 ;; esac
target="$os-$arch"
echo "keel desktop runtime for $target → $out"

sha256() { if command -v sha256sum > /dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT

# fetch <name>: the pinned file for this computer, checked; prints its path
fetch() {
  local line url want got file
  line="$(awk -v n="$1" -v t="$target" '$1 == n && $2 == t { print $3, $4 }' "$src/desktop/runtimes.lock")"
  [ -n "$line" ] || { echo "desktop/runtimes.lock has no $1 for $target" >&2; exit 1; }
  url="${line% *}"; want="${line#* }"
  file="$work/$(basename "${url//%2B/+}")"
  curl -fsSL --retry 3 -o "$file" "$url"
  got="$(sha256 "$file")"
  [ "$got" = "$want" ] || { echo "$1: sha256 $got, desktop/runtimes.lock says $want ($url)" >&2; exit 1; }
  echo "$file"
}

rm -rf -- "$out"
mkdir -p "$out/runtime/bin" "$out/keel"

echo "keel…"
tar -xzf "$tgz" -C "$work"
rel="$(ls -d "$work"/keel-v2-*/ | head -1)"; rel="${rel%/}"
[ -f "$rel/VERSION" ] && [ -f "$rel/api.jar" ] && [ -d "$rel/api-py" ] || { echo "$tgz is not a keel native release with api-py/ (0.27.0 or newer)" >&2; exit 1; }
version="$(cat "$rel/VERSION")"

echo "Python…"
tar -xzf "$(fetch python)" -C "$out/runtime"          # unpacks to python/
py="$out/runtime/python/bin/python3"
"$py" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'
# the engine's and the Python api's packages, exactly as locked (with their hashes), into this Python itself. The two
# projects share fastapi, uvicorn, pydantic and httpx: the engine's lock decides them, and `uv pip check` below fails
# the build when the Python api's own needs are not met by them.
for p in engine api-py; do
  (cd "$rel/$p" && uv export --frozen --no-dev --no-emit-project --no-header --format requirements-txt -q -o "$work/$p.txt")
done
uvp=(uv pip install -q --python "$py" --system --break-system-packages)
"${uvp[@]}" --require-hashes -r "$work/engine.txt"
"${uvp[@]}" --no-deps "$rel/engine" "$rel/api-py"
uv pip check -q --python "$py" --system
# the installed copies are the ones that run: no sources beside them
rm -rf -- "$rel/engine" "$rel/api-py"
# what keel never runs: pip (keel installs nothing into its own Python), Tk and IDLE (no windows), the 2to3/pydoc tools
pyl="$out/runtime/python/lib"
rm -rf -- "$pyl"/python3.12/site-packages/pip "$pyl"/python3.12/site-packages/pip-*.dist-info "$pyl"/python3.12/idlelib \
  "$pyl"/python3.12/tkinter "$pyl"/python3.12/turtledemo "$pyl"/python3.12/ensurepip "$pyl"/tcl* "$pyl"/tk* "$pyl"/itcl* \
  "$pyl"/python3.12/lib-dynload/_tkinter* "$out/runtime/python/bin/2to3"* "$out/runtime/python/bin/idle3"* "$out/runtime/python/bin/pydoc3"*
# console scripts carry this build's folder in their first line: the app has its own wrappers (bin/ below)
for f in "$out/runtime/python/bin"/*; do
  [ -f "$f" ] && [ ! -L "$f" ] || continue
  case "$(basename "$f")" in python*) continue ;; esac
  if head -c 200 "$f" | head -1 | grep -q '^#!'; then rm -f -- "$f"; fi
done
"$py" -c 'import keel_engine, keel_api, langgraph, anthropic, openai; print("  keel_engine", keel_engine.__file__)'

echo "keel's files…"
cp -R "$rel"/. "$out/keel/"
# compiled once here; the check by hash, not by time: the app's files may get other times when they are copied
"$py" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$out/runtime/python/lib/python3.12" "$out/keel/plugins" > /dev/null

echo "Java…"
jlink="${JAVA_HOME:+$JAVA_HOME/bin/}jlink"
command -v "$jlink" > /dev/null 2>&1 || { echo "no jlink: set JAVA_HOME to a JDK 21" >&2; exit 1; }
jv="$("${JAVA_HOME:+$JAVA_HOME/bin/}java" -version 2>&1 | sed -n 's/.*version "\([0-9]*\).*/\1/p' | head -1)"
[ "$jv" = 21 ] || { echo "the JDK is $jv, keel's api needs 21" >&2; exit 1; }
# what jdeps finds in api.jar and the plugins' jars, plus what Spring and the JDBC drivers load by name
mods="java.base,java.compiler,java.desktop,java.instrument,java.logging,java.management,java.naming,java.net.http"
mods="$mods,java.prefs,java.rmi,java.scripting,java.security.jgss,java.security.sasl,java.sql,java.sql.rowset,java.xml"
mods="$mods,jdk.charsets,jdk.crypto.ec,jdk.jfr,jdk.management,jdk.net,jdk.unsupported,jdk.zipfs"
"$jlink" --add-modules "$mods" --strip-debug --no-man-pages --no-header-files --compress=zip-6 --output "$out/runtime/jre"
"$out/runtime/jre/bin/java" -version 2>&1 | head -1 | sed 's/^/  /'

echo "ripgrep…"
tar -xzf "$(fetch rg)" -C "$work"
cp "$(ls -d "$work"/ripgrep-*/)rg" "$out/runtime/bin/rg"
chmod 0755 "$out/runtime/bin/rg"

# keel-start runs these two: the engine and the Python api, on the Python above. -P: never the folder it starts in
# on sys.path (it is a project folder).
for w in "keel-engine keel_engine.main" "keel-api keel_api.main"; do
  set -- $w
  printf '#!/bin/sh\nexec "$(dirname "$0")/../python/bin/python3" -P -m %s "$@"\n' "$2" > "$out/runtime/bin/$1"
  chmod 0755 "$out/runtime/bin/$1"
done
# macOS on Apple chips runs no unsigned code: every program and library inside gets at least an ad hoc signature
# (the app's own signature, electron-builder's afterPack, covers the bundle; a Developer ID signs them all again)
if [ "$os" = mac ]; then
  n=0
  while IFS= read -r -d '' f; do
    if file -b "$f" | grep -q 'Mach-O' && ! codesign -v "$f" > /dev/null 2>&1; then codesign -f -s - "$f" && n=$((n + 1)); fi
  done < <(find "$out/runtime" -type f \( -perm -u+x -o -name '*.so' -o -name '*.dylib' \) -print0)
  echo "  signed ad hoc: $n file(s)"
fi
printf '%s\n' "$version" > "$out/runtime/VERSION"
du -sh "$out/runtime/python" "$out/runtime/jre" "$out/keel" 2> /dev/null | sed 's/^/  /'
echo "$out"
