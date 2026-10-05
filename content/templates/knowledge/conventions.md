# Conventions

What an agent reads to write code that looks like its neighbours. **Observed from the
codebase, not aspirational** — if a rule here is not what the code actually does, the code
wins and this file is wrong.

> **Every claim here carries a citation** — a backticked `path:line`. Every one must
> resolve: a missing path, or a line past the end of the file, is a false claim. A claim you
> cannot cite should not be written; "no convention found for X" is a useful sentence.
>
> A rule about **validation, transactions, error mapping or authorization** needs more than a
> citation, because a citation proves the code *says* something and only a test proves it *does*.
> Cite a test or a reproduction recipe, or prefix the line with `unverified:`.

## Naming

| Thing | Pattern | Seen at |
|---|---|---|
| <controller / endpoint> | {{PATTERN}} | `<path:line>` |
| <use case / service> | {{PATTERN}} | `<path:line>` |
| <repository / port> | {{PATTERN}} | `<path:line>` |
| <test class> | {{PATTERN}} | `<path:line>` |

## Errors

- Domain rule violated: <the exception type, and where it is translated to a status>
- Not found: <type> → <status>
- Validation: <where it happens — bean validation, a value class constructor, both>
- Never: <what this codebase does not do — bare `RuntimeException`, swallowed catches, status codes decided in the domain>

## Tests

| Layer | Tool | Where | Naming |
|---|---|---|---|
| Unit | <JUnit 5, MockK> | `{{BACKEND}}/src/test/kotlin/**/domain` | `<pattern>` |
| Web slice | <@WebMvcTest> | `{{BACKEND}}/src/test/kotlin/**/web` | `<pattern>` |
| Body | <MockMvc + contract validator> | `{{BACKEND}}/src/test/kotlin/**/contract` | `<pattern>` |
| Component | <Vitest, Testing Library, MSW> | `{{FRONTEND}}/src/**/*.test.tsx` | `<pattern>` |

Every test carries its acceptance-criterion ID — keel runs a criterion's test by it and
traces the test back by it. The `{{TESTING_SKILL}}` skill holds the patterns;
this table only says where things live and what they are called.

## Placement

<Three or four lines, from the architecture style: which package a new endpoint, rule,
query or component belongs in. Full reasoning is in the `architecture` skill — do not
restate it here.>

## Local practices worth knowing

<The non-obvious ones an agent would otherwise get wrong: a shared test configuration, a
custom assertion helper, a generated-code directory that must never be hand-edited, a
formatting rule the linter enforces.>
