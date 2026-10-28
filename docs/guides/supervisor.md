# The Supervisor

The Supervisor is a plugin with a strong model that watches your flows, answers gates when it is sure, and calls you when it is not.

## What it does

The Supervisor works like a careful colleague. When a flow waits at a gate, it looks first.

```text
 flow waits at a gate ──► Supervisor reads the spec, the code, the rules and your earlier answers
                            ├─ sure     ──► answers it (or only suggests, in "shadow" mode)
                            └─ unsure, stuck or out of budget ──► calls you: an Inbox card
                                                                  with a short summary
```

- It runs on a strong model.
- It never raises a budget cap.
- Its turns cost tokens. They count in the budget.
- It is **off** until you turn it on.

## Two modes

| Mode | What it does |
|---|---|
| **shadow** (default) | it only suggests; you answer every gate |
| **act** | it answers the gates it may when it is sure, and asks you about the rest |

It asks you when it is not sure, when only you may answer, or when its budget is used up.

## Turn it on

There are three switches. The Supervisor runs for a flow only when all of them say yes.

```text
 keel-wide  ──► the plugin for this project ──► this project ──► this flow
 (off)          (Control › Plugins)             (follows        (follows
                                                 keel-wide)      the project)
```

1. **The plugin**: open **Control › Plugins** and turn **Supervisor** on for this project.
2. **The switches**: open **Jobs › Supervisor**.
   - **On for the whole keel**: tick it.
   - **This project**: follows keel-wide, on, or off.
   - **This flow** (while a flow runs): follows the project, on, or off.
   - **Mode**: shadow or act.

The page also shows "X of Y tokens used" for the flow. If it does not run, it says why, for example "it is off
keel-wide".

Turning any switch off while it looks stops the look at once. Nothing is answered then.

## In keel TUI

```
/supervisor status       each switch, and whether it runs here
/supervisor on           on for this project (off: off; follow: follow keel-wide)
/supervisor keel-on      on for the whole keel (keel-off: off everywhere)
/supervisor flow-on      on for the flow now only (flow-off, flow-follow)
/supervisor shadow       it only suggests
/supervisor act          it answers when it is sure
/supervisor log          its last decisions here, with their reasons
```

keel TUI asks you to confirm each change.

## What it decided

**Jobs › Supervisor** lists every look: when, the step, the result, how sure it was, why, and the tokens. The result
is one of: answered, sent back, suggested, asked you, or "too late (you answered)".

When a setup check keeps failing, the Supervisor may suggest commands. Press **Run these commands** to send them as
your answer. keel runs them, then the checks again.

## From an AI tool (MCP)

- `keel_supervisor` (read): its settings and decisions.
- `keel_supervisor_set` (write): switch it for keel, the project or a flow, and set the mode. Setting `act` asks you
  first.

## Good to know

- The Supervisor is not a run mode. It works best with the **manual** run mode: the flow stops at every gate, and
  the Supervisor looks first.
- It never answers what only you may answer, for example the final review, a cost cap, a new dependency or a
  secret.
- It never pushes and never edits code.

See also [Answer a gate](#/help/answer-a-gate) and [Budgets and caps](#/help/budgets-and-caps).
