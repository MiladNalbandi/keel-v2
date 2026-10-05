# Component shape — React + TypeScript

## Type the props, not the component

```tsx
type BookmarkCardProps = {
  bookmark: Bookmark;
  onRemove: (id: BookmarkId) => void;
};

export function BookmarkCard({ bookmark, onRemove }: BookmarkCardProps) { /* ... */ }
```

`React.FC` adds an implicit `children` you usually do not want and buys nothing else. Name the type after the component and export it only if another module needs it.

Props are the component's contract: an optional prop with a default is a decision, not a convenience. Three or more booleans usually means the component is really two components.

## Split when the test forces it, not before

A component earns a split when one of these is true:

- Two tests need to render **different parts** of it in isolation.
- A part is used in a second place.
- It holds two unrelated pieces of state that never change together.

Length alone is not a reason. A 120-line form with one job is fine; a 40-line component doing fetching, validation and layout is not.

**Container and presentational is a consequence, not a rule.** When a component's state moves into a hook, what is left presents props — that is the split arriving on its own. Do not create a `BookmarkListContainer` before there is state to hold.

## `useEffect` is usually the wrong tool

Reach for it only to synchronise with something **outside** React: a subscription, a timer, an imperative DOM API, a browser event. For everything else there is a better answer:

| Tempting `useEffect` | Do instead |
|---|---|
| Fetch data on mount | The generated client's query hook — see `data.md` |
| Recompute a value when a prop changes | Compute it during render |
| Reset state when a prop changes | A `key` on the component, so React remounts it |
| Notify a parent after state changes | Call the parent's handler in the same event handler |

An effect that writes state React could have derived is how render loops and stale reads start.

## Keys identify, they do not order

```tsx
{bookmarks.map((b) => <BookmarkCard key={b.id} bookmark={b} onRemove={remove} />)}
```

The key is the item's identity — a server id, a slug, a natural key. An array index is a lie as soon as the list reorders or an item is removed: React reuses the wrong element and local state follows the wrong row.

## Conditional rendering stays flat

```tsx
if (query.isPending) return <Spinner />;
if (query.error) return <ErrorPanel error={query.error} onRetry={query.refetch} />;
if (query.data.length === 0) return <EmptyBookmarks />;
return <BookmarkList bookmarks={query.data} />;
```

Early returns beat nested ternaries, and they make the three states from `data.md` visible as three branches a test can reach.

## Events name the intent

`onRemove`, `onSubmit`, `onSelectBookmark` — not `onClick` passed through two layers. The prop name says what happens in the parent's terms; the child does not know it was a click.
