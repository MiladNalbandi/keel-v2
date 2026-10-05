# MVC placement — Kotlin/Spring

There is no `service/`. The controller handles the request and talks to the repository directly. That is a legitimate style for a small application, and this file says where each kind of code goes when the middle layer is absent — and when its absence has stopped being a choice.

```
apps/api/src/main/kotlin/app/
├── controller/
│   ├── BookmarkController.kt       @RestController: the endpoint AND the rule
│   └── dto/BookmarkRequest.kt      request/response types, with validation annotations
├── repository/
│   └── BookmarkRepository.kt       Spring Data interface
├── domain/                         (or model/) the @Entity types
│   └── Bookmark.kt                 @Entity lives HERE — correct in this style
└── config/
    └── SecurityConfig.kt
```

## Where each kind of new code goes

| The test drives… | Put it in | Notes |
|---|---|---|
| Request validation, a status code | `controller/dto/` + `controller/` | Bean validation on the DTO (`@field:NotBlank`), not hand-rolled `if` blocks |
| A business rule | **the controller method** | This is the defining difference. The rule and the transaction boundary are both here |
| A query | `repository/` | A derived method name first; `@Query` only when that cannot express it |
| Mapping entity → response | `controller/` or the DTO's own factory | At the edge. Never return an `@Entity` |
| A new field on a persisted type | `domain/` entity **and** a migration | Always together |
| Auth, CORS, serialisation | `config/` | Not in a controller |
| The transaction | `@Transactional` on the controller method | Because that method owns the rule |

## When the missing service layer is fine, and when it is debt

**Fine** when the controller method reads as one thing: validate, one or two repository calls, map, return. A CRUD endpoint with an ownership check is not improved by a pass-through service — `fun findAll() = repo.findAll()` adds a file and no behaviour.

**Debt** the moment any of these is true:

- A **second entry point** needs the same rule — another controller, a scheduled job, a message listener. Duplicating it is the real cost.
- The rule spans **several repositories** and must be atomic, so the transaction boundary is doing load-bearing work.
- A test has to go through HTTP to exercise a rule that has nothing to do with HTTP.

When one of those arrives, introducing `service/` is **its own change with its own acceptance criterion**. Do not grow one in passing while implementing an unrelated AC — say so at the gate instead, and record the style as `layered` once it lands.

## Imports, allowed and not

- `controller/` imports `repository/`, `domain/` and its own `dto/`.
- **`repository/` imports `domain/` only.** A repository reaching back into a controller is upward and wrong; a `no-controller-in-repository` boundary rule makes that explicit.
- `domain/` entities import JPA. Expected here, unlike hexagonal.

## The two mistakes this style invites

**The fat controller.** A method that validates, loads, applies three rules, maps and handles errors in sixty lines. The problem is not the line count, it is that the rules are now only reachable through HTTP — so every test of them is a `@WebMvcTest` with a mocked repository, and none of them can be a unit test. Extract a private function in the same file first; that costs nothing and keeps the rule nameable. When a private function needs to be shared, that is the signal above.

**`JpaRepository` injected straight into a controller, then leaked.** Injecting it is the style. Returning its entities is not: an `@Entity` as the response body ties the JSON to the table, drags lazy-loading into serialisation, and exposes every column you later add. Map to a DTO at the edge, always.

## Two smaller traps

- **Business rules in a DTO.** Validation annotations describe the *shape* of a request. A rule about what the user may do is authorisation or domain logic, and it belongs in the controller method (or `config/` for the authorisation part), not in the type.
- **Catch-all exception handling per controller.** One `@ControllerAdvice` in `config/` maps domain failures to status codes. A `try/catch` in each method makes the mapping inconsistent and the tests repetitive.
