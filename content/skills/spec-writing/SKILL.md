---
name: spec-writing
description: "Write a spec a human can approve in one read: numbered, layer-tagged, observable acceptance criteria; an ASCII mockup with four states for screens; an ASCII request path marked new or changed; assumptions and out-of-scope written down. keel v2 version of spec-authoring. Load in the spec phase."
user-invocable: false
---

# Writing the spec (keel v2)

The spec gate is the one gate that is never skipped, so this file is what a human really decides on.
Read the `spec-clarify` skill first: if a question would change the criteria, end your run with the questions and write nothing else.
This skill is for the run where you write the file.

## The file: `docs/specs/<slug>.md`, in this order

```
# <Title>                                  one line: what is built and why it matters

## Request                                 the user's words, copied (max 5 lines)
## Decisions                               each answer from the questions, as a sentence:
                                            "user means A (a named entity): no login, no password"
## Assumptions                             what you decided yourself, so the reviewer can object
## Acceptance criteria                     3 to 8 lines, see below
## Mockup                                  [WEB] only: fenced ASCII, the four states
## Request path                            [API] only: fenced ASCII, new vs changed
## Out of scope                            what is not built, with what it costs ("no cache: the query runs per request")
```

Cut a section that has nothing to say, except Acceptance criteria. Keep every line under 80 columns.

## Acceptance criteria: the form the engine reads

One per line (wrap long lines with a 2-space indent), exactly this shape:

```
- **AC-001** [API] Given <state>, when <action>, then <what an outside observer sees>.
```

- Ids `AC-001`, `AC-002`, … in one sequence. The tag is `[API]` or `[WEB]` and **routes the work**: tag by where the assertion lives.
- The *then* is observable from outside: a status code, a stored value, text on screen. "Validates the URL" is not; "returns 422 naming the url field" is.
- One assertion concept per criterion; "and" between two outcomes usually means two criteria.
- **Behaviours, not work steps.** "Imports are updated" or "behaviour stays the same" are steps. A small change needs one to three criteria.
- Name the number when the criterion is about speed or size ("renders in under 200 ms with 10,000 rows"). No number, no criterion: move it to Out of scope.
- Cover the edges you found while drawing: empty, error, duplicate, limit, a field the user may not see.

Details and worked examples: `references/acceptance-criteria.md` (full path below). Read it once when writing criteria.

## Assumptions are not a way around a question

An assumption is something you can safely decide: write it as **one choice**, with the reason. "Ties share a rank (1, 2, 2, 4)" is an
assumption. "Either order is acceptable" is not: it is a missing decision, and a criterion that allows both cannot fail. Pick one,
or (if it changes the criteria and the code cannot say) ask it. Words like *either*, *any*, *appropriate*, *fast*, *etc.* in a
criterion mean the same thing: replace them with the exact outcome or the number.

## The drawings

- **[WEB]**: draw the screen in the four states (loading, empty, filled, error) before you finish the criteria list. A state without a criterion means a missing criterion; a criterion without a state means a missing drawing. How: `references/ui-mockup.md`.
- **[API]**: draw the request path, one line per hop, `NEW` or `CHANGED` on each, ending at the data. Check that every `[API]` criterion appears on it. How: `references/request-path.md`.
- Never put an `AC-` id inside a drawing.
- Not pixel layout: *what is on the screen and in which state*.

## Order of work

1. Read what the request names, `docs/knowledge/` and the nearest similar code. **Few files**: find lines with grep, read only those.
2. Anything a question would change? → end with `keel-questions` (see `spec-clarify`). Otherwise continue.
3. Sketch the states / the path, then write the criteria they imply, then check the two lists match.
4. Write the file once, then re-read it as the approver: *could I say yes to this without opening the code?*
5. Finish with 3 lines: what the spec covers, what you assumed, what is out of scope.

## When the user sent it back

You are in the same conversation. Change **the existing file** to answer the note. Do not explore the project again.
Keep ids stable for criteria that stay; add new ones at the end; say in one line what changed.

## Do not

- run `keel` commands or `git`; keel checks the spec and commits it after you
- write production code or tests in this step
- invent behaviour the request did not ask for: put it under Out of scope as "possible later"
