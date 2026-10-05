# Design principles, in TypeScript and React

Load this in `refactor`, or in GREEN when a criterion touches a component or hook that already
exists.

**Read it against the minimum-code rule.** GREEN forbids any component, hook, prop or abstraction
the current test does not drive. That is not opposed to good design — it decides *when* design is
allowed to appear. The `refactor` phase is where it lands, against duplication that is real, with
the tests green and holding behaviour still.

## Where SOLID actually lands in React

**Single responsibility, expressed as: one reason to re-render, one reason to change.** A component
that fetches, decides and renders has three. The usable split is almost always the same one — the
hook owns the behaviour, the component owns the markup — and it is why most `[WEB]` criteria are
best tested at the hook layer.

**Dependency inversion is mostly already done for you.** Props *are* the injected dependency. A
component that takes `onSave` rather than importing the mutation is inverted, testable, and
reusable without a single interface. Reach for context only when the prop would be threaded
through three or more layers that do not otherwise care — and know that context is a re-render
boundary, not just a wiring convenience.

**Open/closed in React is composition.** `children`, a render prop, a slot. A component that grew
a seventh boolean prop to cover variants has failed this; a component that takes a `<Footer>` has
not. The signal is boolean props that are mutually exclusive — `isCompact` and `isExpanded` in the
same signature means there are two components in there.

**Interface segregation is the prop list.** A component taking a whole `user` object to render one
name is coupled to a shape it does not use — and every test for it now has to build a full user.
Take `name: string`.

## TypeScript, where the type system does the design work

- **Make illegal states unrepresentable.** `{ status: 'loading' } | { status: 'ok', data: T } |
  { status: 'error', error: E }` instead of three independent booleans plus an optional payload.
  Four impossible combinations stop existing, and the `behavioral` lens stops finding a screen that
  shows empty during a load.
- **Never widen to `any` to get past a compiler complaint.** `unknown` plus a narrow is the honest
  version; `any` moves the failure to runtime where no test looks.
- **Branded types for identifiers** — `type UserId = string & { readonly __brand: unique symbol }`.
  Same win as Kotlin's `value class`: swapping two ids becomes a compile error.
- **Derive types from the contract, never restate them.** The generated client already exports
  them. A hand-written duplicate is two definitions that will disagree, and `contract-drift` exists
  because they do.
- **`readonly` and `as const`** on anything that should not be mutated — cheaper than a test
  asserting it was not.

## Hooks — the rules that are not style

- **A custom hook per behaviour, not per component.** `useBookmarks` is reusable; `useHomePage` is
  a component with a different file extension.
- **Every dependency array is a correctness claim.** A missing dependency is a stale closure, which
  reads as an intermittent bug and is the hardest class here to reproduce.
- **Derive during render rather than syncing with an effect.** An effect that sets state from props
  is a double render and a source of truth nobody owns. If it can be computed, compute it.
- **Effects are for synchronising with something outside React** — the network, a subscription, the
  DOM. Not for reacting to your own state.

## Patterns worth naming

| Pattern | Use it when | The cost keel will charge |
|---|---|---|
| Container/presentational | the behaviour is worth testing without a DOM | two files; only worth it once the hook has real logic |
| Compound components | a set that shares implicit state (`Tabs`, `Tab`) | context, and a re-render boundary to reason about |
| Render prop / `children` | the caller decides the markup | a generic that needs a real type, not `any` |
| Custom hook | behaviour used twice, or worth testing alone | none — this is the default split, not a pattern |
| HOC | almost never now | hidden props, a broken display name, a worse stack trace |

## The test that settles most arguments

Name the **second** caller before extracting anything. If you cannot, you are designing for a
future nobody asked about, and that is the code GREEN refuses. If you can, the duplication is
already real — so it belongs in `refactor`, not in the middle of making a criterion pass.
