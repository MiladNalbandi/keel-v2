#!/usr/bin/env bash
# Creates keel's plugin repos, the plugin template and the marketplace repo on GitHub (MiladNalbandi), each with its
# starter files: a short README, a draft keel-plugin.yml and the MIT license. Repos that already exist are skipped.
#
#   docs/plugins/tools/create-plugin-repos.sh             private repos (make them public later)
#   docs/plugins/tools/create-plugin-repos.sh --public    public repos, like keel-v2
#
# Needs: gh logged in as MiladNalbandi, python3 (the marketplace check in CI uses PyYAML; this script does not).
set -euo pipefail

owner=MiladNalbandi
visibility="${1:---private}"
case "$visibility" in --private|--public) ;; *) echo "usage: $0 [--private|--public]" >&2; exit 2 ;; esac

here="$(cd "$(dirname "$0")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel)"
out="$(mktemp -d)/repos"
python3 "$here/plugin_repos.py" "$out" "$root/LICENSE" > "$out.tsv"

while IFS=$'\t' read -r repo description; do
  if gh repo view "$owner/$repo" > /dev/null 2>&1; then
    echo "exists, skipped: $owner/$repo"
    continue
  fi
  git -C "$out/$repo" init -q -b main
  git -C "$out/$repo" add -A
  git -C "$out/$repo" commit -q -m "Start the repo: README, manifest draft and license" \
    -m "Part of splitting keel into a small core plus plugins (keel-v2 docs/plugins). The code still lives in keel-v2 for now." \
    -m "Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>"
  gh repo create "$owner/$repo" "$visibility" --source "$out/$repo" --push --description "$description" > /dev/null
  gh repo edit "$owner/$repo" --add-topic keel --add-topic keel-plugin > /dev/null
  echo "created: https://github.com/$owner/$repo"
done < "$out.tsv"

gh repo edit "$owner/keel-plugin-template" --template > /dev/null && echo "keel-plugin-template is a template repo"
