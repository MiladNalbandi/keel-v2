# Reproducing a performance regression

"It is slow" is not reproducible. A **threshold with a shape** is: a query count that must
not grow with the row count, a response that must stay under a budget at a stated size. Get
the test to fail on the shape, not on a stopwatch reading.

## Assert the shape, not the duration

Wall-clock assertions flake on a loaded laptop and pass on a fast one. The durable
assertion is almost always a *count*.

```kotlin
@DataJpaTest
@Import(TestcontainersConfig::class)
class BookmarkListPerfTest(
  @Autowired val repo: BookmarkRepository,
  @Autowired val stats: Statistics,          // Hibernate statistics, enabled in test config
) {

  @Tag("BUG-047")
  @Test
  fun `BUG-047 listing bookmarks does not query per row`() {
    repo.saveAll((1..50).map { Bookmark(owner = alice, url = "https://x.dev/$it") })
    repo.flush()
    stats.clear()

    val list = service.listFor(alice)          // each bookmark lazily loads its owner

    assertThat(list).hasSize(50)
    assertThat(stats.prepareStatementCount).isLessThanOrEqualTo(2)   // fails: 51
  }
}
```

51 versus 2 is an N+1, it fails identically on every machine, and the number tells the
reviewer what the bug was. Nothing about that assertion depends on how fast the host is.

## When it really is time

Some regressions only show as latency. Then state the size and the budget explicitly, keep
the budget well clear of noise, and warm up first.

```kotlin
@Tag("BUG-052")
@Test
fun `BUG-052 report stays under budget at ten thousand rows`() {
  seed(10_000)
  service.report(alice)                                  // warm the JIT and the caches
  val elapsed = measureTimeMillis { service.report(alice) }
  assertThat(elapsed).isLessThan(2_000)                  // budget, not a measurement
}
```

A budget three times the observed good value still catches an order-of-magnitude
regression and does not flake. A budget 10% above it is a future flake.

## Confirm it is the size that matters

Run the same test at 10 rows and at 10,000. A bug that fails at both sizes is not a
scaling bug — it is a plain slow path, and a unit test on that path is the better
reproduction. A bug that fails only at the larger size is the real thing.

## Find the query before guessing

```
docker compose logs --tail 40 api   # Hibernate SQL if show-sql is on
```

For the shape of the plan, read-only:

```sql
explain analyze select * from bookmark where owner_id = '…' order by created_at desc limit 50;
```

A sequential scan on a large table, or a sort that spills to disk, names the fix (an index)
without any guessing. See `references/data-forensics.md` for the connection.

## Traps

- Do not add a `@Timeout` and call it a reproduction: it tells you the test was slow, not
  which of the fifty statements caused it.
- Benchmarks belong in the module suite only if they are fast. A ten-second perf test in
  the AC loop destroys the feedback speed the loop depends on.
- An index added to fix this is a **new migration**; existing migrations are immutable and
  the guard refuses the edit.
