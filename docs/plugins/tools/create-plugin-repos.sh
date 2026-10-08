#!/usr/bin/env bash
# Creates keel's plugin repos, the plugin template and the marketplace repo in the keel-studio organization, each with its
# starter files: a short README, a draft keel-plugin.yml and the MIT license. It only creates the repos that are missing.
#
#   docs/plugins/tools/create-plugin-repos.sh             private repos (make them public later)
#   docs/plugins/tools/create-plugin-repos.sh --public    public repos, like keel-v2
#
# Needs: gh logged in as an owner of keel-studio, python3 (the marketplace check in CI uses PyYAML; this script does not).
set -euo pipefail

owner=keel-studio
visibility="${1:---private}"
case "$visibility" in --private|--public) ;; *) echo "usage: $0 [--private|--public]" >&2; exit 2 ;; esac

gh api "users/$owner" --silent 2> /dev/null || {
  echo "The organization $owner does not exist yet. Create it first: https://github.com/account/organizations/new?plan=free" >&2
  echo "Then run move-plugin-repos.sh: it moves the repos made earlier and creates the rest." >&2
  exit 1
}

here="$(cd "$(dirname "$0")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel)"
out="$(mktemp -d)/repos"
python3 "$here/plugin_repos.py" "$out" "$root/LICENSE" > "$out.tsv"

# gh repo create, waiting out GitHub's "too many repositories, too quickly" limit (10 minutes, up to 6 times)
create() {
  local tries=0 err
  until err="$(gh repo create "$owner/$1" "$visibility" --source "$out/$1" --push --description "$2" 2>&1 > /dev/null)"; do
    if [[ "$err" == *"too many repositories"* ]] && (( ++tries <= 6 )); then
      echo "GitHub says too many new repos; waiting 10 minutes (try $tries of 6), then $1 again ..."
      sleep 600
    else
      echo "$err" >&2
      return 1
    fi
  done
}

# only the repos that are not on GitHub yet
existing="$(gh repo list "$owner" --limit 1000 --json name --jq '.[].name')"
todo="$(while IFS=$'\t' read -r repo description; do
  grep -qx "$repo" <<< "$existing" || printf '%s\t%s\n' "$repo" "$description"
done < "$out.tsv")"
[ -n "$todo" ] && echo "to create: $(cut -f1 <<< "$todo" | tr '\n' ' ')" || echo "every repo is there already"

while IFS=$'\t' read -r repo description; do
  [ -n "$repo" ] || continue
  git -C "$out/$repo" init -q -b main
  git -C "$out/$repo" add -A
  git -C "$out/$repo" commit -q -m "Start the repo: README, manifest draft and license" \
    -m "Part of splitting keel into a small core plus plugins (keel-v2 docs/plugins). The code still lives in keel-v2 for now." \
    -m "Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>"
  create "$repo" "$description"
  gh repo edit "$owner/$repo" --add-topic keel --add-topic keel-plugin > /dev/null
  echo "created: https://github.com/$owner/$repo"
done <<< "$todo"

gh repo edit "$owner/keel-plugin-template" --template > /dev/null && echo "keel-plugin-template is a template repo"
