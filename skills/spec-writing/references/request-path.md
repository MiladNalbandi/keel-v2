<!-- From keel v1 (MIT), kept in step with its spec-authoring references; keel v2 asks its questions with keel-questions buttons. -->
# The request path

One drawing per spec, under `## Request path`. Flat lines and arrows rather than boxes — this matches the house style of `keel status`, and the point is the *sequence* and what is new, not the shape.

## The form

Every box marked, so the blast radius is visible:

| Mark | Means |
|---|---|
| `+` | New: this did not exist |
| `~` | Changed: it existed and this spec alters it |
| ` ` | Untouched: it is on the path but nothing here changes it |

```
POST /bookmarks                                        + endpoint
  → BookmarkController.create                          + controller
    → SaveBookmark(use case)                           + rule: reject a blank URL
      → BookmarkRepository.save                        ~ new unique index
        → bookmark table                               ~ migration V3
  ← 201 Created { id, url, createdAt }                  + createdAt is new
  ← 422 { field: "url", message }                       + validation response
```

Both responses are drawn, not just the happy one. A `422` that nobody drew is a `422` nobody wrote a criterion for.

## The skeleton comes from the architecture style

Use the sequence for `architecture.style` (`keel arch show` tells you which). These are the runtime layers, not the test layers in a stack pack.

**hexagonal / ddd**

```
HTTP →  adapter/web  →  application (use case)  →  domain (rule)
                            ↓
                     port (interface, in domain)
                            ↓
                     adapter/persistence  →  table
```

Draw the port explicitly. If a use case reaches a table without one, that is the boundary violation `keel verify arch` will flag anyway — better to see it here.

**layered**

```
HTTP →  controller  →  service  →  repository  →  table
```

**mvc**

```
HTTP →  controller (rule lives here)  →  repository  →  table
```

There is no service layer by definition, so say where the rule went. If the answer is "in the controller and it is getting long", that is the fat-controller problem `keel:architecture` (`references/mvc-kotlin.md`) describes, and it belongs in `## Decisions` rather than being left implicit.

**feature-sliced (frontend-led change)**

```
route  →  feature/bookmarks  →  entities/bookmark  →  generated client  →  API
```

## Cross-check against the criteria

Every `[API]` criterion should be traceable to a marked box, and every `+` or `~` box should have a criterion. keel's spec check reports both directions. A `~` box with no criterion is usually a migration or an index that slipped in unnoticed — exactly the thing keel's size check would later escalate on.

## Keep it to the change

This is not an architecture diagram. Draw the path this spec touches and stop. The whole-system view belongs in `docs/knowledge/architecture.md`, which `keel memory` maintains.
