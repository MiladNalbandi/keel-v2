# Answer a gate

A gate is a stop in a flow where keel waits for you: read the card, then approve it or send it back with a reason.

## What a gate is

A flow is a list of steps that agents do for you: a spec, a test, the code, a review. Some steps end at a
**gate**. At a gate keel stops and waits for your answer. In keel's pictures a gate is a ◆.

```text
 spec ─◆─ red ─ green ─◆─ … one loop per criterion … ─ review ─◆─ done
       │              │                                       │
       you approve    you approve each criterion              you approve the end
```

A **criterion** is one thing the change must do (for example `AC-1`). The spec lists them.

When something needs you, keel plays a sound, shows a pop-up and its mascot jumps.

## The gate card

Open the **Flow** page. **Needs you** is pinned on top. The gate card tells you:

- what you approve,
- the cost so far,
- the proof (for example the test output),
- what happens after **Approve**, and what happens after **Send back**.

## Approve or send back

- **Approve**: the flow goes on to the next step.
- **Send back**: write a reason. The agent reads your reason and works again.

Keys in the card: **A** approves. **R** opens the reason box.

A good reason is short and clear, for example:

```
The test only checks an empty list. Add a test with two items.
```

## Choices are cards

Some gates have named choices. For example the spec gate has **approve**, **edit**, **rewrite**, **review**,
**order** and **reject**. Each choice is a card. The card says:

- what the choice does,
- which choice a plain approve takes,
- if the choice needs a reason.

Keys **1–9** pick a card. A reason box opens only for a choice that needs one. Some questions take several
answers: tick each one that applies.

## The Inbox

**Inbox** lists everything that waits for you, in every project:

- gates,
- the explorer's questions (the explorer is the agent that reads the code and asks you things),
- a check that keeps failing,
- a budget pause,
- a new dependency,
- a KeelBot command that needs your OK.

Answer there, or open the flow. When several flows run, the Inbox groups their gates by flow.

## Answer from keel TUI

keel TUI is keel in your terminal (see [The keel TUI](#/help/keel-tui)). These commands answer gates:

```
/inbox                       everything that waits for you
/approve                     approve what waits (or /approve 2 for Inbox item 2)
/reject 2 The test does not check the error case.
/answer edit                 choose one exit of the gate
/answer q1=yes q2=no         answer a gate's questions
```

**Ctrl+N** opens the Inbox panel. A click on "◆ waiting" answers it.

## Answer from your phone

keel's Android app shows your flows, gates and Inbox. You can approve and send back there too. See
[Use keel from your phone](#/help/phone).

## Answer from an AI tool (MCP)

Connect your AI tool at the write level first (see [Connect an AI tool (MCP)](#/help/connect-an-ai-tool)). Then:

- `keel_status` and `keel_inbox` show each question with its options and the recommended one.
- `keel_approve_gate` and `keel_answer` decide a gate. Pass a choice's name as `choice`.
- `keel_read_gate` gives the whole gate (findings, the failing output, the spec), page by page.
- `keel_edit_gate` takes the gate's edit choice with your notes.

A tool that can show forms asks you right there: pick one, tick several, or write your own words.

## When a criterion is stuck

keel tries a ladder first: the same agent with the exact failure, then a different agent, then a stronger model
once. Then it asks you, with real choices: retry with a note, skip, edit the spec, or stop. After 5 send-backs or
retries in a row keel asks "go on anyway / stop".

## Fewer gates: run modes

Each flow has a run mode. It decides how many gates keel answers by itself.

| Run mode | keel decides by itself |
|---|---|
| **manual** (default) | nothing: it stops at every gate |
| **important** | a criterion's gate, when its checks pass and its review is clean |
| **auto** | every gate it can decide; each is logged in the final review and the PR text |
| **readonly** | nothing; agents cannot change anything |

In every mode keel still stops for the token cap, a new dependency, a secret in a commit, a check that keeps
failing, a stuck criterion, and anything only you can answer. Change the mode on the Flow page, or with `/run` in
keel TUI.
