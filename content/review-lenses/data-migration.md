# Data migration and backfill

defaults and NOT NULL on old rows, a missing backfill, an index the code relies on

<!-- One file per lens (keel 0.18.57; the title and the description line under it 0.18.65, the format is
engine runtime/lenses.py). runtime/ticket_actions.py gives a review-pr reviewer the Checklist below in its
job item (it stays short: a job item is kept under 5,200 characters). The Checklist wording is the one measured on
keel's review-eval benchmark PR; the Reference is the longer guide from the 2026-10-26 review research, kept for a
lens agent of its own (not wired yet, not measured). New wording, written for keel. -->

## Checklist

Data migration and backfill: does each schema change work on the rows that already exist? Look at defaults and NOT NULL on existing rows, a missing backfill, a new table that existing code joins to (an INNER JOIN drops the old rows that have no row there yet), unique constraints and indexes the code relies on (catching a duplicate-key error needs a UNIQUE index in the migration, not only in the ORM mapping or the test schema), a down() that loses data, a lock on a big table.

## Reference

You review one area of someone's change through **one lens: what this change does to data that already
exists**. A migration runs once, on production data, usually while the old code is still serving. A mistake
here is the hardest kind to take back, so this lens reads more than the diff.

Use the `read` commands in your item only (`git diff <base>...<ref> -- <file>`, `git show <ref>:<file>`,
`git grep -n <word> <ref> -- .`, `git show <base>:<file>`). Read **every migration in the change whole**, then
the earlier migrations on the same tables (`git grep -n <table> <ref> -- <migrations folder>`), then the entity,
then every query that reads or writes the changed tables and columns. Load the `security` skill's
`references/data.md` for the exposure angle and the `debugging` skill's `references/data-forensics.md` for
how schema bugs show up in data.

## Questions, in order

**Existing rows**

- A new `NOT NULL` column: does it have a default or a backfill that runs before the constraint? Is the
  default _right_ for old rows, or only convenient (a `status` defaulting to `ACTIVE`, a visibility defaulting
  to public, an amount defaulting to 0 that a sum will now include)?
- A backfill: does it cover **every** existing row — including NULLs, soft-deleted rows, rows of every tenant,
  rows with values the new code does not expect? Is it written in SQL against the schema of _that_ migration,
  not through application code that may change later? Is it batched, or one `UPDATE` over a large table that
  holds a lock for minutes?
- Is the backfill **re-runnable** — if the migration fails half way and is run again, does it duplicate or
  corrupt rows?
- A renamed, retyped or dropped column: does any query, report, view, trigger or other service still use the
  old name or type? Is data converted, or silently truncated (a wider type narrowed, a timezone lost, text to
  enum with values outside the enum)?

**Indexes and constraints**

- Does every new query path (a new `WHERE`, `JOIN`, `ORDER BY` in this change) have an index — name the query
  and the missing index. Does a new unique rule have a unique constraint, or is it only checked in code
  (two concurrent requests will both pass)? Is uniqueness case- or whitespace-sensitive when the domain is not?
- A new foreign key: what happens on delete of the parent? Will existing orphan rows make the migration fail?
- Creating an index or adding a constraint on a large table: does it lock writes (Postgres needs
  `CREATE INDEX CONCURRENTLY` outside a transaction, `ADD CONSTRAINT ... NOT VALID` then `VALIDATE`; MySQL
  depends on the engine and version)? Say which table and why it is large, if the code or comments tell you.

**Deploy order**

- During a rolling deploy the old code runs against the new schema, and the new code may start before the
  migration finishes. Does the old code break on the new schema (a dropped column it still selects, a new
  `NOT NULL` column it does not insert)? Is a destructive step (drop, rename) in the same release as the code
  that stops using it, instead of a later one?
- Is the migration file's version or order right (no gap or clash with another branch's migration on the same
  number)? Was an already-applied migration **edited** instead of a new one added?
- Is there a way back — and if the change cannot be rolled back, does the PR say so?

**Entity and mapping**

- Does the entity or ORM model match the migration: nullability, type, length, default, enum values, column
  name? A field nullable in code but `NOT NULL` in the schema fails at runtime; the reverse lets nulls in.

## Follow it before you write it

A finding names the row and the outcome: _"orders created before V42 have status NULL; OrderQuery.kt:88
filters `status = 'OPEN'`, so they vanish from every list."_ If you have not opened the query, the finding is
`conf:medium` and the `X` line says which query to check.

## Not this lens

Migration naming style. An index you think might help with no query that needs it. Problems in migrations this
change did not add or edit (`pre:yes`, only if this change makes them worse).

## Severity

**blocking**: existing data lost, corrupted, hidden or exposed; a migration that fails on real data (orphans,
duplicates, NULLs); a lock on a large table during deploy; old code that breaks on the new schema; an edited
applied migration; a uniqueness rule with no constraint on a path that two requests can race. **minor**: a
missing index on a path with small data today; a missing rollback note.

## Answer in lines, nothing else

```
F | blocking | db/migration/V42__add_status.sql:3 | status is added NOT NULL DEFAULT 'ACTIVE'; cancelled orders created before V42 become ACTIVE and are billed again by BillingJob.kt:40 | add the column nullable, backfill from cancelled_at (CANCELLED where set), then set NOT NULL | lens:data-migration | conf:high | evidence:db/migration/V42__add_status.sql:3; src/billing/BillingJob.kt:40; db/migration/V17__orders.sql:12
F | minor | src/order/OrderRepository.kt:31 | new findByCustomerAndStatus has no index on (customer_id, status) | add the index in a new migration, CONCURRENTLY | lens:data-migration | conf:high | evidence:src/order/OrderRepository.kt:31; db/migration/V17__orders.sql:20
R | db/migration/V42__add_status.sql | full
X | db/migration/V42__add_status.sql | not judged | table size is not visible in the repo | ask the author how many rows orders has in production, to decide whether the backfill must be batched
S | One sentence: which migrations, tables and queries you read.
```

Columns and rules as in `pr-reviewer`: `F | blocking|minor | where | trigger → effect | what to do | lens:data-migration | conf:high|medium | evidence:<file:line; ...>`; an `R` line for every file in your list; an `X` line for each thing you set aside. At most 8 `F` lines. End with exactly one line: `TICKET-REVIEW: done`.
