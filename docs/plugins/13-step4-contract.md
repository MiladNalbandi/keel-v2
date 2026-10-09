# 13 · Step 4 contract: the marketplace, signed install, agents can ask

Step 4 lets a person find a plugin in a catalog, see what it may do, and install, update, roll back or remove it in a
running keel. Agents can search and **ask**; only a person installs. Branch `feat/plugin-step4`, on top of
`plugin-base` (0.15.4 + steps 1–3). Never merged into `main`, never tagged (see the plan's rule).

**Rule of the track:** everything people had in 0.15.4 stays exactly the same (the parity e2e checks it). Step 4 only
**adds**: the Control › Plugins page, `/api/plugins*` and `/api/marketplace*`, four MCP tools at the end of the list, a
`plugin-install` card in the Inbox. Each addition gets an entry in `e2e/parity/allow.yml` with its reason.

```
 catalog (GitHub Pages)               keel engine (Python)                      keel api + web
 ─────────────────────                ────────────────────                      ─────────────
 v1/index.json  ─┐   HTTPS            marketplace/catalog.py   fetch, verify,   /api/marketplace   search, one plugin
 v1/index.json.minisig ─┼──────────▶            cache, search               /api/plugins       installed, install…
                 │                    marketplace/signing.py   minisign Ed25519  Control › Plugins page
 release assets  │                    marketplace/install.py   download, sha256, Inbox card plugin-install
 name-1.0.0.kplug ─┘                             signature, store.install       restart button / rule
 name-1.0.0.kplug.minisig             mcp_server: 4 tools (search, info,
                                      installed, request)
```

## 0 · Defaults for the open questions (Milad can change them)

| question (README)             | default in step 4                                                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 4 images                      | `:latest` stays core + the keel set; step 6 adds `:core`                                                                                                           |
| 5 signing                     | Ed25519 in the **minisign** format; private keys only in GitHub Actions secrets, made by Milad's own script                                                        |
| 7 other publishers            | content-only plugins from anyone; web and code plugins of an unverified publisher are refused unless the rule "allow unverified publishers" is on (off by default) |
| 8 restart                     | a **Restart keel** button; the rule "restart by itself when no agent step runs" exists and is off                                                                  |
| 9 CodeGraph CLI               | stays in the image (no change)                                                                                                                                     |
| 10 Python libraries           | not in step 4: a manifest that asks for Python libraries is refused with a clear message                                                                           |
| private catalogs with a token | later (a source has a URL and a key; no token yet)                                                                                                                 |
| a flow waiting for a plugin   | later: `keel_plugin_request` never blocks; `needs_plugins` refuses the start and asks (§9)                                                                         |

## 1 · Signatures (minisign, Ed25519)

keel signs and checks files in the [minisign](https://jedisct1.github.io/minisign/) format, so a publisher may also use
the `minisign` tool.

- **Public key** (one line, as in a minisign `.pub` file's second line): `base64("Ed" + key_id[8] + public_key[32])`,
  for example `RWQBAgMEBQYHCHm1Vi6P5lT5QHixEuipi6eQH4U65pW+1+DjkQutBJZk`. A `.pub` file is
  `untrusted comment: <text>\n<key line>\n`.
- **Signature file** `<file>.minisig`, four lines:
  `untrusted comment: <text>` / `base64(alg[2] + key_id[8] + signature[64])` / `trusted comment: <text>` /
  `base64(global_signature[64])`.
  - `alg` `ED`: the signature is over `BLAKE2b-512(file)` (minisign's default). `Ed`: over the file itself (legacy).
    keel **verifies both** and **signs `ED`**.
  - The global signature is over `signature[64] + trusted_comment_text` (the text after `trusted comment: `). keel
    checks it too. keel writes the trusted comment `timestamp:<unix seconds>\tfile:<file name>`.
  - The key ids of the signature and the public key must be the same.
- **keel's secret key** (for keel's own tools only; minisign's own encrypted secret key file is not read):
  `keel-secret-key:v1:` + `base64(key_id[8] + ed25519_seed[32])`. It lives only in a file with mode 0600 or in an
  environment variable / Actions secret. It is never written into a repo, an image or a log.

**Test vector** (a test key: never use it for anything else). Both implementations (§5 engine, §11 the `keel-plugin`
CLI) must pass it: they verify both signatures, and signing the message with the secret key gives exactly the `ED`
signature below (Ed25519 is deterministic).

```
secret   keel-secret-key:v1:AQIDBAUGBwgBAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4fIA==
public   RWQBAgMEBQYHCHm1Vi6P5lT5QHixEuipi6eQH4U65pW+1+DjkQutBJZk
message  "keel test vector\n"   (17 bytes)
trusted  "timestamp:1760000000\tfile:test.txt"

ED:
untrusted comment: signature from keel test key
RUQBAgMEBQYHCPMsy8ifCtByWeNYRTEvnCTCIhujuPevW5G+lHgNDw8QgmEgvAp4zen2Aysfolw/3msiNED7JFa6kbT1lG7TSwU=
trusted comment: timestamp:1760000000	file:test.txt
KvuInzyRMTOZtuQBooVaFuyCbaAOwzqjLOmHymXKBN0myaUecmFw6gtPh1HKKeNI37p/PNztORc9ihkjzQjCBQ==

Ed:
untrusted comment: signature from keel test key
RWQBAgMEBQYHCAMQu/HNJ1rnfV7dwPTWKv8tRNvZdEjh8SFqAKpijxpMIGBWkJ95aCohGjnPF61JPY8V6TM0ZCIkivEz1SZOUgA=
trusted comment: timestamp:1760000000	file:test.txt
ym42po2TeCBYbkcsPHxkwODxoFVPPAiAgScygyn2Bjf5jWJHgqybPJ1wd31HR1tADSfUFmaPit5AUrorO7H3Ag==
```

(The trusted comment has a real tab between the timestamp and `file:`.)

## 2 · Trust roots in the image

- `content/trust/catalog.pub`: the official catalog's public key. `content/trust/keel.pub`: the keel publisher's key.
  Only **public** keys. Until Milad makes the real keys (§11), these files do not exist: the official source then
  shows "not set up yet" and nothing breaks.
- The official source: id `keel`, title `keel marketplace`,
  URL `https://keel-studio.github.io/keel-marketplace/v1/index.json`, key from `catalog.pub`. A person may add more
  sources (URL + public key) in Sources and rules; they cannot change the official one's key.
- Catalog URLs and download URLs must be `https://`. `http://` is allowed only for `127.0.0.1`, `localhost` and
  `host.docker.internal` when the environment variable `KEEL_MARKETPLACE_ALLOW_HTTP=1` is set (tests only).

## 3 · The catalog index (format 1)

`v1/index.json` and `v1/index.json.minisig` (signed by the catalog key). The shape of 04-marketplace.md §4.1, with
minisign keys:

```json
{
  "format": 1,
  "built": "2026-10-09T06:00:00Z",
  "expires": "2026-10-23T06:00:00Z",
  "publishers": {
    "keel": { "title": "keel", "keys": ["RWQ…"], "verified": true }
  },
  "plugins": [
    {
      "name": "db",
      "title": "Database",
      "publisher": "keel",
      "category": "code",
      "summary": "…",
      "tags": ["sql"],
      "repo": "https://github.com/keel-studio/keel-plugin-db",
      "trust": "code",
      "versions": [
        {
          "version": "1.0.0",
          "released": "2026-10-09",
          "requires": {
            "sdk": 1,
            "keel": ">=0.15.4",
            "plugins": { "code": ">=1.0.0" }
          },
          "url": "https://github.com/keel-studio/keel-plugin-db/releases/download/v1.0.0/db-1.0.0.kplug",
          "sha256": "…64 hex…",
          "size": 2201344,
          "permissions": {
            "secrets": ["database"],
            "network": ["from-connections"],
            "workspace": "read"
          }
        }
      ]
    }
  ],
  "revoked": [
    {
      "name": "triage",
      "version": "0.2.0",
      "why": "sent errors to a wrong host"
    }
  ]
}
```

- **trust** is `content` | `web` | `code` (03-security.md §3.1). **category**: `code`, `knowledge`, `tickets`,
  `review`, `product`, `other`.
- The version's signature is at `url + ".minisig"`, signed by one of its publisher's keys.
- **Checks when reading an index** (any failure: the source keeps its last good copy and shows the problem):
  1. the signature verifies with the source's key; 2. `format` is 1; 3. names match keel's plugin name rule, versions are
     `x.y.z`, `sha256` is 64 hex, URLs follow §2; 4. a plugin's publisher is listed. An entry that fails is left out and
     listed as a problem; the rest of the index is used.
- **Expired** (`expires` is past): search still works and says "the catalog is old"; install and update are refused
  until a refresh works.
- **Revoked** versions are never installed. An installed revoked version shows a red warning and the fixed version.
- keel reads each source at start (when its copy is older than 6 hours), every 6 hours, and on Refresh. The copy in
  `/data` lets keel work offline.

## 4 · Data on disk (`$KEEL_DATA/plugins/`)

| path                            | what                                                                                                                          |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `sources.json`                  | `{"sources": [{id, title, url, key, on}]}`; the official one is always there                                                  |
| `rules.json`                    | `{agents_may_ask: true, allow_unverified: false, check_daily: true, restart_when_idle: false}`                                |
| `catalog/<id>.json`, `.minisig` | the last good copy of each source; `catalog/<id>.meta.json`: `{fetched_at, ok, problem}`                                      |
| `downloads/`                    | files while they download and are checked; removed after                                                                      |
| `store/<name>/<version>/`       | as today (step 1); a marketplace install keeps the current and the previous version, older ones go                            |
| `installed.json`                | as today, plus for a marketplace install: `source: "marketplace"`, `catalog`, `publisher`, `trust`, `permissions`, `previous` |

## 5 · Engine: `keel_engine/marketplace/` (core)

| module       | what                                                                                                                                                                                                                                |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `signing.py` | `PublicKey.parse(line)`, `verify(data, minisig_text, keys) -> key_id`, `sign(data, secret, file_name) -> text` (§1)                                                                                                                 |
| `sources.py` | sources.json and rules.json: read, write, the official source and its key                                                                                                                                                           |
| `catalog.py` | fetch (urllib, timeouts, size limit 5 MB), verify, cache, the checks of §3, `search(q, category)` over name, title, summary, tags; each hit says `installed`, `update` (a newer fitting version), `fits` (sdk, keel version, needs) |
| `install.py` | `plan(name, version)` → the plugins to install (needed plugins from the same catalog first), their permissions and checks; `install`, `update`, `rollback`, `remove(data=keep                                                       | delete)`, `pending_restart()` |

**Install** (all or nothing, nothing runs at install time):

1. choose the newest version that fits (sdk 1, keel's version in `requires.keel`, its needed plugins);
2. refuse when: revoked; the index is expired; the publisher is unverified and the trust is `web` or `code` and
   `allow_unverified` is off; the manifest asks for Python libraries (§0); a needed plugin is not in the catalog;
3. download to `downloads/` (stop above the entry's `size`, max 200 MB), check `sha256`, download `.minisig` and verify
   with the publisher's keys;
4. `store.install(file, source="marketplace", meta={...})` (store.py gets these keyword arguments; its checks stay), and
   check that the manifest's name, version and permissions are the catalog's;
5. events on the bus: `plugin.install.started`, `plugin.install.done`, `plugin.install.failed` `{name, version, why}`.

**Update** is an install of a newer version; it is **refused when the new version asks for more permissions** (the
answer lists the difference; the person approves it through a request, §7). The old version stays for **rollback**.
**Remove**: an image plugin cannot be removed (only turned off); a plugin another installed plugin needs is refused
(the answer lists them); `data=delete` removes `$KEEL_DATA/plugins/data/<name>/` only; its tables always stay.
**pending_restart()**: what loads after a restart differs from `run/resolved.json` (the plugins and versions).

**Engine routes** (internal, called by the api with the internal token, like the others):

| route                                                               | what                                                                                                      |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `GET /marketplace/search?q=&category=`                              | hits + `{sources: [{id, ok, problem, old}]}`                                                              |
| `GET /marketplace/plugins/{name}`                                   | one plugin: versions, permissions, needs, the checks keel will run, `plan` for the newest fitting version |
| `POST /marketplace/refresh`                                         | read every source now                                                                                     |
| `GET /marketplace/installed`                                        | installed + image plugins: version, parts, from, on, status, problems, revoked, update, `pending_restart` |
| `POST /marketplace/install {name, version?}`                        | install (§5)                                                                                              |
| `POST /marketplace/installed/{name}/update {version?}`, `/rollback` | update, rollback                                                                                          |
| `PUT /marketplace/installed/{name} {on}`                            | on or off (dependents go off too, listed)                                                                 |
| `DELETE /marketplace/installed/{name}?data=keep\|delete`            | remove                                                                                                    |
| `POST /marketplace/install-file {path}`                             | install a `.kplug` file from `/data` (unsigned: `source: "file"`)                                         |
| `GET/PUT /marketplace/sources`, `/marketplace/rules`                | §4                                                                                                        |

Errors: HTTP 4xx `{"error": "…", "hint": "…"}` in plain words ("the signature does not match the publisher's key").
The CLI gets `keel-engine plugins search <q>`, `get <name>[@version]`, `update <name>`, `rollback <name>`,
`remove <name> [--delete-data]`; `keel2 plugins list|install <name>|remove <name>|safe` call them.

## 6 · The api: `/api/plugins` and `/api/marketplace` (core)

The endpoints of 04-marketplace.md §4.3, proxied to §5's routes, plus:

- `GET /api/plugins` also has `restart: {pending: bool, scheduled: bool}` and the plugin host's problems.
- `POST /api/plugins/restart {now?}`: `now` restarts at once (PluginRestart, exit 75); else it waits until no agent step
  runs, then restarts (`scheduled`). The rule `restart_when_idle` does this by itself after an approved install.
- **`plugin-install` approvals**: an `ApprovalHandler` (step 2's interface, `approvals/Approvals.kt`), decisions
  `approve` | `deny`. `POST /api/plugins/requests {name, version?, reason, source, project?}`: refused (409, plain
  words) when `agents_may_ask` is off; one waiting request per plugin (a second one joins it: its reason is added);
  waiting requests end after 7 days. Approve → install (§5) → `restart_when_idle` or the restart banner; deny → nothing.
  The Inbox shows the card through the existing `ApprovalInboxSource`: title "Install <Title> <version>?", the
  reason in the agent's words, the trust level, the permissions, the plugins it needs.
- A notification "<Title> is installed: restart keel to use it" after an install.
- Every install, update, rollback, remove and decision is an event in keel's event log with who and when.

## 7 · Agents: four MCP tools (core, `mcp_server.py`)

| tool                      | mode      | what                                                                                  |
| ------------------------- | --------- | ------------------------------------------------------------------------------------- |
| `keel_marketplace_search` | read      | `{q, category?}` → name, title, trust, summary, installed                             |
| `keel_plugin_info`        | read      | `{name}` → versions, permissions, needs                                               |
| `keel_plugins_installed`  | read      | what this keel has, and what is on in this project                                    |
| `keel_plugin_request`     | read-safe | `{name, version?, reason}` → a `plugin-install` request; listed in read-only mode too |

They come **after** every tool keel lists today, in this order, so the existing list keeps its order (the parity e2e
compares it; the four are allowed additions). There is **no** install tool. With `agents_may_ask` off,
`keel_plugin_request` answers "Ask the person to install it in Control › Plugins".

## 8 · `needs_plugins` (workflows)

A workflow may say `needs_plugins: [db, git]` (validated: plugin names). Starting it when one of them is not loaded
answers 409 `{error: "This workflow needs Database.", missing: ["db"], requests: ["a_…"]}`: keel opens a
`plugin-install` request for each missing plugin (reason: "the workflow <name> needs it") and the web says so. No
running flow waits for an install in step 4.

## 9 · The web (core)

- **Control › Plugins** (`#/plugins`, id `plugins`, group `control`, order 40, icon `plugins`), three tabs:
  - **Installed**: each plugin with version, parts, from (image / marketplace / file), on/off, status and problems,
    revoked warning, Update, Roll back, Remove; a banner "Changes wait for a restart" with **Restart keel**.
  - **Marketplace**: search, categories, cards (trust level, Install / Installed / Update), a detail panel (summary,
    versions, permissions with their levels, needed plugins, the checks keel runs). **Install** opens a dialog that
    lists everything that will be installed and their permissions; it calls `POST /api/plugins/install`.
  - **Sources and rules**: the sources (official + added; add with URL + key; refresh; problems), the four rules,
    "Install from file" (a path in `/data`).
- **The Inbox** shows `plugin-install` cards (Approve and install / Deny) through the approvals it already lists.
- **Core only** (no plugin pages loaded): the Project group shows a "Start with a set" card that links to
  `#/plugins?set=<id>`. The sets are in `content/plugin-sets.yml`: Developer (code, git, review, db, ci, graph,
  keelbot), Review (code, git, review, keelbot), Knowledge (wiki, map, graph), Tickets (tasks, jira). A set installs
  what is missing (or turns on what the image has and is off).
- In a normal keel (all plugins from the image) nothing else in the menu or the pages changes.

## 10 · Publishing (first-party now, others in step 7)

- **`tools/keel-plugin/`**: a small standalone Python package (no keel imports; stdlib + `cryptography`), command
  `keel-plugin`: `keygen` (writes `<name>.key` mode 0600 and `<name>.pub`), `sign <file> --key-env VAR | --key-file F`,
  `verify <file> --pub P`, `pack <plugin-dir>` (like `scripts/build-plugin.sh`'s package step), `lint <kplug|dir>`
  (manifest, names and prefixes, `files.sha256`, no links, no absolute paths), `index <marketplace-dir>` (builds and
  signs `v1/index.json` from `publishers/`, `plugins/` and the releases), `new <name>` (from the template). Passes §1's
  test vector. Its own tests run in CI.
- **`.github/workflows/plugins-release.yml`** (workflow_dispatch only, on `plugin-base`): builds the chosen plugins'
  `.kplug`, signs them with the secret `KEEL_PUBLISHER_KEY`, and creates the release `v<version>` in
  `keel-studio/keel-plugin-<name>` with the secret `KEEL_STUDIO_TOKEN`. It does nothing until Milad sets the secrets.
- **`docs/plugins/marketplace-repo/`**: the files for `keel-studio/keel-marketplace` (`publishers/keel.yml`,
  `plugins/<name>.yml` for the eleven plugins and Product, `revoked.yml`, `.github/workflows/sync.yml` (hourly and by
  hand: read the listed repos' releases, check sha256 and signatures, build and sign the index with the secret
  `KEEL_CATALOG_KEY`, deploy to Pages) and `check-pr.yml`). Pushed there only when Milad says so.
- **`docs/plugins/tools/setup-signing-keys.sh`**: Milad runs it himself. It makes the catalog and publisher keys with
  `keel-plugin keygen` in a temporary folder, stores the private keys with `gh secret set` (reading the files, never
  printing them), deletes them, and writes only the public keys into `content/trust/` and the marketplace files.
  No private key ever passes through Claude.

## 11 · Tests and checks

- engine: signing (the test vector, a changed byte, a wrong key id, a wrong global signature), catalog (a test catalog
  on a local HTTP server: good, bad signature, expired, revoked, a broken entry left out), install / update / rollback /
  remove / refused cases, `pending_restart`, `needs_plugins`, the MCP tools (order: the four at the end).
- api: the endpoints against the stub engine, the `plugin-install` handler (join, 7 days, deny, approve → install),
  restart now / when idle.
- web: the Plugins page's three tabs, the dialog, the Inbox card, the core-only "Start with a set".
- `tools/keel-plugin`: its tests (test vector, keygen → sign → verify, pack → lint, index).
- **e2e `e2e/marketplace/e2e.py`** on a throw-away keel `keel-mkt` (port 8096, own volume, removed after) with a test
  catalog served from the host (`host.docker.internal`, `KEEL_MARKETPLACE_ALLOW_HTTP=1`) and a test plugin `hello`
  (content + web + engine): search; install → restart → loaded; update; rollback; remove; bad signature, wrong
  sha256, expired index, revoked version refused; an agent request → an Inbox card → approve → installed after a
  restart; `agents_may_ask` off → refused.
- The parity e2e against 0.15.4: 0 differences beyond step 4's listed additions.
- CI: `ci.yml` also runs on pushes to `plugin-base`, and runs `tools/keel-plugin`'s tests.
