# Record

One page and one file per flow. Someone asks "who approved this, what ran, what did it cost?" and you have the answer in
one place. keel already keeps all of this; Record only joins it.

```
 threads ─────────┐
 events ──────────┤
 agent_calls ─────┼──▶  Record page  ──▶  Export: Markdown or JSON
 flow_applied ────┘     (and a small panel on the Flow page)
```

| Part | What you see |
| --- | --- |
| **Who asked** | how the flow reached keel (dashboard, MCP client, keel TUI, a task, another flow), the workflow, the request in the person's words, when |
| **What ran** | each agent and code step: the model, status, tokens, cost |
| **Gate answers** | each answer: approve, send back or a named choice, the note, and who gave it (the person, keel's run mode, keel because the gate was not asked, or the Supervisor) |
| **Commits** | each commit with its files, plus the branch and worktree |
| **Cost** | tokens and dollars, added up |

Open it at **Record** in the menu, or from the panel under a flow's steps.

## What it is not

- **It is not proof.** The record is what keel has in its own database. It is not signed, and whoever runs keel can change
  it. It is good working notes. It does not make you compliant with anything.
- **It has no names.** keel has one person, so "the person" means whoever signed in. Separate duties and named approvers
  need user roles in keel's core, and keel does not have them.
- **Plan logins show no cost per call**, so the dollars can be lower than the real bill.
- A flow from before this version of keel has no request text. The record says so.

Record writes nothing and has no tables, agent tools, secrets or network use. Remove it and nothing is lost.
