# Forms

## Controlled inputs, one state object

```tsx
const [draft, setDraft] = useState({ url: '', title: '' });

<input
  id="url"
  value={draft.url}
  onChange={(e) => setDraft({ ...draft, url: e.target.value })}
/>
```

Every input has an `id` and a `<label htmlFor>` pointing at it — that is what makes `getByRole('textbox', { name: 'URL' })` work in both Testing Library and Playwright. See `accessibility.md`.

## The backend owns validation; the client mirrors it for speed

The server is the only place a rule is *enforced*. Client-side validation exists so the user finds out sooner, and it must not become a second implementation of the rule.

- **Generated schema where there is one.** The contract already describes required fields, formats and lengths, and the generated zod schema encodes them. Validate the draft against that rather than restating the rule.
- **Hand-written checks only for what the contract cannot express**, and keep them shallow: presence, obvious shape.
- **Never** duplicate a business rule (pricing, quota, permission). Submit and render what the server says.

```tsx
const result = createBookmarkSchema.safeParse(draft);
const fieldErrors = result.success ? {} : result.error.flatten().fieldErrors;
```

## Disable submit for a reason the user can see

```tsx
<button type="submit" disabled={!result.success || create.isPending}>
  {create.isPending ? 'Saving…' : 'Save'}
</button>
```

A permanently disabled button with no visible explanation is worse than an enabled one that fails: show the field error, then disable. `isPending` prevents the double submit — do not also guard with a boolean of your own.

## Map server errors onto fields

A 422 knows which field it rejected. Put the message on that field, not in a banner:

```tsx
const create = useCreateBookmark({
  onError: (error) => {
    const byField = error.response?.data?.errors;   // shape comes from the contract
    if (byField) setServerErrors(byField);
    else setFormError('Could not save the bookmark. Try again.');
  },
});

const errorFor = (name: keyof Draft) => serverErrors[name] ?? fieldErrors[name]?.[0];
```

Keep one lookup that merges client and server errors, so a field renders whichever applies and the component has one place to read from. A 500 has no field: that is the banner case.

## Errors are announced, not just coloured

```tsx
<input id="url" aria-invalid={!!errorFor('url')} aria-describedby="url-error" />
{errorFor('url') && <p id="url-error" role="alert">{errorFor('url')}</p>}
```

`role="alert"` makes a screen reader read the message when it appears, and gives the test a role to query.

## Submit is a form submit

```tsx
<form onSubmit={(e) => { e.preventDefault(); create.mutate(draft); }}>
```

Not an `onClick` on the button. A real `<form>` gives you Enter-to-submit for free, and it is what `getByRole('form')` and a Playwright `press('Enter')` expect.

## After success

Clear the draft or navigate — pick one and make it visible. Leaving a filled form on screen after a successful save reads as a failure. Invalidate the query rather than pushing the new item into a list by hand (`data.md`).
