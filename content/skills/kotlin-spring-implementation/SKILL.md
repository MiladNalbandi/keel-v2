---
name: kotlin-spring-implementation
description: "How to write Kotlin/Spring backend code in GREEN: controllers, request validation, error mapping, transaction boundaries, and the persistence edge. Load in GREEN for an [API] acceptance criterion, after the architecture skill has said where the file goes."
user-invocable: false
---

# Kotlin/Spring implementation patterns

`architecture` says *where* a thing goes. This says what it should look like when it gets
there. Load both in GREEN on an `[API]` criterion.

| Also read | When |
|---|---|
| `references/design.md` | SOLID judged against the minimum-code rule, Kotlin-specific design (value classes, sealed results, data-class traps), and which patterns earn their keep. In `refactor`, or in GREEN when the criterion touches a shape that already exists |

Everything here is subordinate to GREEN's rule: **the minimum code the current test drives**. A
pattern below that the test does not reach is not owed to you by this document.

## Build bottom-up, stop at green

```
migration → entity → test fixture → authorization rule → use case
          → input validation → response mapping → controller → route
```

Each layer is exercised by the test as it arrives, so what broke is what you just wrote. Starting
at the controller means nothing runs until the last piece lands.

## Controllers stay thin

A controller does four things: bind the request, call one use case, map the result, set the status.
Anything else — a rule, a query, a second call — belongs below it. The test for a thin controller
is that reading it tells you *what* happens and nothing about *how*.

```kotlin
@PostMapping("/bookmarks")
fun create(@Valid @RequestBody body: CreateBookmarkRequest): ResponseEntity<BookmarkResponse> =
    createBookmark(body.toCommand())
        .let { ResponseEntity.status(CREATED).body(it.toResponse()) }
```

## Validation — and the trap that has its own lens

**`@Valid` on a `@RequestBody` works. `@Valid` on a `@RequestParam` or `@PathVariable` does
nothing unless the class carries `@Validated`.** The annotation is right there in the source, it
reads as validation, and it never fires — which is why keel's `exploration` lens exists and why
`librarian` uses this exact case as its example of a citation that proves the code *says*
something without proving it *does*.

So: a validation rule is not implemented until a test has sent the bad input and seen the status.
Constraint annotations are a declaration, not evidence.

Validate shape at the edge (`@NotBlank`, `@Size`, `@Email`) and invariants in the domain. "This
url is well-formed" is the edge. "This user may not have two bookmarks with the same url" is not —
it needs the database, and it belongs where the rest of that rule lives.

## Error mapping — where a failure surfaces decides whether anyone sees it

The common defect: a `catch` that maps a persistence failure to a typed error, on a path where the
failure is raised **at commit** — after the handler has already returned. The catch is unreachable,
the caller gets a 500 with a framework body, and the branch that was supposed to produce a 409 is
dead code that reads as live.

```kotlin
@ExceptionHandler(DataIntegrityViolationException::class)
fun onConflict(e: DataIntegrityViolationException) =
    ResponseEntity.status(CONFLICT).body(ErrorResponse("url already saved"))
```

Handle it where it actually arrives — an `@ExceptionHandler` or `@ControllerAdvice` sees what
escapes the transaction. And prove it with a test that violates the real constraint, not a mocked
port returning a typed error: a mock asserting a value the real adapter never produces is
`test-integrity`'s first finding.

## Transactions

- **One transaction per use case**, opened at the use case, not at the controller or the
  repository. A boundary at the repository makes every call its own transaction, which is how a
  two-write operation half-commits.
- **`@Transactional` does nothing on a private method or a self-call** — the proxy is not in the
  path. This fails silently, so it is worth knowing rather than discovering.
- **Read-modify-write needs a lock or a version column.** Two callers is the ordinary case; the
  `concurrency` lens exists because a single sequential test can never show the problem.
- **Do not hold a transaction across a network call.** A slow third party then holds a database
  connection, and the pool is what runs out first.

## The persistence edge

- Return domain types from the use case, not entities. An entity crossing into the controller is
  how a lazy association becomes a `LazyInitializationException` in the serializer, at the one
  point in the request where the transaction is already closed.
- `save()` on an entity with an assigned id is an upsert, not an insert. If the criterion says
  "creates", the test must prove a second call does not silently overwrite.
- Add indexes for the query the criterion drives, in the same migration that made it necessary.

## What not to add

No field, endpoint, abstraction, helper or branch the current test does not drive — including an
interface with one implementation, a config flag nobody sets, and a `@Service` that forwards to one
repository method. The duplication that GREEN leaves behind is discharged in the `refactor` phase,
where the tests are green and hold the behaviour still.
