# MCP: fewer Inbox OKs for agents (#250)

Status: plan, round 2 (after the goldfish review: who may answer, where the flow comes from, one card across MCP
processes, rule edits). Issue: #250. Fixes on the way: #221, #222, #226, #227.

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
  ("asked by Claude Code and opencode"). No second card.
- Each process gives its own agent its own token, bound to that one question (`tokens.issue(name, key, qid)`, as
  today). **The card is the OK; a token is only the agent's handle for it.**
- An "Allow once" lets the call run **once**, not once per agent. The engine keeps the answer until the question's
  time ends (it no longer forgets it after the first read: `asked()` keeps it, `POST /plugins/asks/{qid}/take` uses
  it up, first caller wins). The second agent with the same call gets: "keel: another agent (Claude Code) already
  ran this same call under the person's OK at 14:02; read its result with keel_status / keel_review_threads". It does
  not run the call twice, and it learns what the other agent did.
- **A restarted MCP server** has lost its tokens. A call with a token it does not know looks up the question by key
  in the engine: still waiting → a new token for the same card ("still waits in keel's Inbox as p_…"); allowed and
  not used yet → it runs and uses it up; nothing → a new card, as today. A wrong token never adds a second card.
- **A restarted keel** (engine) has lost its waiting questions (they live in memory). At start, keel marks the api's
  rows that still wait as `closed` ("keel restarted"); the next call asks again, with one new card.
- A card closes (the engine's approval ends with status `closed`, the Inbox no longer shows it) when: the call ran;
  its time ended (`INBOX_TTL`); the flow it was asked in ended or was stopped; keel restarted.

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
- Part tools (`keel_git_commit`, `keel_pr_create`, `keel_ci_rerun`, …) and gateway write tools no longer block up to
  10 minutes in `_ask_person`. They return at once like keel's own tools: "waits in keel's Inbox as p_…; call again
  with confirm=<token> after the person approved". `_ask_person` is removed.
- A client that shows forms (elicitation) keeps its form, as today. A call covered by an OK (part B) skips the form
  too: the person said yes already.

### 3.4 Clear words (#226)
- A "no" from the person: "keel: not done, the person said no in keel's Inbox: <why>" for every tool, the gateway's
  too. Never words that read like "still waiting".
- The card names the client that asked (MCP `clientInfo.name`, as the audit does), the flow when there is one, and the
  call: "Claude Code (flow f_12, Fix the cart) wants to reply on thread 3 of #42".

## 4. One OK for more calls (part B)

### 4.1 Kinds
The tools are grouped in kinds (one table, `mcp_confirm.KINDS`: tool, or tool + its `op` / `action`, → kind). An
OK is given for a kind, never for "everything".

| Kind | Tools | Scopes the card offers | "Always" in Settings |
|---|---|---|---|
| `review-post` | review reply, resolve, submit | once · until flow ends · 1 hour | yes |
| `review-merge` | review merge | once · until flow ends · 1 hour | yes |
| `rules-stricter` | rule edit `op=add`, `op=on` (keel gets stricter) | once · until flow ends · 1 hour | no |
| `publish` | git push, run_action open_pr / pr | once · until flow ends · 1 hour | yes |
| `data` | db change | once · until flow ends · 1 hour | yes |
| `delete` | delete run, stack remove, KeelBot undo | once · until flow ends · 1 hour | no |
| `stop` | stop flow(s), pause | once · until flow ends · 1 hour | yes |
| `model` | agent model | once · until flow ends · 1 hour | no |
| `safety-off` | rules mode safety-only, release check off, **rule edit `op=change`, `remove`, `off`** | **once only** | **no** |
| `remove-project` | remove project | **once only** | **no** |

Why rule edits are split: switching a rule pack off, removing it or changing its YAML has the same effect as
keel's safety off, and the change lasts after the OK ends. So those always ask, like `keel_rules_mode` safety-only.
Only edits that make keel stricter can be covered. (The builder checks that a pack cannot loosen another pack; if it
can, `op=add` moves to `safety-off` too.)

A tool in `OUT_OF_BAND` without a kind is a bug: a test checks that every one has a kind. A new tool joins a kind or
gets its own; a kind that is not in the table, and a tool + `op` that is not in it, is "once only".

### 4.2 The card
An MCP confirmation card in the Inbox (web, TUI, KeelBot's panel) gets, next to *Deny*:
- **Allow once**: as today, this call only.
- **Allow until flow "Fix the cart" ends**: this kind, in this project, for every agent, until that flow ends or is
  stopped (and at most 8 hours).
- **Allow for 1 hour**: this kind, in this project, for every agent, for 60 minutes.
- **Allow all N** (when N ≥ 2 cards of the same kind wait in the project): allows each of them once.

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
every agent working on the flow. This is on purpose: the client name over MCP is not a proof of who the agent is, so
keel does not pretend it can hold an OK for one agent only. The card and the audit still name the client of each call.

## 5. The per-project setting (part B)

Settings › MCP (per project) gets a section **"Agents may, without asking"**:

```
 Agents may, without asking                               (for this project)
 Review posts      Allowed until "Fix the cart" ends · 6 calls · last 14:02   [Remove]
 Merge             Ask each time                                              [Always]
 Stricter rules    Allowed until 15:30               · 1 call                 [Remove]
 Push / publish    Ask each time                                          [Always]
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
  controller, as an **allow-list** on the caller (`LocalAuth.WHO_ATTR`), not as "refuse the MCP token" (the MCP level
  guard treats a route that no list names as admin, so it alone does not keep an admin MCP token out):

  | Caller (`WHO_ATTR`) | decide, once / deny | decide with a `scope` | `POST` / `DELETE /mcp/grants` |
  |---|---|---|---|
  | `browser` (web sign-in, KeelBot's panel in the web app) | yes | yes | yes |
  | `person` (the web key, keel TUI) | yes | yes | yes |
  | `device` (the paired phone app) | yes | 403 | 403 |
  | `internal` (the engine, KeelBot's tools, `keel2 mcp`'s internal token) | 403 | 403 | 403 |
  | `mcp`, every level, admin too | 403 | 403 | 403 |

  There is no MCP tool for any of it; `keel_answer` cannot answer an MCP confirmation card, with or without a
  scope. The audit writes the caller kind that answered.
- **Two kinds always ask**: `safety-off` and `remove-project`. The api refuses an OK for them (its own table, not
  only the web's buttons), and `grants/use` never covers them.
- **The api decides the kind.** `grants/use` gets the tool and its `op` / `action` and works out the kind from its
  own table (a twin of the engine's `KINDS`, pinned by a test, as `ACTION_LEVELS` is). A kind the engine sends is
  not trusted.
- **Narrow**: an OK is for one kind in one project. A flow OK ends when the api sees the flow end or stop, and after
  8 hours at most; an hour OK ends after 60 minutes; "always" ends when removed. Restarting keel keeps "always" and
  hour OKs, and ends flow OKs whose flow is not running.
- **Audit**: every call that ran under an OK is written with the OK's id (`McpAudit`, as today with via/client/tool),
  and the OK counts its uses.
- **Default**: no OK, every kind asks each time.
- **Not changed**: the MCP levels (read / write / admin), the gateway's read-only tools, `KEEL_MCP_CONFIRM=token`,
  `--yes`.

## 7. Where it lives

- **api** (owner of the data): table `mcp_grants (id, project_id, kind, scope [flow | until | always], flow_id, until,
  created_at, created_by, uses, last_used_at, ended_at, ended_why)`. Routes:
  `GET /api/projects/{pid}/mcp/grants` (also read by `keel_status`), `POST /api/projects/{pid}/mcp/grants {kind,
  scope, flow_id?}` and `DELETE /api/projects/{pid}/mcp/grants/{id}` (person only, table in 6),
  `POST /api/projects/{pid}/mcp/grants/use {tool, op?, client}` → `{allowed, grant_id?}` (the api works out the kind;
  counts a use; on the MCP WRITE level list, so `keel2 mcp --write` may call it),
  `GET /api/projects/{pid}/mcp/grants/{id}/calls` (the calls that ran under it, from `McpAudit`).
  `POST /api/approvals/{id}/decide` takes an optional `scope` (`flow:<id>` | `hour`) for MCP cards; the flow must be
  running in the card's project. The api's approvals rows get status `closed` (part A) and the card's client list.
- **engine**: `mcp_confirm.KINDS`; `/plugins/ask` takes `key` and reuses a waiting question; `asked()` keeps an
  answer until its time ends, `/plugins/asks/{qid}/take` uses it up. `act()` order: precheck → `grants/use` →
  (covered: run) or (token + one card). The card's approval carries `mcp: {tool, kind, clients, key}` so the Inbox
  can show the scopes and close it.
- **web**: the Inbox card's buttons (4.2), Settings › MCP section (5). **keel TUI**: the same buttons on the card
  and the same list (read and Remove).

## 8. Tests

- engine: every `OUT_OF_BAND` tool (and every `op` of `keel_rule_edit`) has a kind; `op=off/remove/change` is
  `safety-off`; a repeat call reuses the card; **two `Tokens` (two MCP servers) with the same call get one card**, the
  first to run uses the OK and the second is told it ran; a restarted MCP server (new `Tokens`) finds the waiting
  card by key; a card closes after the call ran, when its time ends, when its flow ends and after keel restarts; a
  precheck's reason gives no card; a failing precheck still asks; a part tool returns a token at once; the "no"
  words; a covered call runs without a card or form and names the OK; `safety-off` is never covered; an unknown
  kind is once only.
- api: the table in 6, **one test per caller** on decide (with and without a scope) and on the grants routes:
  `mcp` read / write / admin tokens, `internal` token and `device` get the 403s of the table, `browser` and
  `person` pass; refused for `safety-off` / `remove-project`; `grants/use` works out the kind itself (a twin test
  with the engine's `KINDS`) and ignores a kind in the body; a flow OK covers a call from outside the flow too and
  ends when the flow ends, whatever the calls say; a flow OK for a flow that is not running is refused; an hour OK
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

- **Part A** (bugs #221, #222, #226, #227): mostly engine (`act()`, `approvals`, `/plugins/ask` key reuse, take,
  prechecks), plus the api's approvals rows (status `closed`, the clients) and the web card's words. One PR. Small
  risk, no new setting.
- **Part B** (kinds, who may answer, OKs, card buttons, Settings, audit, J28): api + engine + web + TUI, one PR
  after A (the same `act()` code). The person-only check on decide (6) is the first commit of B.

## 11. Not in this plan

- OKs for one agent only (4.4).
- Changing which tools need an OK at all (`OUT_OF_BAND`).
- The gateway's own per-server rules (a connected MCP server's tools): they keep "once" until a later plan.
- The phone app's scope buttons (it keeps once / deny).
- A process that runs as the same user and reads keel's keys from disk: keel's OKs keep the model's tool calls away
  from the person's answer; they do not guard against a program with the person's own file access.
