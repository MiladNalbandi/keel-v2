---
name: spec-clarify
description: Ask the user the few questions that change the spec, as buttons, before any criterion is written. Identity and work-placement probes included. Load in the spec phase.
user-invocable: false
---

# Clarify first, in keel v2

You cannot talk to the user while you work: this is one run. What you can do is **end your run with questions**.
keel shows them as buttons, the user clicks, and **you continue this same conversation** with the answers.
So a question costs the user one click and costs you almost nothing. A wrong guess costs a whole spec.

## When to ask (and when not to)

Ask only what **the code and the docs cannot answer** and that **changes what the criteria say**.
Look first: the request, README, `docs/knowledge/`, the files it names. Then decide.

| Ask | Do not ask |
|---|---|
| A word with several readings: *user*, *login*, *account*, *role* → the **identity probe** | What the code already shows (stack, test command, naming, the pattern to follow) |
| A list, search, filter, sort, total, "slow" → the **work-placement probe** | The test command, gate mode or review lenses (they are in `.keel/config.yml`) |
| Two behaviours that are both reasonable (duplicate, empty, error, limit) | Anything you can decide and write down as an **assumption** in the spec |
| The request touches a public interface or a database schema | Style, names, layout |

At most **4 questions**, most important first. If nothing needs asking, write the spec.

## How to ask: end your answer with ONE block

Write no criteria and no spec file in this run. End your answer with exactly this block (valid JSON):

````
```keel-questions
[
  {
    "id": "identity",
    "question": "What does \"user\" mean here?",
    "why": "Named entity, login and permissions differ by an order of magnitude of scope.",
    "options": [
      {"label": "A · Named entity", "description": "A name and email on a record. No login, no password.", "recommended": true},
      {"label": "B · Login", "description": "People sign in; identity decides what they see. Needs its own spec."},
      {"label": "C · Permissions", "description": "Login already exists; this adds who may do what."}
    ]
  }
]
```
````

Rules for the block:
- 2 to 4 `options` per question; the **recommended one first**, with `"recommended": true`. The user can always type another answer.
- Each option is a **work choice** with what it costs: "Filter on the server: one more round trip per keystroke". Not "yes / no".
- `id` is a short word. `why` is one sentence on what the answer changes.
- Before the block, say in two or three sentences what you looked at. No long text.

## The answers come back

Your next message starts with `Answers to your questions:` and lists each one. Then:
1. Write the answer into the spec as a sentence ("user means A here"), so the next reader does not guess.
2. Write the criteria they imply, then the drawings (see the `spec-writing` skill).
3. **Do not ask again** unless an answer opens a new, bigger question. A second round is the last one: after it,
   choose the recommended option and write it down as an assumption.

## The two probes

**Identity** (words: user, users, auth, login, register, account, member, role, permission). Three readings:
A named entity (small) · B authentication (large: needs its own spec) · C authorization on an existing auth layer (medium).
If the answer is B, say so in the spec's first lines: it is a design decision, not a criterion.

**Work placement** (words: list, table, search, filter, sort, page, total, count, export, chart, "slow"). Three readings:
A in the browser (fits in one response, instant) · B on the server (unbounded data, or a field this user may not see:
**always B**; this is authorization, not speed) · C both, with the server as the truth (say which copy wins).
Two questions settle it: how many rows in two years for the largest customer, and does the user touch it on every keystroke.

Full text of both probes: `references/clarify-probes.md`. Read it only when one of them applies.

## The five questions behind it all

What exactly is built? · Where does it live? · What pattern does it follow? · What must not break? · How will we know it is done
(that last answer becomes the acceptance criteria). If you cannot answer one from the repo, that is your next question.
