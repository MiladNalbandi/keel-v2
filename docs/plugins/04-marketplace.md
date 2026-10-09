# 4 · The marketplace

The marketplace is how people **find**, **install**, **update** and **publish** plugins. It has four pieces:

```
  ┌───────────────────────────┐      ┌───────────────────────────┐      ┌──────────────────────────┐
  │ plugin repos              │      │ marketplace repo          │      │ keel (each install)      │
  │ keel-plugin-db, …         │ ───▶ │ publishers/  plugins/     │ ───▶ │ marketplace client (api) │
  │ GitHub release per tag:   │ sync │ CI: check, lint, build    │ HTTPS│ Plugins page (web)       │
  │ .kplug + .sig             │      │ index.json + .sig (Pages) │      │ MCP tools for agents     │
  └───────────────────────────┘      └───────────────────────────┘      └──────────────────────────┘
       1 publish                          2 catalog                          3 client   4 agents
```

## 4.1 The catalog (a git repo + one JSON file)

A small repo, [`keel-studio/keel-marketplace`](https://github.com/keel-studio/keel-marketplace). It is the list of what keel
can install. It holds **no plugin code**, only pointers.

```
keel-marketplace/
├── publishers/
│   ├── keel.yml           name, title, keys: [ed25519:…], verified: true, contact
│   └── ana-k.yml
├── plugins/
│   ├── db.yml             name, publisher, repo, title, summary, category, tags, icon
│   └── release-notes.yml
├── revoked.yml            versions that must not be installed
└── .github/workflows/
    ├── check-pr.yml       a new publisher or plugin: validate, lint the latest release
    └── sync.yml           every hour: read new releases, verify, add, build and sign the index
```

The built file `v1/index.json` is served by GitHub Pages, next to `v1/index.json.sig`:

```json
{
  "format": 1,
  "built": "2026-10-08T06:00:00Z",
  "expires": "2026-10-22T06:00:00Z",
  "publishers": { "keel": { "keys": ["ed25519:K7f3…c21"], "verified": true } },
  "plugins": [
    {
      "name": "db",
      "title": "Database",
      "publisher": "keel",
      "category": "code",
      "summary": "Connect a database…",
      "repo": "https://github.com/keel-studio/keel-plugin-db",
      "trust": "code",
      "versions": [
        {
          "version": "1.4.0",
          "released": "2026-10-02",
          "requires": {
            "sdk": 1,
            "keel": ">=0.16.0,<1.0.0",
            "plugins": { "code": ">=1.0.0,<2.0.0" }
          },
          "url": "https://github.com/keel-studio/keel-plugin-db/releases/download/v1.4.0/db-1.4.0.kplug",
          "sha256": "9c41…e07a",
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

The index is small (well under 1 MB). keel reads it at start and every 6 hours, keeps a copy in `/data`, and works
offline with that copy.

**More catalogs:** Sources and rules lets a person add another catalog (a URL plus its public key), for example a
company's private list. A private catalog is read with a token from Connections.

## 4.2 Search and the Plugins page

Search runs in keel's api over the cached index (name, title, summary, tags), so the web and the agents get the same
results. No search service is needed.

The web gets one page, **Control › Plugins**, with three tabs:

| tab                   | what it shows                                                                                                                                             |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Installed**         | each plugin: version, parts, from (image / downloaded), status, on/off, update, remove; a banner when changes wait for a restart                          |
| **Marketplace**       | search, categories, cards with the trust level and Install / Installed / Update; a detail page with permissions, needs, versions and the checks keel runs |
| **Sources and rules** | catalogs and their keys, install from file or URL, "agents may ask", "check for updates daily", "allow unverified publishers"                             |

In a **core-only** keel the Project group of the menu is empty and invites installs ("Start with a set": Developer,
Review, Knowledge, Tickets). The mockup shows all of this.

## 4.3 The api

All under `/api/plugins` and `/api/marketplace`. The old per-project switch `PUT /api/projects/{pid}/plugins/{name}`
stays.

| method and path                                                 | what it does                                                                    |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `GET /api/marketplace?q=&category=`                             | search the cached index; each hit says installed / update / fits this keel      |
| `GET /api/marketplace/{name}`                                   | one plugin: versions, permissions, needs, checks                                |
| `POST /api/marketplace/refresh`                                 | read the catalogs now                                                           |
| `GET /api/plugins/installed`                                    | installed plugins with status and problems (`GET /api/plugins` stays 0.15.4's)  |
| `POST /api/plugins/install` `{name, version?}`                  | starts an install job; progress comes as `plugin.install.*` events              |
| `POST /api/plugins/{name}/update` `{version?}`                  | same, for a new version; refused if it asks for new permissions (use a request) |
| `POST /api/plugins/{name}/rollback`                             | back to the kept version                                                        |
| `PUT /api/plugins/{name}` `{on}`                                | turn on or off (dependents follow, shown first)                                 |
| `DELETE /api/plugins/{name}?data=keep\|delete`                  | remove                                                                          |
| `POST /api/plugins/restart` `{now?}`                            | restart keel when no agent step runs (or now)                                   |
| `POST /api/plugins/requests` `{name, version?, reason, source}` | an install **request** → an Inbox approval (4.4)                                |
| `GET/PUT /api/plugins/sources`, `/api/plugins/rules`            | catalogs and rules                                                              |

`keel2` gets matching commands for the terminal: `keel2 plugins list | install <name> | remove <name> | safe`.

## 4.4 Agents can ask, people decide

KeelBot and flow agents can find a plugin and **ask** for it. They can never install it.

```
  agent (KeelBot or a flow step)        keel MCP server + api               Inbox                 person
  ──────────────────────────────        ─────────────────────               ─────                 ──────
  keel_marketplace_search("database") ▶ search the index
                                    ◀── db 1.4.0, trust, permissions, needs
  keel_plugin_request(db, 1.4.0,
    reason="read the sessions schema",
    wait=true)  ──────────────────────▶ rule "agents may ask" on?
                                        one open request per plugin
                                        new row in approvals ───────────▶ card: Database 1.4.0
                                    ◀── "request r-41 waits"             reason, permissions,  ──▶ reads
  the flow waits at a "dependency"                                       needs, what happens        │
  gate (KeelBot goes on without it)                                      [Approve and install] ◀────┘
                                        install (02, 2.8) ◀───────────── approved by the person
                                        restart when no step runs
  event plugin.installed(db) ◀───────── the gate opens, the flow goes on
  the agent now has db_* tools
```

**New MCP tools** in keel's own server (`engine/keel_engine/mcp_server.py`):

| tool                      | mode      | what it does                                             |
| ------------------------- | --------- | -------------------------------------------------------- |
| `keel_marketplace_search` | read      | search; returns name, title, trust, summary, permissions |
| `keel_plugin_info`        | read      | one plugin with versions and needs                       |
| `keel_plugins_installed`  | read      | what this keel has, and what is on in this project       |
| `keel_plugin_request`     | read-safe | creates an approval only; allowed even in read-only mode |

There is **no** install tool.

**Rules for requests:**

- A reason is required. The card shows it in the agent's words.
- One open request per plugin. A second agent asking the same thing joins the first request.
- A request expires after 7 days.
- With `wait=true` a flow pauses at a **dependency** gate. That gate kind already exists in the Inbox.
- The rule "Agents may ask to install plugins" can turn this off. Then the tool answers "ask the person to install it".
- A workflow can also declare `needs_plugins: [db]`. Starting it without the plugin opens the same request.

**This needs a core Approvals service.** Today the only "ask a person" place is inside KeelBot and lives in memory
(`helper.py:487-581`). Step 2 ([05-migration.md](05-migration.md)) adds an `approvals` table
(`id, project_id, kind, title, detail, payload_json, status, requested_by, decided_by, decided_at, why`) and an
`InboxSource` for it. The guard's ask mode, `keel2 mcp --write` and KeelBot then use it too.

## 4.5 Publishing a plugin

**For the keel set** (first-party): each plugin repo has the same CI. A tag `v1.4.0` builds the `.kplug`, signs it with
the keel publisher key, and makes a GitHub release. The marketplace's hourly `sync` picks it up.

**For anyone else:**

```
  1  keel-plugin new my-plugin          a template repo: manifest, engine/, api/, web/, content/, CI
  2  write it; keel-plugin dev          runs it against a local keel (install from folder, unsigned)
  3  keel-plugin lint                   the same checks the catalog runs
  4  keel-plugin keygen                 an Ed25519 key pair; private key → your repo's Actions secret
  5  git tag v0.1.0 && push             CI builds, signs, releases
  6  pull request to keel-marketplace   publishers/you.yml (your public key) + plugins/my-plugin.yml
  7  checks pass + a maintainer merges  listed as "community"; later "verified" after a review
```

`keel-plugin` is a small CLI that ships inside the SDK packages (Python, run with `uvx keel-plugin`).

**Content-only plugins** (workflows, agents, skills) have the easiest path: no code review is needed to list them.

## 4.6 What the marketplace does not do (now)

- No payments, no ratings, no download counts (GitHub release counts can come later).
- No server of our own. GitHub releases and GitHub Pages are enough.
- No automatic updates. A person always says yes.
