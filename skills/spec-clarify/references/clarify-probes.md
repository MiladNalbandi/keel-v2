# The identity and work-placement probes (from keel v1's clarify.md; in keel v2 the interview is done with keel-questions buttons)

Adapted from `ai-coding-toolkit:clarify-loop` and bundled here so phase 1 does not depend on that
plugin being installed. Where both are present they agree; this one is scoped to a keel feature,
which already decides some of what that skill has to ask — the stack, the test commands, the gate
mode and the review lenses are in `.keel/config.yml` from init, so do not ask them again.

Run this **before** writing the spec. Its output is what the criteria get written from.

---

## The five questions

If you cannot answer one from the interview, that is the next question to ask — not a gap to fill
in on the user's behalf.

| Question | What it prevents |
|---|---|
| What exactly is being built? | scope creep, and building the wrong thing |
| Where does it live? | an architectural misfit that review catches late |
| What pattern does it follow? | a second way of doing something that already has one |
| What must not break? | regressions nobody thought to test |
| How will we know it is done? | **this one becomes the acceptance criteria** |

---

## The identity probe — ask it before writing any criteria

**Trigger on any of:** `user`, `users`, `auth`, `login`, `register`, `sign in`, `sign up`,
`account`, `member`, `role`, `permission`.

This is the highest-value question in the interview, because the three readings differ by an order
of magnitude in scope and all three are called "users":

| | What it means | Scope |
|---|---|---|
| **A · Named entity** | a table with name and email. Records are linked to it. No session, no token, no password | **small** — a foreign key, a create endpoint, maybe a list |
| **B · Authentication** | people log in, get a token or session, and identity gates what they see | **large** — password storage, token lifecycle, auth middleware, protected routes |
| **C · Authorization** | authentication already exists; this is who may do what — roles, ownership, permission rules | **medium** — no login flow, guards on existing routes |

Ask which, and if they are unsure: *"describe what a user should be able to do that they cannot do
today."* That question separates the three better than the labels do.

Then **write the answer into the spec** — "user means B here" — because the next person to read it
will otherwise assume whichever one their last project used.

**If the answer is B**, stop and say so. Authentication is a design decision with real
alternatives, not a criterion to write: it wants its own spec, and often its own feature.

---

## The work-placement probe — ask it before writing any criteria

**Trigger on any of:** a list, a table, a search box, a filter, a sort, paging, "show all", a
total, a count, a sum, a group-by, an export, autocomplete, a chart, a dashboard, a report — or
any sentence containing *slow*, *takes too long*, *laggy* or *should be instant*.

This is the identity probe's twin, and it is skipped more often because the three readings all
look like the same feature on a screen. They are not. They differ in which layer does the work,
what goes in the contract, and which one stops working as the data grows:

| | Where the work runs | What it costs | What breaks it |
|---|---|---|---|
| **A · In the browser** | the client already has the rows and filters, sorts, pages and totals them in memory | nothing on the server, no contract change, and every interaction is instant because there is no round trip | the result set outgrowing one response. The same code is correct at 200 rows and unusable at 200,000, and nothing in it changes at the boundary |
| **B · On the server** | a query does it — `WHERE`, `ORDER BY`, a page or a keyset cursor, an aggregate, and the index that makes it hold | a contract change, a round trip per interaction, and a decision about caching | an interaction that has to feel instant. Every keystroke becomes a request |
| **C · Both, server authoritative** | the server computes and pages; the client keeps what it has for immediate feedback and reconciles | one rule implemented twice, in two languages, which *will* drift | nobody writing down which copy is the truth |

### Two questions settle it

1. **"How many rows will this hold in two years, for your largest customer?"** A number, not a
   feeling. If nobody knows, that is the answer: it is unbounded.
2. **"Does the user read this once, or touch it on every keystroke?"** Reading tolerates a round
   trip. Typing does not.

Then the reading follows, and it is not a preference:

| If | Reading |
|---|---|
| The whole set fits in a response you would be willing to send anyway — hundreds of rows, not thousands — and it is not growing with anyone's data | **A** |
| The set is unbounded, grows with a customer's data, **or contains a single field this user may not see** | **B**, always |
| The set is server-owned but the interaction must feel immediate | **C**, and the spec names which side is the truth |

**The third row of that table is a security rule, not a performance one.** Filtering in the
browser over rows the server should never have sent is an authorization bug wearing a performance
costume: the data left the building, and the filter is decoration. If the filter decides *who may
see what*, it is reading **B** — there is no version of this where the client is trusted to hide
a row from the person holding it.

Then **write the answer into the spec** — "this is reading B; the client never holds the full set"
— for the same reason as the identity probe: the next person to read it will otherwise assume
whichever one their last project used.

### What each reading turns into, as criteria

| Reading | The criteria it forces |
|---|---|
| **A** | `[WEB]` only. One criterion for the empty result, one for the error, and **one that names the row count it must still work at** — otherwise "fast" is unfalsifiable and the limit is discovered in production |
| **B** | `[API]`: the query parameters as they appear in the contract, a **deterministic** ordering (an unstable sort makes paging skip and repeat rows), the page size and its maximum, what a request past the last page returns, the authorization filter, and the index the query depends on. Plus `[WEB]` for the loading and error states, which now exist |
| **C** | Both of the above, plus one criterion naming the authoritative side and what the client does when the two disagree |

### Optimizing, once the side is chosen

**Do not optimize anything you have not measured.** A performance criterion without a number is
not a criterion; ask for the number, or move it to Out of scope. This is the same rule the
Performance block below states, and it is the one most often skipped in a feature that is only
*partly* about speed.

*If the work is in the browser:* derive, do not copy — a filtered list stored in state is a second
source of truth that goes stale. Debounce what the user types before it does work. Virtualize a
long list rather than paying for rows nobody can see. Memoize the expensive derivation, not every
component. And a re-render is cheap until it is in a loop; measure before restructuring.

*If the work is on the server:* the index is **part of the criterion**, not a follow-up ticket —
a query plan against ten realistic rows proves nothing, so check it against realistic volume.
Look for the N+1 before looking anywhere else; it is the most common cause and the cheapest fix.
Use a keyset cursor rather than `OFFSET` when rows can be inserted while a user pages, or they
will see the same row twice. Add a cache only after a measured number says the query is the
problem, and only with a stated invalidation rule — a cache with no invalidation story is a bug
that takes a week to notice.

*Either way, a number belongs in the acceptance criterion*: "the list renders in under 200ms with
10,000 rows loaded" is testable; "the list is fast" is a mood.

### Failure modes

- **Choosing A because the data is small today.** Ask what it holds in two years. The rewrite is
  not a filter change — it is a contract change, a new endpoint, new tests and new states.
- **Choosing B for something that never grows**, which buys a round trip, a loading state and an
  error state for a list of twelve.
- **Drifting into C without deciding it.** Adding a client-side filter "for speed" on top of a
  server-filtered list is reading C, and it silently becomes two definitions of one rule.
- **Sending the rows and hiding them in the client.** Covered above; it is the one that reaches
  the security review as a finding rather than a preference.

---

## Per task type

A keel feature is usually the first block, but a `/keel:fix` or a performance change borrows from
the others.

**New feature** — what is being built, in one sentence · which module · which existing file it
should mirror · what must be true when it is done, each item listed · what must not change.

**Bug fix** — what is observed versus expected · the smallest reproduction · when it last worked ·
what else touches that path. *Do not ask for a theory of the cause; that is the investigator's job
and an early theory anchors it.*

**Performance** — what is slow, measured, not felt · the number it must reach · the input size it
holds at · what may be traded for it · and **which side the work should run on**, from the
work-placement probe above — a client-side filter and a server-side query are two different
features, not two ways of writing one.

**Refactor** — what shape it should end in · what behaviour must not change (this is the whole
constraint) · what proves that, and whether those tests exist yet.

**Integration** — whose API · what happens when it is down, slow, or returns something undocumented
· where the credentials live · whether a sandbox exists to test against.

---

## Stop and clarify — do not proceed

- You are guessing what the user wants rather than asking
- The work touches more than three modules
- You cannot tell which of two existing patterns to follow
- The requirements contain *later*, *eventually*, or *maybe*
- It changes a public interface, or **a database schema** — which has its own section in phase 1,
  with the sizing questions that decide the migration strategy

Each of these is cheaper to resolve now than in RED, and much cheaper than at ship.

---

## Record what a constraint will cost, next to the constraint

When the user rules something out — a pattern they do not want, a check they say is not needed, an
abstraction they would rather not have — and you can see it will show in the code, **write the cost
in the spec beside the exclusion.** *"No shared error mapper — the 500 branch is then duplicated in
each controller and none of it is reachable by a test."*

Not to argue. They said no and no stays no. But a constraint recorded without its cost becomes
invisible the moment it reaches the code: the implementation looks deliberate, nobody remembers the
trade, and the person who made it never finds out what it bought. Written down once, it is a
decision the reviewer at ship can see; left unwritten, it is an omission nobody can audit.

This is the same rule GREEN applies while writing and `keel:ac-reviewer` applies at the gate. It
belongs here too, because the spec is where the constraint is first stated and the cheapest place
to say what it means.
