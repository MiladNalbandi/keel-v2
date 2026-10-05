---
name: review
description: How keel's review agents are scoped and how they report — the whole-branch code review, one lens or all lenses of the ship review, or one acceptance criterion's review. Read-only; a review reports and never fixes.
---

# Review

keel reviews at fixed points: the AC gate, the integration gate, the security phase and ship.
Each review agent gets the same kind of scope every time, so a verdict means the same thing
wherever it comes from. This skill says what that scope is and how the answer is written.

## Which agent, which scope

| Review | Agent | Scope |
|---|---|---|
| whole branch | `code-reviewer` | the whole branch diff |
| one lens: `correctness`, `security`, `performance`, `architecture` or `assertions` | `reviewer`, that one lens | the whole branch diff |
| all lenses | `reviewer`, one per lens, in parallel | the whole branch diff |
| one criterion | `ac-reviewer` | that criterion's RED and GREEN commits |

**The lenses for "all"** are `correctness`, `security` and `performance` by default, plus
`architecture` when the project names an architecture style. Ship runs that set; a review that
runs a different set disagrees with ship about what "all" means.

## The scope, worked out before reading anything

**Branch diff.** The base is the base branch your prompt names (default `main`). Scope is
`git diff <base>...HEAD`. If that diff is empty, say so and stop: an agent sent to review nothing
will find something to say anyway.

**One criterion.** Its commits are `git log --format='%h %s' --grep='^test(AC-00n)'` for RED and
`--grep='^feat(AC-00n)'` for GREEN. When test and code landed together in one `feat(AC-00n)`
commit, review that one sha and say it is a single commit. If either commit is missing, say which
and stop; reviewing half a criterion reads as a clean result.

Nothing about what to expect goes into a review — a reviewer told where to look stops looking
anywhere else.

## Report what was found, not a summary of it

- Each finding in its own words, blocking and non-blocking separated, with `file:line`.
- The verdict line exactly as the agent's brief says: `CODE-REVIEW: pass|findings`,
  `BLOCKING: yes|no`, `AC-REVIEW: pass|findings`.
- For all lenses: one section per lens, then a one-line tally (`2 of 4 lenses blocking`).

**Do not fix anything.** A review reports. Blocking findings go back through keel's review-fix
step, which keel starts after the gate; the reviewer never edits code and never commits.
