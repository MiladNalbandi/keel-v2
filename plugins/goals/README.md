# Goals

One goal, its limits, and how far it is. An agent splits the goal into steps. You approve the plan. Each step is a normal keel flow
that **you** start.

```
 you write the goal: what "done" means, most tokens, most flows
        │
        ▼ you press Plan it
 goals-planner (reads only) ──▶ YOU approve the plan, or send it back
        │
        ▼ steps are made; nothing starts
 YOU start step 1 ──▶ a normal flow, with all its gates ──▶ done
 YOU start step 2 ──▶ ...
        │
        ▼
 YOU say: it is done
```

## What it keeps to

- **Nothing starts by itself.** There is no loop that works on the goal while you are away. You press Start for each step, or you open the
  next step from the Inbox and press it there.
- **Limits are real, in two places.** A step does not start past the goal's flow or token limit, and it gets only what is left of the tokens as its
  flow's cap, so the flow pauses at keel's own budget gate. The planning flow's tokens count too.
- **The plan is a person's.** Its gate is `person: true`: no run mode and no Supervisor answers it.
- **You say when it is done.** keel counts steps. It does not know if the goal is reached.

## What planning needs to start

The planning flow (`goals-plan`) checks these at its start, and none of them stops it:

- **No base.** It only reads and works on the branch the project folder is on, so it takes no base. A base given to it is left out, and the
  answer says so.
- **Uncommitted files are fine.** It changes no file of the project, so it starts with the person's own changes in the folder.
- **A busy folder.** When another flow runs in the project folder, planning runs in a worktree at the folder's commit. A local main that went
  its own way from origin/main does not stop it.

## What goes to the model company

The goal's words when you press Plan it, and each step's words when you start it, and what its agents read, as in any flow. On screen, too.

## What it does not do

- **A goal has no named owner.** keel has one person (see the guide, "Signing in"), so the person signed in owns every goal. A real owner and
  separate duties need people and roles in keel's core, which keel does not have. This plugin does not pretend: there is no owner field.
- It does not use keel Product's initiatives, and does not make tasks on the Tasks board. Steps are flows.
- The Supervisor does not start steps. It can still answer gates inside a step's flow, if you turned it on for that flow.
- One step at a time. It does not run steps side by side, even if they could be.
- Steps run in the order you press them. There are no dependencies between steps, only the order the planner gave.
