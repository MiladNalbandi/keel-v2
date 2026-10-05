---
name: web-implementation
description: "How to write React + TypeScript frontend code: component shape, state, consuming the generated API client, forms, accessibility and style. Load in GREEN for a [WEB] acceptance criterion, after architecture has told you where the file goes."
user-invocable: false
---

# Writing the frontend code

This answers **how to construct it**. Two neighbours answer the other questions, and this file does not repeat them:

| Question | Skill |
|---|---|
| Which directory does this file belong in? | `architecture` → the reference for `architecture.style` |
| How do I test it? | `web-testing` |
| How do I drive it end to end? | `playwright` |

The minimum-code rule still governs everything below: **no component, hook, prop, state or abstraction the test does not drive.** Where a pattern here suggests a split, apply it when the test forces it, not in anticipation.

## Load one reference

| The AC drives… | Read |
|---|---|
| A component, or a component that has grown too big | `references/components.md` |
| State — local, lifted, or from the server | `references/state.md` |
| A call to the API | `references/data.md` |
| A form the user submits | `references/forms.md` |
| Anything a user operates with a keyboard or screen reader | `references/accessibility.md` |
| Naming, file layout, imports, formatting arguments | `references/style.md` |
| SOLID in React, types that make illegal states unrepresentable, hook rules, which patterns earn their keep | `references/design.md` — in `refactor`, or in GREEN when the criterion touches a shape that already exists |

Most `[WEB]` criteria need two: `data.md` plus one of `components.md` or `forms.md`.

## Four rules that outrank any pattern

1. **The generated client is the only way to reach the API.** Never hand-write a `fetch`. The generated directory is gitignored and keel refuses edits to it — if the shape is wrong, the contract is wrong, and that is a phase-2 change.

2. **Loading, error and empty are three states, not one flag.** A single `isLoading` boolean cannot express "succeeded and there is nothing to show", which is the state users hit most and tests forget.

3. **Accessible markup and testable markup are the same markup.** The Playwright and Testing Library locators query roles, labels and text. If a test needs a `data-testid` to find something, treat that as a defect in the component, not in the test — and note that adding a test id is a `[WEB]` AC change, never an E2E-phase edit.

4. **Copy the neighbours.** These references describe the intent; the directory you are editing describes the practice. Where they differ, match the practice and say so once.

## What keel already enforces

- In GREEN you may edit source but not test files, and edits are scoped to the `web` lane.
- The generated directory cannot be edited.

Formatting and lint follow the project's own Prettier and ESLint config, and `tsc --noEmit` must pass;
run them when the project has them.
