# Domain

<What this system is for, in three sentences, in the words the business uses. If the code
and the business disagree on a word, say so here — that mismatch causes more bugs than any
missing test.>

> Claims here carry a backticked `path:line`; every one must resolve.
## Glossary

<Only terms whose meaning is not obvious from the name, or where code and business differ.
A glossary of self-evident words is noise.>

| Term | Means | In code |
|---|---|---|
| {{TERM}} | <the business meaning> | `{{TYPE}}` |

## Entities and their rules

<One block per entity that carries rules. An entity with no invariants is a data structure;
leave it out.>

### {{ENTITY}}

- Identified by: `{{ID_TYPE}}`
- Invariants: <what must always hold, in business terms>
- Lifecycle: <the states and the legal transitions between them>
- Owned by: `{{PACKAGE}}`

## Use cases

| Use case | Trigger | Rules that apply | Entry point |
|---|---|---|---|
| {{USE_CASE}} | <who or what starts it> | <the rules, by name> | `{{CLASS}}` |

## Journeys

<End to end, crossing both apps — what a person actually does. Each journey should map to
an `[E2E]` acceptance criterion; name it if one exists.>

### {{JOURNEY}}

1. <step, in the user's words>
2. <step>

Covered by: `{{E2E_SPEC}}` · AC {{AC_ID}}

## Deliberately not supported

<What this domain refuses to do, and why. Saves an agent inventing a feature the business
decided against.>
