#!/usr/bin/env bash
# Downloads the plugins' release files for `keel-plugin index` (sync.yml, check-pr.yml):
#
#   .github/fetch-releases.sh <out-dir> [--latest] [name …]
#
# For each plugins/<name>.yml (every one when no name is given): the .kplug and .kplug.minisig of each release of its
# repo (only the newest with --latest) go into <out-dir>/<name>/<tag>/, each .kplug with <file>.json
# {"url": its download URL, "released": "YYYY-MM-DD"}. Draft releases are skipped. A repo with no release yet is
# skipped with a note. Needs gh (with GH_TOKEN) and jq.
set -euo pipefail

out="${1:?usage: fetch-releases.sh <out-dir> [--latest] [name …]}"
shift
latest=0
if [ "${1:-}" = "--latest" ]; then latest=1; shift; fi
names=("$@")
if [ ${#names[@]} -eq 0 ]; then
  for f in plugins/*.yml; do names+=("$(basename "$f" .yml)"); done
fi
mkdir -p "$out"

for name in "${names[@]}"; do
  [[ "$name" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || { echo "::error::'$name' is not a plugin name" >&2; exit 1; }
  f="plugins/$name.yml"
  [ -f "$f" ] || { echo "::error::there is no $f" >&2; exit 1; }
  repo="$(sed -n 's|^repo:[[:space:]]*["'\'']\{0,1\}https://github\.com/\([A-Za-z0-9_.-]*/[A-Za-z0-9_.-]*\).*|\1|p' "$f" | head -1)"
  [ -n "$repo" ] || { echo "::error::$f has no repo: https://github.com/<owner>/<repo>" >&2; exit 1; }
  # newest first: tag and day
  query='.[] | select(.draft | not) | [.tag_name, ((.published_at // .created_at)[0:10])] | @tsv'
  if ! releases="$(gh api "repos/$repo/releases?per_page=100" --jq "$query" 2> /dev/null)"; then
    echo "::notice::$name: the releases of $repo cannot be read (no repo yet, or it is private)"
    continue
  fi
  if [ "$latest" = 1 ]; then releases="$(head -n 1 <<< "$releases")"; fi
  if [ -z "$releases" ]; then
    echo "$name: no release yet"
    continue
  fi
  while IFS=$'\t' read -r tag day; do
    dir="$out/$name/$tag"
    mkdir -p "$dir"
    if ! gh release download "$tag" --repo "$repo" --dir "$dir" --clobber \
      --pattern '*.kplug' --pattern '*.kplug.minisig' 2> /dev/null; then
      echo "::warning::$name $tag: the release has no .kplug file"
      continue
    fi
    for k in "$dir"/*.kplug; do
      [ -f "$k" ] || continue
      file="$(basename "$k")"
      jq -n --arg url "https://github.com/$repo/releases/download/$tag/$file" --arg day "$day" \
        '{url: $url, released: $day}' > "$k.json"
      echo "$name $tag: $file"
    done
  done <<< "$releases"
done
