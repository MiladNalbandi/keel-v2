# keel marketplace

The list of plugins [keel](https://github.com/MiladNalbandi/keel-v2) can install. This repo holds **no plugin code**.
It only points to each plugin's repo. Every hour a workflow reads the plugins' releases, checks them, and publishes
one signed file:

```
https://keel-studio.github.io/keel-marketplace/v1/index.json          the catalog index
https://keel-studio.github.io/keel-marketplace/v1/index.json.minisig  its signature (the catalog key)
```

keel reads this index, shows the plugins in **Control › Plugins**, and installs one only when a person says yes.

## How it works

```
 plugin repo                    this repo (sync.yml, every hour)               keel
 ───────────                    ────────────────────────────────               ────
 release v1.0.0:                read the releases of plugins/*.yml
   db-1.0.0.kplug       ──▶     lint each .kplug                         ──▶   reads index.json, checks its
   db-1.0.0.kplug.minisig       check its signature (publisher's key)          signature, then the file's
                                build v1/index.json, sign it, Pages            sha256 and signature at install
```

## The files

| file                  | what                                                                        |
| --------------------- | --------------------------------------------------------------------------- |
| `publishers/<id>.yml` | a publisher: title, its **public** keys (minisign, `RWQ…`), verified or not |
| `plugins/<name>.yml`  | a plugin: title, publisher, repo, category, summary, tags, trust level      |
| `revoked.yml`         | versions keel must never install, and why                                   |
| `.github/workflows/`  | `sync.yml` builds and signs the index; `check-pr.yml` checks a pull request |

Private keys never go into this repo. The catalog key is the Actions secret `KEEL_CATALOG_KEY`.

## Add your plugin

1. Make it with `keel-plugin new <name>`, check it with `keel-plugin lint`, and make a key pair with
   `keel-plugin keygen <you>` (keep the `.key` file secret).
2. Release it in a public GitHub repo: `<name>-<version>.kplug` and its `.kplug.minisig`
   (`keel-plugin pack`, then `keel-plugin sign`).
3. Open a pull request here with `publishers/<you>.yml` (your **public** key) and `plugins/<name>.yml`.
4. The checks pass and a maintainer merges. Your plugin is listed. Plugins that run code or add pages need a
   **verified** publisher, or people must allow unverified publishers in keel.

`keel-plugin` runs with uv: `uvx --from "git+https://github.com/MiladNalbandi/keel-v2@plugin-base#subdirectory=tools/keel-plugin" keel-plugin --help`.

## License

MIT
