#!/usr/bin/env bash
# Builds one plugin folder of this repo as a plugin package (docs/plugins/11-step3-contract.md, wave 0; the package
# format is docs/plugins/07-step1-contract.md, sections 2 and 8).
#
#   scripts/build-plugin.sh <plugin-dir> <out-dir> [--no-web] [--no-build]
#
#   <out-dir>/<name>/<version>/         the plugin folder (the image copies it to /opt/keel-v2/plugins)
#   <out-dir>/<name>-<version>.kplug    the same folder as a tar.gz, with no top folder inside
#
#   --no-web     do not build the web part (packs <plugin-dir>/web/dist when it is there)
#   --no-build   build nothing: pack the jars and web files that are already built (the Dockerfile builds them first)
#
# It reads keel-plugin.yml: the name, the version and the parts. Write each part on one line, as the plugins here do:
#   engine: { path: engine, package: keel_plugin_x }     the package folder only (no tests, no caches)
#   api: { jars: [api/keel-plugin-x.jar] }               each jar from api/build/libs (./gradlew <name>PluginJar)
#   web: { entry: web/index.js, css: [web/style.css] }   <plugin-dir>/web/dist (npm run build:plugin -- <name>)
#   content: content    migrations: migrations          copied as they are
# KEEL_PLUGIN_JAR (a plugin with one jar) and KEEL_PLUGIN_WEB_DIST point to other built files (tests).
# files.sha256 lists every file with its sha256; the resolver and the installer check it.
set -euo pipefail

die() { echo "build-plugin: $*" >&2; exit 1; }
usage="usage: scripts/build-plugin.sh <plugin-dir> <out-dir> [--no-web] [--no-build]"

root="$(cd "$(dirname "$0")/.." && pwd)"
dir="" out="" build=1 web=1
for a in "$@"; do
  case "$a" in
    --no-web) web=0 ;;
    --no-build) build=0; web=0 ;;
    -*) die "unknown option $a ($usage)" ;;
    *) if [ -z "$dir" ]; then dir="$a"; elif [ -z "$out" ]; then out="$a"; else die "$usage"; fi ;;
  esac
done
[ -n "$dir" ] && [ -n "$out" ] || die "$usage"
[ -d "$dir" ] || die "no plugin folder $dir"
dir="$(cd "$dir" && pwd)"
manifest="$dir/keel-plugin.yml"
[ -f "$manifest" ] || die "no keel-plugin.yml in $dir"

# ---- read the manifest: top-level scalars, and the parts (one line each)
top() { sed -n "s/^$1:[[:space:]]*//p" "$manifest" | sed 's/[[:space:]]#.*$//' | tr -d "\"' \r"; }
# the text after "<key>:" on its line under "parts:", without a comment
part() {
  # plain [ \t] classes: mawk (Debian's awk) may not know [[:space:]]
  awk -v k="$1" '
    /^[^ \t#]/ { inparts = ($0 ~ /^parts:[ \t]*\r?$/); next }
    inparts && $0 ~ ("^[ \t]+" k ":") {
      sub("^[ \t]+" k ":[ \t]*", ""); sub(/\r$/, ""); sub(/[ \t]+#.*$/, ""); print; exit
    }' "$manifest"
}
# one field of a flow mapping "{ a: x, b: [y, z] }": a scalar, or a list's items one per line
field() { printf '%s\n' "$1" | sed -n "s/.*[{,][[:space:]]*$2:[[:space:]]*\([^],}[]*\).*/\1/p" | tr -d "\"' "; }
items() {
  printf '%s\n' "$1" | sed -n "s/.*[{,][[:space:]]*$2:[[:space:]]*\[\([^]]*\)\].*/\1/p" | tr ',' '\n' | tr -d "\"' " | sed '/^$/d'
}
# a part path must stay inside the plugin folder
safe() { case "$1" in "" | /* | *..* | *\\*) die "parts: '$1' is not a path inside the plugin folder" ;; esac; }

name="$(top name)"
version="$(top version)"
case "$name" in "" | *[!a-z0-9-]*) die "no valid name in $manifest" ;; esac
case "$version" in "" | *[!0-9A-Za-z.+-]*) die "no valid version in $manifest" ;; esac
camel="$(printf '%s' "$name" | awk -F- '{ out = $1; for (i = 2; i <= NF; i++) out = out toupper(substr($i, 1, 1)) substr($i, 2); print out }')"

engine_line="$(part engine)" api_line="$(part api)" web_line="$(part web)"
content="$(part content | tr -d "\"' ")" migrations="$(part migrations | tr -d "\"' ")"
for line in "$engine_line" "$api_line" "$web_line"; do
  case "$line" in "" | "{"*"}") ;; *) die "write each part on one line, like engine: { path: engine, package: x } ($manifest)" ;; esac
done

# ---- build the parts
if [ "$build" = 1 ] && [ -n "$api_line" ]; then
  (cd "$root/api" && ./gradlew --no-daemon -q "${camel}PluginJar")
fi
if [ "$web" = 1 ] && [ -n "$web_line" ]; then
  [ -d "$root/web/node_modules" ] || die "web/node_modules is missing: run 'npm ci' in web/ first (or use --no-web)"
  if [ -f "$dir/web/vite.config.ts" ]; then
    # a plugin with its own build (keel Product: npm run build:product)
    (cd "$root/web" && npx vite build --config "$dir/web/vite.config.ts")
  else
    npm --prefix "$root/web" run build:plugin -- "$name"
  fi
fi

# ---- stage the plugin folder
mkdir -p "$out"
out="$(cd "$out" && pwd)"
stage="$out/$name/$version"
kplug="$out/$name-$version.kplug"
rm -rf "$stage" "$kplug"
mkdir -p "$stage"
cp "$manifest" "$stage/"
[ -f "$dir/README.md" ] && cp "$dir/README.md" "$stage/"

if [ -n "$engine_line" ]; then
  epath="$(field "$engine_line" path)" package="$(field "$engine_line" package)"
  safe "$epath"; safe "$package"
  [ -d "$dir/$epath/$package" ] || die "parts.engine: no package $package in $dir/$epath"
  mkdir -p "$stage/$epath"
  cp -R "$dir/$epath/$package" "$stage/$epath/"
fi

if [ -n "$api_line" ]; then
  jars="$(items "$api_line" jars)"
  [ -n "$jars" ] || die "parts.api.jars names no jar"
  [ -z "$(field "$api_line" lib)" ] || die "parts.api.lib is not packed by this script yet"
  count="$(printf '%s\n' "$jars" | grep -c .)"
  [ -z "${KEEL_PLUGIN_JAR:-}" ] || [ "$count" = 1 ] || die "KEEL_PLUGIN_JAR is for a plugin with one jar"
  while IFS= read -r jar; do
    safe "$jar"
    src="${KEEL_PLUGIN_JAR:-$root/api/build/libs/$(basename "$jar")}"
    [ -f "$src" ] || die "no api jar at $src (cd api && ./gradlew ${camel}PluginJar)"
    mkdir -p "$stage/$(dirname "$jar")"
    cp "$src" "$stage/$jar"
  done <<< "$jars"
fi

if [ -n "$web_line" ]; then
  entry="$(field "$web_line" entry)"
  safe "$entry"
  dist="${KEEL_PLUGIN_WEB_DIST:-$dir/web/dist}"
  wdir="$(dirname "$entry")"
  if [ -f "$dist/$(basename "$entry")" ]; then
    mkdir -p "$stage/$wdir"
    cp -R "$dist/." "$stage/$wdir/"
  else
    echo "build-plugin: warning: $dist/$(basename "$entry") is missing, so the package has no web part (no pages)" >&2
  fi
fi

for folder in "$content" "$migrations"; do
  [ -n "$folder" ] || continue
  safe "$folder"
  [ -d "$dir/$folder" ] || die "parts: no folder $dir/$folder"
  mkdir -p "$stage/$(dirname "$folder")"
  cp -R "$dir/$folder" "$stage/$folder"
done

# no caches or Finder files; the installer refuses links
find "$stage" \( -name __pycache__ -o -name '*.pyc' -o -name .DS_Store \) -prune -exec rm -rf {} +
[ -z "$(find "$stage" -type l)" ] || die "a plugin may not hold links: $(find "$stage" -type l | head -3)"

# ---- files.sha256: "<sha256>  <path>" for every file but itself, sorted (the same order everywhere)
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi; }
(
  cd "$stage"
  find . -type f ! -path ./files.sha256 | sed 's|^\./||' | LC_ALL=C sort |
    while IFS= read -r f; do sha256 "$f"; done > files.sha256
)

# ---- the .kplug: the folder's contents (no top folder), folders and files in sorted order
list="$(mktemp)"
trap 'rm -f "$list"' EXIT
(cd "$stage" && find . -mindepth 1 | sed 's|^\./||' | LC_ALL=C sort) > "$list"
# owner 0, and no extended attributes (macOS's tar adds them, and ._ files without COPYFILE_DISABLE)
if tar --version 2>/dev/null | grep -q bsdtar; then
  flags=(--uid 0 --gid 0 --no-xattrs --no-acls --no-fflags --no-mac-metadata)
else
  flags=(--owner=0 --group=0 --numeric-owner)
fi
COPYFILE_DISABLE=1 tar "${flags[@]}" --no-recursion -czf "$kplug" -C "$stage" -T "$list"

title="$(sed -n 's/^title:[[:space:]]*//p' "$manifest" | tr -d "\"'\r")"
echo "${title:-$name} $version: $kplug ($(grep -c '' "$stage/files.sha256") files)"
echo "  unpacked: $stage"
