# Budgets and caps

keel counts every token and every cent its agents use, and it pauses a flow when it reaches the cap you set.

## What keel counts

keel counts every agent call: its **tokens** (the pieces of text a model reads and writes; you pay for them),
its money, its model and its effort.

- Failed, cancelled and out-of-turns calls count too.
- A long step counts while it runs (Claude Code and opencode report tokens as they go).
- A resumed Claude session is counted once.

## Where you see it

- The bar on top of every page shows each provider's fullest window.
- The **Budget** page shows today, this month, per flow and per agent, and the caps.
- keel TUI's top line shows the tokens and money of today and this month. `/budget` shows the rest.
- From an AI tool: `keel_budget`.

## Plan usage

For each provider that is set up, keel shows what its plan says is used and what remains (5 hours, week, month).
It also says where the number came from and how old it is. The Claude card shows the live weekly number.

Before a subscription agent runs, keel warns at **80%** of a plan window and pauses at **95%**.

## The token cap per flow

Each flow has a **cap**: the most tokens it may use. Set it in **Settings › Budget** (the default is 500,000
tokens per flow).

```text
 Start a flow ─ keel's estimate ─ cap = high end + 30% (when above the Settings cap)
      │
      ▼
 the flow runs ─ tokens reach the cap ─◆ cap gate ─ approve ─ the cap goes up by at least
                                                │             half of what the flow used
                                                └ stop
```

**When a flow reaches its cap**, keel can:

- **pause** (the default): a cap gate waits in the Inbox,
- go on with a **cheaper model**,
- **stop**.

A flow started without a cap (from MCP, keel TUI, the API or a KeelBot button) gets the estimate's high end plus
30%, and keel says so.

The cap gate is one of the stops that keel never answers by itself, in any run mode.

## Your own caps

On the **Budget** page you can set your own caps:

| Cap | For |
|---|---|
| per day | one day |
| per flow | one flow |
| per step | one step of a flow |
| API spend per month | money spent on API keys in a month |

And caps per **account**:

| Account | Default |
|---|---|
| Claude or Codex subscription | tokens in the last 5 hours |
| Copilot | premium requests this month (300 by default) |
| API keys | USD spent this month (100 by default) |

0 means "not set".

## Copilot prices

Copilot costs its premium requests × the model's multiplier × $0.04. Models included in the plan cost $0. To use
your own multipliers and prices, edit `prices.json` in keel's data folder.

## Tips to spend less

- Use a smaller flow for a small job (see [Pick a flow](#/help/pick-a-flow)).
- Use less thinking effort, or a cheaper model, for simple steps (see
  [Choose models per step](#/help/models-per-step)).
- Project notes are off for a new project. Building them costs about $4–6 per project.
- The Supervisor's turns cost tokens too, and they count toward the flow's cap (see
  [The Supervisor](#/help/supervisor)).
