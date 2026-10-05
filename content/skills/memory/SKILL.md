---
name: memory
description: Read back what keel knows about this project — architecture, domain vocabulary, conventions, data and integrations — and how it is written. Load when you need project context you do not have, rather than re-deriving it from the code.
---

# Project memory

`docs/knowledge/` holds what an agent needs to work in this repo: the architecture and its
boundaries, the domain's own vocabulary, the conventions that make new code match its
neighbours, the data and fixture strategy, and the integrations with their test stand-ins.
keel builds it at setup and refreshes it at the end of a flow (the memory step) or from the
Wiki page (knowledge refresh). The user chooses which sections exist; a section that was not
chosen is simply absent, which is a fact and not a problem.

A section that is on disk is read back as project authority whether or not it was chosen, so
an unsourced one is still wrong.

## Start here

Read `docs/knowledge/index.md` when it exists: it lists the sections and what each holds. The
Wiki page shows whether a section is **stale against HEAD** (commits since it was written). A
stale section must never be presented as current: if you use one, say so.

## Read one section, not all of them

**Selective is the default, and that is deliberate.** The knowledge base is the largest thing
keel writes. Reading all of it spends the context that splitting it was meant to save.

Pick by the question you actually have:

| You need to know | Read |
|---|---|
| Which package this new code goes in | `docs/knowledge/architecture.md` |
| What a business term means, or a rule an entity must hold | `docs/knowledge/domain.md` |
| What to call it, how errors are raised, where the test lives | `docs/knowledge/conventions.md` |
| Whether a migration or fixture is involved | `docs/knowledge/data.md` |
| What happens when this call leaves the process | `docs/knowledge/integrations.md` |
| How a user moves through the product | `docs/knowledge/journeys.md` |

Trust a section's `file:line` citations and open those lines rather than searching again.

## How it is written

One `librarian` per section, in parallel. Each gets one section and nothing about the others:
five readers each covering a slice beats one reader trying to hold the whole codebase, and the
sections are independent by construction. Each writes its own file and ends
`SECTION: <name> claims:<n> cited:<n> unverified:<n>`.

Every librarian keeps the same two rules, because they are what make the result worth keeping:

- **Every claim carries a backticked `path:line`.** A claim that cannot be cited is not written.
  A path that does not exist, or a line past the end of the file, is a false claim.
- **A rule about validation, transactions, error mapping or authorization needs a proof** — a
  citation into a test or a reproduction recipe somebody ran — or the literal prefix
  `unverified:`. A citation proves the code *says* something; only a test proves it *does*. A
  repo was once onboarded with `@Valid @Min @Max` recorded as its pagination convention, on a
  controller with no `@Validated` on the class, so Spring ignored it. The annotation was really
  on that line.

No template placeholder (`<...>`, `{{...}}`) may survive in a written section.

## What is not here

Commands live in `docs/RUNNING.md`. Decisions live in `docs/adr/`. Work in progress lives in
`docs/specs/`. The knowledge base points at them rather than copying them, because a copy goes
stale silently.
