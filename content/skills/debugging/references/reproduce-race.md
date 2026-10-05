# Reproducing a race

A race fails on interleaving, not on input. Running the test once proves nothing, so the
technique is to either **repeat it** until the unlucky interleaving happens, or **force**
the interleaving with a latch.

## Repeat until it fails

Cheapest first. If the window is wide, repetition finds it.

```kotlin
@Tag("BUG-014")
@RepeatedTest(200)
fun `BUG-014 concurrent claims may not oversell the last seat`() {
  val seats = SeatRepository.remaining(eventId)
  val pool = Executors.newFixedThreadPool(8)
  val latch = CountDownLatch(1)
  val claimed = AtomicInteger()

  repeat(8) {
    pool.submit {
      latch.await()                       // all threads start together
      runCatching { service.claim(eventId) }.onSuccess { claimed.incrementAndGet() }
    }
  }
  latch.countDown()
  pool.shutdown()
  pool.awaitTermination(5, SECONDS)

  assertThat(claimed.get()).isLessThanOrEqualTo(seats)
}
```

The latch is the important part: without it the threads start staggered and mostly miss
each other. `@RepeatedTest` plus a latch beats `@RepeatedTest` alone by a wide margin.

## Force the interleaving

When repetition will not do it, make the window deterministic. Inject a seam the test can
block on — usually a hook already present for metrics or events.

```kotlin
val reachedCheck = CountDownLatch(1)
val releaseCheck = CountDownLatch(1)

// test double that parks inside the critical section
val repo = object : SeatRepository {
  override fun remaining(id: EventId): Int {
    reachedCheck.countDown()
    releaseCheck.await()                  // hold thread A mid-check
    return delegate.remaining(id)
  }
}
```

Thread A parks between check and write; thread B runs to completion; release A. That is the
lost-update shape, and it fails every time rather than one run in fifty.

## Waiting for a condition, never for a duration

`Thread.sleep` in a test is a future flake. Use awaitility, and assert the condition:

```kotlin
await().atMost(2, SECONDS).untilAsserted {
  assertThat(repo.remaining(eventId)).isZero()
}
```

## Confirming it is really the race

Run the same test with concurrency set to one. It must **pass**. If it fails serially too,
the bug is a plain logic bug and this whole approach is the wrong tool — rewrite it as a
unit test.

## Traps

- A test that fails one run in fifty is not a reproduction; it is a coin toss. Get the
  failure rate near 100% before Gate R, or you cannot tell a fix from luck.
- Shared mutable fixtures make *any* parallel test look like a race. Rule that out first
  (`references/reproduce-flaky.md`).
- Keep the repeat count high enough to be reliable and low enough to stay in the loop's
  time budget; 200 fast iterations beats 10 slow ones.
