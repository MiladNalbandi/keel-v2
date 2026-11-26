# MCP: fewer Inbox OKs for agents (#250)

Status: plan, for review. Issue: #250. Fixes on the way: #221, #222, #226, #227.

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
2. **One OK can cover more calls**: the card offers *Allow once*, *Allow for this flow*, *Allow for 1 hour*; and *Allow
   all N* when several cards of one kind wait. One OK covers every agent in the project. Section 4.
3. **A clear per-project setting**: Settings › MCP › "Agents may, without asking": each kind, what holds now (ask each
   time, allowed for this flow, allowed until 15:30, always), and Remove. Section 5.
4. **The safety stays**: only a person gives an OK or changes the setting; two kinds always ask; every call that ran
   under an OK names it in the audit. Section 6.

The default does not change: with no OK given, every kind asks each time, as today.

## 3. No wasted cards (part A)

### 3.1 One card per call while it waits (#221)
- The card's key is `(project, tool, arguments key)` (the key `Tokens.key` already builds). A call whose key has a
  waiting card reuses that card and its token; it does not add one.
- A card closes (the engine's approval ends with `outcome: "closed"`, the Inbox no longer shows it) when:
  the call ran with its token; its token ended (`INBOX_TTL`); the flow it was asked in ended or was stopped.
- A wrong or old token does not add a card: the answer says the token is not valid and names the waiting card, if any.

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
- A client that shows forms (elicitation) keeps its form, as today.

### 3.4 Clear words (#226)
- A "no" from the person: "keel: not done, the person said no in keel's Inbox: <why>" for every tool, the gateway's
  too. Never words that read like "still waiting".
- The card names the client that asked (MCP `clientInfo.name`, as the audit does), the flow when there is one, and the
  call: "Claude Code (flow f_12, Fix the cart) wants to reply on thread 3 of #42".

## 4. One OK for more calls (part B)

### 4.1 Kinds
The tools are grouped in kinds (one table, `mcp_confirm.KINDS`, tool → kind). An OK is given for a kind, never for
"everything".

| Kind | Tools | Scopes the card offers | "Always" in Settings |
|---|---|---|---|
| `review-post` | review reply, resolve, submit | once · this flow · 1 hour | yes |
| `review-merge` | review merge | once · this flow · 1 hour | yes |
| `rules` | rule edit | once · this flow · 1 hour | yes |
| `publish` | git push, run_action open_pr / pr | once · this flow · 1 hour | yes |
| `data` | db change | once · this flow · 1 hour | yes |
| `delete` | delete run, stack remove, KeelBot undo | once · this flow · 1 hour | no |
| `stop` | stop flow(s), pause | once · this flow · 1 hour | yes |
| `model` | agent model | once · this flow · 1 hour | no |
| `safety-off` | rules mode safety-only, release check off | **once only** | **no** |
| `remove-project` | remove project | **once only** | **no** |

A tool in `OUT_OF_BAND` without a kind is a bug: a test checks that every one has a kind. A new tool joins a kind or
gets its own; a kind that is not in the table is "once only".

### 4.2 The card
An MCP confirmation card in the Inbox (web, TUI, KeelBot's panel) gets, next to *Deny*:
- **Allow once**: as today, this call only.
- **Allow for this flow**: this kind, in this project, until the flow ends or stops. Only when the call came in a flow.
- **Allow for 1 hour**: this kind, in this project, for 60 minutes.
- **Allow all N** (when N ≥ 2 cards of the same kind wait in the project): allows each of them once.

"For this flow" and "1 hour" also allow, at once, the other cards of that kind that wait in the project. Each of
those cards then shows "allowed by your OK for review replies (this flow)".

### 4.3 What an agent sees
- A call covered by an OK runs at once; the answer ends with one line: "(ran under the person's OK g_… for review
  posts, this flow)". No token, no card.
- A call that is not covered: as today (token, card), with the words of 3.3 and 3.4.
- `keel_status` and `keel_next` list the OKs that hold for the project ("review posts allowed for this flow"), so an
  agent can plan without trying first. Several agents read the same list.

### 4.4 Multi-agent
An OK belongs to the project, not to one client: a person who allows review posts for this flow allows them for
every agent working on the flow. This is on purpose: the client name over MCP is not a proof of who the agent is, so
keel does not pretend it can hold an OK for one agent only. The card and the audit still name the client of each call.

## 5. The per-project setting (part B)

Settings › MCP (per project) gets a section **"Agents may, without asking"**:

```
 Agents may, without asking                               (for this project)
 Review posts      Allowed for this flow (f_12)  · 6 calls · last 14:02   [Remove]
 Merge             Ask each time                                          [Always]
 Rule changes      Allowed until 15:30           · 1 call                 [Remove]
 Push / publish    Ask each time                                          [Always]
 …
 Keel's safety off Ask each time (always asks)
 Remove project    Ask each time (always asks)
```
- Each row shows what holds now and how often it was used. *Remove* ends the OK at once. *Always* gives a lasting OK
  for the kind (only for the kinds with "yes" in 4.1), after a second click that says what it allows.
- The same list over the api and in keel TUI's Settings, read only over MCP (`keel_status`).

## 6. Safety

- **Only a person gives an OK.** The routes that make, change or remove an OK, and an Inbox answer with a scope, take
  the person's session (the dashboard's sign-in, keel TUI). A request with an MCP level token (`X-Keel-Mcp-Token`), or
  from KeelBot's or an agent's tools, gets 403. There is no MCP tool for it. `keel_answer` (and the Inbox route with an MCP token) cannot answer an MCP
  confirmation card (as today for out-of-band cards), with or without a scope.
- **Two kinds always ask**: `safety-off` and `remove-project`; the api refuses an OK for them (its own list, not only
  the web's buttons).
- **Narrow**: an OK is for one kind in one project. A flow OK ends with the flow; an hour OK ends after 60 minutes;
  "always" ends when removed. Restarting keel keeps "always" and hour OKs, and ends flow OKs whose flow is not running.
- **Audit**: every call that ran under an OK is written with the OK's id (`McpAudit`, as today with via/client/tool),
  and the OK counts its uses.
- **Default**: no OK, every kind asks each time.
- **Not changed**: the MCP levels (read / write / admin), the gateway's read-only tools, `KEEL_MCP_CONFIRM=token`,
  `--yes`.

## 7. Where it lives

- **api** (owner of the data): table `mcp_grants (id, project_id, kind, scope [flow | until | always], flow_id, until,
  created_at, created_by, uses, last_used_at, ended_at, ended_why)`. Routes:
  `GET /api/projects/{pid}/mcp/grants`, `POST /api/projects/{pid}/mcp/grants {kind, scope, flow_id?}` (person only),
  `DELETE /api/projects/{pid}/mcp/grants/{id}` (person only),
  `POST /api/projects/{pid}/mcp/grants/use {kind, flow_id?, tool, client}` → `{allowed, grant_id?}` (counts a use; the
  MCP server's own token may call it). The Inbox answer route takes an optional `scope` for MCP cards.
- **engine**: `mcp_confirm.KINDS`; `act()` order: precheck → `grants/use` → (covered: run) or (token + one card).
  The card's approval carries `mcp: {tool, kind, client, flow_id, key}` so the Inbox can show the scopes and close it.
- **web**: the Inbox card's buttons (4.2), Settings › MCP section (5). **keel TUI**: the same buttons on the card
  and the same list (read and Remove).

## 8. Tests

- engine: every `OUT_OF_BAND` tool has a kind; a repeat call reuses the card; a card closes after the call ran, when
  its token ends and when its flow ends; a precheck's reason gives no card; a failing precheck still asks; a part
  tool returns a token at once; the "no" words; a covered call runs without a card and names the OK; `safety-off` is
  never covered.
- api: an OK is refused with an MCP token (403); refused for `safety-off` / `remove-project`; a flow OK ends with
  the flow; an hour OK ends after 60 minutes (clock moved in the test); uses are counted; Remove ends it.
- web: the card's four buttons and when each shows; "Allow for this flow" answers the other waiting cards; the
  Settings rows, *Always* with its second click, *Remove*.
- e2e (daily journey J28): two MCP clients on one flow each reply to 3 review threads. Today: 6 cards. After one
  "Allow for this flow": 1 card, 6 replies posted, the audit names the OK 6 times. Then *Remove*, and the next call
  asks again.

## 9. How we see it worked

The same scripted run as the MCP test (3 agents, review replies, resolves and one submit on one flow): Inbox cards
and wall time before and after. Target: cards from about 1 per call to 1 per kind per flow, and no card for a call
that cannot work.

## 10. Order of the work

- **Part A** (bugs #221, #222, #226, #227): engine only (+ the card's words), one PR. Small risk, no new setting.
- **Part B** (kinds, OKs, card buttons, Settings, audit, J28): api + engine + web + TUI, one PR after A (the same
  `act()` code).

## 11. Not in this plan

- OKs for one agent only (4.4).
- Changing which tools need an OK at all (`OUT_OF_BAND`).
- The gateway's own per-server rules (a connected MCP server's tools): they keep "once" until a later plan.
