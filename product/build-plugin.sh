#!/usr/bin/env bash
# Builds keel Product as a plugin package (docs/plugins/07-step1-contract.md, sections 2 and 8).
#
#   product/build-plugin.sh <out-dir> [--no-web] [--no-build]
#
#   <out-dir>/product/<version>/        the plugin folder (the product image copies it to /opt/keel/plugins)
#   <out-dir>/product-<version>.kplug   the same folder as a tar.gz, with no top folder inside
#
#   --no-web     do not build the web part (packs product/web/dist when it is there)
#   --no-build   build nothing: pack the jar and web files that are already built (the Dockerfile builds them first)
#
# The version comes from product/keel-plugin.yml. KEEL_PRODUCT_JAR and KEEL_PRODUCT_WEB_DIST point to other built
# files (tests). files.sha256 lists every file with its sha256; the resolver and the installer check it.
set -euo pipefail

die() { echo "build-plugin: $*" >&2; exit 1; }

root="$(cd "$(dirname "$0")/.." && pwd)"
out="" build=1 web=1
for a in "$@"; do
  case "$a" in
    --no-web) web=0 ;;
    --no-build) build=0; web=0 ;;
    -*) die "unknown option $a (usage: product/build-plugin.sh <out-dir> [--no-web] [--no-build])" ;;
    *) out="$a" ;;
  esac
done
[ -n "$out" ] || die "usage: product/build-plugin.sh <out-dir> [--no-web] [--no-build]"

manifest="$root/product/keel-plugin.yml"
version="$(sed -n 's/^version:[[:space:]]*//p' "$manifest" | tr -d "\"' \r")"
case "$version" in "" | *[!0-9A-Za-z.+-]*) die "no valid version in $manifest" ;; esac
jar="${KEEL_PRODUCT_JAR:-$root/api/build/libs/keel-plugin-product.jar}"
dist="${KEEL_PRODUCT_WEB_DIST:-$root/product/web/dist}"

# ---- build the parts
if [ "$build" = 1 ]; then
  (cd "$root/api" && ./gradlew --no-daemon -q productPluginJar)
fi
if [ "$web" = 1 ]; then
  [ -d "$root/web/node_modules" ] || die "web/node_modules is missing: run 'npm ci' in web/ first (or use --no-web)"
  npm --prefix "$root/web" run build:product
fi
[ -f "$jar" ] || die "no api jar at $jar (cd api && ./gradlew productPluginJar)"

# ---- stage the plugin folder
mkdir -p "$out"
out="$(cd "$out" && pwd)"
stage="$out/product/$version"
kplug="$out/product-$version.kplug"
rm -rf "$stage" "$kplug"
mkdir -p "$stage/engine" "$stage/api"
cp "$manifest" "$root/product/README.md" "$stage/"
cp -R "$root/product/engine/keel_product" "$stage/engine/"
cp "$jar" "$stage/api/keel-plugin-product.jar"
if [ -f "$dist/index.js" ]; then
  mkdir -p "$stage/web"
  cp -R "$dist/." "$stage/web/"
else
  echo "build-plugin: warning: $dist/index.js is missing, so the package has no web part (no pages)" >&2
fi
cp -R "$root/product/content" "$stage/content"
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

echo "keel Product $version: $kplug ($(grep -c '' "$stage/files.sha256") files)"
echo "  unpacked: $stage"
