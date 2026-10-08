#!/usr/bin/env bash
# Runs each plugin's engine tests (docs/plugins/11-step3-contract.md, wave 0): plugins/<name>/engine/tests, from
# engine/ (keel's own venv), with that plugin's engine folder on PYTHONPATH. Each plugin runs on its own, so their
# conftest files never meet.
#
#   scripts/test-plugins.sh             every plugin in plugins/ that has engine tests
#   scripts/test-plugins.sh map db      only these (a name, or "product" for keel Product in product/)
#
# Extra pytest options go after "--": scripts/test-plugins.sh map -- -k scan
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
names=() extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --) shift; extra=("$@"); break ;;
    *) names+=("$1"); shift ;;
  esac
done

dir_of() { if [ "$1" = product ]; then echo "$root/product"; else echo "$root/plugins/$1"; fi; }

if [ ${#names[@]} -eq 0 ]; then
  for d in "$root"/plugins/*/; do
    [ -d "$d/engine/tests" ] && names+=("$(basename "$d")")
  done
fi
if [ ${#names[@]} -eq 0 ]; then echo "test-plugins: no plugin has engine tests"; exit 0; fi

failed=()
for name in "${names[@]}"; do
  dir="$(dir_of "$name")"
  [ -d "$dir/engine/tests" ] || { echo "test-plugins: $name has no engine tests ($dir/engine/tests)" >&2; failed+=("$name"); continue; }
  echo "== $name"
  # no bytecode flag from the caller's shell: a test may pack the plugin and checks that no cache gets in
  if ! (cd "$root/engine" && env -u PYTHONDONTWRITEBYTECODE PYTHONPATH="$dir/engine" \
        uv run pytest -q -p no:cacheprovider "$dir/engine/tests" ${extra[@]+"${extra[@]}"}); then
    failed+=("$name")
  fi
done

if [ ${#failed[@]} -gt 0 ]; then echo "test-plugins: failed: ${failed[*]}" >&2; exit 1; fi
echo "test-plugins: ok (${names[*]})"
