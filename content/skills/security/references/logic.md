# Logic flaws — the ones that need intent

A scanner finds injection because injection looks wrong. None of the flaws below look wrong.
They are correct code doing something nobody asked for, and the only way to see them is to
compare the code against **what the system was supposed to permit** — which is what the
spec's authorization rules and acceptance criteria record.

So: read `specs/NNN-slug.md` first, and for each flaw ask its question of that spec.

## IDOR — an id you are allowed to send for a row you are not allowed to see

```kotlin
@GetMapping("/bookmarks/{id}")
fun get(@PathVariable id: UUID) = repo.findByIdOrNull(id)   // exists != yours
```

**Ask the spec:** who may read this resource? If the answer is "its owner" and the query
filters only by id, that is blocking.

The fix belongs in the query, not in a check after it — `findByIdAndOwnerId(id, owner)`
cannot be forgotten by the next caller, and it returns the same 404 either way, so it does
not confirm the row exists.

## Authorization bypass through an unchecked relation

The direct object is checked and the thing reached *through* it is not.

```kotlin
// The folder is the user's. The bookmark being moved into it may not be.
fun move(folderId: UUID, bookmarkId: UUID, auth: Authentication) {
    val folder = folders.findByIdAndOwnerId(folderId, auth.name) ?: throw NotFound()
    bookmarks.findById(bookmarkId).get().folder = folder
}
```

**Ask the spec:** every id in this request — is each one checked, or only the first? Any
operation taking two ids deserves this question explicitly.

## Workflow steps skipped

State machines are usually enforced by the UI's ordering and nothing else.

```kotlin
fun ship(orderId: UUID) { order.status = SHIPPED }   // from any prior status
```

**Ask the spec:** which transitions are legal? If it lists `draft → paid → shipped` and the
code accepts any current status, a client can ship an unpaid order. Blocking.

## Values nobody thought to bound

```kotlin
data class Transfer(val amount: BigDecimal)   // negative reverses the transfer
data class Page(val size: Int)                // 1_000_000 is a denial of service
```

**Ask the spec:** what is the valid range? Negative quantities, zero, absurdly large
values, and `-0.0` are the usual four. A negative amount that inverts a transfer is
blocking; an unbounded page size is blocking if the table grows.

## TOCTOU — check and act are not atomic

```kotlin
// Two concurrent requests both read 100, both withdraw 80.
val balance = accounts.balanceOf(id)
if (balance >= amount) accounts.debit(id, amount)
```

**Ask the spec:** can this run twice at once? If the rule is "the balance may never go
negative", the invariant needs the database to hold it — a conditional update
(`update ... where balance >= :amount`), a constraint, or a lock. A read-then-write over a
value the spec calls an invariant is blocking.

This is the flaw most likely to have a passing test, because tests run one request at a time.

## Replay, and enumeration

**Ask the spec:** what happens if the client sends this twice? A payment, an email or a state
transition with no idempotency key is a finding — blocking for money and messages.

And: a 404 for a missing row beside a 403 for someone else's row together confirm which ids
exist. Return the same response for both unless the spec says existence is public.

## Trusting a client-supplied identity

```kotlin
fun create(@RequestBody body: CreateBookmark) = repo.save(Bookmark(ownerId = body.ownerId))
```

The owner comes from the authenticated principal, never from the body. Always blocking.

## How to write these up

Name the actor, the action and the outcome, and cite the spec rule that is violated:

> `BookmarkService.kt:34` — a signed-in user can read another user's bookmark by changing
> the path id. `specs/012-bookmarks.md#AC-003` says a bookmark is visible only to its
> owner. Blocking.

If the spec is silent on the rule, that is itself the finding: say so and ask, rather than
guessing what was intended.
