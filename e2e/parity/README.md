# Parity e2e

Milad's rule for the plugin track: the plugin-based keel is finished only when it is **the same for people** as the
released v0.15.1: every page, flow and part. This tool checks that and shows every difference.

It starts two throw-away keels on the same small project and compares them:

| keel            | port | volume               | image                             |
| --------------- | ---- | -------------------- | --------------------------------- |
| `keel-parity-a` | 8094 | `keel-parity-a-data` | `--a`, the released keel (0.15.1) |
| `keel-parity-b` | 8095 | `keel-parity-b-data` | `--b`, the new keel               |

It never touches another keel (`keel-v2`, `keel-product`, `keel-lab`) and removes both containers and volumes at the
end (unless `--keep`).

## Run it

```sh
python3 e2e/parity/parity.py --a ghcr.io/miladnalbandi/keel-v2:0.15.1 --b keel-v2:dev
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
     approved as "small", runs the test-first loop and stops at the AC gate. Where it stopped must be the same.
3. **api**: about 120 read-only GET endpoints (`ENDPOINTS` and `FLOW_ENDPOINTS` in `parity.py`; from
   `docs/CONTRACT.md` and the controllers; nothing that needs a model or a token, nothing that changes state) on
   both. The **status** and the **JSON shape** must match: the keys and the value types, lists by their first element.
   Values do not count (ids, times, versions differ anyway); keys that are data (ids, shas, dates) count only by their
   place. A few ask for what does not exist, so the errors must match too. An endpoint that A does not have (404 or 405) and B answers is **new in B**: listed, not a failure.
4. **web**: `pages.mjs` (headless Chromium) reads the menu (groups, labels, order, links) and the frame around the
   pages, searches in the launcher (⌘K) in every scope, then opens every menu page, `#/projects`, `#/code`,
   `#/keelbot` and a few deep links, each with a full load. In the main area it records the headings, tabs, views
   (the Code page's activity bar), regions, buttons, fields, alerts, badges, the listed rows and the looks (the
   computed styles: a missing stylesheet shows here). It clicks every tab and every Code view (they only show things)
   and records each one too. B must have the same, and no console error or failed request that A does not have too.
   A page that differs is looked at a second time (to rule out slow loading). On a difference it saves screenshots of
   A and B in `e2e/parity/out/web-<round>/`; `seen.json` there has what each page showed on A and B.

## Read the table

```
parity   A = ghcr.io/miladnalbandi/keel-v2:0.15.1
         B = keel-v2:dev

               same  different  allowed  new in B  skipped
  api [off]     107          0        1         6        0
  web [off]      28          0        0         0        0
  api [on]      107          0        1         6        0
  web [on]       28          0        0         0        0
  flow            1          0        0         0        0
  api [flow]    113          0        1         6        0
  web [flow]     28          0        0         0        0

PASSED: 0 difference(s) not on the allow list (7.4 min)
```

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
