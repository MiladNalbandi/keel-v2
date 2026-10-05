# Data

Migrations live in `{{MIGRATIONS}}`, applied by <Flyway / Liquibase>. An existing migration
is **immutable** — keel refuses to edit one; add a new file instead.

> Claims here carry a backticked `path:line`; every one must resolve.
## Schema

<Tables that carry rules, not every table. For each: what it holds, and the constraint that
matters. A column list duplicates the migration and rots; the constraint does not.>

| Table | Holds | Constraints that matter |
|---|---|---|
| {{TABLE}} | <one line> | <unique, not-null, FK — the ones a test should assert> |

## Relationships

<Only where cardinality or cascade behaviour is not obvious from the names.>

```mermaid
erDiagram
    {{TABLE_A}} ||--o{ {{TABLE_B}} : "<verb>"
```

## Migrations

- Naming: `{{MIGRATION_PATTERN}}`
- Applied to the dev database by: <the project's migrate command>
- Drift: drop the dev volume when data has drifted too far to be worth keeping.

<Anything a migration in this project must do that is not obvious: a backfill pattern, a
zero-downtime rule, a naming convention for indexes.>

## Fixtures and seeds

| Need | How | Where |
|---|---|---|
| Unit and slice tests | <builders / factories> | `{{FIXTURE_PACKAGE}}` |
| Data-slice and integration | Testcontainers PostgreSQL, fresh per run | <config class> |
| E2E | Created **through the API** in a Playwright fixture, never by clicking | `{{E2E_DIR}}/fixtures` |

Testcontainers starts its own throwaway database, separate from the dev stack — so junk in
the dev database can never affect a test run, and a bad migration cannot be masked by it.

## Local bring-up

Commands are in [../RUNNING.md](../RUNNING.md), verified by the run ladder. Do not restate
them here; they change and this file would go stale silently.
