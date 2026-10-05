---
name: react-testing
description: Test patterns for a plain-JavaScript React frontend (no TypeScript) — everything the web-testing skill teaches, minus the type-level pieces. Load when writing or fixing frontend tests in a react-js project.
user-invocable: false
---

# Plain-JS React test patterns

This is `web-testing` with TypeScript removed. **Read `web-testing` first** — the file
classification rules, the colocation layout, AC tagging, MSW-against-the-contract testing, and
the assertion table are identical for a plain-JS React project. Everything below is only the
delta.

## What's different

| `web-testing` says | Here, instead |
|---|---|
| A test file ends `.test.ts` / `.test.tsx` / `.spec.ts` / `.spec.tsx` | `.test.js` / `.test.jsx` / `.spec.js` / `.spec.jsx` — same suffix rule, same `__tests__/` trap, just the JS extensions |
| Parse responses with the generated zod schema | There is no generated type layer to validate against. Assert the response shape directly in the test — the fields the AC names, and nothing the AC does not |
| "Types and the client come from the contract" | Only the client comes from the contract. Call its exported functions the same way; there is no compiler to catch a renamed field, so the test asserting the shape is what stands in for that check |
| `vitest.config.ts`, `test/setup.ts` | `vitest.config.js`, `test/setup.js` — same file, same role, `.js` extension |

## The one thing worth being stricter about, precisely because nothing else will catch it

Without a type checker, a response-shape assertion is the **only** thing that notices a
contract change reached this component. Do not skip it because "the component obviously uses
`.url`" — write the assertion, so a renamed field fails here instead of silently rendering
`undefined` in production.

## Rules keel enforces

Identical to `web-testing`'s: RED locks source files, GREEN locks tests, and `.skip(`,
`.only(`, `xit(`, `test.fixme` are weakened tests that review blocks, regardless of file extension.
