# Layered — frontend

The default when a React app has no stronger opinion: horizontal layers, each depending only
downward. Simpler than feature-sliced, and the right answer until the number of features makes
"which feature owns this" the harder question than "which layer is this".

```
src/
  pages/        route entry points. Compose, fetch, decide. No business rules.
  components/   presentational and reusable. Props in, events out.
  hooks/        stateful behaviour a component can use. Where a mutation belongs.
  api/          the generated client and schemas. Never hand-edited.
  lib/          pure helpers with no React in them.
```

## Where a new thing goes

| You are adding | It goes in | Because |
|---|---|---|
| a route or screen | `pages/` | it is the composition point, and the only layer allowed to know routing |
| something two screens show | `components/` | a component that knows its route cannot be the second screen's |
| a call to the API | `hooks/`, over `api/` | the generated client is transport; the hook is the behaviour around it — loading, error, retry, invalidation |
| a validation or format rule | `lib/` | if it does not need React, it does not need to be in a component to be tested |
| a type from the contract | nowhere — import it | it already exists under `api/generated`; writing it again is how the two drift |

## The boundary to keep

```
components/** may not import pages/** or routes/**
```

That one rule carries the direction of the whole style. A component importing a page has inverted
the dependency: the page composes the component, so the component knowing the page means neither
can be moved or reused, and a route change reaches into the presentational layer.

When it bites, it is usually one of two things, and neither wants an exception:

- **The component needs a route to navigate to.** Take it as a prop, or take a callback. The page
  knows its own routes; the component does not need to.
- **The component needs data the page fetched.** Props, or a hook both can call. A component
  reaching up for context is the same inversion with a longer path.

The architecture review lens checks this on changed files.

## Where tests go

Beside the file, with the suffix — `useBookmarks.test.ts` next to `useBookmarks.ts`. See
`web-testing`: the suffix is what makes keel classify it as a test, and the layer decides what
kind of test is worth writing. A `pages/` test that asserts composition and a `lib/` test that
asserts a pure function are both cheap; a `components/` test that re-asserts what the hook already
proves is not.
