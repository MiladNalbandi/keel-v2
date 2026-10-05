# Talking to the API

## Only the generated client

The contract is the source of truth. Types, the client and the MSW handlers are generated from it into `frontend.generated` (default `src/api/generated`), which is **gitignored and refused by keel's edit guard**.

```tsx
import { useListBookmarks, useCreateBookmark } from '@/api/generated/bookmarks';
```

Never hand-write a `fetch` or an axios call to your own API. A hand-written call cannot drift *visibly*: the contract changes, codegen changes the generated types, and your call keeps compiling against a shape the server no longer returns. With the generated client, the same change breaks the build — which is the point.

If the generated shape is wrong or missing, the **contract** is wrong. That is a contract change (its own step, before the code), not something to patch around in a component.

## Parse at the boundary

```tsx
import { bookmarkSchema } from '@/api/generated/schemas';

const parsed = bookmarkSchema.array().safeParse(response.data);
if (!parsed.success) throw new Error('bookmarks response did not match the contract');
```

The generated zod schemas turn "the server lied" into a loud failure at the edge instead of `undefined` surfacing three components later. Parse once, where the data enters; everything downstream then holds a type it can trust.

`web-testing` requires at least one test per endpoint that parses a real response body, so a contract mismatch fails on the frontend too.

## Loading, error and empty are three states

```tsx
const query = useListBookmarks();

if (query.isPending) return <BookmarkListSkeleton />;
if (query.error) return <ErrorPanel error={query.error} onRetry={query.refetch} />;
if (query.data.length === 0) return <EmptyBookmarks onAdd={openForm} />;
return <BookmarkList bookmarks={query.data} />;
```

A single `isLoading` boolean cannot express **succeeded, and there is nothing to show** — which is the state a new user sees first and the state tests forget. Treat it as its own branch with its own copy, not as a list that happens to render zero rows.

An error state needs a way out. `onRetry` is usually that; if retrying cannot help, say what the user should do instead.

## Mutations invalidate, they do not patch

```tsx
const create = useCreateBookmark({
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['bookmarks'] }),
});
```

After a write, let the cache refetch. Hand-patching the cached array duplicates the server's rules on the client — ordering, defaults, derived fields — and the two drift. Optimistic updates are worth it only when an AC asks for them, and then the rollback path needs a test.

## Never send a request from an effect

The query hooks already handle mount, refetch, dedupe and cancellation. A `useEffect` that calls the client re-runs on every dependency change, races itself, and leaks on unmount. See `components.md`.

## What belongs where

| Concern | Where |
|---|---|
| The call and its cache | The query/mutation hook, in a model or hook file |
| Turning a response into view data | The component, or a plain function beside it |
| Retry, dedupe, cancellation | The generated client — do not reimplement |
| Base URL, auth headers | The client's configuration, once |
