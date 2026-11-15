#!/usr/bin/env bash
# Build keel v2's native release: keel-v2-<version>-native.tar.gz (+ .sha256) in <out dir> (default: dist/).
#
#   api.jar            the api with the web app inside (the same bootJar as the image)
#   engine/            the engine's source, pyproject.toml and uv.lock (keel2 runs `uv sync --frozen` on install)
#   api-py/            0.27.0 the Python api's source, pyproject.toml and uv.lock: the front door, as in the image
#   content/           agents, skills, stacks, templates
#   plugins/           every plugin of plugins/, packed as the full image has them (<name>/<version>/: scripts/build-plugin.sh)
#   cli/keel-tui.mjs   keel TUI, keel in the terminal (keel2 tui runs it with this computer's Node)
#   keel-native-start  starts the engine and the api (docker/keel-native-start) with keel-start's loop (docker/keel-start)
#   VERSION
#
# Needs JDK 21 and Node 20+ (npm). The release job in .github/workflows/docker.yml uploads the two files.
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd -P)"
out="${1:-$src/dist}"
version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$src/engine/pyproject.toml" | head -1)"
[ -n "$version" ] || { echo "no version in engine/pyproject.toml" >&2; exit 1; }
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
stage="$work/keel-v2-$version"
mkdir -p "$stage" "$out"

echo "web…"
# keel's bundle first, then each plugin's web part on its own (plugins/<name>/web/dist), as the image's web stage does
(cd "$src/web" && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build >/dev/null && npm run build:plugins >/dev/null)
echo "keel TUI…"
(cd "$src/cli" && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build >/dev/null)
mkdir -p "$stage/cli" && cp "$src/cli/dist/keel-tui.mjs" "$stage/cli/keel-tui.mjs" && chmod +x "$stage/cli/keel-tui.mjs"
echo "api…"
# built from a copy, as the image does: the web app goes into the copy's static/ (the checkout's own stays untouched);
# bootJar compiles and packs, it does not run the tests. The plugins' api jars (pluginJars) are built beside it, never
# inside it: the copy has plugins/ and keel Product's manifest next to api/, where Gradle reads them.
repo="$work/repo"
mkdir -p "$repo/product" "$repo/scripts"
(cd "$src/api" && tar --exclude ./build --exclude ./.gradle --exclude ./.kotlin -cf - .) | (mkdir -p "$repo/api" && cd "$repo/api" && tar -xf -)
(cd "$src" && tar --exclude '*/build' --exclude '*/.gradle' --exclude '*/node_modules' --exclude '*/__pycache__' \
  --exclude '*/.pytest_cache' -cf - plugins) | (cd "$repo" && tar -xf -)
cp -R "$src/product/api" "$repo/product/api"
cp "$src/product/keel-plugin.yml" "$repo/product/"
cp "$src/scripts/build-plugin.sh" "$repo/scripts/"
rm -rf "$repo/api/src/main/resources/static"
cp -R "$src/web/dist" "$repo/api/src/main/resources/static"
(cd "$repo/api" && ./gradlew --no-daemon -q bootJar pluginJars)
# shellcheck disable=SC2010 # the jar names are our own gradle output: plain names
cp "$(ls "$repo"/api/build/libs/*.jar | grep -v -- '-plain' | grep -v '/keel-plugin-' | head -1)" "$stage/api.jar"

echo "plugins…"
# every plugin of plugins/, packed with the parts built above (the full image's /opt/keel-v2/plugins)
for d in "$repo"/plugins/*/; do
  [ -f "$d/keel-plugin.yml" ] || continue
  bash "$repo/scripts/build-plugin.sh" "$d" "$stage/plugins" --no-build >/dev/null
done
rm -f "$stage/plugins"/*.kplug

echo "engine, Python api, content…"
mkdir -p "$stage/engine"
# .python-version pins the engine to the Python the image uses (3.12): without it uv takes the newest Python on
# the computer, and a dependency may have no build for it yet (ormsgpack on 3.15)
cp "$src/engine/pyproject.toml" "$src/engine/uv.lock" "$src/engine/.python-version" "$stage/engine/"
(cd "$src/engine" && tar --exclude '__pycache__' --exclude '.pytest_cache' -cf - keel_engine) | (cd "$stage/engine" && tar -xf -)
mkdir -p "$stage/api-py"
cp "$src/api-py/pyproject.toml" "$src/api-py/uv.lock" "$src/engine/.python-version" "$stage/api-py/"
(cd "$src/api-py" && tar --exclude '__pycache__' --exclude '.pytest_cache' -cf - keel_api) | (cd "$stage/api-py" && tar -xf -)
cp -R "$src/content" "$stage/content"
cp "$src/docker/keel-native-start" "$stage/keel-native-start"
cp "$src/docker/keel-start" "$stage/keel-start"
chmod +x "$stage/keel-native-start" "$stage/keel-start"
printf '%s\n' "$version" > "$stage/VERSION"

name="keel-v2-$version-native.tar.gz"
tar -C "$work" -czf "$out/$name" "keel-v2-$version"
(cd "$out" && { command -v sha256sum >/dev/null && sha256sum "$name" || shasum -a 256 "$name"; } > "$name.sha256")
echo "$out/$name"
