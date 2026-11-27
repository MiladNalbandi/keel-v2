# MCP: fewer Inbox OKs for agents (#250)

Status: plan, round 3 (round 2 fixed who may answer, the flow, one card across MCP processes and rule edits; round 3
fixes the part tools' gate, single use on the api, the kind on every path, and drops "the flow a card was asked in"). Issue: #250. Fixes on the way: #221, #222, #226, #227.

## 1. Why

keel's goal: an agent that connects to keel works better, and several agents work better together. keel does not
manage agents. The person decides what is risky; keel should ask them **few, clear** questions.

Today, over MCP, every call of an "out of band" tool (`engine/keel_engine/mcp_confirm.py` `OUT_OF_BAND`) puts a new card
in keel's Inbox and the agent waits for one click per call:

| Kind of action | Tools today (each call = one card) |
|---|---|
| posted as the person | `keel_review_reply`, `keel_review_resolve`, `keel_review_submit` |
| merge | `keel_review_merge` |
| rules | `keel_rule_edit` (a rule pack off, changed or removed) |
| push / publish | `keel_git_push`, `keel_run_action` `open_pr` / `pr` |
| data | `keel_db_change` |
| delete | `keel_delete_run`, `keel_stack_remove`, `keel_keelbot_undo` |
| stop | `keel_stop_flow`, `keel_stop_flows`, `keel_pause` |
| model | `keel_agent_model` |
| keel's own safety off | `keel_rules_mode` safety-only, `keel_release_check` off |
| remove a project | `keel_remove_project` |

One agent that answers 8 review threads and resolves them needs 16 clicks. Three agents on one project triple that.
The squad's MCP test of 0.40.0 (`docs/test-report-mcp-0.40.0.md`) measured the cost: 3 agents through keel took 7.6
min, 3 plain agents about 25 s. The Inbox OKs were one of the main waits. It also found that many cards are wasted:

- #221 a repeat call (or a wrong token) adds another card, and no card is ever closed;
- #222 keel asks first and only then finds that the call cannot work (no database, unknown chat, branch review, …);
- #226 a "no" from the person reads like "still waiting", and the card does not say which agent asked;
- #227 part tools and gateway tools block the call up to 10 minutes; keel's own tools return a token at once.

Many clicks do not make anything safer either: a person who clicks "Allow" 20 times stops reading the cards.

## 2. What changes (summary)

1. **No wasted cards** (bugs, no change to what needs an OK): #221, #222, #226, #227. Section 3.
2. **One OK can cover more calls**: the card offers *Allow once*, *Allow until flow … ends*, *Allow for 1 hour*; and
   *Allow all N* when several cards of one kind wait. One OK covers every agent in the project. Section 4.
3. **A clear per-project setting**: Settings › MCP › "Agents may, without asking": each kind, what holds now (ask each
   time, allowed until a flow ends, allowed until 15:30, always), and Remove. Section 5.
4. **The safety stays**: only a person gives an OK or changes the setting; two kinds always ask; every call that ran
   under an OK names it in the audit. Section 6.

The default does not change: with no OK given, every kind asks each time, as today.

## 3. No wasted cards (part A)

### 3.1 One card per call while it waits (#221)
- The card's key is `(project, tool, arguments key)`: `Tokens.key` already builds the arguments key, a sha256 of the
  tool and its arguments, the same in every process. `keel2 mcp` sends it with the question (`POST
  /projects/{pid}/plugins/ask {title, command, key}`).
- **The lookup is in the engine**, not in the MCP server. Each agent runs its own `keel2 mcp` process with its own
  `Tokens`, but all of them ask the same engine (`approvals.Approvals`). When a question with the same project and key
  still waits there, `/plugins/ask` returns that question (`reused: true`) and adds the second client to its card
  ("asked by Claude Code and opencode"). No second card. Find-or-open is one step under `Approvals`' lock, so two
  calls at the same moment still make one card.
- Each process gives its own agent its own token, bound to that one question (`tokens.issue(name, key, qid)`, as
  today). **The card is the OK; a token is only the agent's handle for it.**
- **Single use lives on the api's row.** An "Allow once" lets the call run **once**, not once per agent. The api's
  `approvals` row gets `used_at`. `POST /api/plugins/asks/{qid}/take` is one atomic `UPDATE … SET used_at = now
  WHERE id = ? AND decision = allow AND used_at IS NULL` and answers `{taken: true | false}`. `act()` runs the call
  **only after** `take` said true; a plain read (`GET /plugins/asks/{qid}`) never lets a call run. `take` is on the
  MCP WRITE level list (7).
- The second agent with the same call (its `take` says false, `used_at` set) gets: "keel: another agent (Claude
  Code) already ran this same call under the person's OK at 14:02; read its result with keel_status /
  keel_review_threads". It does not run the call twice, and it learns what the other agent did.
- **How long answers last.** An allow that nobody used ends `INBOX_TTL` (10 min) after the answer (status
  `expired`, unused); the next call asks again. A "no" holds for the same 10 minutes: a repeat of that call (same
  key) gets the person's no again ("the person said no at 14:02: <why>"), with no new card; after that it asks again.
- **The card is the OK, the token is the handle.** A call with a token this process does not know (a restarted MCP
  server, or the other agent's token) looks up the question by key: still waiting → a new token for the same card
  ("still waits in keel's Inbox as p_…"); allowed and not used → it runs after `take`; denied → the no; nothing → a
  new card. So an unknown token plus an allowed question runs the call once, because the person allowed that exact
  call. A wrong token never adds a second card.
- **A restarted keel** (engine) has lost its waiting questions (they live in memory). At start the api's
  `ApprovalService.sync()` marks the rows that still wait as `closed` ("keel restarted"); the next call asks again,
  with one new card.
- A card closes (status `closed`, the Inbox no longer shows it) when: the call ran; its time ended (`INBOX_TTL`);
  every MCP server that asked it has ended (`keel2 mcp` closes its waiting questions when it exits, `POST
  /api/plugins/asks/{qid}/close`; best effort, the time ends them anyway); keel restarted.
- **Cards of a stopped flow (#221).** An MCP call carries no flow id, so keel cannot tie a card to a flow, and it does
  not guess. A card of a stopped flow ends with the rules above: at most 10 minutes, or sooner when its agent's MCP
  server ends.

### 3.2 Check before asking (#222)
- A confirm tool may give a `precheck(api, **arguments) -> str | None`: the reason the call cannot work, or None.
  `act()` runs it before it makes a card or a token. A reason goes back to the agent at once ("keel: cannot run …:
  <reason>"), with no card.
- Prechecks for the cases in #222: `keel_db_change` (no connection), `keel_keelbot_keep/undo` (unknown chat),
  `keel_skill_import` (bad host), the market installs (unknown name), `keel_delete_run` (flow can still resume),
  `keel_restore_doc` (no stored version), `keel_review_checkout` (branch review), `keel_pr_create` (on the base branch),
  `keel_ci_rerun` (no GitHub token), `keel_rule_edit` (no rule name), and the review posts (`keel_review_reply` /
  `resolve` on a branch review, unknown thread; `keel_review_merge` on a closed pull request).
- A precheck only reads. If it fails itself (the api is down), keel asks as before (never "allowed" by an error).

### 3.3 One style for every tool (#227)
Part tools and the gateway's write tools ask today inside the tool (`_ask_person`, it blocks up to 10 minutes). For
some of them that inner ask is the **only** gate: their `act()` has no `confirm`, so a plain token would pass. They
become Inbox-only (`OUT_OF_BAND`, `confirm=True`) **before** the inner ask is removed, so the token alone never
does anything:

| Tool | Gate today | After part A | Kind (4.1) |
|---|---|---|---|
| `keel_git_commit` (plugins/git) | inner ask only | `act(confirm=True)`, out-of-band, inner ask removed | `commit` |
| `keel_pr_create` (plugins/git) | inner ask only | the same | `publish` |
| `keel_ci_rerun` (plugins/ci) | inner ask only | the same | `ci` |
| `keel_git_push` (plugins/git) | `act` card **and** inner ask (two cards) | `act` card only | `publish` |
| `keel_db_change` (plugins/db) | `act` card **and** inner ask (two cards) | `act` card only | `data` |
| gateway write tools (`_gateway_ask`) | inner ask only | the same `act()` path, out-of-band | `gateway` |

- They return at once like keel's own tools: "waits in keel's Inbox as p_…; call again with confirm=<token> after
  the person approved". `_ask_person` is removed.
- `keel_db_change`'s card keeps what the inner ask showed: the rows it changes and the SQL (its dry run moves into
  the card's text).
- A client that shows forms (elicitation) keeps its form, as today. A call covered by an OK (part B) skips the form
  too: the person said yes already.

### 3.4 Clear words (#226)
- A "no" from the person: "keel: not done, the person said no in keel's Inbox: <why>" for every tool, the gateway's
  too. Never words that read like "still waiting".
- The card names the client(s) that asked (MCP `clientInfo.name`, as the audit does), the project and the call:
  "Claude Code wants to reply on thread 3 of #42 (project shop)". It names no flow: an MCP call has none (3.1).

## 4. One OK for more calls (part B)

### 4.1 Kinds
The tools are grouped in kinds (one table, `mcp_confirm.KINDS`: tool, or tool + its `op` / `action`, → kind). An
OK is given for a kind, never for "everything".

| Kind | Tools | Scopes the card offers | "Always" in Settings |
|---|---|---|---|
| `review-post` | review reply, resolve, submit | once · until flow ends · 1 hour | yes |
| `review-merge` | review merge | once · until flow ends · 1 hour | yes |
| `commit` | git commit | once · until flow ends · 1 hour | yes |
| `ci` | ci rerun | once · until flow ends · 1 hour | yes |
| `rules-stricter` | rule edit `op=add`, `op=on` (keel gets stricter) | once · until flow ends · 1 hour | no |
| `publish` | git push, pr create, run_action open_pr / pr | once · until flow ends · 1 hour | no |
| `data` | db change (**any** statement) | once · until flow ends · 1 hour | no |
| `model` | agent model | once · until flow ends · 1 hour | no |
| `delete` | delete run, stack remove, KeelBot undo | **once only** (and *Allow all N*) | no |
| `stop` | stop flow(s), pause | **once only** (and *Allow all N*) | no |
| `gateway` | a connected MCP server's write tools | **once only** (and *Allow all N*) | no |
| `safety-off` | rules mode safety-only, release check off, **rule edit `op=change`, `remove`, `off`** | **once only** | **no** |
| `remove-project` | remove project | **once only** | **no** |

- A scoped OK covers **any arguments** of its kind: "data for 1 hour" allows every statement, a `DROP` too. The
  card's scope buttons say so ("any database change in shop for 1 hour").
- `delete` and `stop` are once only: a project-wide hour would reach other agents' flows and runs
  (`keel_stop_flows` all). `gateway` stays once until its own plan (11).
- Why rule edits are split: switching a rule pack off, removing it or changing its YAML has the same effect as
  keel's safety off, and the change lasts after the OK ends. So those always ask, like `keel_rules_mode` safety-only.
  Only edits that make keel stricter can be covered: a pack only refuses, and `op=add` only makes a new pack (an id
  that exists is a 409).

**The kind is always the api's.** The card's approval row stores the call's `tool` and its `op` / `action` (not a
kind). The api works out the kind from its own table (a twin of the engine's `KINDS`, pinned by a test, as
`ACTION_LEVELS` is) on **every** path: a scoped decide, "also allow the other waiting cards", *Allow all N*, and
`grants/use`. A `kind` in a request body is ignored.

A tool in `OUT_OF_BAND` without a kind is a bug: a test checks that every one has a kind. A new tool joins a kind or
gets its own; a kind that is not in the table, and a tool + `op` that is not in it, is "once only".

### 4.2 The card
An MCP confirmation card in the Inbox (web, TUI, KeelBot's panel) gets, next to *Deny*:
- **Allow once**: as today, this call only.
- **Allow until flow "Fix the cart" ends**: this kind, in this project, for every agent, until that flow ends or is
  stopped (and at most 8 hours).
- **Allow for 1 hour**: this kind, in this project, for every agent, for 60 minutes.
- **Allow all N** (when N ≥ 2 cards of the same kind wait in the project): allows each of them once. One call,
  `POST /api/approvals/decide-many {ids, decision: "once"}`: every id must be a waiting MCP card of the same project
  and the same kind (worked out by the api), else 400 and nothing is answered. It answers the cards the person saw
  (the ids), not "whatever waits now".
- An MCP card has no *Always* button; `decide` with `always` on an MCP card is a 400. A lasting OK is given only in
  Settings (5), after its second click.

**Where the flow comes from.** An MCP call carries no flow id, and keel never takes one from the agent's arguments
(an agent could name any flow). The flow of the button is chosen by keel when it shows the card: the project's running
flows from the api (`GET /api/projects/{pid}/flows`, status running or waiting). One button per running flow (at most
3); no running flow, no button. The OK covers **every call of that kind in the project**, inside the flow or not, and
ends when the api sees that flow end; the button's words say so ("for every agent on this project, until …"). It is a
time limit tied to the work, not a promise that only the flow's calls are covered: keel cannot prove where a call
came from, so it does not pretend (4.4).

"Until flow ends" and "1 hour" also allow, at once, the other cards of that kind that wait in the project. The second
click lists them ("this also allows: reply on thread 4 (opencode), resolve thread 5 (Claude Code)"). Each of those
cards then shows "allowed by your OK for review posts (until Fix the cart ends)".

**Who sees which buttons.** The web Inbox, KeelBot's panel in the web app and keel TUI show all four. The phone app
(a paired device) shows *Allow once* and *Deny*, as today: it may not give a wider OK (6).

### 4.3 What an agent sees
- A call covered by an OK runs at once; the answer ends with one line: "(ran under the person's OK g_… for review
  posts, until Fix the cart ends)". No token, no card, no form.
- A call that is not covered: as today (token, card), with the words of 3.3 and 3.4.
- `keel_status` and `keel_next` list the OKs that hold for the project ("review posts allowed until Fix the cart ends"), so an
  agent can plan without trying first. Several agents read the same list.

### 4.4 Multi-agent
An OK belongs to the project, not to one client: a person who allows review posts until a flow ends allows them for
every agent on the project. This is on purpose: the client name over MCP is not a proof of who the agent is, so
keel does not pretend it can hold an OK for one agent only. The card and the audit still name the client of each call.

## 5. The per-project setting (part B)

Settings › MCP (per project) gets a section **"Agents may, without asking"**:

```
 Agents may, without asking                               (for this project)
 Review posts      Allowed until "Fix the cart" ends · 6 calls · last 14:02   [Remove]
 Merge             Ask each time                                              [Always]
 Stricter rules    Allowed until 15:30               · 1 call                 [Remove]
 Push / publish    Ask each time
 …
 Keel's safety off Ask each time (always asks)
 Remove project    Ask each time (always asks)
```
- Each row shows what holds now and how often it was used. "6 calls" opens the list of the calls that ran under the
  OK (tool, client, time, from `McpAudit`), so the person sees what was done without being asked each time.
  *Remove* ends the OK at once. *Always* gives a lasting OK for the kind (only for the kinds with "yes" in 4.1),
  after a second click that says what it allows.
- The same list over the api and in keel TUI's Settings, read only over MCP (`keel_status`).

## 6. Safety

- **Only a person answers, and only a person gives a wider OK.** An MCP confirmation card is answered on
  `POST /api/approvals/{id}/decide` (`InboxService.kt`, `ApprovalController.decide`). The check sits in the
  controller, as an **allow-list** on the caller (`LocalAuth.WHO_ATTR`):

  | Caller (`WHO_ATTR`) | decide, once / deny | decide with a `scope` | `POST` / `DELETE /mcp/grants` |
  |---|---|---|---|
  | `browser` (web sign-in, KeelBot's panel in the web app) | yes | yes | yes |
  | `person` (the web key, keel TUI) | yes | yes | yes |
  | `device` (the paired phone app) | yes | 403 | 403 |
  | `internal` (the engine, KeelBot's tools, `keel2 mcp`'s internal token) | 403 | 403 | 403 |
  | `mcp`, every level, admin too | 403 | 403 | 403 |

  `decide-many` (4.2) follows the "with a scope" column. There is no MCP tool for any of it; `keel_answer` cannot answer an MCP confirmation card, with or without a
  scope. The audit writes the caller kind that answered.
- **Two kinds always ask, one card at a time**: `safety-off` and `remove-project`. The api refuses an OK for them (its
  own table, not only the web's buttons), `decide-many` refuses them, and `grants/use` never covers them. `delete`,
  `stop` and `gateway` get no scoped OK either (4.1).
- **The api decides the kind** on every path (4.1). A kind in a request is ignored.
- **The flow is checked when the OK is used**, not only when it is given: `grants/use` asks the api's flow state,
  and a flow OK whose flow has ended is ended then (`ended_why: flow ended`).
- **Narrow**: an OK is for one kind in one project. A flow OK ends when the api sees the flow end or stop, and after
  8 hours at most; an hour OK ends after 60 minutes; "always" ends when removed. Restarting keel keeps "always" and
  hour OKs, and ends flow OKs whose flow is not running.
- **Audit**: the api writes the `McpAudit` row of a covered call itself, in `grants/use` (tool, client, the OK's id),
  so no header from the MCP server is trusted for it; the OK counts its uses.
- **Default**: no OK, every kind asks each time.
- **Not changed**: the MCP levels (read / write / admin), the gateway's read-only tools, `KEEL_MCP_CONFIRM=token`,
  `--yes`.

## 7. Where it lives

- **api** (owner of the data): table `mcp_grants (id, project_id, kind, scope [flow | until | always], flow_id, until,
  created_at, created_by, uses, last_used_at, ended_at, ended_why)`. Routes:
  `GET /api/projects/{pid}/mcp/grants` (also read by `keel_status`), `POST /api/projects/{pid}/mcp/grants {kind,
  scope, flow_id?}` and `DELETE /api/projects/{pid}/mcp/grants/{id}` (person only, table in 6),
  `POST /api/projects/{pid}/mcp/grants/use {tool, op?, client}` → `{allowed, grant_id?}` (the api works out the kind,
  checks the flow, counts a use, writes the audit row),
  `GET /api/projects/{pid}/mcp/grants/{id}/calls` (the calls that ran under it, from `McpAudit`).
  `POST /api/approvals/{id}/decide` takes an optional `scope` (`flow:<id>` | `hour`) for MCP cards; the flow must be
  running in the card's project. `POST /api/approvals/decide-many` (4.2).
  Approvals rows (part A): `used_at`, status `closed`, the card's clients, the call's `tool` + `op` / `action` and
  `key`. `POST /api/plugins/asks/{qid}/take` and `/close` (3.1).
- **MCP level lists** (`McpLevels`): READ gets `GET …/mcp/grants`; WRITE gets `…/mcp/grants/use`,
  `/plugins/asks/{qid}/take`, `/plugins/asks/{qid}/close`. `POST` / `DELETE …/mcp/grants`, `decide` and
  `decide-many` are on no MCP list; their controllers check the caller (6).
- **engine**: `mcp_confirm.KINDS`; `/plugins/ask` takes `key`, `tool`, `op` and reuses a waiting question (locked
  find-or-open). `act()` order: precheck → `grants/use` → (covered: run) or (token + one card → after the person's
  allow, `take` → run). The part tools of 3.3 move to `confirm=True`; `_ask_person` goes.
- **web**: the Inbox card's buttons (4.2), Settings › MCP section (5). **keel TUI**: the same buttons on the card
  and the same list (read and Remove).

## 8. Tests

- engine: every `OUT_OF_BAND` tool (and every `op` of `keel_rule_edit`, every `action` of `keel_run_action`) has a
  kind; `op=off/remove/change` is `safety-off`; **for each part tool of 3.3 a token alone does nothing** (no allow on
  its card → not run); `keel_git_push` and `keel_db_change` make one card, not two; a repeat call reuses the card;
  **two `Tokens` (two MCP servers) with the same call at the same moment get one card**, the first `take` runs it and
  the second is told it ran; a restarted MCP server (new `Tokens`) finds the waiting card by key; a repeat of a
  denied call gets the no without a card; a card closes after the call ran, when its time ends, when its MCP
  servers end and after keel restarts; a precheck's reason gives no card; a failing precheck still asks; a part
  tool returns a token at once; the "no" words; a covered call runs without a card or form and names the OK;
  `safety-off` is never covered; an unknown kind is once only.
- api: the table in 6, **one test per caller** on decide (with and without a scope) and on the grants routes:
  `mcp` read / write / admin tokens, `internal` token, **an MCP token sent with the internal header too**, and
  `device` get the 403s of the table, `browser` and `person` pass; refused for `safety-off` / `remove-project`; the
  kind is worked out by the api on every path (a twin test with the engine's `KINDS`; **a forged `kind` in the body
  of decide, decide-many and grants/use changes nothing**); `take` is true once, also when two callers race, and a
  plain read never counts as taken; an unused allow ends after 10 minutes; `decide-many` with cards of two kinds is
  a 400; `always` on an MCP card is a 400; after an engine restart the waiting rows are closed and an old token
  runs nothing; a flow OK covers a call from outside the flow too and
  ends when the flow ends (checked when used), whatever the calls say; a flow OK for a flow that is not running is refused; an hour OK
  ends after 60 minutes and a flow OK after 8 hours (clock moved in the test); after a restart flow OKs end and hour
  and always OKs stay; uses are counted and listed; Remove ends it.
- web: the card's buttons and when each shows (one per running flow, none without; only once / deny on a device);
  "Allow until flow ends" answers the other waiting cards and the second click lists them; "Allow all N" with cards
  from two clients; the Settings rows, the calls list, *Always* with its second click, *Remove*.
- e2e (daily journey J28): two MCP clients (two `keel2 mcp` processes) on one flow each reply to 3 review threads.
  Today: 6 cards. After one "Allow until flow ends": 1 card, 6 replies posted, the audit names the OK 6 times. Then
  *Remove*, and the next call asks again. Both clients make the same resolve call: 1 card, it runs once.

## 9. How we see it worked

The same scripted run as the MCP test (3 agents, review replies, resolves and one submit on one flow): Inbox cards
and wall time before and after. Target: cards from about 1 per call to 1 per kind per flow, and no card for a call
that cannot work.

## 10. Order of the work

- **First, on its own**: the caller check on `decide` (the table in 6, once / deny columns) and its tests.
- **Part A** (bugs #221, #222, #226, #227): engine (`act()`, `approvals`, `/plugins/ask` key reuse, prechecks, the
  part tools of 3.3 to `confirm=True` before `_ask_person` goes), plugins git / ci / db, the api's approvals rows
  (`used_at`, `take`, `close`, status `closed`, the clients, tool + op) and the web card's words. One PR. No new
  setting. Safe alone: no tool loses its Inbox gate (3.3 table, tested).
- **Part B** (kinds, scopes, OKs, decide-many, card buttons, Settings, audit, J28): api + engine + web + TUI, one PR
  after A (the same `act()` code).

## 11. Not in this plan

- OKs for one agent only (4.4).
- Taking a tool out of `OUT_OF_BAND`. (Part A only moves tools that already ask the person into it, 3.3.)
- The gateway's own per-server rules (a connected MCP server's tools): they keep "once" until a later plan.
- The phone app's scope buttons (it keeps once / deny).
- A process that runs as the same user and reads keel's keys from disk, or an agent that drives the person's
  signed-in browser: keel's OKs keep the model's tool calls away from the person's answer; they do not guard against
  a program with the person's own file access or browser session.
