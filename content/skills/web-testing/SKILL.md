---
name: web-testing
description: "Test patterns for a TypeScript frontend: Vitest with Testing Library, MSW handlers generated from the API contract, zod response parsing, and tagging tests with acceptance-criteria IDs. Load when writing or fixing frontend tests."
user-invocable: false
---

# TypeScript frontend test patterns

## Where the file goes — enforced, not a convention

keel decides whether a file is a test **from its path**, and the phase guards act on that answer: a
file it reads as source cannot be written in RED, and — worse — *can* be written in GREEN, where
tests are meant to be frozen. A misplaced test does not fail loudly; it quietly stops being a test
as far as the flow is concerned.

A file counts as `web-test` when **either**:

- the filename ends `.test.ts` · `.test.tsx` · `.spec.ts` · `.spec.tsx`, **or**
- it sits under a `test/` or `tests/` directory

| Path | keel reads it as |
|---|---|
| `src/components/Button.test.tsx` | ✅ `web-test` |
| `src/components/Button.spec.tsx` | ✅ `web-test` |
| `tests/Button.tsx` | ✅ `web-test` |
| `src/__tests__/Button.test.tsx` | ✅ `web-test` — the suffix is what saves it |
| **`src/__tests__/Button.tsx`** | ❌ **`web-src`** — `__tests__` is not `tests` |

That last row is the trap, and `__tests__/` with a bare filename is a common React layout.
Underscores are not stripped. **Always carry the `.test.`/`.spec.` suffix** and the directory stops
mattering.

## Starting a frontend with no tests at all

Colocate, and let the suffix do the work:

```
apps/web/
  src/
    components/
      BookmarkForm.tsx
      BookmarkForm.test.tsx        ← beside what it tests
    hooks/
      useBookmarks.ts
      useBookmarks.test.ts
  test/
    setup.ts                       ← jest-dom, cleanup
    msw/
      handlers.ts                  ← generated from the contract
      server.ts
  vitest.config.ts
```

Colocation beats a mirrored tree: the test moves with the component, a renamed folder cannot orphan
it, and keel runs a criterion's test by its AC tag rather than by location anyway. Shared setup goes under
`test/`, which is a test path by directory and needs no suffix.

**`vitest.config.ts` and `test/setup.ts` are new files, not new dependencies.** Writing them in RED
is fine. Needing Vitest, Testing Library or MSW *installed* is not — that is an acceptance criterion
the spec must carry and a human must approve, and the Bash guard refuses the install mid-flow.

## Tag the test with its AC

```ts
it('AC-004 shows a validation error for an empty url', async () => { /* ... */ });
```

keel runs it alone with `vitest run -t AC-004`.

## Test against the generated client, not a hand-written fetch

- Types and the client come from the contract; never hand-edit the generated folder (keel blocks it).
- Mock at the network level with MSW handlers generated from the same contract, so a contract change breaks the test.
- Parse responses with the generated zod schema in at least one test per endpoint, so a body mismatch fails on the frontend too.

## What to assert

| AC kind | Assert |
|---|---|
| Rendering | Roles, labels and text, not class names |
| Validation | The message the user sees, and that no request was sent |
| Error state | The message for a 4xx or 5xx handler |
| Loading | The intermediate state, using a delayed handler |

## Rules keel enforces

- RED: only test files — `.test.ts`, `.test.tsx`, `.spec.ts`, `.spec.tsx`, or anything under
  `test/`/`tests/`. See the classification table at the top; the suffix is what decides it, not the
  extension. GREEN: only source files.
- No `.skip(`, `.only(`, `xit(` or `test.fixme`: a parked test is a weakened test, and review blocks it.
- Component tests never start a real backend; that is what the E2E phase is for.
