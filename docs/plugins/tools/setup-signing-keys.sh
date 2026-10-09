#!/usr/bin/env bash
# Makes keel's two signing keys and stores their secret halves as GitHub Actions secrets (docs/plugins/14-publishing.md).
# Milad runs it himself, once (and again only to change the keys). It never prints a secret key and keeps none: the
# keys are made in a temporary folder that is removed at the end, even when something fails.
#
#   docs/plugins/tools/setup-signing-keys.sh [--dry-run] [--root DIR]
#
#   the catalog key   secret KEEL_CATALOG_KEY in keel-studio/keel-marketplace: signs the catalog index
#                     public key -> content/trust/catalog.pub
#   the keel key      secret KEEL_PUBLISHER_KEY in MiladNalbandi/keel-v2: signs keel's plugin releases
#                     public key -> content/trust/keel.pub and docs/plugins/marketplace-repo/publishers/keel.yml
#
#   --dry-run    everything except `gh secret set` (for tests): the keys are made, the public keys written, the
#                secret keys thrown away
#   --root DIR   the keel-v2 checkout that gets the public keys (default: the checkout this script is in)
#
# Needs: gh, logged in with admin rights on both repos (not for --dry-run), and uv (it runs keel-plugin from
# tools/keel-plugin) or KEEL_PLUGIN=<a keel-plugin command>. Run it again later only to change the keys: then every
# plugin release must be signed again, and the keels that have the old catalog.pub need an update.
set -euo pipefail

catalog_repo=keel-studio/keel-marketplace
keel_repo=MiladNalbandi/keel-v2

die() { echo "setup-signing-keys: $*" >&2; exit 1; }

dry=0 root=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1 ;;
    --root) [ $# -ge 2 ] || die "--root needs a folder"; root="$2"; shift ;;
    -h | --help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option $1 (usage: setup-signing-keys.sh [--dry-run] [--root DIR])" ;;
  esac
  shift
done

here="$(cd "$(dirname "$0")" && pwd)"
tool="$(cd "$here/../../.." && pwd)/tools/keel-plugin"
[ -n "$root" ] || root="$(git -C "$here" rev-parse --show-toplevel)"
[ -d "$root" ] || die "no folder $root"
root="$(cd "$root" && pwd)"
publisher="$root/docs/plugins/marketplace-repo/publishers/keel.yml"
[ -f "$publisher" ] || die "there is no $publisher (is $root a keel-v2 checkout on plugin-base?)"
grep -q '^keys:' "$publisher" || die "$publisher has no 'keys:' line to fill in"

if [ -n "${KEEL_PLUGIN:-}" ]; then
  kp=("$KEEL_PLUGIN")
else
  command -v uv > /dev/null 2>&1 || die "uv is needed to run keel-plugin (https://docs.astral.sh/uv/), or set KEEL_PLUGIN"
  kp=(uv run --quiet --project "$tool" keel-plugin)
fi
if [ "$dry" = 0 ]; then
  command -v gh > /dev/null 2>&1 || die "gh is needed (https://cli.github.com)"
  gh auth status > /dev/null 2>&1 || die "gh is not logged in: run 'gh auth login' first"
fi

# the secret keys live only here, readable only by you, and go away at the end
umask 077
tmp="$(mktemp -d "${TMPDIR:-/tmp}/keel-keys.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

"${kp[@]}" keygen catalog --dir "$tmp" > /dev/null
"${kp[@]}" keygen keel --dir "$tmp" > /dev/null
catalog_pub="$(sed -n 2p "$tmp/catalog.pub")"
keel_pub="$(sed -n 2p "$tmp/keel.pub")"
for key in "$catalog_pub" "$keel_pub"; do
  [[ "$key" =~ ^RW[A-Za-z0-9+/]{54}$ ]] || die "keel-plugin keygen did not write a public key line"
done

# the secrets first: if gh fails, no public key changes (run the script again)
if [ "$dry" = 1 ]; then
  echo "dry run: the secrets are not stored (gh secret set KEEL_CATALOG_KEY --repo $catalog_repo," \
    "gh secret set KEEL_PUBLISHER_KEY --repo $keel_repo)"
else
  gh secret set KEEL_CATALOG_KEY --repo "$catalog_repo" < "$tmp/catalog.key" > /dev/null
  echo "stored the secret KEEL_CATALOG_KEY in $catalog_repo"
  gh secret set KEEL_PUBLISHER_KEY --repo "$keel_repo" < "$tmp/keel.key" > /dev/null
  echo "stored the secret KEEL_PUBLISHER_KEY in $keel_repo"
fi
rm -f "$tmp/catalog.key" "$tmp/keel.key"

# only the public keys go into the repo
umask 022
mkdir -p "$root/content/trust"
cp "$tmp/catalog.pub" "$root/content/trust/catalog.pub"
cp "$tmp/keel.pub" "$root/content/trust/keel.pub"
awk -v key="$keel_pub" '/^keys:/ { print "keys: [" key "]"; next } { print }' "$publisher" > "$tmp/keel.yml"
grep -qF "keys: [$keel_pub]" "$tmp/keel.yml" || die "could not write the key into $publisher"
cat "$tmp/keel.yml" > "$publisher"

cat << EOF

public keys (safe to share):
  catalog  $catalog_pub
  keel     $keel_pub
written:
  content/trust/catalog.pub
  content/trust/keel.pub
  docs/plugins/marketplace-repo/publishers/keel.yml (keys:)

Next:
  1. Look at the change (git -C "$root" diff --stat) and commit it on plugin-base.
  2. KEEL_STUDIO_TOKEN (not made here): the release workflow needs a token that can make releases in keel-studio.
     Make a fine-grained token at https://github.com/settings/personal-access-tokens/new
       resource owner: keel-studio; repositories: the keel-plugin-* repos (or all);
       permissions: Contents "Read and write" (Metadata "Read" comes with it); expiry: up to a year.
     Then store it (gh asks for the value, so it is never in your shell history):
       gh secret set KEEL_STUDIO_TOKEN --repo $keel_repo
EOF
