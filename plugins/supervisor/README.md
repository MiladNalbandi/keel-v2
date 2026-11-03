# Supervisor

A strong model that watches your flows. It looks at each gate a flow waits at, from the project's own context, and it answers or asks you. It is **off** until you turn it on, and then it only
suggests until you pick another mode.

```
 a flow waits, fails or goes quiet
        │
        ▼
 read-only look ──► a diagnosis in the flow's log (failure, review findings; every mode)
        │
        ├─ an offered "retry" ───────► retry with a note              mode retry and up
        ├─ review findings ──────────► send back with a reason        mode act and up
        ├─ a plain gate ─────────────► answer it, when sure           mode act and up
        ├─ a small, safe fix ────────► fix, then keel checks again    mode fix only
        └─ anything else ────────────► ask you
```

## Modes

| Mode | What it may do |
| --- | --- |
| `shadow` | only suggests. The default |
| `retry` | also tries a stuck step again, with a note |
| `act` | also answers gates and sends findings back, when it is sure |
| `fix` | also makes one small fix |

Pick it keel-wide in Settings › General › Supervisor, with the stall time, or for one flow in Jobs › Supervisor. A flow's own choice wins. An MCP client below the admin level cannot set `fix` or the keel-wide row.

## What you see

- A chip in the flow's header: `watching`, `looking…`, `acted`, `escalated`, `paused (budget)`, `paused (repeats)`. Off for this project or flow it says `off`. Off keel-wide there is no chip.
- A note on the flow's own Inbox item: what it saw, why it did not answer, and **Use suggestion**, which fills in the answer. You still send it.
- Jobs › Supervisor: every look, answer and escalation, with its reason.

## In every mode

- **A failed flow** gets a diagnosis in its log: what happened, the likely cause, what it suggests. It only writes.
- **A quiet flow.** No event for the stall time (30 minutes unless you set another): one read-only look, once per stall, and it asks you. A step that still runs is not stopped: a long build may be fine, and only you know.
- **Your earlier answers.** The brief holds your last 10 answers to the same kind of gate in this project's other flows, as data. If keel cannot read them, it looks without them.

## Mode fix: the limits are in code

The model writes; keel then checks what really changed.

- At most 3 files and 40 changed lines (added plus removed). Settings `fix_max_files`, `fix_max_lines` per flow.
- Only on the flow's own branch, never on `main`, `master`, `develop`, `trunk` or the project's base branch. Only in a flow with a phase, whose rules hold for the edits. Never in a read-only flow.
- Never: a file with secrets, a dependency or lock file, a CI file, anything under `.keel/`, a tool's config. It deletes no test.
- Only at a gate that can check it again (`recheck`, else `retry`): keel's own check then runs. If it fails, the gate comes back, and the repeat limit stops the loop.
- It runs no command that changes files or git: no commit, no push, no merge. The guard refuses them for every kind of model. keel's commit step commits the fix after its checks.

Over a limit, it puts back only the files that turn changed, and your Inbox item shows the patch it put back. Text in a file with secrets is never in that patch. If you answered first, the fix is put back too. If git moved anyway (a commit made some way around the guard), it does not touch git: it stops and tells you.

## What it never does

It never answers a cap, a new dependency, a secret, a read-only commit, a note only you write, the final review or the pull request, in any mode. No setting, prompt or file changes that list. It never raises a cap, stops a running step, pushes or merges.

Checked once with a real model (Sonnet, one small flow): shadow only suggested, `act` answered a plain gate after it ran the tests itself, and it asked the person at the pull request gate. **Mode `fix`, the diagnosis and the stall timer have not run on a real model**: their tests use a fake one. docs/plans/supervisor.md says what is built.
