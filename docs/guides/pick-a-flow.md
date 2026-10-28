# Pick a flow

A flow is a ready plan of steps for one kind of work; pick the smallest one that fits the job.

## How to start one

Open **Flow › Start a flow** and write what you want. **How big is it?** lists quick, implement, small, change and
feature, with their steps and an estimate. It marks the one that fits your words (or fix for a bug). You pick.

In keel TUI:

```
/flow                          pick the workflow, then say the idea
/flow quick fix the typo on the login page
```

You can also ask KeelBot "which workflow fits?". It names one and gives a **Start the flow** button.

## From small to big

```text
 quick ─ implement ─ small ─ change ─ feature
 one look            1–3 criteria      spec, plan, every criterion, full review, ship
 ◄── less time and fewer tokens                     more checks and more gates ──►
```

A **criterion** is one thing the change must do. **Test-first** means: first a test that fails, then the code that
makes it pass.

## Build something

| Flow | Use it when |
|---|---|
| **quick** | a tiny change: one agent makes it and a test, keel runs the tests, you look once, one commit |
| **implement** | you want the usual order (code, then its tests); done when they pass; no gate |
| **small** | a small feature, test-first, 1–3 criteria, one review; no ship part |
| **change** | a small change without a spec; keel decides: trivial (one commit) or 1–3 criteria; too big goes to feature |
| **feature** | something new: spec with questions, plan, each criterion test-first, reviews, ship |

## Fix something

| Flow | Use it when |
|---|---|
| **fix** | a bug you can show: first a failing test that shows the bug, then the cause, the fix and a regression test |
| **diagnose** | a bug you cannot repeat yet: 3–4 guesses, one investigator each; then fix, feature, or a note |
| **address** | a reviewer's comment ("Address it"), or a change to test code only |
| **hunt** · **hunt-next** | look for bugs (read only); hunt-next takes the top bug to fix (or feature) |

## Think before you build

| Flow | Use it when |
|---|---|
| **clarify** | you want questions answered before any code; you get a spec document, then the flow that fits |
| **spike** | research first: web docs, one researcher per angle, findings with one recommendation |

## Check and finish

| Flow | Use it when |
|---|---|
| **review** | review your branch; it changes nothing |
| **review-pr** | someone else's pull request against a ticket (see [Review a pull request](#/help/review-a-pull-request)) |
| **lint** | fix formatting and lint problems, with no change in behaviour |
| **cover** | add tests for the changed lines that have none |
| **ship** | final checks and the PR text; keel never pushes (see [Run your CI jobs before ship](#/help/ci-before-ship)) |

## Set up

| Flow | Use it when |
|---|---|
| **init** | set a project up for keel (stack, test commands, `.keel/config.yml`) |
| **knowledge-refresh** | refresh the project notes |
| **plugin** | build a keel plugin |

## Good to know

- ◆ in a flow's picture means keel stops and waits for you (see [Answer a gate](#/help/answer-a-gate)).
- keel never pushes. It commits on its own branch.
- Several flows can run at once. The next one runs in its own worktree (a copy of the project on its own branch).
- The flows are YAML files. The **Workflows** page edits them, and you can make your own.
