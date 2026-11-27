# keel 0.40 → 0.41.3: whole app end to end, rules modes, loop guard — test report and benchmark

Tested 22–27 Nov 2026 for [#203](https://github.com/MiladNalbandi/keel-v2/issues/203). The task asked for 0.40.0. keel
moved on while the test ran, so the final numbers are for **keel 0.41.3** (latest, 27 Nov). The rules checks ran on 0.40.0
and 0.40.1. They were repeated on 0.41.1 and 0.41.3, because 0.41.x did not change the rules code. MCP tools and
`keel_rules_mode` are **not** repeated here. They are in the MCP report, [test-report-mcp-0.40.0.md](test-report-mcp-0.40.0.md)
(#231).

The question for every feature: **does it help an agent work better and faster, alone and next to other agents?** keel is a
tool an agent connects to; it is not there to manage agents.

## Summary

- **The daily e2e suite passes on 0.41.3**: 33 passed, 1 skipped (J20, the Org plugin is parked), 0 failed, in 3.8 min. Earlier runs, kept in the task's comments on #203: 0.41.1 31 passed, 0.40.0 29 passed and 1 failed. J24 (Review comment → Ask KeelBot), which failed on 0.40.0
  (409s in the console), passes since #251 (0.41.2).
- **Main flow** (feature flow: spec → contract → tests → code → review → commits, 8 gates): **every run finished**: 5 of 5 on
  0.40.1, 0.41.1 and 0.41.3, 3 of 3 on 0.40.0. 0.41.3 median 14.4 s per flow on the fake model; a gate answer takes 191 ms.
- **Rules modes work** in Settings, Start a flow, the Flow page switch, Rules now and History. **One real bug**: a mode set
  in `.keel/config.yml` is applied, but Settings and Start a flow show Normal, and picking Normal still starts a Safety only
  flow ([#256](https://github.com/MiladNalbandi/keel-v2/issues/256)).
- **Safety holds in both modes**: 44 checks with keel's own guard — everything that cannot be undone, secrets, rule files and
  git settings are refused in Normal **and** Safety only; only the phase rules switch off in Safety only.
- **The loop guard works, all five choices clicked in the browser**: Allow once, Switch to Safety only, Stop (Normal) and
  Try the step again, Stop (T3/secret). For keel's goal this is the most useful 0.40 change: an agent stuck on the same rule
  stops after 3 tries instead of burning tokens, and the person answers once.
- **On 0.41.3 every page loads in 0.6–1.3 s** at desktop (1360 px) and phone (390 px) width: 25 pages, no sideways scrolling,
  no console errors at all.
- **6 bugs filed**: [#256](https://github.com/MiladNalbandi/keel-v2/issues/256) to
  [#261](https://github.com/MiladNalbandi/keel-v2/issues/261) (1 in keel's rules, 3 in the e2e test scripts, 2 small UI/CLI
  texts). No security problem found.

## How it was tested

- keel from the **release tarballs** (0.40.0, 0.40.1, 0.41.1, 0.41.3), native mode (`keel2 start --native --from-tarball`),
  own throw-away `KEEL2_HOME` and port, Java 21 runtime in `/tmp`. The test box has **no Docker, no model key and no
  bubblewrap**. Milad's own keel was not touched.
- **Models**: keel's fake model (`KEEL_FAKE=1`, $0) for flows and pages; for the loop guard a tiny scripted
  OpenAI-compatible stub set up as a *local model*, so an agent makes exactly the calls the test needs ($0).
- **Project**: `shop`, a small Python repo with tests; `fresh`, an empty git repo, for a brand-new project.
- **Browser**: the repo's daily e2e suite (`e2e/daily`, Playwright, Chromium) plus own Playwright scripts with real clicks and
  key presses, at 1360 px and 390 px, recording console errors and failed requests.
- **Timing**: wall time at the client. Flow numbers are the median of 5 runs. Other agents' jobs shared the machine, so
  run-to-run noise is large (see "Benchmark").

## Results per feature

| Feature | Works? | How fast (0.41.x, fake model) | Cost per use | Notes |
|---|---|---|---|---|
| **Rules: Settings › Flow and gates › Rules** | ✔ | switch saves at once, kept after reload | $0 | Normal ⇄ Safety only, clicked in the browser. |
| **Rules: Start a flow › Rules** | ✔ / ✘ | dialog ~1 s | $0 | Picking Safety only works. With `.keel/config.yml` Safety only, the dialog shows Normal and a Normal pick still gives Safety only: [#256](https://github.com/MiladNalbandi/keel-v2/issues/256). |
| **Rules: Flow page chip + switch** | ✔ | switch < 1 s | $0 | Chip shows Safety only; the switch is offered while the flow runs or waits (correct). |
| **Rules: `.keel/config.yml rules: {mode: safety-only}`** | ✔ (engine) | — | $0 | New flows without a mode get Safety only. Settings does not show it ([#256](https://github.com/MiladNalbandi/keel-v2/issues/256)). The new rule "keel creates no `.keel` folder" (#246) will move this setting, so #256 may change when #246 is done. |
| **Rules now panel + History** | ✔ | panel < 1 s | $0 | Rules now shows the Safety only block; History tags lines "Safety only" and shows the mode. |
| **Safety only keeps T3** | ✔ | guard decision < 5 ms | $0 | `rm -rf /`, `~`, `$HOME`, `DROP TABLE` (psql, sqlite3), `git push --delete` / `:main`, `terraform destroy`, `mkfs`, `dd` to a disk: refused in both modes, live (agent run) and in 44 direct guard checks. |
| **Secrets, rule files, git settings** | ✔ | — | $0 | `.env` read, `cat .env`, writing `.keel/config.yml`, `git config`, `.git/hooks`, force push, `--no-verify`: refused in both modes. The secret value never reached the model (checked in the stub's request log). |
| **Phase rules in Safety only** | ✔ | — | $0 | Off in Safety only (writing `src/` in the test phase is allowed), on in Normal. |
| **Loop guard, Normal variant** (rule pack blocks 3×) | ✔ | question in 4–5 s | $0 | Choices: Allow it this once ✔ (the command ran once, flow stays Normal; phone width), Switch this flow to Safety only ✔ (gate log `rules-loop safety`), Stop the flow ✔. |
| **Loop guard, T3/secret variant** | ✔ | question in 3–13 s | $0 | Choices: Try the step again ✔ (the step re-ran, T3 refused again, the flow went on), Stop the flow ✔ (phone width). No "Allow" for T3 — correct. |
| **MCP + `keel_rules_mode`** | see #231 | — | — | Not repeated: [test-report-mcp-0.40.0.md](test-report-mcp-0.40.0.md). |
| **Start a flow → tested commits** (feature flow) | ✔ | 14.4 s per flow (median of 5), start 1.4 s | 820,700 in / 101,000 out tokens, $0 | 5/5 done, 8 gates each. The token count is the fake model's made-up count (#212). |
| **Gates + Inbox** | ✔ | gate answer 191 ms | $0 | Answered from the Inbox in the browser and by the API. Loop-guard cards show what was tried and why. |
| **Code (editor, explorer, source control)** | ✔ | J5/J6 pass | $0 | Wheel scrolling, tabs, path bar, diff, commit. |
| **Code Review** | ✔ | J24/J26 pass | $0 | Comment on lines, Ask KeelBot in the box, MCP review reads. The Review page asks nothing while Code Review is off (no more 409s, #251). |
| **KeelBot** | ✔ (no model) | page < 0.8 s; your message shows at Enter | — | J29 (0.41.3): the message shows at once, Retry, "New messages ↓", desktop and phone. With no model it says so clearly ("KeelBot has no model yet", correct since #153). Real answers not tested (no key). Chat speed was fixed in 0.41.3 (#242). |
| **Plugins (Settings › Plugins)** | ✔ | page 1.1–1.2 s | $0 | J25: parked plugins start off, core on, one click + restart turns one on. Per-project plugins (Code Review, Git, Database, CI/CD) start off in a new project — by design (docs/plugins/02), but a new user's agent then has no Git or Review tools until someone turns them on. |
| **Supervisor** | page only | page 0.65–0.84 s | — | Opens without errors; suggestions need a real model (not tested). |
| **Budget bar + Budget page** | ✔ | page 0.67–0.95 s | $0 | J10: tokens, cost and flows equal the API. |
| **Wiki** | page only | page ~0.9 s | — | Opens without errors; no content written. |
| **Database plugin** | ✔ | schema 10 ms, query 56 ms | $0 | SQLite connection; a file outside the project folder is refused (good). |
| **Database diagram (Map plugin, parked)** | ✔ | ~3 s after Rebuild | $0 | Turned on + restart; 3 tables / 2 foreign keys drawn from `db/schema.sql`. It reads migrations only, not the live DB (by design). Two search fields overlap: [#261](https://github.com/MiladNalbandi/keel-v2/issues/261). |
| **Code graph** | page only | 0.6–0.8 s | — | Graph and class view open without errors. |
| **Settings** | ✔ | 0.65–0.7 s | — | J22: the menu stays while the tab changes. |
| **Home + Start here (0.41)** | ✔ | 0.65–0.7 s | — | J28: the intro, Connect your agent, Hide and open again. |
| **keel2 terminal app (TUI, parked)** | ✔ | J4 passes | $0 | `/approve` in the TUI moves the flow and the dashboard shows it. |
| **Phone width (390 px)** | ✔ | same as desktop | — | 25 pages, no sideways scrolling; loop-guard choices clicked at 390 px. There is no separate phone app. |
| **Native sandbox (0.41.1)** | ✔ (warns) | — | — | Without bubblewrap keel still starts and `keel2 status` warns clearly; the hint says "install bubblewrap" twice: [#260](https://github.com/MiladNalbandi/keel-v2/issues/260). |
| **Backup / restore** | ✔ | J12 ~46 s | $0 | Corrupt restores are refused with the data intact. |

## Benchmark

Feature flow, fake model, median of 5 runs (3 runs on 0.40.0):

| keel | Done | Wall time per flow | Start | Gate answer | GET flow state | Tokens per flow |
|---|---|---|---|---|---|---|
| 0.40.0 | 3/3 | ~50 s | — | — | — | 820,700 / 101,000 |
| 0.40.1 | 5/5 | 13.6 s | 1.26 s | ~180 ms | — | 820,700 / 101,000 |
| 0.41.1 | 5/5 | 27.1 s | 1.9 s | 238 ms | 262 ms (p90 675 ms) | 820,700 / 101,000 |
| 0.41.3 | 5/5 | 14.4 s | 1.38 s | 191 ms | 198 ms (p90 301 ms) | 820,700 / 101,000 |

The spread between versions (13–50 s for the same flow) is probably not keel. The machine was shared with other agents'
jobs. 0.41.1's 27 s was measured while the daily suite and other jobs ran (a first try during the suite gave 37 s).
0.41.3 on a quieter machine (load average about 2) is back at 14.4 s. Inside one run every step takes under 1.8 s (median per step: spec 0.28 s, plan 0.41 s, red 0.31 s, green 0.51 s, review
0.46 s, the slowest are the gates, 0.75–1.76 s, which include the test driver's polling). **A real model sets the speed and cost
of a flow; this box has no model key, so real token cost per flow is not measured.**

Pages on 0.41.3: 25 pages, 0.59–1.27 s each, at both widths.
Per page on 0.41.1 (time until the page settled, desktop / phone): Home 677/651 ms, Inbox 590/577, Flow 1036/1010, Code
704/683, KeelBot 698/718, Review 665/635, Wiki 894/894, Features 1332/1104, Settings 711/650, Plugins 1118/1207, Budget
950/668, Graph 781/617, Supervisor 838/650.

## Judged by keel's goal

- **Good for agents**: the loop guard turns "stuck on a rule" from an endless retry into one question; Safety only lets a
  trusted agent work without phase rules while the dangerous things stay closed; a secret never reaches the model.
- **Several agents at once** (from the MCP report, #231): keel keeps agents apart (own worktree, own commits), but it does
  not make them faster together yet; they share no knowledge and re-read the same files.
- **Gaps**: a new project has Git and Code Review off, so a freshly connected agent gets less from keel until a person turns
  them on; the rules mode a project sets in its config is invisible in the UI (#256).

## Bugs filed

| Issue | What |
|---|---|
| [#256](https://github.com/MiladNalbandi/keel-v2/issues/256) | Rules mode from `.keel/config.yml`: Settings and Start a flow show Normal, and picking Normal still starts Safety only |
| [#257](https://github.com/MiladNalbandi/keel-v2/issues/257) | e2e `run.sh` hangs on Node 24 without mise (the `.mjs` reporter on Playwright 1.49.1) |
| [#258](https://github.com/MiladNalbandi/keel-v2/issues/258) | e2e `run.sh` deletes the cached/released keel tarball when a source or `__pycache__` file is newer, then rebuilds |
| [#259](https://github.com/MiladNalbandi/keel-v2/issues/259) | `e2e/lib.py` prints a traceback at exit when docker is not installed |
| [#260](https://github.com/MiladNalbandi/keel-v2/issues/260) | `keel2 status`: the bubblewrap hint says "install bubblewrap" twice |
| [#261](https://github.com/MiladNalbandi/keel-v2/issues/261) | Map (parked): two search fields drawn on top of each other |

Seen and already fixed during the test: Review page 409s and J24 (#248, fixed in 0.41.2 by #251); KeelBot chat stuck on
"Sending" (#235, fixed in 0.41.3).

## Not tested, and why

- **Real model answers, real token cost per flow, KeelBot answers, Supervisor suggestions, Wiki writing**: no model API key on
  the test box; only the fake model and the scripted stub.
- **Docker mode**: no Docker on the test box; everything ran natively.
- **Sandbox switched on**: no bubblewrap on the test box (only the "no sandbox" warning was checked).
- **MCP tools and `keel_rules_mode`**: covered by the MCP report (#231), not repeated.
- **Several agents at once**: covered by the MCP report (#231).
- **A phone app**: keel has none; the web app was tested at 390 px.
- **J20 (Hand to Org)**: skipped by the suite, the Org plugin is parked.
- **The terminal app beyond J4** (`/approve`).
