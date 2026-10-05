# Frontend security — TypeScript + React

## The client can hide, never enforce

Every check in the browser is advice. Anything the UI refuses must also be refused by the
API, and the API's refusal is what needs the test.

```tsx
// Fine as UX. Not a security control.
{user.isAdmin && <DeleteButton id={bookmark.id} />}
```

A finding here is worth writing **only** when the server-side check is missing: "the delete
button is hidden for non-admins and `DELETE /bookmarks/{id}` has no authorization" is
blocking. "The button is only hidden" on its own is not.

Check for: a new route guarded only by a client-side redirect, and a form that validates a
business rule the server does not.

## XSS

`dangerouslySetInnerHTML` is the only common way to inject in React, and it is always worth
a finding unless the value is sanitised on the way in.

```tsx
// Blocking when `note` comes from a user or an API response.
<div dangerouslySetInnerHTML={{ __html: bookmark.note }} />

// Either render as text, or sanitise explicitly at the boundary.
<div>{bookmark.note}</div>
```

Also injectable:

- `href={userValue}` — a `javascript:` URL executes. Validate the scheme.
- `<script src={...}>` or any dynamic `src`/`srcdoc`.
- Passing user text into a markdown renderer configured to allow raw HTML.
- `new Function(...)` or `eval` on anything derived from a response.

## Token storage

| Where | Consequence |
|---|---|
| `localStorage` / `sessionStorage` | Readable by any script on the origin: one XSS is full account takeover |
| A non-`httpOnly` cookie | Same |
| An `httpOnly`, `Secure`, `SameSite` cookie | Not readable by script; needs CSRF handling |
| In memory only | Safest; lost on reload, so it needs a refresh flow |

A change that moves a token *into* web storage is blocking. A token already there is a
non-blocking note unless this diff introduced the XSS to go with it.

Never log a token, and never put one in a URL — it lands in history, referrers and server
logs.

## CSRF

Cookie auth needs CSRF protection; bearer-token-in-header auth does not, because a
cross-site form cannot set the header.

Check for: a change from header auth to cookie auth with no CSRF token, a state-changing
`GET`, or `SameSite=None` without a reason.

## Data exposure in the client

The frontend usually over-fetches. Look for a response type that carries fields the screen
never renders — an email, an internal id, a password hash — because the fix belongs in the
API contract, not the component.

```ts
// If the list only shows url and title, the API should not return ownerEmail.
const rows = BookmarkListSchema.parse(await res.json())
```

Parsing responses with zod helps here: an unexpected field is visible at the boundary
rather than flowing invisibly into the store.

## Quick pass

- Any new `dangerouslySetInnerHTML`, dynamic `href`, or raw-HTML markdown option?
- Did a token move into `localStorage`, a URL, or a log line?
- Does a new screen rely on a client check the API does not also make?
- Does a response type carry a field the UI never shows?
