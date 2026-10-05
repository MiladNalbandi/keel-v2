# The lenses

One brief per lens. keel gives each `hunter` **exactly one** of these, its lane, and nothing about the others.

The section that matters in each is **what a provable candidate looks like**. A hunter's output is judged by
whether a prover can reproduce it, so a lens that produces well-written unease is worse than one that produces three
concrete, checkable claims.

Adding a lens is a line in `hunt.lenses` in `.keel/config.yml`, an entry in `hunt.lens_lanes` saying which side of the
tree it reads, and a brief. The flow, the guards and the agents do not change.

**Put a project's own lens in `.keel/lenses/<name>.md`.** keel reads that file first, for every lens, so a project can
override one of these as well as add its own, and it is version-controlled with the code it describes.

---

## exploration

**Looks for:** the gap between what the running system does and what it promised. The only lens
that acts rather than reads: it drives the live stack and records what came back — a status code,
a response shape, a rendered value, a console error, a row count before and after — then holds
that against the promise.

**Look first at:** the contract, to learn what each endpoint is supposed to return. Then the route
inventory, then every input on it: the happy path once, then the same call with an empty value, a
boundary value, a malformed identifier, a value belonging to someone else, and sent twice.

**Load:** the contract, plus the `playwright` skill on the web lane for stable locators and trimmed
output. **Never** a controller, a service or a component.

Two different things are being separated there, and the line matters. **Technique** — how to drive
a browser, how to read a log — tells you nothing about what this code intends, so load it freely.
**Implementation source** does, and that is the whole lens: the contract is a promise written
separately from the code that answers it, so behaviour can disagree with it. Source cannot — a
hunter that reads a controller learns what the code intends and then confirms it does what it
intends, which is true by construction. It also stops looking, and reports what it expected instead
of what it saw.

**Where "expected" comes from** — say which, in every candidate: the contract; what the interface
shows the user (a required marker, a *Delete* label, a *No results* empty state — read from the
rendered page, never the component); or ordinary HTTP (malformed → 400, absent → 404, duplicate →
4xx never 5xx, never a raw stack trace, an action reporting success has happened). No expected
value from any of the three means an observation, not a defect — it belongs in your prose.

**A provable candidate:** a mismatch in three parts — **Expected** (and its source), **Actual**
(observed), **Action** (the exact call or click, and how many times out of how many). *"A request
colliding with data already there returns 5xx with a raw framework body; the contract promises a
4xx. 3/3."* That is an e2e test with its parts named: Expected is the assertion, Action is the
`repro_hint`, Actual is what fails today.

Not a candidate: anything concluded from reading implementation source. Anything run once. Anything
where you cannot say what the right answer was.

**Report clean bills too** — "malformed UUID correctly returns 400" is worth writing down, because
this is the only lens that can establish it and it stops the next hunt re-checking it.

**Where this stops and `behavioral` starts:** both find inert controls. `behavioral` reads the
render path and shows the state is unused; this one clicks the control and watches nothing happen.
Same bug, opposite routes — the dedup pass is built for that. Opening the component to explain what
you saw is running the other lens with worse tooling.

**Hard constraints:** the stack must be up — say so and stop if it is not. Write
nothing into the repository; scripts go to the scratchpad. Run each recipe twice.

---

## messaging

**Off by default** — add it to `hunt.lenses` and `messaging: [api]` to `hunt.lens_lanes` when the
project has a broker. A sibling of `exploration`, separate because four things diverge: you act and
observe in *different places*; "nothing happened" needs an explicit settle bound; a finding is
repeat-N-and-count rather than one pass; and it uses disruption, which nothing else here does.

**Looks for:** what happens to a message that is not the happy one. Consumed twice and applied
twice. Failed and vanished instead of reaching a dead-letter queue. An offset committed before the
work finished, so a restart loses it. Ordering assumed within a partition and broken across them.
One poisoned message stopping the whole partition behind it. Back-pressure that drops rather than
slows.

**Look first at:** the topic or queue inventory. For each: publish the happy message and watch the
effect land, then the same message twice, a malformed payload, one missing a required field, a
burst larger than the consumer's batch — then kill the consumer mid-batch and restart it.

**Load:** the message schema or registry entry, plus the `debugging` skill
(`references/reproduce-flaky.md`) for the repeat-until-it-breaks technique — findings here are
statistical, and that is the method for making one show up on demand. **Never** a consumer or
producer class: technique yes, implementation source no, same line as `exploration` draws.

**Where "expected" comes from:** the message schema; the delivery guarantee the project claims; or
ordinary sense (a message that cannot be processed goes somewhere a human can find it, never
nowhere; work is not acknowledged before it is done). **If nobody has stated the guarantee, that is
your first finding** — under at-least-once a duplicate is expected and *applying it twice* is the
defect, under exactly-once the duplicate itself is. Same observation, opposite verdicts.

**A provable candidate:** Expected / Actual / Action as above, plus two this lens cannot do
without. A **settle bound** — *no DLQ entry after 30s* is a claim, *no DLQ entry* is not. And a
**count, not an outcome** — *"published 100, 3 applied twice"* is reproducible, *"it duplicated"*
leaves a prover nothing to work with.

**Hard constraints:** **a dedicated broker, never a shared environment — stop and say so if you
cannot confirm the broker is yours.** This is the one precondition the HTTP lenses do not need, and
it is not a preference: a committed offset does not roll back when the hunt ends, and neither does
a poisoned DLQ, a reset consumer group or a filled partition. Disruption is in scope here and
nowhere else; say what you disrupted and whether you restored it. At least one consumer must be
running.

---

## observability

**Looks for:** failures the system cannot account for. An exception swallowed into a log nobody
emits. A 500 returned to a caller and recorded nowhere. A log line that names the symptom but not
the input, so it cannot be reproduced from. A request with no correlation id, so its path across
components cannot be reassembled. An error logged below `error`, where no alert will ever see it.
A failure reported to the user as success. A critical path with no metric, so a slow degradation
has nothing to show up in.

**The method, and it is the whole lens:** cause a failure you already understand, then ask what the
system recorded about it. You are the oracle — you know exactly what happened, because you did it.
The question is whether someone who was *not* in the room could reconstruct it from what the system
kept. That is the mismatch: **Expected** is a record sufficient to diagnose the failure without
you; **Actual** is what is actually there.

**Look first at:** the failures the other lenses hand you — every proven candidate in the backlog
is a failure whose logging you can now check for free. Then cause your own: a malformed payload, a
dependency made unreachable, a constraint violated, a timeout. For each, look at stdout, the
container logs, any error reporting, and what the browser console holds on the web lane.

**Load:** the `debugging` skill (`references/logs-and-traces.md`) — the project's container logs and the rest
of getting the three lines that matter out of a log without pulling thousands into context, which
is most of the work here. **Never** a controller, a service, or the logging config.

Technique is fine; the logging setup is not. Reading it tells you what the code intends to record
and you will then confirm it intends it — the same tautology `exploration` avoids. What matters is
what is in the log after a real failure, not what a logger declares.

**A provable candidate:** Expected / Actual / Action, same as `exploration`. *"A POST with a
malformed body returns 500 to the caller; the api's logs over the same window contain
no entry for the request at all. 3/3."* Name the failure you caused, what the record should have
let someone do, and what it actually contains.

Not a candidate: **"there is no log line here"**, said about a path you did not make fail — that
is a checklist, and this lens is worthless as a checklist. An absence is only a finding when you
caused the failure that needed it. Also not a candidate: a preference about log format, a wish for
structured logging, or a missing metric on a path nothing goes wrong on.

**Report clean bills too.** "Every 5xx I caused appeared in the log with its request id within 2s"
is worth as much as a finding — it is what tells the next person the trail can be trusted.

**Where this stops and the others start.** `technical` finds a swallowed exception by reading the
block that swallows it; this one finds it by causing the exception and seeing nothing appear.
`security` cares about what logs *expose* — credentials, PII — while this one cares about what they
*omit*; if you find secrets in a log, that is a `security` finding and you should say so rather
than file it here.

**Hard constraints:** you need log access — say so and stop if you have none, because without it
every finding here would be an assumption. Cause failures only against your own stack, and say in
your prose what you caused, so the next reader can tell your noise from real traffic. Run each
recipe twice: a log that appears once and not again is worse than one that never appears.

---

## security

**Looks for:** what an actor can reach that they should not. Missing or bypassable
authorization, a tenancy boundary that is a filter rather than a rule, client-controlled
ownership, unvalidated input reaching a query or the filesystem, credentials or PII in a
response or a log, secrets in the repo, fixed identifiers that make guessing unnecessary.

**Look first at:** every controller and route, in full — this is the one lens where the
endpoint list is the work list. Then anything under `change.auth_paths` in `.keel/config.yml`, then the migrations
for what is unique and what is nullable.

**Load:** the `security` skill, and follow its routing table — `references/api-kotlin.md` plus
`logic.md` for an endpoint, `data.md` for a migration, `web.md` for frontend source.

**A provable candidate:** names the actor, the request, and what comes back. *An unauthenticated
request to a collection endpoint, sent with no owner or tenant parameter at all, returns every
tenant's rows rather than none — each carrying its owner's identifier.* Provable with one `curl`.

Not a candidate: "authorization could be stronger". Also not a candidate: a CVE with no call
path into it — that is the `dependency-triager`'s job and it needs scanner output, not
prose. keel runs the dependency audit before this lens and gives you its output as `dependency_scan`.

**Note:** absence of authentication is a design decision in some projects, not a finding.
Report what its absence *enables*, concretely, rather than reporting its absence.

---

## behavioral

**Looks for:** the gap between what the interface promises and what it does. A control that
looks live and is inert — a filter that never filters, a sort header bound to nothing, a button
with no handler, a form that reports the wrong error. Error text that leaks internals to a
user. State that survives when it should be cleared, and an action that reports failure after
succeeding.

**Look first at:** the frontend components with the most controls, then the error paths on both
sides of the wire, then anything a double-click could hit twice.

**Load:** the `web-implementation` skill if the project has a frontend.

**A provable candidate:** describes a user action and what is on screen afterwards. *"Typing in
the email filter re-renders the list but never narrows it: the input is bound to state that the
render path does not read."* Provable with Playwright, or by reading the render path and
showing the state is unused.

Not a candidate: a styling preference, a missing feature nobody specified — that one is
`kind: "unspecified"` and goes to the feature flow, not a defect.

**This lens is the one most often missing from a test suite**, which is why it finds things
nothing else does: a unit test asserts the handler, not that the handler is wired to a button.

---

## technical

**Looks for:** correctness in the code paths a test never took. Exceptions raised outside the
block that catches them, transaction boundaries that defer a failure past its handler, an
upsert where an insert was meant, `existsById` followed by `deleteById` with no lock,
read-modify-write with no version, unbounded queries, N+1s on a request path.

**Look first at:** persistence adapters, then anything `@Transactional`, then every `catch`
that returns a typed error — and ask when it can actually fire.

**Load:** the `architecture` skill for where a boundary is supposed to be, and the `debugging` skill
(`references/data-forensics.md`) for reading state without changing it.

**A provable candidate:** names the call and the observable wrong result. *Deleting a row that
another table still references returns 5xx with a raw framework body instead of a conflict: the
constraint violation is raised at commit, after the adapter's catch has already returned.*
Provable with one request.

Not a candidate: "this could be refactored". If you cannot say what a caller observes, it is
not this lens's finding.

---

## concurrency

**Looks for:** what breaks when two callers arrive at once. Read-modify-write with no lock and no
version column. Check-then-act that is not atomic — an existence check then a write, a lookup by
natural key then a `save`. A next value derived in memory from the current maximum and written
under a unique constraint. An upsert that is really a blind `save()` on an assigned id. Shared
mutable state on a request path. A transaction whose isolation level cannot hold the invariant the
code assumes.

**Look first at:** every `@Transactional` method that reads then writes the same row; every unique
constraint in the migrations, then who computes the value that fills it; any counter, sequence or
version number the application maintains itself.

**Load:** the `debugging` skill, `references/reproduce-race.md` — the technique for making a race fail on
demand, and the reason a single sequential request can never show one.

**A provable candidate:** names the two callers and what one of them loses. *Two concurrent writes
to the same endpoint each read the current maximum of a sequence column, each compute the same
next value, and both attempt to write it; the unique constraint rejects the loser, which surfaces
as a 5xx and a lost write.* Provable with eight parallel requests — and only with parallel
requests, which is why this lens exists separately from `technical`.

Not a candidate: "this isn't thread-safe" with no path where two callers meet.

**Note for whoever proves it:** a recipe here must itself be concurrent. `seq 8 | xargs -P8 -I{} curl
…` run twice. A sequential probe that passes proves nothing about this class.

---

## idempotency

**Looks for:** what breaks when the *same* caller arrives twice. A `POST` that creates a second row
on retry. A missing idempotency key where the client may legitimately re-send. A handler on an
at-least-once queue that is not replay-safe. A double-submitted form that produces two of something.
A migration that fails or duplicates when run a second time. A `DELETE` whose second call reports an
error rather than the same success.

**Look first at:** every create endpoint, and ask what a client with a dropped response does next;
every message handler; the frontend controls that fire a mutation with no in-flight guard.

**Load:** the `debugging` skill, `references/reproduce-race.md` for the repeat-until-it-breaks technique.

**A provable candidate:** names the repeat and the divergence. *A creating request sent twice with
an identical body produces two rows; nothing keys on the request itself, so a client retrying a
timed-out call silently doubles it.* Provable by sending the same request twice and counting.

Not a candidate: a naturally idempotent `PUT` you merely dislike. And note that a *correct* 409 on the
second call is not a bug — the finding is a second **effect**, not a second error.

---

## contract-drift

**Looks for:** the three-way disagreement between the contract, the server and the client. An
endpoint the client calls that no controller serves. A controller with no contract entry. A
response shape the contract promises and the code does not return. Generated client code that
has drifted from the spec it was generated from. A documented error code that is unreachable.

**Look first at:** the contract file, then every route registration, then every call site in
the generated client. This lens is close to mechanical — walk all three lists and diff them.

**Load:** nothing. Read the contract.

**A provable candidate:** names both sides. *The generated client calls a nested collection route
that no controller serves; the request 404s and the component renders the error body in place of
the list.* Provable with one request.

This lens finds things fast and cheaply. Run it even on a narrow hunt.

---

## reachability

**Looks for:** whether the plumbing actually works, right now, live — nothing more. Not a wrong
status code, not a missing validation rule, not an edge case: just, does a plain happy-path call
to this frontend route or this backend endpoint come back at all, without a 5xx and without
hanging. This is the cheapest live check there is, and it exists to be that cheap.

**Where this stops and `exploration` starts:** `exploration` is the matrix — happy path, empty,
boundary, malformed, someone else's value, sent twice — each recipe run twice for a 3/3 count.
This lens runs the happy path **once**, on every target, and stops. It trades the edge-case depth
for breadth and speed: cover everything reachable in one pass rather than a handful of endpoints
in real depth. A target this lens fails is worth `exploration` proving properly next; a target it
passes tells `exploration` nothing — passing a 200 on the happy path is the bar `exploration`
starts from, not clears.

**Where this stops and `contract-drift` starts:** `contract-drift` diffs three lists and never
makes a call — it is static and finds a route with no controller before the app is even running.
This lens is live and finds the opposite class: a route that exists on both sides, is correctly
wired, and still fails the moment something actually calls it — a missing bean, an
unhandled exception on startup path, a broken dependency, a frontend page that 500s server-side
on render. Static agreement is not proof it works.

**Look first at:** the contract, for the backend endpoint list; the route or page inventory, for
the frontend. Build one target per entry — method and URL, nothing else — and put them in a
targets file for `reachability-probe.sh` (below). Skip anything that needs a body only a real user
would supply meaningfully (a file upload, a payment token); note it as untested rather than
guessing a body that makes the result meaningless.

**Load:** the contract, plus `references/reachability-probe.sh` — the whole technique is that
script. **Never** a controller, a service, or a component: same line every live lens in this file
draws, for the same reason — source tells you what the code intends, not what it does right now.

**Run it:**

```
bash references/reachability-probe.sh targets.txt 3
```

One line per target, `FAIL` on a 5xx or a timeout. It exits non-zero if anything failed, and it
does not retry — a target that fails once is reported once, and the next lens with a deeper
technique (`exploration`, `observability`) picks it up from there.

**A provable candidate:** names the target and what came back. *"`GET /api/bookmarks` returns 500
on its plain happy-path call, no query parameters, right now — the probe script shows it, and the
same call from the browser network tab confirms it."* Provable by running the script again.

Not a candidate: a non-5xx status you merely find surprising (a 401 you were not expecting is a
`security` or `exploration` finding about *whether* it should be 401, not a reachability finding —
this lens only cares whether something answered). Also not a candidate: a target that needs a body
you had to invent, since the response proves nothing about a call no real client would make.

**Report clean bills too** — a target list that all answered inside its timeout is the fast
"nothing is on fire" signal the rest of the hunt builds on.

**Hard constraints:** the stack must be up — say so and stop if it is not. keel sets no
wall-clock timer on a hunter; the time budget is a property of the target list and the per-call
timeout you choose (N targets × T seconds is the worst case), not something this script enforces
beyond the per-call `--max-time`. Keep the list to what the budget you were given actually allows,
and say in your prose how many targets you covered and how many you left out. Run each target
**once** — this is the one lens where running it twice is the wrong call, because the second pass
buys nothing `exploration`'s proper repeat-and-prove does not already do better.

---

## test-integrity

**Looks for:** tests that pass while production is broken. A mock asserting a contract the real
adapter never produces. A test whose assertions restate the implementation. A `verify` that
only proves a mock was called. A disabled or skipped test. A suite whose branch coverage is
zero, meaning no error path was ever taken. Dead test infrastructure nothing uses.

**Look first at:** the coverage report, lowest branches first, then the tests for the code the
other lenses are flagging — *especially* where a test exists and the bug is real anyway. That
combination is the signal.

**Load:** the `kotlin-spring-testing` or `web-testing` skill, whichever matches the file.

**A provable candidate:** names the test and the production behaviour it fails to catch.
*A use-case test mocks its port to return a typed persistence error and asserts on it — a value
the real adapter never returns, because the constraint violation is raised at commit. The test
passes while the endpoint 5xxs.* Provable by running the real path.

**Why this lens exists:** a passing test for a broken behaviour is worse than no test, because
it reads as coverage and stops anyone looking. It is also the lens that explains why the other
lenses found so much.

---

## data-migration

**Looks for:** what the schema permits that the domain does not. Missing CHECK constraints,
nullable columns that are required in practice, uniqueness that is case-sensitive when the
domain is not, foreign keys with no `ON DELETE` behaviour and no application-side cascade,
missing indexes on a query path, pagination with no stable sort, seed data with fixed
identifiers or a different clock from the application's.

**Look first at:** every migration in order, then the entity mappings against them, then the
queries that sort or page.

**Load:** the `security` skill, `references/data.md` for the exposure angle.

**A provable candidate:** names a row you can insert, or a query that returns the wrong thing.
*An email column is unique case-sensitively, so the same address differing only in case is two
accounts.* Provable with one insert against a disposable database.

Not a candidate: a naming preference, or an index you think might help without a query that
needs it.
