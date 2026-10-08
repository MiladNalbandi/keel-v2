#!/usr/bin/env bash
# Moves keel's plugin repos, the plugin template and the marketplace repo from MiladNalbandi into the keel-studio
# organization, points the links inside them to keel-studio, then creates the repos that are still missing there.
# GitHub keeps redirects from the old addresses. Run it again at any time: it only does what is left.
#
#   docs/plugins/tools/move-plugin-repos.sh --public
#
# Needs: the organization (https://github.com/account/organizations/new?plan=free, name keel-studio), gh logged in as
# MiladNalbandi (an owner of it), python3, perl.
set -euo pipefail

from=MiladNalbandi
to=keel-studio
visibility="${1:---public}"

here="$(cd "$(dirname "$0")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel)"
work="$(mktemp -d)"

gh api "orgs/$to" --silent 2> /dev/null || {
  echo "The organization $to does not exist yet. Create it first: https://github.com/account/organizations/new?plan=free" >&2
  exit 1
}

python3 "$here/plugin_repos.py" "$work/starter" "$root/LICENSE" | cut -f1 > "$work/names"

# 1. move: repos still owned by $from go to $to (a moved repo answers at both addresses, so ask who owns it)
while read -r repo; do
  owner="$(gh api "repos/$from/$repo" --jq .owner.login 2> /dev/null || true)"
  [ "$owner" = "$from" ] || continue
  gh api -X POST "repos/$from/$repo/transfer" -f new_owner="$to" --silent
  for _ in $(seq 1 30); do
    [ "$(gh api "repos/$to/$repo" --jq .owner.login 2> /dev/null || true)" = "$to" ] && break
    sleep 2
  done
  echo "moved: $from/$repo → https://github.com/$to/$repo"
done < "$work/names"

# 2. links: point README, manifest and catalog links to $to (keel-v2 itself stays at $from)
while read -r repo; do
  gh api "repos/$to/$repo" --silent 2> /dev/null || continue
  dir="$work/clone/$repo"
  gh repo clone "$to/$repo" "$dir" -- -q
  files="$(git -C "$dir" grep -l -e "github.com/$from/keel-plugin-" -e "github.com/$from/keel-marketplace" || true)"
  [ -n "$files" ] || continue
  (cd "$dir" && echo "$files" | xargs perl -pi -e "s#github.com/$from/(keel-plugin-|keel-marketplace)#github.com/$to/\$1#g")
  git -C "$dir" commit -q -am "Links: the repo moved to the $to organization" -m "Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>"
  git -C "$dir" push -q
  echo "links fixed: $to/$repo"
done < "$work/names"

# 3. create what is still missing, in $to
"$here/create-plugin-repos.sh" "$visibility"
