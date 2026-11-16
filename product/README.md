# keel Product (add-on, beta)

keel Product takes an idea from a product owner or product manager to the teams' stories:

    idea → brief → impact → decision → plan → delivery (keel Tasks or Jira) → outcome

keel writes and checks each stage; people decide at every gate. It reads the code for the impact (read only, one
analyst per repo), knows which team owns which repo (by hand or from CODEOWNERS), keeps every document as a versioned
commit in its own product repo, and hands each story to its repo's project as a keel task with its acceptance
criteria, so a developer starts it as a normal keel flow.

**Status: beta (0.1.0-beta.1).** It is a plugin package (`product-<version>.kplug`). Since keel 0.31 it is inside every
keel: the full Docker image, the native install and the keel app (`/opt/keel-v2/plugins` in the image), **off until a
person turns it on** (`starts_off: true` in its manifest). Turn it on in **Add features** (the keel Product card: On ·
Off, then Restart keel now) or in Settings › Plugins; off again the same way. There is no separate product image any
more: an old `:product-beta` keel moves to `:latest` with `keel2 update`, and Product stays on. Do not use it for real
work until its release gates pass.

## How it is versioned

keel Product has its own version, apart from keel's:

|                   | where                                                                                                                             | now               |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| keel Product      | `keel-plugin.yml` `version`, `engine/keel_product/__init__.py` `VERSION`, `api/.../ProductAutoConfiguration.kt` `PRODUCT_VERSION` | 0.1.0-beta.1      |
| the keel it needs | `keel-plugin.yml` `requires.keel`, `engine/keel_product/__init__.py` `requires`                                                   | `>=0.15.0,<1.0.0` |

`engine/tests/test_manifest.py` checks that these are the same. At every start keel checks the manifest
(`requires.sdk`, `requires.keel`, `files.sha256`) and loads the plugin only when it fits (else it is listed under
problems), so a keel upgrade never runs a plugin that was not made for it. keel's own code knows nothing about
Product: the plugin uses keel's extension points (engine add-on, api `KeelAddon` and Spring auto-configuration, web
`product/web/index.tsx`), and keel's jar, engine and web bundle stay the same size without it.

## Teams, Jira and the Product map

The design is docs/product/pm-jira-teams-design.md. In short:

- **Teams** own keel projects (`PUT /api/teams/{id}/projects/{pid}`, or CODEOWNERS paths). Each team has its own Jira:
  which connection (a project's, else keel's keel-wide one: `PUT /api/jira`), its project key, issue types, Epic Link
  field, labels and component; and a **ticket-key pattern** (`SHOPS-\d+`; default: its Jira project key).
- **Ticket keys in git**: keel reads each team project's branches and commits (last 90 days, every 15 minutes or
  `POST /api/product/tickets/scan`) and links them to the keys of the team's pattern (`GET /api/product/tickets`).
- **Drafts**: the plan's epics and stories can be edited (`/api/initiatives/{id}/plan/…`) until a story is sent.
  `GET …/tickets/preview` shows what goes where; `POST …/tickets/send` makes Initiative (when `PUT /api/product/jira`
  says where) → Epic → Story → Sub-task and keeps every key, so a second send makes no twin. An agent can only ask
  (`POST …/tickets/ask`): an approval in the Inbox.
- **Tickets you already have** (docs/plans/product-link-tickets.md): tickets on the teams' Tasks boards (synced Jira
  tickets or local tasks) are linked to an initiative by hand: `GET …/tickets/candidates?q=&all=`, `POST …/tickets`
  (`{tasks}` or `{keys}`), `DELETE …/tickets/{key}`, `GET …/tickets`. Nothing changes in Jira.
- **Product map** (`GET /api/product/map`): the business flows a flow analyst found in each team repo
  (`POST /api/product/flows/run`, read only, the `product-flows` workflow), with the tickets whose commits changed them,
  and the team projects' Jira tickets on the Tasks boards with their initiative (`board`).
- **Jira connections** (`GET /api/product/jira/connections`): which one is connected; with no keel-wide connection a
  team and the initiatives use a project's own (no second token).

## Modes

Settings › General › _What this keel does_: **Dev only**, **Product only** or **Product and Dev** (auto = both when
Product is on). With both, each person picks a View (All, Product, Dev) under the project picker. Turning a part
off deletes nothing; the product api answers 409 while Product is off.

## Layout

    product/keel-plugin.yml   the plugin manifest (name, version, what keel it needs, its parts)
    product/build-plugin.sh   builds the parts and packs them (see below)
    product/content/          workflows (product-discover, -impact, -decide, -plan, -outcome, -flows) and the 5 agents
    product/engine/           keel_product: actions (documents, plan check, presentation, decision), fake answers, routes
    product/api/              Spring auto-configuration: initiatives, teams, delivery, follow-ups (own tables, own Flyway
                              history); built as a thin jar (cd api && ./gradlew productPluginJar)
    product/web/              the Product map, Initiatives and Teams pages (built on their own: npm run build:product in web/)
    product/e2e/              the end-to-end script

## Build the plugin package

    product/build-plugin.sh out              # builds the api jar and the web part, then packs them
    product/build-plugin.sh out --no-web     # does not build the web part (packs product/web/dist if it is there)

It writes `out/product-<version>.kplug` (a tar.gz, no top folder inside) and the same folder unpacked:

    out/product/<version>/
      keel-plugin.yml  README.md  files.sha256      files.sha256: "<sha256>  <path>" for every other file
      engine/keel_product/                          the engine part (no tests, no caches)
      api/keel-plugin-product.jar                   the api part
      web/index.js  web/style.css                   the web part
      content/                                      workflows and agents

The product image puts the unpacked folder in `/opt/keel-v2/plugins/product/<version>/`, and keel-start loads it at every
start.

## Tests

    cd engine && PYTHONPATH=../product/engine uv run pytest -q ../product/engine/tests
    cd api && ./gradlew productTest
    cd web && npx vitest run ../product/web
    docker build --build-arg EDITION=product -t keel-v2:product .
    python3 product/e2e/e2e.py --image keel-v2:product            # a throw-away keel-lab with the fake model and a stub Jira
    KEEL_E2E_NAME=keel-pj KEEL_E2E_PORT=8097 python3 product/e2e/e2e.py --image keel-v2:product   # next to a keel-lab in use

`product/e2e/e2e.py --real claude` runs the same walk on a running keel-lab with a real model.
