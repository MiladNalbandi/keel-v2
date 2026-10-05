# Hexagonal placement — Kotlin/Spring

The rule that generates every answer below: **the domain package must compile with no framework on the classpath.** If a change would make `domain/` import Spring or JPA, it belongs on the other side of a port.

```
apps/api/src/main/kotlin/app/
├── domain/                     no framework imports, ever
│   ├── Bookmark.kt             the type and its invariants
│   ├── BookmarkId.kt           value class, not String
│   ├── UrlRule.kt              a rule that needs no collaborator
│   └── Bookmarks.kt            a PORT: interface, declared here, implemented elsewhere
├── application/
│   └── SaveBookmark.kt         one use case, orchestrates domain + ports
└── adapter/
    ├── web/BookmarkEndpoint.kt @RestController, DTOs, mapping
    └── persistence/
        ├── JpaBookmark.kt      @Entity lives HERE, not on the domain type
        └── JpaBookmarks.kt     implements domain.Bookmarks
```

## Where each kind of new code goes

| The test drives… | Put it in | Notes |
|---|---|---|
| A validation rule with no I/O | `domain/` | Usually a method on the type, not a new class |
| A rule needing data it does not hold | `application/`, calling a port | Add the port method to the existing interface in `domain/` |
| A new endpoint | `adapter/web/` | Controller maps request → use case → response; no rules here |
| A new query | Port method in `domain/`, implementation in `adapter/persistence/` | The signature speaks domain types, not JPA ones |
| A new external call (email, payment) | Port in `domain/`, client in `adapter/` | The domain names the capability; the adapter names the vendor |
| A migration | `src/main/resources/db/migration/` | New file only; existing ones are immutable |

## Imports, allowed and not

- `domain/` imports `domain/` and the Kotlin stdlib. Nothing else.
- `application/` imports `domain/`. Not `adapter/`.
- `adapter/` imports `application/` and `domain/` freely — this is the only direction that goes inward.

The `domain-framework-free` boundary rule states the first of those; the others are convention.

## The two mistakes this style invites

**A port for one implementation that will never have another.** The interface is justified when it keeps the framework out of `domain/`, not by a hypothetical second implementation. One implementation is normal and fine — but do not add the port at all if the test does not require `domain/` to reference the capability.

**An anaemic domain with the rules in the use case.** If `SaveBookmark` is doing the validation that `Bookmark` could do in its constructor, the rule is in the wrong place. Test it directly on the domain type — the test being easy to write with no Spring is the signal you got it right.

## Two smaller traps

- **Mapping in the wrong direction.** `adapter/persistence/JpaBookmark.kt` maps to and from `domain.Bookmark`. The domain type never grows a `toJpa()`.
- **A DTO leaking inward.** A use case takes domain types or a small command class defined in `application/`; it never takes the web request class.
