# Accessibility

## The test locators and the accessibility tree are the same thing

`web-testing` and `playwright` both locate by **role, accessible name and text**:

```ts
page.getByRole('textbox', { name: 'URL' })
screen.getByRole('button', { name: 'Save' })
```

Those queries read the accessibility tree. So a component a test cannot find is a component a screen reader cannot describe. **If a test needs `data-testid` to reach an element, fix the element.** Adding a test id is a `[WEB]` acceptance-criterion change, never something done during the E2E phase — and it is usually the wrong fix.

This is the cheapest accessibility check available: write the test with a role and a name first, and the markup has to earn it.

## Every input has a label

```tsx
<label htmlFor="url">URL</label>
<input id="url" name="url" type="url" />
```

Placeholder text is not a label — it disappears on focus and is not announced reliably. When a visible label genuinely does not fit, `aria-label` is the fallback, and it becomes the accessible name the test queries.

## Use the element that already has the behaviour

| Need | Element |
|---|---|
| Something that navigates | `<a href>` |
| Something that acts | `<button type="button">` |
| Submits a form | `<button type="submit">` inside `<form>` |
| A list of things | `<ul>` / `<li>` |

A `<div onClick>` has no role, no keyboard handling and no focus. Recreating those with `tabIndex`, `role` and a keydown handler is three bugs waiting; the native element is shorter and correct.

## Name buttons by what they do

`<button aria-label="Remove bookmark">` beats an unlabelled icon button, and it is what the test asks for. An icon with no accessible name is invisible to both a screen reader and `getByRole`.

Where several rows each have a "Remove", include the subject so the name is unambiguous: `Remove https://x.dev`. Tests then locate one row without an index.

## Announce what changes

- **Validation errors**: `role="alert"` on the message, plus `aria-invalid` and `aria-describedby` on the input (`forms.md`).
- **Async results**: a `role="status"` region for "Saved" so it is announced without stealing focus.
- **Nothing to show**: the empty state is real content, not an absence — it needs text a test can assert.

## Focus goes somewhere deliberate

After a dialog opens, focus moves into it; after it closes, back to the control that opened it. After a destructive action removes the focused row, focus moves to a sensible neighbour rather than being lost to `<body>` — where the next Tab starts from the top of the page.

```tsx
const closeRef = useRef<HTMLButtonElement>(null);
useEffect(() => { closeRef.current?.focus(); }, []);
```

This is one of the few legitimate uses of `useEffect`: synchronising with an imperative DOM API.

## Do not remove the focus ring

If the default outline clashes, replace it with a visible one. `outline: none` with nothing in its place makes the app unusable by keyboard, and no test will catch it.
