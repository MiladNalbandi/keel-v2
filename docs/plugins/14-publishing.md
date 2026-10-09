# 14 · Publishing: how a plugin gets into the marketplace

This page says how keel's own plugins are released, how the catalog is built, and what Milad must do **once** before
the first release. The spec is [13-step4-contract.md](13-step4-contract.md), sections 1, 3 and 10.

```
 keel-v2, branch plugin-base           keel-studio/keel-plugin-<name>     keel-studio/keel-marketplace          keel
 ───────────────────────────           ──────────────────────────────     ────────────────────────────          ────
 plugins-release.yml (by hand)         release v1.0.0                     sync.yml (every hour)
   build the web and api parts   ──▶     db-1.0.0.kplug          ──▶       download each release          ──▶  reads v1/index.json
   keel-plugin pack, lint                db-1.0.0.kplug.minisig            lint it, check its signature         checks its signature,
   keel-plugin sign                                                        keel-plugin index, sign              then sha256 and the
   secret: KEEL_PUBLISHER_KEY                                              GitHub Pages: v1/index.json          file's signature
   secret: KEEL_STUDIO_TOKEN                                               secret: KEEL_CATALOG_KEY             at install
```

## The two keys

A key pair has two halves. The **secret key** signs. The **public key** checks. Only public keys go into a repo or the
image. The secret keys live only in GitHub Actions secrets.

| key         | it signs                        | secret key (Actions secret)                          | public key                                                         |
| ----------- | ------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------ |
| **keel**    | each `.kplug` of keel's plugins | `KEEL_PUBLISHER_KEY` in `MiladNalbandi/keel-v2`      | `content/trust/keel.pub`, `publishers/keel.yml` in the marketplace |
| **catalog** | the catalog index `index.json`  | `KEEL_CATALOG_KEY` in `keel-studio/keel-marketplace` | `content/trust/catalog.pub` (keel checks the index with it)        |

The format is minisign (Ed25519). A public key is one line that starts with `RW`. keel's secret key is one line that
starts with `keel-secret-key:v1:`. Nobody ever prints it.

## The tool: keel-plugin

`tools/keel-plugin` is a small Python tool. It needs only the standard library and `cryptography`. It never imports
keel. Run it from this repo, or from anywhere with uv:

```
uv run --project tools/keel-plugin keel-plugin --help
uvx --from "git+https://github.com/MiladNalbandi/keel-v2@plugin-base#subdirectory=tools/keel-plugin" keel-plugin --help
```

| command                                                        | what it does                                                                                                    |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `keel-plugin new <name>`                                       | a new plugin folder: manifest, `engine/`, `web/`, `content/`, README                                            |
| `keel-plugin lint <file.kplug or folder>`                      | the checks keel and the catalog run: manifest, names, parts, `files.sha256`, links, remote code in the web part |
| `keel-plugin pack <built folder> --out dist`                   | `dist/<name>-<version>.kplug` (a tar.gz with no top folder, and `files.sha256`)                                 |
| `keel-plugin keygen <name>`                                    | `<name>.key` (secret, mode 0600) and `<name>.pub`                                                               |
| `keel-plugin sign <file> --key-env VAR` (or `--key-file F`)    | `<file>.minisig`                                                                                                |
| `keel-plugin verify <file> --pub <key line or .pub file>`      | ok, or what does not match                                                                                      |
| `keel-plugin index <marketplace> --releases DIR --key-env VAR` | builds `v1/index.json` and signs it                                                                             |
| `keel-plugin index <marketplace> --check`                      | checks only the marketplace files                                                                               |

`lint` exits with 1 when it finds an error. Warnings (for example a pre-release version) do not stop anything. The
tool reads YAML with its own small reader (it has no PyYAML): anchors (`&`), aliases (`*`) and tags (`!`) are refused
with a clear message. Its tests run in CI (the `keel-plugin` job), with the signature test vector of the contract.

## Release keel's plugins

1. Change the plugin's `version` in its `keel-plugin.yml` (and the `VERSION` in its engine package, when it has one).
   Commit on `plugin-base`.
2. Start the workflow (only on `plugin-base`):

   ```
   gh workflow run plugins-release.yml --repo MiladNalbandi/keel-v2 --ref plugin-base -f plugins=db,map
   gh workflow run plugins-release.yml --repo MiladNalbandi/keel-v2 --ref plugin-base -f plugins=all
   ```

3. The workflow builds each plugin like the Dockerfile does: the web part (`npm run build:plugin`, or
   `build:product`), the api jar (`./gradlew <name>PluginJar`), and the folder (`scripts/build-plugin.sh --no-build`).
   Then `keel-plugin pack`, `lint`, `sign`, and a check that the file verifies with `content/trust/keel.pub`.
4. It makes the release `v<version>` in `keel-studio/keel-plugin-<name>` with the `.kplug` and the `.kplug.minisig`.
   When that release is there already, it gets the new files.
5. Within an hour the marketplace's `sync` lists the new version. To see it sooner:
   `gh workflow run sync.yml --repo keel-studio/keel-marketplace`.

When a secret is missing, the workflow stops at its first step and says which one.

## How the catalog is built

`sync.yml` in `keel-studio/keel-marketplace` runs every hour, after each push to `main`, and by hand:

1. `keel-plugin index . --check`: the publisher, plugin and revoked files are valid.
2. `.github/fetch-releases.sh`: for each `plugins/<name>.yml`, it downloads every release's `.kplug` and
   `.kplug.minisig` from the plugin's repo (draft releases are skipped).
3. `keel-plugin index`: for each file it runs `lint`, checks the signature with the publisher's keys, and checks that
   the manifest's name and publisher match. A file that fails is **left out**, and the log says why. The rest goes in.
4. It writes `v1/index.json` (format 1: sha256, size, `requires` and `permissions` of each version) with an expiry
   date 14 days later, signs it with `KEEL_CATALOG_KEY`, and deploys it to GitHub Pages:
   `https://keel-studio.github.io/keel-marketplace/v1/index.json`.

A pull request to the marketplace runs `check-pr.yml`: the files must be valid, and the newest release of a new or
changed plugin must pass `lint` and carry a good signature. It uses no secret.

To stop a bad version: add it to `revoked.yml` (`- { name: db, version: "1.0.1", why: "…" }`) and push. keel never
installs a revoked version and warns where one is installed.

## What Milad must do once

1. **Make the keys.** In a keel-v2 checkout on `plugin-base`, logged in with `gh` (admin on both repos):

   ```
   docs/plugins/tools/setup-signing-keys.sh --dry-run    # a try: throw-away keys, writes their public keys, stores nothing
   docs/plugins/tools/setup-signing-keys.sh              # for real: new keys, written over the try's public keys
   ```

   It makes both keys in a temporary folder, stores the secret keys with `gh secret set` (as
   `KEEL_CATALOG_KEY` and `KEEL_PUBLISHER_KEY`), deletes them, and writes only the public keys into
   `content/trust/catalog.pub`, `content/trust/keel.pub` and `docs/plugins/marketplace-repo/publishers/keel.yml`.
   Commit these three files on `plugin-base`. No private key ever passes through Claude.

2. **Make the token `KEEL_STUDIO_TOKEN`.** The release workflow needs it to make releases in keel-studio. At
   <https://github.com/settings/personal-access-tokens/new>: resource owner `keel-studio`, repositories: the
   `keel-plugin-*` repos (or all), permission **Contents: Read and write**, an expiry date. (If keel-studio does not
   allow fine-grained tokens yet: Organization settings › Personal access tokens › allow them.) Then:

   ```
   gh secret set KEEL_STUDIO_TOKEN --repo MiladNalbandi/keel-v2      # gh asks for the value
   ```

3. **Fill the marketplace repo.** Its files are in `docs/plugins/marketplace-repo/`. They replace the starter files
   (the old `tools/check.py` and `.github/workflows/check.yml` go):

   ```
   git clone https://github.com/keel-studio/keel-marketplace /tmp/keel-marketplace
   cd /tmp/keel-marketplace && git rm -rq . && cp -R <keel-v2>/docs/plugins/marketplace-repo/. .
   git add -A && git commit -m "The catalog: keel's plugins, the sync and the checks" && git push
   ```

4. **Turn on GitHub Pages** for `keel-studio/keel-marketplace`: Settings › Pages › Source: **GitHub Actions**. Pages
   for free needs a **public** repo. The plugin repos must be public too: keel downloads the files without a token.

5. **Let GitHub start the release workflow.** GitHub starts a `workflow_dispatch` workflow only when the file is also
   on the default branch. Put a copy of `.github/workflows/plugins-release.yml` on `main` (only this file; it refuses
   to run anywhere but `plugin-base`, and a run with `--ref plugin-base` uses plugin-base's copy).

6. **First release.** Run `plugins-release.yml` with `plugins=all`, then `sync.yml` by hand, then open
   `https://keel-studio.github.io/keel-marketplace/v1/index.json`.

## Changing a key later

Run `setup-signing-keys.sh` again. Then every plugin must be released and signed again, the catalog rebuilt, and
keel's image built with the new `catalog.pub`: keels with the old one cannot read the new index. So change keys only
when a secret key may be known to someone.
