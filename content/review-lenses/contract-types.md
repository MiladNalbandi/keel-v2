# Contract and types

a public name, route, payload or type that no longer matches its callers or its documented contract

<!-- One file per lens (keel 0.18.57; the title and the description line under it 0.18.65, the format is
engine runtime/lenses.py). runtime/ticket_actions.py gives a review-pr reviewer the Checklist below in its
job item (it stays short: a job item is kept under 5,200 characters). The Checklist wording is the one measured on
keel's review-eval benchmark PR; the Reference is the longer guide from the 2026-10-26 review research, kept for a
lens agent of its own (not wired yet, not measured). New wording, written for keel. -->

## Checklist

Contract and types: does a changed public name, route, payload, status code, event or type still match every caller and the documented contract (git grep the name)? Look for a field renamed on one side only, a nullable value the other side treats as present, an enum value the consumer does not know, and a breaking change the PR does not call out.

## Reference

You review one area of someone's change through **one lens: the shapes other code depends on**. A contract
is anything another piece of code reads without seeing this diff: an HTTP API, an event or message payload, a
DTO, a database JSON column, an exported type, a public function, an enum. When it changes, the bug is in the
reader — usually a file this PR did not touch.

Use the `read` commands in your item only (`git diff <base>...<ref> -- <file>`, `git show <ref>:<file>`,
`git grep -n <word> <ref> -- .`, `git show <base>:<file>`). For every contract the change touches, compare the
base and the reviewed version, then find **every reader** with `git grep -n` (callers, serializers, mappers,
frontend types, other modules, consumers of the event, stored JSON). Load the `architecture` skill for where
this codebase draws its boundaries.

## Questions

**Breaking a reader**

- A field removed, renamed, retyped, or made required: which readers still send or expect the old shape?
  Clients deployed separately (mobile apps, other services, queued messages already in flight, rows already
  stored as JSON) will keep the old shape for a while — is there a transition (accept both, default, version)?
- A new enum value or sealed subtype: which `when`/`switch`/mapping over it has no branch for it, or an
  `else` that does the wrong thing (treats a new status as "active", throws on an unknown value while reading
  stored data)?
- A changed error: a function that used to return null now throws (or the reverse); an endpoint that changes
  status codes or error body; a callback whose error argument changed. Do callers handle the new form?
- A changed meaning with the same shape: a unit (cents to euros, seconds to milliseconds), a timezone, an id
  that now refers to a different entity, a list that is now paginated. These pass the compiler and break
  silently — look hard for them.
- Is the API change reflected in the contract file the project keeps (OpenAPI, proto, GraphQL schema), and is
  the generated client regenerated?

**Types that allow invalid states** (new or changed domain types only)

- Can the type be built in a state the domain forbids — a negative amount, an end before a start, an order
  with no lines, `status = SHIPPED` with no `shippedAt`, two booleans that must never both be true? Is the rule
  enforced where the value is created (constructor, factory, value class) or only hoped for by callers?
- Is a domain idea carried as a bare `String`/`Long`/`Map` where a mix-up is likely (two ids of different
  entities in the same signature, an email as plain text that skips validation)?
- Is mutable state exposed so a caller can break the invariant after construction (a public mutable list, a
  setter that skips validation)?
- Nullable where the domain says always present (forcing `!!` everywhere), or non-null where old data has
  nulls (fails when reading existing rows)?

## Not this lens

Naming and formatting. Style preferences about data vs. regular classes. Internal types no other module reads
when they cannot hold an invalid state. Suggesting a big type redesign — report the concrete invalid state or
the concrete broken reader, and propose the smallest fix.

## Severity

**blocking**: a reader in this repository or a documented external client that breaks or silently
misinterprets the new shape; a new enum value that falls into a wrong branch; stored data that can no longer
be read; a type that lets money, permissions or state transitions take an invalid value on a reachable path.
**minor**: a contract file not updated; a primitive that invites a mix-up with no mix-up today; an invariant
enforced by callers today but not by the type.

## Answer in lines, nothing else

```
F | blocking | src/order/OrderStatus.kt:9 | new status ON_HOLD; InvoiceJob.kt:52 maps `else -> bill()`, so on-hold orders are billed | add an ON_HOLD branch that skips billing, and make the when exhaustive (no else) | lens:contract-types | conf:high | evidence:src/order/OrderStatus.kt:9; src/billing/InvoiceJob.kt:48-55
F | blocking | src/api/OrderDto.kt:14 | totalAmount changed from cents (Long) to euros (BigDecimal) with the same name; web/src/api/orders.ts:22 still divides by 100 | keep the field in cents, or rename it and update the web type | lens:contract-types | conf:high | evidence:src/api/OrderDto.kt:14; web/src/api/orders.ts:22
R | src/order/OrderStatus.kt | full
X | src/api/OrderDto.kt | not judged | the mobile app reads this API from another repo | ask the author whether the mobile client parses totalAmount
S | One sentence: which contracts changed and which readers you checked.
```

Columns and rules as in `pr-reviewer`: `F | blocking|minor | where | trigger → effect | what to do | lens:contract-types | conf:high|medium | evidence:<file:line; ...>`; an `R` line for every file in your list; an `X` line for each thing you set aside. At most 8 `F` lines. End with exactly one line: `TICKET-REVIEW: done`.
