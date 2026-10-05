# Endpoint security — Kotlin + Spring

## Authorization belongs on the method, not the controller

A controller check guards one entry point. The same service called from a scheduled job, a
message listener or another controller has no check at all.

```kotlin
// Weak: only this HTTP path is guarded.
@RestController
class BookmarkController(private val service: BookmarkService) {
    @GetMapping("/bookmarks/{id}")
    fun get(@PathVariable id: UUID, auth: Authentication): BookmarkResponse {
        val b = service.find(id)
        if (b.ownerId != auth.name) throw AccessDeniedException("not yours")
        return b.toResponse()
    }
}

// Better: the rule travels with the operation.
@Service
class BookmarkService(private val repo: BookmarkRepository) {
    @PreAuthorize("@owns.bookmark(#id, authentication)")
    fun find(id: UUID): Bookmark = repo.findByIdOrNull(id) ?: throw NotFound(id)
}
```

Check for: every new public method on a service reachable from a controller has either a
`@PreAuthorize`, or an explicit owner check inside it, or a comment saying why it is public.
A new endpoint with no authorization at all is blocking unless the spec says it is public.

`@PreAuthorize` needs `@EnableMethodSecurity`. Without it the annotation is **silently
ignored** — grep for it once per module and treat its absence as blocking.

## Validation at the boundary

`@Valid` on the parameter is what triggers bean validation. Without it the constraints on
the DTO are decoration.

```kotlin
data class CreateBookmark(
    @field:NotBlank @field:Size(max = 2048) @field:URL val url: String,
    @field:Size(max = 120) val title: String?,
)

@PostMapping("/bookmarks")
fun create(@Valid @RequestBody body: CreateBookmark): ...   // @Valid is required
```

Use `@field:` on Kotlin constructor properties — plain `@NotBlank` lands on the constructor
parameter and is not seen by the validator.

## Mass assignment

Never bind a request body straight onto an entity. A client that can send `ownerId`,
`role` or `createdAt` will.

```kotlin
// Blocking: the client chooses the owner.
fun create(@RequestBody b: Bookmark) = repo.save(b)

// The DTO names exactly what a client may set.
fun create(@Valid @RequestBody body: CreateBookmark, auth: Authentication) =
    repo.save(Bookmark(url = body.url, title = body.title, ownerId = auth.name))
```

Check every new DTO field against the spec: a field a client should not control is blocking.

## Injection

Parameter binding, never string building — in JPQL and in native SQL alike.

```kotlin
@Query("select b from Bookmark b where b.ownerId = :owner")          // fine
@Query(value = "select * from bookmark where tag = '" + tag + "'")   // blocking
```

Also: a `Sort` or `Pageable` field name taken from a query parameter is injectable in some
providers — allowlist sortable fields rather than passing the raw string through.

## Error responses that leak

A stack trace, a SQL fragment, a class name or an internal id in a 4xx/5xx body tells an
attacker how the system is built. Assert the shape of the error body in the body test, not
just the status.

```kotlin
@ExceptionHandler(NotFound::class)
fun notFound(e: NotFound) = ResponseEntity.status(404).body(ApiError("not_found"))
```

Check for: a new `catch` that puts `e.message` into a response, a `printStackTrace`, or a
`@ResponseStatus(reason = ...)` carrying detail.

## Quick pass

- Does every new endpoint appear in the contract file with its real auth requirement?
- Does an id in the path get checked for ownership, or only for existence?
- Is there a new endpoint that returns a list without a limit?
- Does a 404 and a 403 look different in a way that confirms a resource exists?
