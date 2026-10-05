---
id: NNN
slug: <NNN-kebab-name>
title: <feature>
# draft → approved and frozen together at the spec gate → superseded if a rewrite replaces it.
status: draft
created: YYYY-MM-DD
# The spec gate. What it certifies is that the criteria were agreed *before* anyone read the
# implementation — which is why phase 1 writes them from the interview and the contract, and
# only then lets the explorers open the code to fill in the plan below.
approved: ''
# The same gate. The plan — AC order, files per AC, test layer — is appended before it, so the
# document stops growing at the moment it is approved and both dates are the same day. They were
# a phase apart while the plan had a gate of its own. From here the phase guards deny specs/ in
# red, green and gate, and a change is an amendment.
frozen: ''
# Set only when another spec replaces this one, so an archived spec says what happened to it.
superseded_by: ''
contract: <contracts/openapi.yaml#operationId, or "no contract impact">
---

## Context

<why this exists, in three sentences>

## User stories

<!-- One per distinct actor. Same role on every line usually means there is one story, and a story with no "so that" is a task with a role bolted on. -->

- As a <role>, I want <capability>, so that <benefit>.
- As a <role>, I want <capability>, so that <benefit>.

## Acceptance criteria

- AC-001 [API] Given <state>, when <action>, then <observable result>
- AC-002 [API] …
- AC-003 [WEB] …
- AC-004 [E2E] …
- AC-005 [SMOKE] …

Tag a trivial criterion with `[gate: skip]` to skip its human gate.

## UI mockup

<Four states, 80 columns, box-drawing. Draw these *before* finishing the criteria
above: the states you draw are how you find the criteria you would otherwise miss.
Omit this section entirely when the spec has no [WEB] criterion. See
the spec-writing skill's references/ui-mockup.md for the notation.>

```
Default
┌─ <screen> ──────────────────────────────────────────┐
│  [ <input>                      ]  ( <action> )     │
│                                                     │
│  <content>                                          │
└─────────────────────────────────────────────────────┘

Empty
<what the user sees instead of content, and what invites them to act>

Loading
<what is on screen while the request is in flight>

Error
<what failed, what it says, where it says it, and what survives>
```

## Request path

<The path this spec touches, marked + new, ~ changed, blank untouched. Draw both
the success and the failure response. Omit when the spec has no [API] criterion.
See the spec-writing skill's references/request-path.md for the skeleton per
architecture style.>

```
<METHOD> <path>                                        + endpoint
  → <Controller>.<method>                              +
    → <use case or service>                            + <the rule>
      → <repository>.<method>                          ~
        → <table>                                      ~ migration V<n>
  ← <success status> { <fields> }                       +
  ← <failure status> { <shape> }                        +
```

## Data and migrations

## Validation and security rules

## Contract changes

## Out of scope

<!-- One line each. Where an exclusion has a known cost in the code — a pattern that will be
     duplicated, a branch that will be unreachable, a check that cannot be tested — write the cost
     next to it. A constraint with its cost recorded is a decision; one without is something that
     will be silently complied with and never mentioned again. -->

## Smoke checks

## Decisions

<links to ADRs for anything with a real alternative>

## Plan

<!-- Written in phase 1, after the criteria are settled and the explorers have read the code.
     Criteria first, explorers second: a spec written with the code already open describes what
     is convenient rather than what was wanted, and nothing afterwards can tell the two apart.

     One table, one row per criterion, dependencies first:

       | AC     | Order | Files                | Test layer | Lane |
       |--------|-------|----------------------|------------|------|
       | AC-001 | 1     | the explorer's map   | the lowest that can express it | api |

     Then one line for the contract delta: which paths and schemas change, or "none". -->

<the AC order, the files per AC, the test layer per AC, the lane, and the contract delta>

## Definition of done

This feature is mergeable only when all of it holds. Most is enforced — the point of writing it
down is the two or three lines that are not, and the fact that a reviewer can read the list.

- [ ] Every AC is numbered, layer-tagged and observable from outside.
- [ ] Each AC has a test that **failed before** its implementation — keel refused anything else.
- [ ] The contract matches real behaviour, or this spec says it is unaffected.
- [ ] Validation, authorization and ownership rules are explicit here and tested.
- [ ] No sensitive data in a response or a log; error responses are intentional.
- [ ] The migration strategy was chosen against real numbers, and its reversibility is stated.
- [ ] The full suite passes for the shipping commit — all modules, E2E, smoke.
- [ ] Coverage holds; every accepted line has a recorded reason.
- [ ] Every AC traces to its test and commits, and **no behaviour outside this spec was added**.
- [ ] Every non-obvious decision has an ADR.
- [ ] Any dependency added was a criterion here, approved by a person.

---

<!-- Amendments go below, appended and dated, once `frozen` is set at the spec gate. Never edit
     above this line: a criterion rewritten in place leaves a document that reads as though it
     always said that. Before `frozen`, phase 1 may still append and the criteria may still
     change — that is not an amendment, it is the document not being finished. -->

## Amendments

### YYYY-MM-DD — AC-00X

Was: <the criterion as it was agreed>
Now: <what it says instead>
Why: <what changed, measured rather than assumed>
Approved: <who>, at the spec gate.
