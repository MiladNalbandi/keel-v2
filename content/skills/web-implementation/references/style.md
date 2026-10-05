# Style and file layout

## Formatting is not a discussion

Prettier decides it: line width, quotes, semicolons and trailing commas are whatever the project's Prettier config says, so they are never a discussion. ESLint is the project's own config too — on a Kotlin+TS repo the static checks are detekt, ktlint and eslint together.

Two consequences:

- **Never reformat a file you did not otherwise change.** It buries the real diff and the reviewer has to hunt for it.
- **Never argue with a lint rule in code.** If a rule is wrong, change the config in its own commit, with a reason. A scattering of `// eslint-disable-next-line` is a config change nobody reviewed.

## Naming

| Thing | Convention | Example |
|---|---|---|
| Component | `PascalCase`, file matches the component | `BookmarkCard.tsx` |
| Hook | `useCamelCase`, one per file | `useAddBookmark.ts` |
| Type / interface | `PascalCase`, no `I` prefix | `Bookmark`, `BookmarkCardProps` |
| Plain function, variable | `camelCase` | `formatUrl` |
| Constant set at module level | `SCREAMING_SNAKE` only for true constants | `MAX_TAGS` |
| Test | mirrors its subject | `BookmarkCard.test.tsx` |

Name by what it *is* in the domain, not by its shape: `BookmarkList`, not `ItemsContainer`. Booleans read as assertions — `isPending`, `hasTags`, `canRemove`.

## One exported component per file

Small helpers used only by that component can live beside it. The moment a second file imports one, it moves — to `shared/ui/` if it knows no domain words, or to the entity that owns it. Where a directory has a public surface (feature-sliced `index.ts`), import from that surface and never reach deeper.

## Import order

Let the linter enforce it; write it in this order so the diff is stable:

```tsx
import { useState } from 'react';              // external packages

import { useListBookmarks } from '@/api/generated/bookmarks';   // generated
import { Button } from '@/shared/ui';                            // internal, by alias

import { BookmarkRow } from './BookmarkRow';   // relative, same feature
import type { Bookmark } from './types';       // types last
```

Use the path alias for anything outside the current directory. A relative path that climbs (`../../../shared`) is a sign the file is in the wrong place — check `architecture`.

## What strictness buys

`strict` is on. Lean on it rather than working around it:

- **No `any`.** `unknown` plus a narrowing check says "I do not know yet"; `any` says "stop checking", and the error resurfaces at runtime instead.
- **No non-null `!`** on data that came over the wire. If it can be absent, handle absent — that is usually a branch a test needs anyway.
- **`type` for shapes, `interface` when something will implement or extend it.** Either is fine; be consistent within a directory.
- **Discriminated unions over optional fields.** `{ kind: 'failed'; message: string }` makes the impossible combination unrepresentable (`state.md`).

## Comments

Explain **why**, never what. The name and the types cover what. A comment earns its place when it records a constraint, a workaround with a link, or a decision a reader would otherwise undo. Delete commented-out code rather than shipping it.
