# The Python api: moving the Kotlin api to FastAPI

Status: phase 0 and 1 done (api-py/, the front door). Progress: `cd api-py && uv run python ../scripts/rewrite-routes.py`.

## Context

keel-v2 (`/Users/mili/Projects/claude/keel-v2`, GitHub `MiladNalbandi/keel-v2`) has three parts in one image:

```
web (React) ──► api  (Kotlin, Spring Boot 3.3, :8080)  ──► engine (Python, FastAPI, :8090)
TUI, IDEs,        148 files, ~19k lines, 257 routes          already FastAPI + uv + pytest
phone, MCP        + 21 plugin jars + Product (~20k lines,
                    ~285 routes) loaded into the same JVM
```

The goal is one backend language: rewrite the Kotlin api in FastAPI, like the engine.

**What you chose:**
- Port the plugins and Product as well, so keel itself has no Kotlin left.
- Move step by step, so every step can ship as a release.
- The new api runs as its own process on :8080, next to the engine on :8090.

## How "step by step" works (the strangler pattern)

The new Python api stands at the front door on :8080. It answers the routes it already has. Every other route it
passes on, as it is, to the old Kotlin api, which now listens only on `127.0.0.1:8081`. Each release moves more routes
to Python. When nothing is passed on any more, we delete Kotlin.

```
                 ┌──────────── :8080  Python api (new) ────────────┐
clients ───────► │ auth, path checks, CSP, static web  (always)    │
                 │ ported routes ──► answered here                 │
                 │ any other route ──► proxy ─────────┐            │
                 └────────────────────────────────────┼────────────┘
                                                      ▼
                                 127.0.0.1:8081  Kotlin api (old, shrinking)
both ──► same /data/keel.db (SQLite, WAL)       both ──► engine :8090 (unchanged)
```

Clients see no change at any step. The proxy keeps the Host, cookies and headers, and adds `X-Forwarded-For`.
Kotlin still runs its own `LocalAuth` behind the proxy, so it is checked twice, which is safe.

## Phases (each phase = one PR + one release, checked with the parity test)

### Phase 0 — Get ready (no behaviour change)
1. Update the local checkout: `git -C …/keel-v2 switch main && git pull --ff-only` (`.keel/` stays untracked).
2. Make a route list: a script `scripts/rewrite-routes.py` that reads Kotlin's `@*Mapping` lines (core + plugins +
   product) and FastAPI's route table, and prints what is ported and what is left. It is our progress bar.
3. Create `api-py/`: a uv project (package `keel_api`, Python 3.12, FastAPI, uvicorn, pydantic 2, httpx, pyyaml,
   cryptography; dev: pytest, pytest-asyncio). Same tools and style as `engine/pyproject.toml`. No dependency on the
   engine (keep the two processes apart, as today).

### Phase 1 — The Python front door
Port first what the front door must own, because it guards every request:
- `auth/LocalAuth.kt` → a raw ASGI middleware. It checks `scope["raw_path"]` **before** Starlette decodes it (odd paths
  → 400), then Host, MCP token, internal token, Bearer (web key or `kd_` device token), cookie + `X-Keel-Web` + Origin,
  else 401 with `X-Keel-Auth: sign-in`. Same order as the Kotlin code.
- `common/ApiPath.kt`, `Hosts.kt`, `auth/AuthLimiter.kt` (+ `ClientIp`), `auth/WebSignIn*.kt` (web key file, cookie
  name, `web_sessions` rows, sign-in codes, `/web/login`), `mcp/McpLevels.kt` (HMAC tokens + level guard for Python's own
  routes), `McpAudit.kt`.
- `common/Errors.kt` → exception handlers that answer `{error, hint?}` with the same texts and status codes.
- `common/PageSecurity.kt` (CSP with the inline-script hashes) and the static web serving + SPA fallback from
  `AppConfig.kt`/`WebConfig`, plus `GET /api/health`.
- The proxy: one catch-all route, added last, that streams the request and answer through `httpx.AsyncClient` (SSE too).
- `docker/keel-start` and `docker/keel-native-start`: start the engine, then Kotlin on `127.0.0.1:8081`, then Python on
  :8080. Wait for both health checks. Exit code 75 from either one still means "restart all". `KEEL_SUPERVISED` is
  passed to both.
- Kotlin still owns the database schema (Flyway) in this phase. Python opens `keel.db` with WAL and
  `busy_timeout=10000`, and starts only after Kotlin is healthy.

### Phase 2 — One event hub that both sides can feed
- Move the SSE hub (`events/EventHub.kt`) to Python. Keep the same rules: a `: connected` comment first, a `: ping`
  every 20 s, the `project` and `notify` filters, and NAMED event names versus `event: engine`.
- Make one small Kotlin change: `EventHub.publish` also sends each event to Python's new `POST /internal/hub`
  (internal token). The Python routes publish to the hub directly.
- `POST /internal/events` (the engine's event ingest) stays in Kotlin for now and is reached through the proxy.

### Phase 3 — Port the core packages, simple ones first
Each package is one PR: the service, its routes, and its Kotlin `*ApiTest` rewritten as a pytest test. Ported routes
win over the proxy by themselves. The Kotlin *services* stay, because Kotlin plugins still call them in the same JVM.
Only their routes become unused.
- **3a. Small and read-mostly:** addons, help, ui, diagrams, hunt, tui, updates, notifications, settings, reviewsetup.
- **3b. Content:** agents, skills, stacks, rules, workflows (+ `library/*.yaml`), knowledge, mcp servers.
- **3c. Accounts:** budget, devices, connections. The AES-GCM secrets must read the rows Kotlin wrote. Local model
  servers, Vertex, GitHub login, and the PTY logins also move: `script` + asyncio subprocess + the same regexes.
- **3d. Projects:** projects, doctor, workspace, repo, jobs, inbox, approvals, quality.
- **3e. The hard ones:** flow (`FlowService`, ~1.7k lines), events ingest + `ContinueAfterRestart` +
  `ThreadStateRefresh`, marketplace + pluginhost (restart plan, exit 75).

**Rule for timed jobs:** when a scheduled job moves (quality tick, release check, plugin requests, McpClients
flush), switch it off in Kotlin in the **same** PR with a `keel.*` flag. Otherwise it runs twice.

### Phase 4 — Python plugins (Kotlin plugins keep working until each one is moved)
- **The plugin SDK:** a module `keel_api.sdk` with the parts plugins really use today: the db, events, projects,
  support/Proc, connections, flow hooks and settings. These are the `FlowContributor`, `ConnectionKind`,
  `InboxSource` and `McpRouteLevels` protocols.
- **A plugin's api part** becomes `plugins/<name>/api/` with `plugin.py` → `register(host)`. That function adds
  routers, contributors and route levels. The SQL `migrations/` stay as they are, with the same
  `<name>_schema_history` tables.
- **The loader:** it reads `/data/plugins/run/resolved.json` (written by the engine) and imports the plugins in
  dependency order. It mounts their routers and serves `/plugins/{name}/{version}/web/**`. A plugin that has no
  `plugin.py` yet stays a Kotlin jar behind the proxy, so we can move one plugin at a time.
- **Order:** plugins that need no other plugin come first. Tasks and Jira come before Product. Each plugin is one PR,
  with its Kotlin tests ported.
- **The plugin package:** in `.kplug`, the api part changes from a jar to a Python folder. Update
  `scripts/build-plugin.sh`, the plugin lint, `plugins-release.yml`, `docs/plugins/*`, and the plugin-repo template's CI
  (Java → uv). `plugins.lock` has no entries yet, so no released plugin breaks.

### Phase 5 — Switch off Kotlin
1. **Database migrations move to Python.** Write a small runner that understands Flyway. It reads and writes
   `flyway_schema_history` and the plugin history tables in the same format and checksum, then applies `V29+`. The SQL
   files move to `api-py/keel_api/migrations/`. Port `MigrationUpgradeTest`: it takes a `keel.db` made by 0.23.x and
   upgrades it.
2. Delete the Kotlin api, the Gradle files and the plugin `api/*.kt`. Rename `api-py/` → `api/`.
3. **Dockerfile:** drop the `api` (gradle) stage and install the api with uv. Keep the JDK in the runtime image,
   because user projects still compile inside it. `keel-start`: no more `java … PropertiesLauncher`.
4. **Native mode:** `scripts/build-native.sh` and `keel2` need Python 3.12 + uv instead of Java 21 and `api.jar`.
5. **CI:** the `api`/`product`/`native-smoke` jobs move to `uv run pytest`. The version check stays: the api, the
   engine and `KEEL2_VERSION` must match.

## Things that must stay exactly the same (existing installs must keep working)
- **Web sign-in:** the `$DATA/run/web-key` file (0600), the cookie name `keel_<8 hex>` or `__Host-…`, the session
  hash in `web_sessions`, and the host:port binding.
- **Saved secrets:** AES-GCM rows, with the key from `sha256(KEEL_SECRET)` or `$DATA/master.key`.
- **Tokens:** device tokens (SHA-256) and the MCP tokens (an HMAC of the internal token for each level).
- **JSON:** snake_case keys, `null`s included, unknown fields ignored. Use one pydantic base model with
  `extra="ignore"`, and never use `exclude_none`.
- **Errors:** the shape `{error, hint?, errors?}` and the fixed texts.
- **Restart:** the `KEEL_SUPERVISED` handshake and exit code 75. The api waits for idle agents before a restart,
  like `RestartPlan.kt`.
- **Safe downloads:** `HttpFetch` resolves the address once, refuses private addresses, connects to the address it
  checked, and still verifies TLS by host name. This needs a custom httpx transport.

## Python design notes
- Keep the code close to the Kotlin code, so a reviewer can compare them side by side. Use the same package names
  under `keel_api/` (auth, common, flow, …).
- **Database:** stdlib `sqlite3` with plain SQL, like Kotlin's `JdbcTemplate`, in plain `def` routes, which FastAPI
  runs in a thread pool. Use `async` only for the proxy, SSE and the engine client.
- **Engine client:** `httpx` with the same timeouts (30 s / 180 s / 20 min / 2 s). It answers 503 `EngineDown` when
  the engine cannot be reached, and 502 when the engine fails with a 5xx.
- **Timed jobs:** asyncio tasks started in the FastAPI lifespan.
- **Tests:** pytest + `TestClient`. The engine, GitHub, Jira and the market are replaced by small local stub servers,
  like `api/src/test/.../support/Stub*.kt`.

## Critical files
- **New:** `api-py/` (`pyproject.toml`, `keel_api/main.py`, `auth/`, `common/`, `proxy.py`, `events/hub.py`, `sdk.py`, `plugins/loader.py`), `scripts/rewrite-routes.py`.
- **Changed:** `docker/keel-start`, `docker/keel-native-start`, `Dockerfile`, `.github/workflows/ci.yml`, `keel2`,
  `scripts/build-native.sh`, `scripts/build-plugin.sh`, `api/.../events/EventHub.kt` (forward to the hub), the
  Kotlin scheduler flags, `docs/CONTRACT.md`, `docs/plugins/*`.
- **Reference while porting:** `api/src/main/kotlin/keel/api/**`, `api/src/test/.../support/RouteLevels.kt`,
  `docs/CONTRACT.md` (the "api" section starts around line 182), `engine/keel_engine/mcp_api.py` (the MCP client
  side).

## Verification (every phase)
1. `cd api-py && uv run pytest -q`. While Kotlin still lives, also `cd api && ./gradlew test pluginTest pluginJars`.
2. Build the image and run the parity test against the last release:
   `python3 e2e/parity/parity.py --a ghcr.io/miladnalbandi/keel-v2:0.23.1 --b keel-v2:dev --only api`, then the full
   run (pages too). Any difference must be fixed or listed with a reason.
3. `cli/test/contract.test.ts`, `docker/tests/keel-start.test.sh`, the `e2e/*/e2e.py` suites and `e2e/daily`
   (Playwright).
4. Security checks. Odd paths (`//`, `%2f`, `..`, `;`) must answer 400. A wrong Host must answer 403. A cookie POST
   without `X-Keel-Web` must be refused. An MCP read token on a write route must be refused.
5. `scripts/rewrite-routes.py` shows the count of ported routes going up, and no route lost.
6. **Phase 5 only:** start the new image on a real 0.23.x `/data` volume. The old web cookie must still sign in. Saved
   secrets must still decrypt. Paired phones must still work. Plugin tables must still be there.

## Risks
- **Size.** About 39k lines of Kotlin, with about 540 routes, move in total, so plan for many releases.
  Phases 3e and 4 are the largest.
- **Two writers on one SQLite file** during the move. WAL and `busy_timeout` handle it, but no schema change may be
  made in Python before Phase 5. Until then, new migrations are added as Flyway `V29+` files that Kotlin runs.
- **Places where Kotlin calls itself.** For example, `McpService` starts the MCP server with an api URL. Those must
  point to :8080, not :8081.

## Phase 1 notes (what was built)
- `api-py/keel_api/`: `auth/` (LocalAuth as a raw ASGI middleware, web key, sessions, sign-in codes, limiter,
  ClientIp), `common/` (path reading, errors, SQLite, web app + CSP), `devices/store.py` (read-only), `mcp/levels.py`
  (same HMAC tokens; guard for Python's own routes), `proxy.py` (everything else to the Kotlin api, streamed).
- Python answers the sign-in routes (`/api/web/*`, `/web/login`) and the web app's files (`KEEL_WEB_DIR`).
- `GET /api/health` is still the Kotlin api's (passed through): its `latest` comes from the update check, which moves
  with the `updates` package. So the supervisor's health check through :8080 also proves the Kotlin api is up.
- `docker/keel-start`: `KEEL_FRONT=auto|1|0`; with the front on, the Kotlin api gets `KEEL_PORT=8081` and
  `SERVER_ADDRESS=127.0.0.1`. `KEEL_FRONT=0` is the old single-api start.
- Checked live: the real Kotlin jar behind the Python front gives the same status and body for every refusal
  (no sign-in, odd paths, wrong keys and tokens, wrong host); a sign-in made by Python works on Kotlin routes; an MCP
  token from Kotlin works in Python; the SSE stream passes through.
