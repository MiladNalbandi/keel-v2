# Reproducing a data bug

Wrong rows, wrong counts, a constraint that fires when it should not, a query that misses
a case. These reproduce reliably once the fixture holds **the exact rows** that trigger it —
so the work is finding those rows, then writing them down.

## Find the shape first, read-only

Look at the real data before inventing a fixture. Read-only SQL only; see
`references/data-forensics.md` for the connection.

```sql
-- the row the report complained about, and its neighbours
select id, owner_id, url, created_at from bookmark where owner_id = '…' order by created_at;

-- is it a duplicate problem, a null problem, or a collation problem?
select url, count(*) from bookmark group by url having count(*) > 1;
select count(*) from bookmark where url is null;
```

What you are after is the minimal distinguishing feature: a null, a duplicate differing
only by case, a row with a boundary timestamp, two rows the query should separate.

## Write it as a data-slice test

`@DataJpaTest` with Testcontainers, seeded with exactly those rows and nothing else. A
fixture with ten plausible rows hides which one matters.

```kotlin
@DataJpaTest
@Import(TestcontainersConfig::class)
class BookmarkQueryTest(@Autowired val repo: BookmarkRepository) {

  @Tag("BUG-031")
  @Test
  fun `BUG-031 duplicate detection is case-insensitive on the host`() {
    repo.saveAll(listOf(
      Bookmark(owner = alice, url = "https://Example.dev/a"),
      Bookmark(owner = alice, url = "https://example.dev/a"),
    ))
    repo.flush()

    assertThat(repo.findDuplicatesFor(alice)).hasSize(2)   // fails: returns 0
  }
}
```

Two rows, one difference, and the assertion names the rule. That is the whole test.

## When a migration is involved

The test container applies every migration from scratch, so a migration bug shows up as
schema, not data. Check the dev database separately — it is on the old schema until told
otherwise:

apply pending migrations with the project's own migrate command (`commands.migrate`, for example
`./gradlew -q flywayMigrate`), and drop the dev volume when the data has drifted too far.

Drift between the two is a common cause of "it fails in E2E but the unit tests pass".

**Existing migrations are immutable** — the guard refuses the edit. A migration fix is a new
migration.

## Traps

- Seeding through the API instead of the repository couples the reproduction to a second
  layer. For a data bug, seed at the repository.
- `repo.save()` without `flush()` may not hit the database before the assertion, so the test
  passes for the wrong reason.
- Postgres collation and case sensitivity differ from an in-memory H2. Reproduce on the same
  engine as production, which is what Testcontainers is for.
- A count that is right in the test and wrong in production usually means the production
  query has a different `where` clause — compare the generated SQL, not the Kotlin.
