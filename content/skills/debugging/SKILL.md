---
name: debugging
description: How to make a bug fail on demand, by bug class - races, flakes, data bugs, performance regressions - plus bisecting, reading logs and read-only data forensics. Load in the bug flow when writing the reproducing test, and when investigating a confirmed failure.
user-invocable: false
---

# Making a bug fail on demand

The hard part of a bug flow is almost never the fix. It is getting the thing to fail
reliably, because until it does there is nothing to confirm at Gate R and no way to know
the fix worked.

Reproduction technique depends on the **class** of bug, and that is the only thing this
skill routes on.

## Load one reference

| The symptom looks like | Class | Read |
|---|---|---|
| Passes alone, fails under load or alongside other work; timing words in the report | race | `references/reproduce-race.md` |
| Passes on rerun, fails in CI, fails only in a certain order | flaky | `references/reproduce-flaky.md` |
| Wrong rows, wrong counts, a constraint or migration involved | data | `references/reproduce-data.md` |
| Slow, timing out, fine with ten rows and not with ten thousand | perf | `references/reproduce-perf.md` |
| Wrong output for a known input, deterministic | plain logic | No reference needed. Write the unit or slice test directly. |

Then, for the investigation itself:

| Need | Read |
|---|---|
| Find the commit that introduced it | `references/bisect.md` |
| Read application logs and a trimmed stack | `references/logs-and-traces.md` |
| Inspect the data without changing it | `references/data-forensics.md` |

## The rule that outranks every technique

**A test that passes for the wrong reason is worse than no test.** A reproducing test has
to fail *because of the bug*, not because of a fixture, a stubbed clock or a coincidence.
If you cannot tell which, you do not have a reproduction yet.

keel refuses a red run that failed on a compile error, a Spring context failure or Docker,
and says which pattern it matched. That refusal is doing its job —
fix the setup rather than arguing with it.

## When it will not reproduce

Say so, and stop. Do not weaken the assertion until something goes red, and do not commit
a test that fails for a reason you cannot name. "Not reproducible yet" is a real answer:
the evidence you gathered is what the next, read-only investigation starts from.
