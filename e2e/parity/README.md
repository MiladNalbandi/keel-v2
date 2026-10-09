# Parity e2e

Milad's rule for the plugin track: the plugin-based keel is finished only when it is **the same for people** as the
latest release (v0.15.1 when the track began, v0.15.4 since the track took main's 0.15.2–0.15.4): every page, flow and
part. This tool checks that and shows every difference.

It starts two throw-away keels on the same small project and compares them:

| keel            | port | volume               | image                             |
| --------------- | ---- | -------------------- | --------------------------------- |
| `keel-parity-a` | 8094 | `keel-parity-a-data` | `--a`, the released keel (0.15.4) |
| `keel-parity-b` | 8095 | `keel-parity-b-data` | `--b`, the new keel               |

It never touches another keel (`keel-v2`, `keel-product`, `keel-lab`) and removes both containers and volumes at the
end (unless `--keep`).

## Run it

```sh
python3 e2e/parity/parity.py --a ghcr.io/miladnalbandi/keel-v2:0.15.4 --b keel-v2:dev
python3 e2e/parity/parity.py --a … --b … --only api          # only the api (fast, no browser)
python3 e2e/parity/parity.py --a … --b … --keep              # leave both keels running to look at them
python3 e2e/parity/parity.py --a … --b … --verbose           # also print what differs for the allowed differences
```

It needs Docker, python3 (no packages) and Node with npx (the web part runs `npx -y -p playwright@1`; it uses the
Chromium in `~/Library/Caches/ms-playwright` when playwright's own build is not downloaded). A run takes about 8
minutes. The web part also runs alone on two running keels:
`npx -y -p playwright@1 node e2e/parity/pages.mjs http://127.0.0.1:8094 http://127.0.0.1:8095 /tmp/out`.

**Compare like with like.** An image built with `--build-arg INSTALL_CLIS=0` has no agent CLIs and no CodeGraph, so
its Graph, Code status bar, launcher and Connections differ from 0.15.1's. The tool warns when it sees that. For such
a test image add `--allow e2e/parity/allow-no-clis.yml`. The real check is with an image built the normal way.

## What it does

1. **A fixture project**, `shop`, made in a temp folder: a git repo with a TypeScript web app (`web/`) and a Python
   api (`server/`), two SQL migrations, an OpenAPI file, CODEOWNERS, a README, a knowledge page, a decision, two
   commits, a changed and a new file, and a SQLite database. Each keel gets its own copy (keel writes into the
   project), mounted at `/workspace`. Both add it through the api (`POST /api/projects`) and build its index and map.
2. **Three rounds**, each after the same calls on both keels:
   - `[off]`: as keel starts (the plugins are off);
   - `[on]`: the four plugins on (CI/CD, Database, Git, Code Review) and the shop's database added as a connection;
   - `[flow]`: a `change` flow runs with keel's fake model (no login, no token). It stops at the scope gate, is
     approved as "small", runs the test-first loop and stops at the AC gate. Where it stopped must be the same. (A
     step may run twice on one keel, even on 0.15.1, so in this round lists are compared without their length and
     order, and Live agents and Jobs, which show each agent call step by step, only by their structure; their tabs
     too, whatever count a tab's name shows, like "Finished 3" and "Finished 4".)
3. **api**: about 120 read-only GET endpoints (`ENDPOINTS` and `FLOW_ENDPOINTS` in `parity.py`; from
   `docs/CONTRACT.md` and the controllers; nothing that needs a model or a token, nothing that changes state) on
   both. The **status** and the **JSON shape** must match: the keys and the value types, lists by their first element
   and their length (the same data on both: a list with fewer items lost a workflow, a skill, an agent). Values do
   not count (ids, times, versions differ anyway); keys that are data (ids, shas, dates) count only by their place. A
   few ask for what does not exist, so the errors must match too. An endpoint that A does not have (404 or 405) and B
   answers is **new in B**: listed, not a failure.
4. **mcp**: in each round, keel's own MCP server inside each container (`python -m keel_engine.mcp`, what agents
   and `keel2 mcp` use) answers `tools/list`, read-only and with `--write`. The same tools must come in the same order.
   When B keeps A's whole list in its order and only adds tools after it, the difference is one line,
   `B adds at the end: <tools>` (so an allow entry can name exactly those); else both lists are printed.
5. **web**: `pages.mjs` (headless Chromium) reads the menu (groups, labels, order, links) and the frame around the
   pages, searches in the launcher (⌘K) in every scope, then opens every menu page, `#/projects`, `#/code`,
   `#/keelbot` and a few deep links, each with a full load. In the main area it records the headings, tabs, views
   (the Code page's activity bar), regions, buttons, fields, alerts, badges, the listed rows and the looks (the
   computed styles: a missing stylesheet shows here). It clicks every tab and every Code view (they only show things)
   and records each one too. B must have the same, and no console error or failed request that A does not have too.
   A page that differs is looked at a second time (to rule out slow loading). On a difference it saves screenshots of
   A and B in `e2e/parity/out/web-<round>/`; `seen.json` there has what each page showed on A and B.

## Read the table

The step 2 plugin track (built with `INSTALL_CLIS=0`, run with `--allow e2e/parity/allow-no-clis.yml`):

```
parity   A = ghcr.io/miladnalbandi/keel-v2:0.15.1
         B = keel-v2:parity-b

               same  different  allowed  new in B  skipped
  api [off]     100          0        7         6        0
  web [off]      22          0        6         0        0
  api [on]      100          0        7         6        0
  web [on]       22          0        6         0        0
  flow            1          0        0         0        0
  api [flow]    108          0        5         6        0
  web [flow]     22          0        6         0        0

allowed differences:
  api GET /api/features [off]   <- step 1, the plugin host adds the loaded plugins' web parts to /api/features ...
  web #/graph buttons [off]   <- no CodeGraph in the image, so the Graph page offers "Build the index" ...
  ...
new in B:
  api GET /api/approvals [off]   <- step 2, approvals became a core service that every part asks through ...
  ...

PASSED: 0 difference(s) not on the allow list (4.7 min)
```

With A = B = 0.15.1 every row is "same" (113 / 28 / 113 / 28 / 1 / 119 / 28).

- **same**: the same on A and B. For the web, one row is one page (or the menu, the frame, the launcher).
- **different**: B is not like A, and the allow list does not say why. Each one is printed under the table with what
  differs (`$.plugins: only in B`, `button: "Rebuild" only in A`, `status 200 in A, 404 in B`) and, for a page, its
  screenshots. **Any one of these makes the run fail (exit 1).**
- **allowed**: different, but on the allow list; its reason is printed next to it (`--verbose` prints what differs).
- **new in B**: an endpoint only B has.
- **skipped**: an endpoint whose `{value}` (a workflow, a skill, a code graph group) neither keel could give.
- **notes**: what A does too, for example a failed request that 0.15.1 also makes (not a parity difference).

Exit codes: 0 parity, 1 a difference not on the allow list, 2 the run itself broke (the keels' logs are printed).

## The allow list

`allow.yml` lists the differences that are meant to be, each with a reason:

```yaml
- what: api GET /api/features # the id as the table prints it; * matches anything
  detail: "$.plugins: only in B" # optional: the lines of the difference it allows
  why: the plugin host (step 1) lists the loaded plugins' web parts
```

`what` matches the id (`api GET /api/features [on]`, `web #/map buttons [off]`, `web menu [on]`); without `[round]`
it matches every round. With `detail`, every line of the difference must match the detail of a matching entry, so a
new, other difference on the same endpoint still fails. Entries that matched nothing are listed at the end: remove
them when the difference is gone. Keep the list short: a difference people would notice is a bug to fix, not an entry.
