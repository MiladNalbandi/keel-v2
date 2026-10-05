# Data security — schema, migrations, PII

## A migration is a permission change

Read every new migration for what it makes *reachable*, not just what it adds.

```sql
-- Widens access: every existing row becomes visible to a new join path.
alter table bookmark drop constraint bookmark_owner_fk;

-- Backfills a column with a default that makes private rows public.
alter table bookmark add column visibility text not null default 'public';
```

The second is the common one and it is blocking: a `not null default` applied to existing
rows is a decision about data that already exists. For anything access-related the safe
default is the restrictive value, with a deliberate backfill after.

Check for: a dropped foreign key or unique constraint, a nullable column becoming nullable
again, a default that grants rather than withholds, a new index on a column that is only
useful for an unauthorised lookup, and `grant` statements.

Existing migrations are immutable — keel blocks editing them. A fix is always a new file.

## PII, and what must never be logged

Treat as PII: email, name, phone, address, IP, any free-text note a user wrote, and any id
that is also a login. Tokens and passwords are worse: they are credentials, not data.

```kotlin
// Blocking: the whole entity goes to the log, including the owner's email.
log.info("saved {}", bookmark)

// Name the fields you mean.
log.info("saved bookmark id={} owner={}", bookmark.id, bookmark.ownerId.hash())
```

A data class in Kotlin generates `toString()` over every property, so any `log.info("$x")`
on an entity prints everything. That makes an interpolated entity in a log line worth a
finding on sight.

Check for: a new log line carrying an entity, a request body, a header map, an exception
from an auth path, or a stack trace in a place that ships to an aggregator.

## Encryption and storage

- A new column holding a credential, a token, or a third-party secret must say how it is
  encrypted — column-level, or the whole volume. "It is in the database" is not an answer.
- A password must be hashed with a slow algorithm (`bcrypt`, `argon2`), never a fast one.
  A new `MessageDigest.getInstance("SHA-256")` on a password is blocking.
- A token stored for reuse should be stored as a hash if it only ever needs comparing.

## Least privilege

The application role rarely needs DDL. A read-only role for the investigator to use during
a bug flow is worth having, and the bug flow expects it:

```
docker compose exec db psql -U readonly -c "select ..."
```

Check for: a migration granting `all privileges`, application code connecting as the owner
role, or a new connection string with a superuser.

## Queries and the data they return

- A query without a `limit` on a table that grows is both a performance and a disclosure
  problem — the page size is a boundary.
- A `select *` in a native query means a later column addition silently starts being
  returned. Name the columns.
- A soft-deleted row must be excluded in every new query path, not just the main one.

## Quick pass

- Does any new migration remove a constraint or add a permissive default?
- Does any new log line print an entity, a body, or a header?
- Does a new column hold a credential, and does it say how it is protected?
- Does every new query filter by owner as well as by id?
