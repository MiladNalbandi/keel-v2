# Logs and stack traces

Both are big and mostly irrelevant. The skill is extracting the three lines that matter
without pulling thousands into context.

## Application logs

```
docker compose logs --tail 40 api   # the service's last 40 lines
docker compose logs --tail 40 db
docker compose logs --tail 40       # every service
```

Keep the tail short, deliberately: an agent that dumps a full log destroys the context the
flow needs. When 40 lines is genuinely not enough, grep the log for the line you want rather
than widening the tail.

`build/` is **read-blocked**: keel refuses a direct `Read` of build output because it wastes
context. Use `grep` for the line you want instead of reading the file:

```
docker compose logs api | grep -n 'BookmarkController' | head -20
grep -rn -A 5 'Caused by' build/test-results/ | head -40
```

## Reading a stack trace

Read it in this order, and stop as soon as you have a file you own:

1. **The last `Caused by:`**, not the first line. The top of a Spring trace is almost always
   a wrapper — `BeanCreationException`, `UndeclaredThrowableException` — and the real cause
   is at the bottom.
2. **The first frame in your own package.** Everything above it is framework machinery.
   `grep` for the package rather than scrolling.
3. **The message on that frame**, which usually names the value that was wrong.

```
grep -rn 'Caused by' build/test-results/ | head   # or the trimmed output keel gave you
```

keel already does some of this: a failing test run comes back to you as the tail of its
output, not the whole log. Prefer what it printed over re-reading the file.

## What the failure classifier is telling you

keel classifies a red run's failure before accepting it, so the category in its message is
itself a diagnosis:

| It says | Read it as |
|---|---|
| a setup problem, `applicationcontext` or `no qualifying bean` | Wiring, not behaviour. A missing `@MockkBean` or an unimported config. |
| `could not connect to docker` | The dev stack or Testcontainers, not the code. Start the stack (`docker compose up -d`). |
| `unresolved reference`, `compilation error` | The test does not compile; there is nothing to diagnose yet. |
| an assertion failure | A real reproduction. This is the one you want. |

## Correlating a log line with a request

When the symptom is intermittent in a running app, find the request rather than reading
everything:

```
docker compose logs api | grep -n 'POST /bookmarks'
```

Then match on whatever correlation id the app logs. If it logs none, that is a finding worth
raising at the gate — not something to work around by reading more log.

## Traps

- Do not paste a full trace into the conversation. Quote the `Caused by` line and the first
  frame you own.
- A log that shows nothing at the moment of failure usually means the level is too high, not
  that nothing happened.
- Timestamps in logs are the container's zone; a "wrong time" symptom is often just that.
