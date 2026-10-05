---
name: react-implementation
description: How to write plain-JavaScript React code (no TypeScript) — everything the web-implementation skill teaches, minus the type-level pieces. Load in GREEN for a [WEB] acceptance criterion in a react-js project, after the architecture skill has said where the file goes.
user-invocable: false
---

# Writing plain-JS React code

This is `web-implementation` with TypeScript removed. **Read `web-implementation`
first** — component shape, state, forms, accessibility and style are identical for a plain-JS
React project. Its reference files apply here too:

| The AC drives… | Read (same files `web-implementation` points at) |
|---|---|
| A component | `skills/web-implementation/references/components.md` |
| State | `skills/web-implementation/references/state.md` |
| A call to the API | `skills/web-implementation/references/data.md` — skip its type-narrowing advice; the rest (one client, no hand-written `fetch`) applies unchanged |
| A form | `skills/web-implementation/references/forms.md` |
| Accessibility | `skills/web-implementation/references/accessibility.md` |
| Naming, layout, imports | `skills/web-implementation/references/style.md` |

`references/design.md`'s SOLID guidance applies; its type-level advice ("types that make illegal
states unrepresentable") does not — there is no type checker here to enforce it.

## What's different

- **Props are documented, not typed.** A `propTypes` block or a JSDoc `@param` comment says what
  a component expects; neither is checked at build time, so the *test* — not a type — is what
  actually proves a required prop's absence is handled.
- **The generated client still exists, and the rule is unchanged**: never hand-write a `fetch`.
  The client's shape still comes from the contract; there is simply no compile-time check that a
  caller matches it, which is exactly why `react-testing`'s response-shape assertion is not
  optional here the way it might feel optional in a typed project.
- **"Loading, error, empty are three states, not one flag"** — unchanged, and if anything more
  important: a type system would have refused an `undefined` slipping through as a fourth silent
  state; here nothing does.

## Rules keel enforces

Identical to `web-implementation`'s: the generated directory is gitignored and keel refuses
edits to it; the minimum-code rule governs every addition regardless of language.
