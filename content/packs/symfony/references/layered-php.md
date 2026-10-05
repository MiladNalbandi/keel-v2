# Layered placement — Symfony

Packaging is by technical role, and calls only ever go **downward**: controller → service →
repository → database. This is the right default for most applications; it needs no ports and no
events.

```
src/
├── Controller/
│   └── BookmarkController.php     the route, request/response DTOs
├── Service/
│   └── BookmarkService.php        the rules and the transaction boundary
├── Repository/
│   └── BookmarkRepository.php     extends Doctrine's ServiceEntityRepository
├── Entity/
│   └── Bookmark.php               #[ORM\Entity] lives HERE in this style — that is correct
└── EventListener/
    └── ExceptionListener.php      cross-cutting concerns
```

## Where each kind of new code goes

| The test drives… | Put it in | Notes |
|---|---|---|
| Request validation, a status code | `Controller/` | `#[Assert\*]` on the request DTO, not hand-rolled ifs |
| A business rule | `Service/` | The rule lives with the transaction that applies it |
| A query | `Repository/` | A query builder method first; raw DQL only when that cannot express it |
| A new field on a persisted type | `Entity/`, plus a migration | Entity and migration change together, always |
| Something cross-cutting (auth, exception mapping) | `EventListener/` | Not in a controller |
| A new route | `Controller/` + `Service/` | One controller method, one service method |

## If there is no `Service/`, you are reading the wrong file

A thin-controller-only Symfony app that calls the repository directly has no middle layer — this
file assumes one exists. Keep it that way once it does: adding a rule is the moment `Service/`
earns its file, not before.

## Imports, allowed and not

- `Controller/` imports `Service/` and `Entity/`.
- `Service/` imports `Repository/` and `Entity/`.
- **`Repository/` imports `Entity/` only.** A repository importing a controller or a service is
  upward and wrong; the `no-controller-in-repository` boundary rule states this.
- `Entity/` classes import Doctrine's ORM attributes. That is expected here, unlike hexagonal.

## The two mistakes this style invites

**A service that is only a pass-through.** `public function findAll(): array { return
$this->repo->findAll(); }` adds a file and no behaviour. Let the controller call the repository
until a rule actually exists — then the service appears with the rule in it.

**An anaemic service holding another layer's job.** A `Service/` method doing request parsing, or
building a response DTO, has taken the controller's work. Keep mapping at the edge.

## Two smaller traps

- **An entity used as the response body.** Symfony's serializer will happily emit it, which leaks
  the schema and couples the API to the table. Map to a DTO in `Controller/`.
- **A transaction opened in the controller.** The transaction belongs to the service method that
  owns the rule, not the route that happened to trigger it.
