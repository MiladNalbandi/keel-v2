---
name: symfony-implementation
description: "How to write Symfony backend code in GREEN: controllers, request validation, error mapping, transaction boundaries, and the persistence edge. Load in GREEN for an [API] acceptance criterion, after the architecture skill has said where the file goes."
user-invocable: false
---

# Symfony implementation patterns

`architecture` says *where* a thing goes. This says what it should look like when it gets
there. Load both in GREEN on an `[API]` criterion.

Everything here is subordinate to GREEN's rule: **the minimum code the current test drives**. A
pattern below that the test does not reach is not owed to you by this document.

## Build bottom-up, stop at green

```
migration → entity → test fixture → authorization rule → use case
          → request DTO → response mapping → controller → route
```

Each layer is exercised by the test as it arrives, so what broke is what you just wrote. Starting
at the controller means nothing runs until the last piece lands.

## Controllers stay thin

A controller does four things: bind the request, call one use case (a service, not the
EntityManager directly), map the result, set the status. Anything else — a rule, a query, a
second call — belongs below it.

```php
#[Route('/bookmarks', methods: ['POST'])]
public function create(
    #[MapRequestPayload] CreateBookmarkRequest $request,
    CreateBookmark $createBookmark,
): JsonResponse {
    $bookmark = $createBookmark->handle($request->toCommand());
    return $this->json(BookmarkResponse::fromDomain($bookmark), Response::HTTP_CREATED);
}
```

## Validation — and the trap that has its own lens

**A nested object inside a request DTO is not validated unless its property carries
`#[Assert\Valid]`.** `#[MapRequestPayload]` and manual `$validator->validate($request)` calls both
run the constraints declared on the top-level object's own properties — a `CreateBookmarkRequest`
with an `Address $address` property validates nothing inside `$address` unless that property is
also annotated `#[Assert\Valid]`. The nested class's own `#[Assert\NotBlank]` annotations are real
code that reads as validation and silently never fires — the same shape as Kotlin's `@Valid` on a
`@RequestParam` trap, and exactly what keel's `exploration` lens is built to catch.

So: a validation rule is not implemented until a test has sent the bad input and seen the 422/400,
including for anything nested. Constraint attributes are a declaration, not evidence.

Validate shape at the edge (`#[Assert\NotBlank]`, `#[Assert\Url]`, `#[Assert\Length]`) and
invariants in the domain. "This url is well-formed" is the edge. "This user may not have two
bookmarks with the same url" is not — it needs the database, and it belongs where the rest of
that rule lives (a unique index plus a mapped exception, see below — not a validator constraint
that would need its own query to enforce).

## Error mapping — where a failure surfaces decides whether anyone sees it

The common defect: a `try/catch` around the use-case call that maps a persistence failure to a
typed error, on a path where Doctrine actually raises the constraint violation **at flush** —
often after the handler believes it has already succeeded, or inside a kernel event the
controller's own `catch` never sees. The `catch` is unreachable, the caller gets Symfony's
default 500 error page, and the branch meant to produce a 409 is dead code that reads as live.

```php
#[AsEventListener(event: KernelEvents::EXCEPTION)]
final class UniqueConstraintExceptionListener
{
    public function __invoke(ExceptionEvent $event): void
    {
        if (!$event->getThrowable() instanceof UniqueConstraintViolationException) return;
        $event->setResponse(new JsonResponse(['error' => 'already exists'], Response::HTTP_CONFLICT));
    }
}
```

Handle it where it actually arrives — a kernel exception listener sees what escapes the flush, the
same role Spring's `@ExceptionHandler` plays. And prove it with a test that violates the real
database constraint, not a mocked repository returning a typed error: a mock asserting a value the
real adapter never produces is `test-integrity`'s first finding, in this stack exactly as in any
other.

## Transactions

- **One transaction per use case**, opened at the use case, not scattered across repository
  calls. `$this->entityManager->wrapInTransaction(fn () => ...)` around the whole operation, not
  a `flush()` per write inside it.
- **A service that calls `flush()` after every repository method is not transactional** — each
  `flush()` is its own implicit commit, which is how a two-write operation half-commits the same
  way an un-transactional Spring path does.
- **Read-modify-write needs a lock or a version column** (`#[ORM\Version]` for Doctrine's
  optimistic locking). Two callers is the ordinary case; the `concurrency` lens exists because a
  single sequential test can never show the problem.
- **Do not hold a transaction open across an external HTTP call.** A slow third party then holds
  a database connection, and the pool is what runs out first — identical failure shape to any
  other stack, just Doctrine's connection pool instead of HikariCP's.

## The persistence edge

- Return domain types or DTOs from the use case, not Doctrine entities. An entity serialized
  directly is how a lazy association turns into either an infinite-recursion loop (bidirectional
  relations) or a `Doctrine\ORM\PersistentCollection` that fails to initialize once the entity
  manager's request-scoped connection has already closed — Symfony's version of Kotlin's
  `LazyInitializationException` outside an open session.
- `$entityManager->persist()` followed by `flush()` on an entity with an assigned id is still an
  insert only the first time; a second `persist()` on the same managed instance is a no-op that
  can silently mask "this should have failed as a duplicate." If the criterion says "creates,"
  the test must prove a second call does not silently succeed.
- Add indexes for the query the criterion drives, in the same migration that made it necessary —
  `bin/console doctrine:migrations:diff` proposes one from the mapping; review it, don't
  trust it blindly.

## What not to add

No field, route, abstraction, helper or branch the current test does not drive — including an
interface with one implementation, a config parameter nobody reads, and a service that forwards
to one repository method. The duplication that GREEN leaves behind is discharged in the
`refactor` phase, where the tests are green and hold the behaviour still.
