# Org

An org of agents that **you** draw: roles and the links between them. You give a mission to any role. A lead plans, gives
the work to the roles it links to, each of them runs its own keel workflow in its own worktree, the lead checks their work,
and you get one report.

```
 you ──mission──▶ Tech lead ── lead-intake: reads the mission and the code, asks you, plans
                     │            ◆ the lead's plan (you approve: each mission, to whom, its workflow, its budget)
                     │ org:delegate (checked against the links)
          ┌──────────┴──────────┐
          ▼                     ▼
   Frontend engineer      Backend engineer      each one: its own flow (small, feature, fix …), its own worktree
          │                     │               at most limits.max_running flows at once; the rest wait in a queue
          └──────────┬──────────┘
                     ▼ all ended
                 Tech lead ── lead-review: checks each change, writes the report
                     │            ◆ the lead's report (only you answer: accept, or send back with a note)
                     ▼
                    done        (send back: the lead plans again with your note, at most limits.send_backs times)
```

## Nothing is hard-coded

keel knows no "CEO" or "tech lead". An org is YAML: roles (a name, the workflows it may run, its autonomy) and links (what
may pass from one role to another: `delegate`, `report`, `ask`, `approve`, `notify`). Any shape works: a tree, peers, a
matrix. The example on the Org page is one lead with two engineers.

```yaml
roles:
  me:   { human: true }
  lead: { title: Tech lead, workflows: [lead-intake, lead-review], plan: lead-intake, review: lead-review, autonomy: propose }
  fe:   { title: Frontend engineer, workflows: [small, feature, fix], autonomy: act }
links:
  - { from: me, to: lead, can: [delegate] }
  - { from: lead, to: fe, can: [delegate] }
  - { from: fe, to: lead, can: [report, ask] }
```

- `plan`: the flow a role runs first on a mission. A flow that ends with `org:delegate` gives work out.
- `review`: the flow it runs when every mission it gave out has ended. Without one, its report is its team's reports.
- `autonomy`: `watch` (the mission waits for you to start it), `propose` (its flows run in manual mode: every gate asks
  you), `act` (auto mode: keel answers the gates it may; the final review, money, secrets and new dependencies still stop).

## Ready-made orgs

Settings › Plugins › Marketplace › **Orgs** (and **New org** on the Org page) has six orgs to start from. Each one names
no project and only workflows keel has; pick one, change any line, save it as your own.

| Org | Who is in it | Levels |
|---|---|---|
| Solo developer | you, a developer and a scout; no lead: you give each mission straight to the one who does it | 2 |
| Small team | you, a tech lead, a frontend and a backend engineer | 3 |
| Bug squad | you, a triage lead, a fixer and a test writer | 3 |
| Startup | you as founder, a CTO, a web lead and an API lead, and their engineers and QA | 4 |
| Open-source maintainer | you, an issue triager, a fixer, a PR reviewer and a docs writer | 3 |
| Quality team | you, a quality lead, a test writer, a bug hunter and a linter | 3 |

In an org with leads in the middle (Startup), a middle lead plans for its own team and checks its work, and its report
goes up to its boss's review: only the mission **you** gave asks you to accept the report.

## The chart

Each role is a box, in levels from the top down the `delegate` links. A solid line is a work line (the arrow: where work
goes; reports come back up it); a dotted line is a side link (`ask`, `approve`, `notify` between roles that do not give
each other work). A dot marks where a line meets a box. Hover or focus a role to light its lines.

## What it keeps to

- **keel checks every hand-over.** `org:delegate` gives work only to a role with a `delegate` link from this one, only with
  a workflow that role may run, within the org's limits (children per mission, depth). The lead's plan gate shows exactly
  what will be given out, already checked.
- **The org is not in the repo.** It lives in keel's own data, so an agent cannot change its links or budgets. A flow proves
  which mission it runs with a key the runner gave it: a flow someone else started cannot give work out in a mission's name.
- **Budgets hold.** A team member gets what the mission has left, less what its lead used so far and a fifth kept for the
  review, as its flow's cap; the flow pauses at keel's budget gate. A flow can pass its cap by its last agent call.
- **No flow waits for another.** The runner keeps each mission's state in the database and wakes the next flow on an event,
  so a restart loses nothing.
- **Ticket text is data.** The lead reads it but follows only the mission and your answers.

## Not yet (issue #92 is step O1)

- O2: a boss role answers its team's questions and gates when it may and is sure, else asks up the chain to you.
- O3: wake-ups: a Jira ticket assigned to a role (Task automations), CI red, a schedule.
- O4: an org builder you click, with live mission trees.
- O5: upper roles that are not engineering (CEO, Product) with their own flows, and KPI checks once a data source is chosen.

## Parts

- `engine/keel_plugin_org`: the org's YAML (`config.py`), its tables in the engine's database (`store.py`), the runner
  (`runner.py`), the actions `org:check`, `org:delegate`, `org:report`, `org:close` (`actions.py`), and the routes
  (`routes.py`). keel's Python api forwards `/api/org/*` to them; there is no Kotlin part.
- `engine/keel_plugin_org/templates/`: the ready-made orgs (`templates.py` lists them in order).
- `content/`: the `org-tech-lead` agent and the `lead-intake` and `lead-review` workflows.
- `web/`: the Org page (Work › Org): missions, the chart (`Chart.tsx`), the YAML, each mission's tree, report and
  history; the ready-made orgs (`Templates.tsx`) in the Marketplace's Orgs kind and on New org.

Tests: `scripts/test-plugins.sh org` (the engine part, fake model), `plugins/org/web/test` (the chart and the ready-made
orgs, in keel's web tests) and `api-py/tests/test_engine_parts.py` (the forwarding).
