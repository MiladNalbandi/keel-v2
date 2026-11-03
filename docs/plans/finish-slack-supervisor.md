# keel v2 0.21.0: finish Slack and the Supervisor (plan and status)

Branch `feat/newwork-plugins`, on main `d5c308a9` (0.20.1). It also holds the 8 new plugins: answers, record, notify, desk,
library, links, goals, slack. Status of each phase: see the table at the end.

```
 A. Supervisor        B. Slack             C. Prove it             D. Release
 ┌───────────────┐    ┌───────────────┐    ┌─────────────────┐     ┌───────────────┐
 │ A1 acts when  │    │ B1 free chat  │    │ C1 all tests    │     │ commit, PR    │
 │    stuck      │    │ B2 start flow │    │ C2 image builds │     │ CI, merge     │
 │ A2 chip, note │ ─► │ B3 assign     │ ─► │ C3 e2e suites   │ ─►  │ tag, images   │
 │ A3 settings   │    │ B4 forms      │    │ C4 real model   │     │ publish       │
 │ A4 past       │    │ B5 all gates  │    │    check        │     │ deploy        │
 │    answers    │    │    (opt-in)   │    │                 │     │               │
 └───────────────┘    └───────────────┘    └─────────────────┘     └───────────────┘
                                             any red = stop
```

## Rules for all of it

- Every new power is **off by default**, and a person turns it on. A new install behaves as 0.20.1 did.
- Slack's words are data. They never become instructions that widen what an agent may do.
- Only people on the Slack allow list act. A message from a bot is never read.
- The Supervisor's safety rules (`supervisor.md` section 8) stay: only offered answers, never a cap, repeat limits, pause on
  escalation, stale is final, kill switch.
- Budgets and caps count for everything that runs a model, also from Slack.
- Tests use a fake model and the local stand-in for Slack (`e2e/slack/fake_slack.py`). Only C4 uses a real model.
- A page or README never claims more than was tested.

## A. The Supervisor

### A1. It acts when stuck (`supervisor.md` P3)

```
 a flow fails or stalls
        │
        ▼
 Supervisor reads the failure ──► diagnosis, written in the flow's log
        │
        ├─ an offered "retry" exists ──► retry with a note (mode retry and up)
        ├─ review findings ───────────► send back with a reason (mode act and up)
        ├─ a small, safe fix ─────────► fix under the size limits (mode fix only)
        └─ anything else ─────────────► ask the person
```

- **Diagnosis** of a failed flow and of review findings: in every mode, also shadow. It only writes.
- **Stall timer:** a flow with no event for a set time (default 30 minutes, a setting) counts as stuck. The Supervisor looks
  once per stall. A step that still runs is not stopped: it asks the person.
- **Mode `fix`, new and above `act`:** small code fixes under KeelBot's fix rules. Limits, in code: at most 3 files and 40
  changed lines (settings), on the flow's own branch, keel's tests must pass, no push, no merge. Over a limit: it asks the
  person. Files on the never list (secrets, dependency files, CI files, `.keel/`) are refused.
- This replaces the earlier default "it never edits", only for a person who picks mode `fix`.

### A2. On the flow page and in the Inbox (`supervisor.md` P4)

- A chip in the flow's header: `off`, `watching`, `looking…`, `acted`, `escalated`, `paused (budget)`, `paused (repeats)`.
- A note on the flow's own Inbox item, with **Use suggestion**. It fills in the answer. The person still sends it.
- Both come through new web slots in core (`web/src/sdk/slots.ts`), so without the plugin nothing changes.

### A3. The switches on the Settings page

A row on Settings for keel-wide on or off, the mode, and the stall time. Jobs › Supervisor, the terminal app and MCP keep
working. A plugin's own keys need a way into the page: use a settings section slot, not a new core key.

### A4. Past answers in the brief

The brief also holds the person's earlier answers to the same kind of gate in other flows of the project: the last 10, short,
from the api's events through one new read call. Their text is data. A failed call means a brief without them, not a failed
turn.

## B. Slack

```
 message from a person on the allow list
        │
        ▼
 a fixed command? ──yes──► status · waiting · answer · task · start
        │ no
        ▼
 free chat on? ──no──► "I know these commands: …"
        │ yes
        ▼
 read-only answer from the project (tools that only read), with the budget
```

### B1. Free chat

- Setting **Let people ask keel in Slack**, default off. Settings › Slack and the README say what then goes to the model company.
- Any message that is not a command gets an answer in the same Slack thread. It reads the project through read-only tools and
  keel's own status data. A reply in the thread continues the chat.
- It cannot write, commit, start, or answer a gate. Asking it to says which command does that.
- Per person limits: one turn at a time, and a daily cost cap (a setting). Over it: it says so.

### B2. Start a flow

- Setting **Let people start flows from Slack**, default off.
- `start <flow> [in <project>] <what to do>`, such as `start change in shop: add a late filter`. It uses the same start route as the
  dashboard, with the project's budget and caps, and records `slack:<name> (<member id>)` as who started it.
- It asks for one confirm button before it starts. The flow's gates then come to the channel as usual.

### B3. Assign a task

- `task Fix the export @sara` and `assign ORD-12 @sara`. The name must be on the allow list.
- The Tasks plugin gets an `assignee` name on a local task, shown on the board with a filter. Jira keeps its own assignee.
- The person gets a direct message with the task. It is a label, not an account: keel still has one login, and the board and the
  README say this.

### B4. Questions with a form

A gate with several questions opens a Slack modal, with the same options and the same check as the Inbox. A form Slack cannot
show (a free file, too many fields) says **answer it in keel**, as today.

### B5. All gates from Slack, as a choice

- Setting **Allow every gate from Slack**, default off, with a warning on the page.
- With it on, gates with `person: true` and the Supervisor's never list get buttons too, for people on a second, shorter list
  (setting: **who may answer these**). The Supervisor's own limits do not change: it still never answers them.
- It asks for a reason and one confirm. **Budget and secret gates stay in keel always:** a stolen Slack session must not be able to
  spend money without limit or reach a secret.

### Not in this plan

- More than one Slack workspace per keel.
- Real accounts for each person in keel. This is a larger change, and B3 says what it is.

## C. Prove it

Any red result stops the release. Only one heavy job runs at a time: this computer has 15 GB, and a Docker build was already
killed for lack of memory.

| Step | What | Passes when |
|---|---|---|
| C1 | web, engine, API, keel-plugin, launcher tests, lint | no failure that clean main does not have |
| C2 | `docker build --build-arg EDITION=product` and the normal image | both build |
| C3 | e2e: `plugins-each` (now 19 plugins), `plugins --only core-only,image,broken`, `core --only off`, `supervisor`, Slack on the stand-in | all pass |
| C4 | a real model, in a throw-away keel, with a low cap ($3): the Supervisor in shadow, then act, and one Slack free chat on the stand-in | safety rules hold, the cost stays under the cap; notes say what it really did |

**Cannot be proven here:** real Slack. It needs the owner's Slack app. The release notes and the README say so, and Slack stays
marked beta until then. Linear and mail are the same: only stand-ins.

## D. Release

`0.21.0` in the five version files (new features, not a patch). Commit → PR → CI → merge → main CI → draft release → tag → images
→ publish → deploy to the running keel with `keel2 update` (product edition), then `install.sh` for the `keel2` script.
The notes say plainly what was and was not tested.

## Effect on existing users

- Every plugin is in the full image, so a menu gets 4 new items and Settings 2 new tabs. Number-key shortcuts shift.
- The core changes stay small: a `person: true` gate, the request text in `thread.started`, `by` on the resume route (kept only with
  a `slack:` prefix), the `gate_kind` event field, the new web slots.

## Status

| Phase | State |
|---|---|
| A1–A4 | built |
| B1–B5 | built |
| C1 | same failures as clean main locally: 12 engine, 1 git, 6 keelbot, `WorkspaceDoctorApiTest`; CI decides |
| C2 | both images built |
| C3 | Slack 20, Supervisor 13, core off 37, plugins 18 checks passed. `plugins-each`: the full run (86 min) found 79 issues from 3 causes (a stale tool list, a route that answers 405, an old link), all fixed; then the affected plugins ran again and passed. The full run was not repeated |
| C4 | real model (Sonnet), $0.46 for the flow and the Supervisor's looks. Shadow only suggested. Act answered the done gate after running the tests. It asked the person at the pull request gate. A late look was marked stale and changed nothing. Slack chat on the stand-in answered, and refused to delete, commit or push; the repo stayed clean. Not run on a real model: mode `fix`, the diagnosis, the stall timer |
| D | see the release |

**Known limits:** real Slack, Linear and mail were never used. In mode `fix`, a person's answer during a fix can let the next step start in the same folder before the fix is put back. A model that goes around the guard (a script that runs git) is caught after the turn, not prevented. In shadow mode the flow chip says `escalated` when the Supervisor only suggests.
