# Org: a hierarchy of agents (issue #92)

You draw an org: roles (boxes) and links (what may pass between them). You give a mission to a role; it plans, gives work
to the roles it links to, each runs its own keel workflow, and reports come back up to you. Nothing is hard-coded: "CEO",
"tech lead" and "engineer" are only names in the YAML.

```
 roles  = boxes: a name, the workflows it may run, its autonomy (watch | propose | act)
 links  = arrows: delegate, report, ask, approve, notify          keel checks every hand-over against them
 flows  = what each role does: normal keel workflows (lead-intake, lead-review, small, feature, fix …)
```

## How a mission runs (no flow waits for another)

```
 Org runner (engine part, lifespan; sleeps, wakes on thread.done / thread.failed / gate.waiting, sweeps every 30 s)
   ├─▶ the role's plan flow (lead-intake): reads, asks (asks: true), org:check, ◆ plan gate (propose), org:delegate → ends
   ├─▶ each child mission: its own flow through POST /api/projects/{pid}/flows (where: worktree, cap, run mode)
   │       at most limits.max_running flows per org; the rest wait in the queue (status queued)
   ├─▶ all children ended → the role's review flow (lead-review): org:report, ◆ report gate (person: true), org:close
   └─▶ accept → done (and every report under it) · send back → the plan flow again with your note (≤ limits.send_backs)
```

The mission tables are the state (`org_missions`, `org_threads`, `org_history` in the engine's database), so a restart
loses nothing. The mission document (request, plan summary, children's reports) is what passes between flows, not chat.

## Safety

- The org's YAML lives in keel's data, not in the repo: agents cannot edit links or budgets.
- `org:delegate` checks the delegate link, the child role's workflows, `limits.children` and `limits.depth`, and proves the
  flow runs the mission with the key the runner put in `data.org.key`.
- Budgets: a child gets what the mission has left, less the lead's usage so far and a fifth for its review, as its flow cap.
- `propose` runs flows in manual mode, `act` in auto mode (keel never pushes or opens a PR there; the final review stops).
- Ticket text is data; the lead follows the mission and the person's answers.
- The Python api forwards `/api/org/*` with the internal token and applies the MCP level guard (GET read, else admin).

## Steps

| Step | What | State |
|---|---|---|
| O1 | Org YAML, missions, the runner, org:check/delegate/report/close, the lead workflows, `/api/org/*`, the Org page; the chart with its lines; six ready-made orgs in Marketplace › Orgs (slot `market.kind`); a lead in the middle plans for its team and reports up to its boss | this issue |
| O2 | The escalation chain: a boss role answers its team's gates and questions when it may (`may_decide`) and is sure (the Supervisor's answer-or-call logic), else up the links to you; a mid-level boss may send its team's work back | next |
| O3 | Wake-ups: Task automations point a Jira account or label at a role; CI red; schedules (`wake:`) | |
| O4 | A clickable org builder and live mission trees | |
| O5 | Roles that are not engineering (CEO, Product) with their own flows, and KPI checks, once a KPI data source is chosen | |

## Open questions

- One human (you) for now. Named people (a real designer or PM on Slack) would give roles a `person` field.
- KPIs need a data source: the Database plugin, an analytics API, or a number typed in.
- Goals overlaps: a Goal is close to a mission for one role. Keep Goals as it is; later a Goal can become one.
- Many agents cost more than one (about 15× the tokens of one chat): the lead → engineers level comes first.
