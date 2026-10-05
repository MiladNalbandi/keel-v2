# State — local, lifted, server

## Three kinds, and only one of them is yours

| Kind | Lives in | Example |
|---|---|---|
| **Server state** | The generated client's cache, never `useState` | The list of bookmarks |
| **Lifted UI state** | The nearest common ancestor of the components that need it | Which row is selected |
| **Local UI state** | The component itself | Whether a menu is open |

Copying server data into `useState` creates a second source of truth that goes stale the moment anything else mutates it. If you find yourself writing `setBookmarks(response.data)`, the cache already holds that — read it from there. See `data.md`.

## Derive, do not duplicate

```tsx
// Wrong: two states that must agree, and one day will not.
const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
const [count, setCount] = useState(0);

// Right: one state, one derivation.
const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
const count = bookmarks.length;
```

Anything computable from existing state during render is not state. That includes filtered lists, totals, validity flags and "is anything selected". Derived values cannot disagree with their source.

`useMemo` is for when the derivation is genuinely expensive or its identity must be stable for a dependency array — not for `bookmarks.length`.

## State that always changes together is one state

```tsx
// Two calls that are never made apart: an invalid intermediate render exists between them.
const [isLoading, setIsLoading] = useState(false);
const [error, setError] = useState<string | null>(null);

// One value, and the impossible combinations cannot be expressed.
type Status =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'failed'; message: string };

const [status, setStatus] = useState<Status>({ kind: 'idle' });
```

A discriminated union makes `isLoading && error` unrepresentable rather than merely unlikely, and the compiler then forces every branch to be handled — which is the same set of branches a test must cover.

## Lift only as far as the shared ancestor

Move state up when two siblings need it, and no further. State parked in a page component that only one leaf reads makes every intermediate component re-render and forces props through layers that do not care. If the props are being threaded through three levels untouched, the state is too high or the tree is wrong.

## Initialise from props carefully

```tsx
// Runs once; later prop changes are ignored — usually a bug.
const [draft, setDraft] = useState(bookmark.url);

// Reset deliberately, by identity:
<BookmarkEditor key={bookmark.id} bookmark={bookmark} />
```

A `key` change remounts the component and re-runs the initialiser. That is clearer than an effect that watches the prop and calls a setter.

## Updates that read the previous value use the function form

```tsx
setSelected((prev) => prev.filter((id) => id !== removed));
```

Two updates in one handler that both read the current value will otherwise see the same stale snapshot.
