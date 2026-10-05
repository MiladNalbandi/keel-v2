# Looking at the data without changing it

During a bug flow the data is evidence. Reading it is fine; changing it destroys the thing
you are trying to explain, and makes the reproduction unrepeatable.

## Connect read-only

```
docker compose exec db psql -U app -d app
```

Then, in the session, make it impossible to write by accident:

```sql
set default_transaction_read_only = on;
begin read only;
```

Better still, if the project has one, connect as a read-only role. `mcp.database_readonly`
exists in config for exactly this: a database MCP server connected with read-only
credentials, for the investigator to use instead of psql.

During `bug-investigate` **nothing is writable anyway** — the guard denies every bucket but
`other` until Gate F — so a write attempt here is a signal you have skipped ahead.

## The queries worth running first

Shape before contents. These four answer most data bugs:

```sql
-- 1. Does the row the report mentions look how you expect?
select * from bookmark where id = '…';

-- 2. Is it a duplicate, a null, or a case problem?
select url, count(*) from bookmark group by url having count(*) > 1 limit 20;
select count(*) from bookmark where url is null;

-- 3. Is it a boundary? Look either side of the timestamp in the report.
select id, created_at from bookmark
where created_at between '2026-03-01' and '2026-03-02' order by created_at;

-- 4. Is the constraint what you think it is?
\d+ bookmark
```

`\d+ bookmark` is the one people skip and should not: it prints the real columns, types,
nullability, defaults, indexes and constraints. A surprising number of "wrong data" bugs
are a missing unique index or a default nobody remembers.

## Confirming migration state

The dev database is on whatever schema was last applied, which may be behind the branch:

```sql
select version, description, success, installed_on
from flyway_schema_history order by installed_rank desc limit 10;
```

A failed row, or a version older than the newest file in the migrations directory, explains
a whole class of confusing failures. Apply pending migrations with the project's migrate command;
drop the dev volume when the data has drifted too far to be worth keeping.

## Turning a finding into the reproduction

The point of all this is a fixture. Copy the *minimal distinguishing rows* into a data-slice
test and assert the rule — see `reproduce-data.md`. Two rows that differ in one way beat
twenty realistic ones, because the test then names the cause.

## Traps

- Never `update`, `delete` or `truncate` to "check" something. Read the plan instead:
  `explain analyze select …`.
- Production credentials do not belong in this flow at all. If the bug only reproduces
  against production data, that is a finding to raise, not a connection to open.
- Counts from the dev database prove nothing about the test database; Testcontainers starts
  clean every run, which is the point.
