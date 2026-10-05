# Hexagonal placement — Symfony

The rule that generates every answer below: **the `Domain` namespace must load with no Symfony
or Doctrine class on its dependency graph.** If a change would make `Domain\` reference
`Symfony\` or `Doctrine\ORM\`, it belongs on the other side of a port.

```
src/
├── Domain/                       no framework imports, ever
│   ├── Bookmark.php              the type and its invariants
│   ├── BookmarkId.php            a value object, not a bare string
│   ├── UrlRule.php               a rule that needs no collaborator
│   └── Bookmarks.php             a PORT: an interface, declared here, implemented elsewhere
├── Application/
│   └── SaveBookmark.php          one use case, orchestrates Domain + ports
└── Infrastructure/
    ├── Web/BookmarkController.php     the route, request DTOs, mapping
    └── Persistence/
        ├── DoctrineBookmark.php       #[ORM\Entity] lives HERE, not on the domain type
        └── DoctrineBookmarks.php      implements Domain\Bookmarks
```

## Where each kind of new code goes

| The test drives… | Put it in | Notes |
|---|---|---|
| A validation rule with no I/O | `Domain/` | Usually a method on the type, not a new class |
| A rule needing data it does not hold | `Application/`, calling a port | Add the port method to the existing interface in `Domain/` |
| A new route | `Infrastructure/Web/` | Controller maps request → use case → response; no rules here |
| A new query | Port method in `Domain/`, implementation in `Infrastructure/Persistence/` | The signature speaks domain types, not Doctrine ones |
| A new external call (email, payment) | Port in `Domain/`, client in `Infrastructure/` | The domain names the capability; the adapter names the vendor |
| A migration | Doctrine migrations directory | New file only; existing ones are immutable |

## Imports, allowed and not

- `Domain\` imports `Domain\` and PHP's standard library. Nothing else — no `Symfony\`, no
  `Doctrine\ORM\`.
- `Application\` imports `Domain\`. Not `Infrastructure\`.
- `Infrastructure\` imports `Application\` and `Domain\` freely — this is the only direction
  that goes inward.

The `domain-framework-free` boundary rule states the first of those; the others
are convention.

## The two mistakes this style invites

**A port for one implementation that will never have another.** The interface is justified when
it keeps the framework out of `Domain/`, not by a hypothetical second implementation. One
implementation is normal and fine — but do not add the port at all if the test does not require
`Domain/` to reference the capability.

**An anaemic domain with the rules in the use case.** If `SaveBookmark` is doing the validation
that `Bookmark` could do in its own constructor, the rule is in the wrong place. Test it directly
on the domain type — the test being easy to write with no Symfony kernel booted is the signal you
got it right.

## Two smaller traps

- **Mapping in the wrong direction.** `Infrastructure/Persistence/DoctrineBookmark.php` maps to
  and from `Domain\Bookmark`. The domain type never grows a `toDoctrineEntity()`.
- **A DTO leaking inward.** A use case takes domain types or a small command object defined in
  `Application/`; it never takes the `#[MapRequestPayload]` request class.
