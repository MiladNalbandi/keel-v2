# Feature-sliced placement — TypeScript/React

Slices are named after what the user does, not after what the code is. The layers are ordered, and a slice may only import from layers **below** it — never sideways into a sibling slice.

```
apps/web/src/
├── app/                        providers, router, global styles — the only place they live
├── pages/
│   └── BookmarksPage.tsx       composes widgets; holds no rules
├── widgets/
│   └── BookmarkList/           a composite block used by a page
├── features/                   one user action per slice
│   └── add-bookmark/
│       ├── ui/AddBookmarkForm.tsx
│       ├── model/useAddBookmark.ts
│       └── index.ts            the PUBLIC surface: import from here, never deeper
├── entities/
│   └── bookmark/
│       ├── model/types.ts      the zod schema and the type
│       └── ui/BookmarkCard.tsx presentation of the thing itself
└── shared/
    ├── api/                    the generated client and MSW handlers
    ├── ui/                     Button, Input — no domain knowledge
    └── lib/                    formatters, hooks with no domain knowledge
```

Layer order, lowest first: `shared` → `entities` → `features` → `widgets` → `pages` → `app`.

## Where each kind of new code goes

| The test drives… | Put it in | Notes |
|---|---|---|
| A form the user submits | `features/<action>/ui/` | Named for the verb: `add-bookmark`, not `bookmark-form` |
| The state and mutation behind it | `features/<action>/model/` | The hook that calls the client and holds the local state |
| How a domain object renders | `entities/<thing>/ui/` | No feature logic, no mutation |
| Its type or zod schema | `entities/<thing>/model/` | One source of truth for the shape |
| A button, an input | `shared/ui/` | If it knows a domain word, it does not belong here |
| A page route | `pages/` | Composition only |
| Generated API types and MSW handlers | `shared/api/` | Generated, gitignored, never hand-edited |

## Imports, allowed and not

- Downward only. `features/add-bookmark` may import `entities/bookmark` and `shared/*`.
- **No sibling imports.** `features/add-bookmark` may not import `features/edit-bookmark`. The `feature-isolation` boundary rule states this, allowing `@/shared/**` and `@/entities/**`.
- Import a slice through its `index.ts`, not a path into its internals.
- If two features genuinely need the same thing, it moves **down** into `entities/` or `shared/` — as its own change, not mid-AC.

## The two mistakes this style invites

**A slice named after a noun.** `features/bookmark/` grows into a dumping ground holding every bookmark-related action. Slices are actions: `add-bookmark`, `share-bookmark`. The noun belongs in `entities/`.

**A sibling import to avoid a small duplication.** It is the one rule this style exists to keep, and the check will refuse it. Two similar-looking form fields in two features are fine; lifting them into `shared/ui/` prematurely couples both features to a shape neither one owns yet.

## Two smaller traps

- **A rule in `entities/ui/`.** An entity component renders what it is given. Decisions live in a feature's `model/`.
- **Anything in `shared/` importing upward.** It makes `shared` un-reusable and creates a cycle.
