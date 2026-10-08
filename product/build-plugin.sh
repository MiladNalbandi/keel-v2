#!/usr/bin/env bash
# Builds keel Product as a plugin package (docs/plugins/07-step1-contract.md, sections 2 and 8), with the packer every
# plugin of this repo uses (scripts/build-plugin.sh).
#
#   product/build-plugin.sh <out-dir> [--no-web] [--no-build]
#
#   <out-dir>/product/<version>/        the plugin folder (the product image copies it to /opt/keel-v2/plugins)
#   <out-dir>/product-<version>.kplug   the same folder as a tar.gz, with no top folder inside
#
#   --no-web     do not build the web part (packs product/web/dist when it is there)
#   --no-build   build nothing: pack the jar and web files that are already built (the Dockerfile builds them first)
#
# The version comes from product/keel-plugin.yml. KEEL_PRODUCT_JAR and KEEL_PRODUCT_WEB_DIST point to other built
# files (tests). files.sha256 lists every file with its sha256; the resolver and the installer check it.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
for a in "$@"; do
  case "$a" in
    --no-web | --no-build) ;;
    -*) echo "build-plugin: unknown option $a (usage: product/build-plugin.sh <out-dir> [--no-web] [--no-build])" >&2; exit 1 ;;
  esac
done
[ $# -gt 0 ] || { echo "build-plugin: usage: product/build-plugin.sh <out-dir> [--no-web] [--no-build]" >&2; exit 1; }

[ -z "${KEEL_PRODUCT_JAR:-}" ] || export KEEL_PLUGIN_JAR="$KEEL_PRODUCT_JAR"
[ -z "${KEEL_PRODUCT_WEB_DIST:-}" ] || export KEEL_PLUGIN_WEB_DIST="$KEEL_PRODUCT_WEB_DIST"
exec bash "$root/scripts/build-plugin.sh" "$root/product" "$@"
