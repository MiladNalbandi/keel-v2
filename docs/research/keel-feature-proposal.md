# keel: the full feature proposal, with the system before and after

Research for [#254](https://github.com/MiladNalbandi/keel-v2/issues/254), 2026-11-26, for keel 0.41.1. Nothing here
is built; this is a proposal to decide on. It builds on three documents and does not repeat them:

- [K1] the MCP test report, `docs/test-report-mcp-0.40.0.md` (the three-agent race);
- [K2] the agent toolkit plan, `docs/research/agent-toolkit-plan.md` (`keel_howto`, `keel_brief`, the small tool set);
- the two plans in review: fewer Inbox OKs ([#250](https://github.com/MiladNalbandi/keel-v2/issues/250)) and no
  `.keel` folder ([#246](https://github.com/MiladNalbandi/keel-v2/issues/246)).

**The question.** What should keel become in the next month so that an agent with keel is smarter and cheaper than
one without, and three agents on one keel finish sooner and with fewer tokens than three agents without it?

**keel's goal, used to judge everything here.** keel is a tool an agent connects to. It makes the agent smarter and
more efficient. It does not control or manage agents. Several agents on one keel must be more efficient together.
By default keel writes nothing into the user's project.

## Answer in short

1. **keel loses the race today for four reasons, and only one of them is a missing feature.** Two bugs cost most of
   the 7.6 minutes ([#217](https://github.com/MiladNalbandi/keel-v2/issues/217),
   [#218](https://github.com/MiladNalbandi/keel-v2/issues/218)). Fixed text costs most of the extra tokens (work
   orders, the session protocol, 131 tool definitions). The only path for a connected agent is a flow with gates.
   And nothing is shared between agents. Section 1.
2. **Add a light path.** Today a connected agent can only work inside the `harness` flow. The proposal lets an agent
   use keel's knowledge, test runs and "who works on what" with no flow and no gate, in a worktree keel gives it
   outside the project. Flows with gates stay, for work the person wants to look at. This is the change that can
   flip the race. Section 3, feature 7.
3. **Make keel's fixed cost close to zero.** A five-line brief arrives at session start through the client's hook
   (no tool call), the agent set is 12 tools, every answer has a size cap, and the same answers work from the shell
   (`keel2 howto commit`) with no tool definitions at all. Public tests show why: the same GitHub task cost 1,421
   input tokens with a CLI and 59,417 with a 46-tool MCP server [R45]. Section 3, features 2 to 6.
4. **Share what is expensive to find twice.** Test results (same tree, same result, no second run), facts with a
   citation, who touches which files (read from git, not from what agents say), and a warning before two branches
   collide. Section 3, features 11 to 16.
5. **Measure first, and every week.** A race harness and a meter come in week 1, before any feature. One public
   study found an MCP code tool that saved 14% of the tokens and lost 7 points of solved tasks [R48]. keel must
   not ship a tool like that without knowing. Sections 5 and 6.
6. **27 features are ranked** in section 3: 10 from the plans that exist and 17 new ones. Each has its goal, effort
   and a way to measure it. The month covers 17 of them; the other 10 wait for the race result.

How sure:

- **high** on point 1 (keel's own measurements) and on the fixed-cost rules of point 3 (several sources agree, two
  of them measured);
- **medium** on points 2 and 4: the patterns are well documented, but no public study measures them for a tool like
  keel, and the one paper that reports a large gain is by the tool's own author [R7];
- **low** on the size of the gain. The targets in section 6 are our estimates. On three tiny tasks the margin
  against plain agents is thin, and one plain agent doing all three tasks (17 s, 20k tokens) will stay the cheapest.

## 1. keel today

### 1.1 The system before

<!-- diagram: system-before -->
```mermaid
%%{init: {"flowchart": {"wrappingWidth": 320}}}%%
flowchart TB
  PERSON["The person<br/>web dashboard, desktop, JetBrains"]

  subgraph AGENTS["Connected agents: Claude Code, Codex, Cursor"]
    direction LR
    A1["Agent 1"]
    A2["Agent 2"]
    A3["Agent 3"]
  end

  MCP["keel2 mcp: one process per agent<br/>131 tools at write level<br/>about 28k tokens if a client loads all"]

  subgraph KEEL["keel: one container, or native"]
    APIPY["api-py, port 8080<br/>sign-in, web files, sessions route"]
    KOTLIN["Kotlin api, port 8081<br/>flows, Inbox, tasks, knowledge"]
    ENGINE["engine, port 8090<br/>flows with gates, keel's own agents,<br/>KeelBot, session hub, plugins"]
    INBOX["Inbox<br/>one card per call"]
  end

  subgraph STORE["keel's data folder"]
    DB1[("keel.db<br/>flows, approvals, facts, tasks")]
    DB2[("checkpoints.db<br/>flow state")]
    INDEX["code graph index"]
  end

  subgraph PROJECT["The user's project folder"]
    CODE["code and .git"]
    DOTKEEL[".keel folder<br/>config, rules, specs, knowledge,<br/>worktrees of parallel flows"]
  end

  MODELS["Model APIs<br/>for keel's own agents"]

  A1 --> MCP
  A2 --> MCP
  A3 --> MCP
  MCP -->|"HTTP"| APIPY
  PERSON --> APIPY
  APIPY --> KOTLIN
  APIPY -->|"sessions"| ENGINE
  KOTLIN --> ENGINE
  KOTLIN --> INBOX
  PERSON -.->|"approves"| INBOX
  KOTLIN --> DB1
  ENGINE --> DB2
  ENGINE --> INDEX
  ENGINE -->|"reads, writes, commits"| CODE
  ENGINE -->|"writes"| DOTKEEL
  ENGINE --> MODELS

  P1["1. Work orders go by project,<br/>not by agent (#217)"]
  P2["2. The agent waits 90 s<br/>for nothing (#218)"]
  P3["3. Agents cannot see<br/>each other (#219)"]
  P4["4. Every agent reads<br/>the rules itself"]
  P5["5. keel writes into<br/>the project (#246)"]
  P6["6. One click per call;<br/>some calls block 10 min (#250)"]

  P1 -.- ENGINE
  P2 -.- MCP
  P3 -.- ENGINE
  P4 -.- AGENTS
  P5 -.- DOTKEEL
  P6 -.- INBOX

  classDef pain fill:#fdecea,stroke:#c0392b,color:#7b241c
  class P1,P2,P3,P4,P5,P6 pain
  classDef project fill:#fff8e1,stroke:#b7950b
  class DOTKEEL project
```

Export: [SVG](img/keel-proposal/system-before.svg) · [PNG](img/keel-proposal/system-before.png)

How to read it (checked in the code of 0.41.1):

- **Three servers.** `api-py` is in front on port 8080. It passes most routes to the Kotlin api (8081) and the
  session routes straight to the engine (8090). The engine runs the flows, keel's own agents and the plugins.
- **One MCP process per agent.** `keel2 mcp` is a small program each client starts. It calls the api over HTTP. It
  has three levels: 69 tools at read, 131 at write, 141 at admin [K1]. There is no small set for agents yet.
- **A connected agent can only do one thing: the `harness` flow.** `keel_start_flow` with `engine=session` turns
  quick, small, implement, feature, change and fix into `harness` (plan, plan gate, build, keel's tests, done gate,
  commit, PR gate). There is no way to use keel's help without a flow.
- **Two SQLite files and the project folder.** Flows, Inbox cards, facts and tasks are in `keel.db`. Flow state is
  in `checkpoints.db`. Config, rules, specs, knowledge pages and the worktrees of parallel flows are in `.keel/`
  inside the project. Sessions and waiting questions are held in the engine's memory.
- **The six red boxes** are the problems this proposal removes. Each has an open issue or a plan.

### 1.2 One job with three agents, before

The job from the test report [K1]: three small tasks in a `shop` project (`discount()`, `Cart.remove()`,
`Inventory.restock()`), each with a test and a CHANGELOG line, and a rules file `docs/CONVENTIONS.md`.

<!-- diagram: job-before -->
```mermaid
sequenceDiagram
  autonumber
  participant P as Person
  participant A as Agents 1, 2, 3
  participant K as keel
  participant R as Project repo

  A->>K: keel_session_open (3 times)
  K-->>A: protocol text, about 1.3k characters each
  A->>K: keel_start_flow (3 times, 1 to 3 s each)
  K->>R: worktrees for flow 2 and 3, inside .keel
  A->>K: keel_session_next
  K-->>A: the oldest work order of the project
  Note over A,K: Agents get each other's orders (#217). About 4 to 5 minutes lost.
  A->>R: each agent reads docs/CONVENTIONS.md itself
  A->>K: keel_session_plan
  K->>P: plan gate (3 cards)
  P-->>K: OK
  A->>R: write code and tests
  A->>K: keel_session_done
  K->>R: keel runs the tests
  K->>P: done gate (3 cards)
  P-->>K: OK
  K->>R: one commit per task, on 3 branches
  A->>K: keel_session_next
  Note over A,K: Waits up to 90 s although the flow is finished (#218)
  Note over R: 3 branches, CHANGELOG conflict at merge, 2 of 4 hidden checks pass
  Note over P,R: 7.6 minutes, 90k tokens, 43 tool calls
```

Export: [SVG](img/keel-proposal/job-before.svg) · [PNG](img/keel-proposal/job-before.png)

### 1.3 Where the time and the tokens went

| Run [K1] | Wall time | Tokens | Tool calls | Hidden checks |
|---|---|---|---|---|
| plain, 1 agent, 3 tasks | 17 s | 20k | 4 | 4/4 |
| plain, 3 agents, one folder | about 25 s | 60k | 17 | 4/4 |
| keel, 1 agent, 3 flows | 4.6 min | 31k | 14 | 4/4 |
| keel, 3 agents, 3 flows | 7.6 min | 90k | 43 | 2/4 after merging |

| Cost | Size | Cause | Kind |
|---|---|---|---|
| Wrong work orders | 4 to 5 of the 7.6 minutes | `keel_session_next` hands out the oldest order of the project (#217) | bug |
| Waiting for nothing | 90 s per wait | `keel_session_next` waits its full time when the flow is done or waits for the person (#218) | bug |
| Gates | seconds with the test's "person bot", minutes with a real person | a connected agent can only run the `harness` flow, which has three gates | design |
| Fixed text | 26k to 32k tokens per keel agent against about 20k plain | the work order's standing text and the session protocol | design |
| Tool list | up to about 28k tokens per agent, **not counted in the race** | the test agents used a bridge and never loaded keel's 131 tool definitions [K1] | design |
| Repeated reading | 7 of 8 agents read the conventions file themselves | no shared knowledge an agent can ask or add to | missing |
| Merge | 2 of 4 hidden checks after merging | three branches all added a CHANGELOG line; nothing warned or helped | missing |
| Presence | n/a | `keel_live` says "no agent is running" while three work (#219) | bug |

Three things follow.

- **The race is mostly lost to bugs and fixed cost, not to missing features.** Fixing #217 and #218 removes most of
  the time. That is week 1.
- **With a real client the token gap is larger than measured.** A client that loads every tool definition pays about
  28k tokens per agent before any work. So the small tool set is not optional.
- **The plain three-agent run was not clean either.** One agent's CHANGELOG line went into another's commit, and one
  could not stage its line because HEAD kept moving: 17 tool calls where 12 would do [K1]. This waste is what keel
  can remove, and it is where a win on time and tokens can come from.

The squad's own experiment says the same from the other side: building through the `harness` flow with a connected
session worked end to end at zero keel tokens, but needed about three workarounds per run and was slower than the
plain loop on a similar fix round. keel "adds no quality by itself" there, because it ran the same tests the agent
ran (squad experiment log, 2026-11-26, internal).

## 2. What others do

The toolkit plan [K2] already covers how agents read rule files (`AGENTS.md`, `CLAUDE.md`, Cursor rules), the basic
MCP tool rules, and worktrees, lock files and Copilot Memory. This section adds what that plan lists as "not opened"
and what is new since. Sources are at the end; "✔✔" there means two readers checked the page.

### 2.1 Shared memory

| Tool | How a memory is made | Shared with | Stale or wrong memories | Source |
|---|---|---|---|---|
| GitHub Copilot Memory | the agent stores a fact with citations to code | every Copilot surface on the same repository (cloud agent, code review, CLI, VS Code) | citations are checked against the current branch before use; facts expire after 90 days by default (7 to 180, set by the owner) | [R18] [R19] |
| Claude Code auto memory | Claude writes one file per memory plus an index | one machine, one repository; not other machines, not the cloud | only the first 200 lines or 25KB of the index load; Claude is told to merge or drop old entries | [R20] |
| Codex memories | made in the background from past chats; off by default | one machine | "a helpful recall layer, not ... the only source for rules that must always apply" | [R21] |
| Devin Knowledge | a person accepts what Devin suggests; each item has a trigger | organisation | retrieved "when relevant, not all at once"; now being moved to Skills | [R22] |
| Beads | `bd remember` stores a fact, `bd prime` injects the facts later | everyone with the project's database | old closed tasks are summarised ("memory decay"); ids are hashes so parallel branches do not collide | [R11] |
| mem0 | a model extracts facts from the conversation | by user, agent or run id | the automatic path only adds: a changed fact is stored next to the old one | [R23] |
| Letta | "memory blocks" in the prompt; one block can be attached to several agents | the agents that share the block | "last write wins": two writers overwrite each other | [R24] |
| Zep / Graphiti | facts as edges in a graph, each with a time window | by graph | "old facts are invalidated - not deleted" | [R25] |
| MCP reference memory server | entities with short observations, in one file | whoever shares the file | the whole file is rewritten on every write; "No default caps" | [R28] |

What is measured:

- **Citations work.** Copilot's coding agent merged 90% of its pull requests with memory against 83% without. In a
  test with false memories planted on purpose, agents "consistently verified citations, discovered contradictions,
  and updated incorrect memories" [R19]. Cross-agent sharing is shown there as an example, not measured on its own.
- **Memory can hurt.** MemDev-Bench (October 2026): "Memory also causes failures on otherwise solvable tasks even
  with related history" [R29]. REM (October 2026) did better with less: a short summary at the start and more only
  when the agent struggles gave 27.6% against 24.9% solved at 17% lower cost, and "the bottleneck in experience
  reuse is not retrieval fidelity but how experience is stored and governed" [R30].
- **Memory can be attacked.** One paper shows that a single bad write keeps working for a long time and that
  prompt-injection defences do not cover it [R31]. Cisco showed a poisoned `MEMORY.md` that survived across projects
  and restarts [R32].
- **Vendor benchmarks disagree.** mem0 and Zep each publish numbers on the LoCoMo test in which the other loses.
  Letta reports that a plain agent with a file system beat mem0's published score (74.0% against 68.5%) [R26] [R27].
  These sources are older than a year, but the point stands: do not copy a number, measure.

### 2.2 Several agents on one repository

| Who | What they tried | What happened | Source |
|---|---|---|---|
| Cursor (hundreds of agents) | locks on shared state | "20 agents would slow to the throughput of 1-3 with most time spent waiting on locks" | [R3] |
| Cursor | an integrator that merges everything | "It quickly became an obvious bottleneck", removed | [R3] |
| Cursor, final design | workers "don't communicate with any other planners or workers"; overlaps are accepted | "we accept some moments of turbulence and let the system naturally converge" | [R3] |
| Cognition (Devin), April 2026 | one writer, other agents only add thinking | works: "multiple agents contribute intelligence to a task while writes stay single-threaded"; of parallel-writer ideas, "most ... still don't see meaningful adoption" | [R4] |
| Claude Code agent teams | shared task list, claims, messages between teammates | "Task status can lag"; about 7x the tokens of one session when teammates plan; a message from another session costs a full turn with the whole context | [R1] [R2] |
| Beads | a "ready" list and an atomic claim (`bd update <id> --claim`) | agents pick only tasks with no open blocker | [R11] |
| mcp_agent_mail | advisory file leases with a time limit | "reservations are still granted; conflicts are returned alongside grants"; broad patterns like `src/**` collide; unreleased leases stay until they expire | [R12] |
| Gas Town | a boss agent, a merge queue, watchdogs for stuck agents | a full manager design: the opposite of keel's goal, but its merge queue and "stuck" view are useful ideas | [R13] |
| GitHub mission control | one page for all agent sessions | advice only: "Agents working in parallel can create merge conflicts if they touch the same files" | [R14] |

What is measured:

- **Agents that talk to each other do worse.** CooperBench (January 2026, over 600 tasks): "agents achieve on
  average 30% lower success rates when working together" than alone [R6]. This is about agents sending each other
  messages. It supports a shared board the agent reads when it needs to, not a chat.
- **Different agents collide twice as often.** In 33,596 agent pull requests, pairs from different agents had a
  41.7% merge-conflict rate against 19.8% for pairs from the same agent, and nearly 42% of the conflicts were
  structural, not just two edits in one line [R8]. A second study of 142K agent pull requests found 27.67% in
  conflict with the base branch [R9].
- **Shared state before the pull request helps, says the tool's own author.** The paper on `grite` (June 2026)
  reports that work re-doing a teammate's task fell from 78% to 0% with an event log and leases kept in git [R7].
  It is one author's own tool and benchmark; we read the abstract only.
- **Awareness helps the person too.** With a dashboard of parallel agent work, 16 people finished about 63% more
  tickets on short tasks, although they did not feel more in control [R10].
- **Nobody has measured the speed-up of parallel coding agents independently.** We found vendor statements only.
  Anthropic: multi-agent systems use "about 15x more tokens than chats", and "most coding tasks involve fewer truly
  parallelizable tasks than research" [R5] (older than a year).

Bringing branches together: GitHub's merge queue tests each pull request on top of the ones before it and removes
the one that fails [R15]. Git's `merge=union` keeps the lines of both sides for append-only files, but "tends to
leave the added lines in the resulting file in random order" [R16]. towncrier avoids the problem with one small file
per change [R17].

### 2.3 MCP design and token cost

| Finding | Number | Source |
|---|---|---|
| The same task ("latest release and three bug issues" on GitHub), Claude Sonnet 5.5 | CLI 1,421 input tokens · skill 3,315 · MCP with 2 tools 23,228 · the same with results cut at 4,000 characters 5,357 · MCP with 46 tools 59,417 · with 91 tools 101,351 | [R45] |
| Claude Code's own advice | "Prefer CLI tools when available ... they don't add any per-tool listing" | [R2] |
| Claude Code loads MCP tools on demand | "Only tool names and server instructions load at session start"; instructions and upfront descriptions are cut at 4,096 characters | [R36] |
| Too many tools hurt the choice | "Claude's ability to pick the right tool degrades once you exceed 30–50 available tools" | [R43] |
| A changed tool list is expensive | changing tool definitions invalidates the whole prompt cache; the spec now says servers "SHOULD return tools from `tools/list` in a deterministic order" | [R44] [R33] |
| Tool results are capped by the client | Claude Code: 25,000 tokens by default, a warning at 10,000 | [R36] |
| Slow calls | Claude Code moves a call to the background after two minutes; Codex's default tool timeout is 60 s | [R36] [R39] |
| A brief with no tool call | Claude Code's `SessionStart` hook adds context "at the start of the conversation, before the first prompt", up to 10,000 characters, and a hook can be of type `mcp_tool`. Codex, Copilot CLI and Cursor have a session-start hook too | [R37] [R40] [R41] [R42] |
| Waiting for a person | the MCP Tasks extension names "human approvals": the server returns a handle "instead of blocking", the client polls; both sides must opt in | [R34] |
| Server push to the model | found in one client only: Claude Code "channels", a research preview behind a flag | [R38] |
| Skills over MCP | an official extension; Claude Code loads skills from servers in public beta | [R35] [R36] |

The newest MCP specification is 2026-07-28. It removes sessions and the `initialize` handshake ("Servers that need
cross-call state use explicit, server-minted handles passed as ordinary tool arguments"), adds a required
`server/discover` call, moves tasks into an extension, and deprecates sampling, roots and logging [R33]. For keel:
an agent's identity must be a handle keel gives out, not the connection.

What support each client has differs a lot (resources, prompts, hooks, timeouts). The safe design is the lowest
common one: few tools, fast answers, short results, no reliance on push.

### 2.4 Project knowledge and code tools

- **An MCP code tool can cost solved tasks.** `mcpbr` (February 2026, one independent author) ran a code-graph MCP
  server on all 500 SWE-bench Verified tasks: 42.3% fewer tool calls, 14.0% fewer tokens, 15.2% lower cost, and
  the solved rate fell from 49.8% to 42.4%. It helped on 1 of 12 repositories and hurt on 10 [R48]. keel's own
  test of 2026-10-06 agrees in kind: pre-fetched "where to look" hints saved no tokens [K2].
- **Overviews do not help; unusual rules do.** The ETH Zurich study says repository overviews "were not helpful",
  context files raise cost by over 20%, and they are "useful for specifying non-standard coding practices" [R50].
  Of 98 public agent skills, 92 had no significant effect; the average pass rate moved from 76.6% to 76.9% [R51].
- **Semantic search adds a little to grep.** Cursor's own A/B test: +0.3% code retention overall, +2.6% on
  repositories with 1,000 files or more (vendor number, three weeks older than a year) [R49].
- **Rules learned from review need a life cycle.** Cursor Bugbot and CodeRabbit both turn review feedback into
  candidate rules, promote them, disable them on bad signal, and let a person edit them [R53] [R54].
- **Session history is kept next to git by some tools.** Entire saves a checkpoint (transcript, files, tokens) per
  commit in its own git ref [R55]. We found no product that tells an agent "another agent already tried this and
  it failed".

### 2.5 How to measure

- GitHub's Copilot team: "token count of individual interactions alone isn't a meaningful measure of efficiency".
  A tool that shortened output made agents re-run commands: "We saved tokens locally and spent more globally."
  They compress install, build and test output "only when the savings are substantial", keep the original, and
  watch for agents re-running commands or opening the original [R52].
- One run means little: on SWE-bench Verified "single-run pass@1 estimates vary by 2.2 to 6.0 percentage points
  depending on which run is selected", so "reported improvements of 2--3 percentage points may reflect evaluation
  noise" [R56]. Anthropic found the machine's resource limits alone moved a benchmark by 6 points [R57].
- `mcpbr` is the only method we found for "the same agent with and without this MCP server": same tasks, same
  model, paired runs [R48]. No public benchmark tests several agents sharing one tool.

### 2.6 What nobody does yet

We did not find any tool that does these. They are where keel can be first, and also where there is no proof yet.

1. Answers "how do we do X in this repository" from the repository's own configs and history, with a source and a
   confidence [K2].
2. Shares **test results** between agents on the same code.
3. Tells an agent before it starts: "these two live branches will conflict in this file."
4. Tells an agent "this was tried and failed, here is why."
5. Gives several different agents (Claude Code, Codex, Cursor) one memory with checked citations. Copilot does it
   for its own surfaces only.

## 3. The proposal

### 3.1 Nine rules, each from evidence

| # | Rule | Why |
|---|---|---|
| 1 | Answer when the agent asks; push almost nothing | Overviews "were not helpful" and cost over 20% [R50]; keel's own pushed hints saved nothing [K2]; a short start plus more on demand won in REM [R30] |
| 2 | Add to grep and read, never replace them | A code tool saved tokens and lost solved tasks [R48] |
| 3 | Every answer has a source, or says "not found" | Checked citations are what made Copilot's memory work [R19] |
| 4 | Show, do not block | Locks cut 20 agents to the speed of 1 to 3 [R3]; advisory leases return the conflict with the grant [R12] |
| 5 | Read the state from git, not from what agents report | "Task status can lag" [R1] |
| 6 | No chat between agents; one board they read | Talking agents solved 30% less [R6]; a pushed message costs a full turn [R2] |
| 7 | Fixed cost near zero: few tools, short answers, a size cap | 1,421 against 59,417 tokens for one task [R45] |
| 8 | Nothing in the project; keel's own store | keel's rule, [#246](https://github.com/MiladNalbandi/keel-v2/issues/246) |
| 9 | Judge by the finished job, over several runs | "We saved tokens locally and spent more globally" [R52]; one run varies by 2 to 6 points [R56] |

### 3.2 The base (not features)

These must be done first. Nothing below pays off while they are open.

| Work | Issue or plan | Gain |
|---|---|---|
| Work orders go to the agent that started the flow; `keel_session_next(flow=…)` | [#217](https://github.com/MiladNalbandi/keel-v2/issues/217) | about 4 to 5 minutes in the race |
| `keel_session_next` answers at once when the flow is done or waits for the person | [#218](https://github.com/MiladNalbandi/keel-v2/issues/218) | 90 s per wait |
| Connected sessions are visible | [#219](https://github.com/MiladNalbandi/keel-v2/issues/219) | needed by features 12 and 13 |
| No wasted Inbox cards; no call blocks for 10 minutes | [#250](https://github.com/MiladNalbandi/keel-v2/issues/250) part A (#221, #222, #226, #227) | Codex gives a tool call 60 s by default [R39], so a 10-minute block fails there |
| keel's files out of the project | [#246](https://github.com/MiladNalbandi/keel-v2/issues/246), plan `docs/plans/no-keel-folder.md` (in review on its branch) | keel's rule; worktrees move to keel's own folder |

### 3.3 All features, ranked

Goal: **S** = smarter (the agent knows the project), **C** = cheaper (fewer tokens for one agent), **T** = faster
together (several agents). Effort: S = days, M = about a week, L = more. "From": K2 = the toolkit plan, #250 = the
fewer-OKs plan, **new** = proposed here. ★ = needed for the race to flip. The rank is our judgement of gain against
effort, with the evidence named; the numbers are not measured yet.

| # | Feature | Goal | Effort | From | Evidence | How to measure |
|---|---|---|---|---|---|---|
| 1 ★ | **Meter and race harness**: keel counts every tool call (bytes out, repeats, seconds waiting for the person); `keel2 bench race` runs the three-task job with and without keel | S C T | M | new | [R48] [R52] [R56] | it exists when the baseline of section 6 is recorded with 5 runs per arm |
| 2 ★ | **Agent tool set** `keel2 mcp --agent` (12 tools) | C | S | K2 | [R43] [R45] | bytes of tool definitions: about 114 KB today, under 12 KB |
| 3 ★ | **Tool hygiene**: every answer capped at 4,000 characters with a `detail` switch, a fixed tool order, descriptions under 600 characters, the first 512 characters of `instructions` stand alone | C | S | new | [R33] [R36] [R39] [R45] | median and largest answer size per tool in the meter; prompt-cache misses in the client |
| 4 ★ | **Slim work orders**: the fixed text of a work order under 1,500 characters; the protocol once per session | C | S | new | [K1] | tokens per keel agent in the flow path: 26k to 32k today, within 2k of plain |
| 5 ★ | **`keel_brief`, delivered by the client's hook**: five lines at session start, no tool call | S C | S | K2 + new | [R37] [R40] [R50] | tool calls before the first edit; agents that open the rules file themselves |
| 6 | **Shell path**: `keel2 brief`, `keel2 howto`, `keel2 who`, `keel2 test` print the same answers; one skill teaches them | C | S | new | [R2] [R45] | tokens per agent, shell arm against MCP arm, in the race |
| 7 ★ | **Light path**: `keel_workspace(task)` gives a worktree outside the project and a branch; keel's help works with no flow and no gate | T C | M | new | [K1] [R3] [R4] | wall time and tokens of the three-agent race; files changed by two agents at once (target 0) |
| 8 | **`keel_howto(topic="commit")`**, which replaces `keel_commit_style` | S | S | K2 | [K2] | commits in the project's style on the first try (hidden check) |
| 9 | **`keel_howto`** topics `branch`, `test`, `style`, `pr` | S | M | K2 | [K2] [R50] | calls per session; reads of `CONTRIBUTING` and CI files by agents |
| 10 ★ | **`keel_test(scope)`**: keel's own test command; one line when green, the failing assertions and a path to the full log when red | C S | M | K2 + new rule | [K1] [R52] | tokens of test output per job; how often the agent opens the full log |
| 11 | **Shared test results**: the same code and command are not run twice; keel's own check reuses the agent's run | C T | M | new | none found (section 2.6) | test runs per job; seconds of test time saved |
| 12 ★ | **`keel_who`**: who is connected, on which branch, changing which files, read from sessions and git | T | M | K2 | [R1] [R7] [R10] | jobs where two agents did the same task (target 0) |
| 13 | **`keel_claim(paths, minutes, why)`**: a note that expires; activity in the worktree renews it; overlap is told, never refused | T | S | K2 + new | [R3] [R12] | claims that expired unused; overlaps told before the edit against found at merge |
| 14 | **`keel_remember(fact, cite)`** and recall, with the safety rules of section 3.5 | S T | M | K2 + new | [R18] [R19] [R29] [R31] | hidden-check pass rate with and without; wrong facts found on the Knowledge page |
| 15 | **Merge radar**: keel test-merges the live branches in the background and says which will conflict, in which file | T | M | new | [R8] [R9] | conflicts predicted against conflicts met at merge |
| 16 | **Land in order**: `keel_land(branches)` merges finished branches one after another into a new branch in keel's worktree, runs the tests on the result, names the branch that breaks | T | M | new | [R15] [R16] [K1] | hidden checks after merging: 2 of 4 today, 4 of 4 |
| 17 | **One OK for many calls** | T | M | #250 | #250 | clicks per job; seconds agents wait for the person |
| 18 | **Handoff notes**: `keel_handoff(done, next, open)`; the next agent on the branch gets the last one in its brief | S T | S | new | [R55]; the squad's own habit | tool calls before the first edit when a second agent takes over a branch |
| 19 | **Traps**: "tried and failed" facts, offered by keel when the same test failed twice and then passed | S T | M | new | none found (section 2.6); [R29] warns | repeated identical failures across agents |
| 20 | **Ready list**: `keel_tasks(ready=true)` from the Tasks plugin; taking a task says who has it | T | M | new | [R7] [R11] | two agents on one ticket (target 0) |
| 21 | **Review record**: `keel_review_history` gives any reviewer the earlier rounds and what was fixed | S T | S–M | new | [R4]; squad experiment | real findings per review round; findings repeated from an earlier round |
| 22 | **Waiting without blocking**: MCP Tasks where the client supports it; a push in Claude Code when a gate is answered | T | M–L | new | [R34] [R38] | seconds between the person's OK and the agent's next call |
| 23 | **Agents panel** for the person: sessions, branches, files, predicted conflicts, what waits for you. Read-only | T | M | new | [R10] | the person's time per job (to be asked, not measured) |
| 24 | **`keel_about(paths)`**: the facts, people and last changes for the files the agent is about to touch | S | M | new | [R48] warns | only shipped if the race shows no loss in hidden checks |
| 25 | **Learned rules** from gate notes and repeated review findings: candidate, approved by the person, switched off on bad signal | S | L | new | [R53] [R54] | findings of the same kind per month |
| 26 | **MCP 2026-07-28**: `server/discover`, an agent handle as a tool argument, `ttlMs` | – | M | new | [R33] | keel connects with a client that speaks only the new version |
| 27 | **Export to `AGENTS.md`**, only when the person asks | S | S | K2 | [R50] | agents without MCP pass the same hidden checks |

Counted: 27 rows. 10 come from plans that exist (the toolkit plan's eight features, with who and claim as two
rows, and one from #250). 17 are new. Rows 1 to 17 are the month (section 5); rows 18 to 27 wait for the race
result.

### 3.4 The new features, one by one

The example answers are illustrations. Their names, numbers and paths are made up.

**1. Meter and race harness.** The api already counts calls per client (`McpClients`). The meter adds, per tool:
calls, bytes returned, the same call repeated within a minute, and seconds spent waiting for the person. `keel2
stats` prints it; the Usage page shows it. `keel2 bench race` sets up the `shop` project of [K1], starts the agents
in each arm, applies the hidden checks, and writes one table. It comes first because rule 9 needs it, and because
the first measurement was done through a bridge that never loaded keel's tool list.

**3. Tool hygiene.** Four small rules for the agent set, each with a test: (a) no answer over 4,000 characters
without `detail=true`, and a capped answer ends with what was left out; (b) the tool list is in one fixed order and
never changes during a session, because a changed list throws the client's prompt cache away [R44]; (c) no
description over 600 characters; (d) the first 512 characters of the server `instructions` say everything an agent
must know, because Codex advises that and Claude Code cuts at 4,096 [R39] [R36]. In Claude Code the three most
used tools are marked to load at once (`anthropic/alwaysLoad`); the others load on demand.

**5. The brief by hook.** `keel_brief` is the toolkit plan's feature. New is how it arrives. Claude Code, Codex,
Copilot CLI and Cursor can add text at session start from a hook, with no tool call [R37] [R40] [R41] [R42].

```text
keel2 hooks print claude-code      prints the few lines of settings to add; keel writes nothing itself
keel2 hooks install claude-code    writes them to the person's own client settings, only when asked
```

The hook calls keel and gets the five lines. A hook file is the person's client configuration, not the project:
keel prints it by default and installs it only on request. Where a client has no hook, the first lines of the
server `instructions` say "call `keel_brief` once".

**6. Shell path.** The same answers from the shell: `keel2 howto commit`, `keel2 brief`, `keel2 who`,
`keel2 test changed`. A CLI adds no tool definitions at all, which is why it was the cheapest arm in the public
tests [R45] and why Claude Code's own cost page says to prefer one [R2]. The MCP tools and the commands share one
function each, so they cannot disagree. A short skill (keel can serve it over MCP where the client supports that
[R35]) lists the four commands. Which path is cheaper for keel is an open question: the race decides.

**7. Light path.** Today the only work a connected agent can do with keel is a `harness` flow. The light path is
keel's help without a flow.

```text
call    keel_workspace(task="discount() in pricing")
answer  worktree  ~/.keel2/keel/worktrees/shop/discount-7f3a     (your own copy, outside the project)
        branch    feat/discount, from main
        rules     docs/CONVENTIONS.md (money, rounding, errors) · test: uvx pytest -q
        others    a2 on feat/cart-remove (cart.py) · a3 on feat/restock (inventory.py)
        then      commit here yourself · keel_test(scope="changed") · keel_land brings branches together
```

- keel makes the worktree and the branch and then steps back. It does not plan, order, check or commit. The agent
  works as it does without keel, only in its own copy and knowing what the others do.
- Because keel made the worktree, `keel_who` can read its branch, changed files and last commit from git. No agent
  has to report anything (rule 5).
- **What the person gives up on this path:** keel's gates and keel's rule guard. The agent runs in its own program
  with its own permissions, exactly as it does without keel. keel still never pushes. For work the person wants to
  approve, flows with gates stay as they are. A per-project switch turns the light path off.
- The worktree is in keel's own folder, as the no-`.keel`-folder plan sets it. Inside `.git` only a branch and the
  worktree's metadata are added, which that plan allows.

**10. `keel_test`, compact.** The toolkit plan's feature, with GitHub's measured rule added [R52]: green is one
line; red shows the failing assertions and the path of the full log, which keel keeps in its own store. keel
counts how often an agent asks for the full log. If that is often, the short form is cutting too much.

**11. Shared test results.**

```text
call    keel_test(scope="changed")
answer  uvx pytest -q tests/test_pricing.py · 12 passed (1.8 s)
call    keel_test(scope="changed")                (another agent, or keel's own check, on the same code)
answer  12 passed · the same code was tested 40 s ago by a1, not run again (run=true runs it)
```

The key is the project, the command, and a hash of the exact content (the commit plus uncommitted changes). In a
flow, keel's own check after `keel_session_done` reuses the run the agent just made through keel, so the flow does
not test the same code twice. It is still keel that ran the tests, not the agent's word.

**13. Claims that renew themselves.** As in the toolkit plan: a claim is a note with a time limit, and an overlap
is told with the grant. New: work in the claimed paths of the agent's worktree renews the claim, and a quiet
worktree lets it run out. No agent has to remember to renew or release, which is what failed elsewhere [R3] [R12].

<!-- diagram: claim-life -->
```mermaid
stateDiagram-v2
  [*] --> Active: keel_claim, or the first edit in a keel worktree
  Active --> Active: new edits or commits renew it
  Active --> Ended: the time limit passes with no activity
  Active --> Ended: the branch is landed, or the agent releases it
  Ended --> [*]
  note right of Active
    Another agent claims the same paths:
    both get a note. Nobody is blocked.
  end note
```

Export: [SVG](img/keel-proposal/claim-life.svg) · [PNG](img/keel-proposal/claim-life.png)

**15. Merge radar.** While branches are live, keel test-merges each pair in the background (`git merge-tree`, which
changes no working folder) and remembers the result per pair of commits. `keel_who`, `keel_workspace` and
`keel_status` then say: "feat/cart-remove and feat/restock both add lines to CHANGELOG.md: they will conflict."
Different agents on one repository conflict about twice as often as one agent with itself [R8], and keel's race
ended exactly this way [K1]. Design point for the spec: the test-merge must write its temporary objects to keel's
own folder, not into the project's `.git`.

**16. Land in order.**

```text
call    keel_land(branches=["feat/discount", "feat/cart-remove", "feat/restock"])
answer  merged 3 of 3 into keel/land-12-22 (a new branch in keel's worktree; nothing pushed, main untouched)
        CHANGELOG.md: all three added lines at the same place: kept all, in merge order (check the order)
        tests on the result: 31 passed (2.4 s)
```

It is a helper any agent or the person can call, not a gate. It merges in the given order and tests the combined
code, as a merge queue does [R15]. A branch that breaks the result is left out and named with its failing test.
Lines are kept from both sides only where both sides only added lines; anything else is reported as a conflict.
It grows out of `keel_branch_sync`, which exists and which no agent used [K1].

**18 to 27, in one line each.**

- **18 Handoff notes.** Three fields, kept per branch in keel's store; the next `keel_brief` or `keel_workspace` on
  that branch shows the last one in at most five lines.
- **19 Traps.** A fact of kind `trap` ("pytest needs `uvx` here: there is no `python` on PATH"). keel offers to
  save one when the same test failed twice and then passed. Short life (30 days) unless confirmed, because wrong
  experience hurts [R29].
- **20 Ready list.** Tasks with no open blocker and nobody on them. keel already refuses a second flow on the same
  ticket; here it only says who has it.
- **21 Review record.** The squad's experiment found that a fresh reviewer ("goldfish") and one that knows the
  earlier rounds ("elephant") find different problems, and both together found every real problem in each round
  while either alone found about half. Cognition reports the same for a clean-context reviewer [R4]. keel already
  stores review threads; this tool gives the earlier rounds to any agent, so the "elephant" does not depend on one
  session's memory.
- **22 Waiting without blocking.** After #250 every tool returns at once with a ticket. On top: the MCP Tasks
  extension where a client declares it [R34], and Claude Code's channel to tell an agent "your gate was answered"
  [R38]. Later, because support differs per client and channels are a preview.
- **23 Agents panel.** One page for the person. No start or stop buttons for connected agents: keel shows, it does
  not manage.
- **24 `keel_about(paths)`.** Useful in theory, risky in practice [R48]. Build it only behind a measurement.
- **25 Learned rules.** The largest item. It needs the Knowledge page, a review queue and good signals.
- **26 MCP 2026-07-28.** The new version has no sessions [R33]. keel's agent handle (features 7 and 12) is
  designed as a plain tool argument from the start, so this is mostly `server/discover` and cache hints.
- **27 Export.** Unchanged from the toolkit plan: only on request, because it writes into the repository.

### 3.5 Safety rules for the shared memory (feature 14)

| Rule | Why |
|---|---|
| A fact needs a citation (`path:line`), or it is marked `unverified` | [R19] |
| keel checks the citation when it shows the fact, and says "still there", "changed" or "gone" | [R18] [R19] |
| A new fact that disagrees with an old one does not overwrite it: the old one is marked "replaced by" | adding only piles up contradictions [R23]; last-write-wins loses edits [R24]; Graphiti invalidates instead of deleting [R25] |
| Facts run out after 90 days unless a check or a person confirms them; the person can set the time | Copilot's default [R18] |
| Every fact records which client wrote it and when; recalled facts are labelled as notes from other agents, not as instructions | one bad write can work for a long time [R31] [R32] |
| Writes are limited per session; values that look like secrets are refused | [R31]; CodeRabbit strips credentials before storing [R54] |
| At most a handful of facts are shown at once, only those for the topic or paths asked | unrelated memory breaks tasks [R29]; little at the start won [R30] |
| Rules that must always hold do not live in memory; they are the person's written rules in keel's settings | [R18] [R21] |
| The person sees, edits and deletes every fact on the Knowledge page | [R22] [R54] |

## 4. The system after

### 4.1 The whole system

<!-- diagram: system-after -->
```mermaid
%%{init: {"flowchart": {"wrappingWidth": 320}}}%%
flowchart TB
  PERSON["The person<br/>web dashboard, desktop, JetBrains<br/>new: Agents panel"]

  subgraph AGENTS["Connected agents: Claude Code, Codex, Cursor"]
    direction LR
    A1["Agent 1"]
    A2["Agent 2"]
    A3["Agent 3"]
  end

  subgraph DOORS["Three cheap ways in"]
    direction LR
    HOOK["Session-start hook<br/>5-line brief, no tool call"]
    MCPA["keel2 mcp --agent<br/>12 tools, answers capped"]
    CLI["keel2 in the shell<br/>no tool definitions"]
  end

  subgraph KEEL["keel"]
    subgraph KIT["Agent toolkit: asked, never pushed"]
      direction LR
      KNOW["Brief and howto<br/>from configs, CI, git history<br/>no model"]
      MEM["Shared memory<br/>facts with checked citations"]
      WHO["Who and claims<br/>from sessions and git"]
      TEST["Tests<br/>run once, result shared"]
      MERGE["Merge radar<br/>and land in order"]
    end
    LIGHT["Light path<br/>own worktree, no gate"]
    FLOWS["Flows with gates<br/>for work the person wants to see"]
    INBOX["Inbox<br/>one OK can cover many calls"]
    METER["Meter<br/>calls, answer size, waits"]
  end

  subgraph STORE["keel's own store, one per project"]
    DB[("keel.db<br/>flows, facts, claims, sessions,<br/>test results, grants, meter")]
    FILES["config, rules, specs, knowledge"]
    TREES["worktrees"]
  end

  subgraph PROJECT["The user's project folder"]
    CODE["code and .git only<br/>keel writes no file here"]
  end

  A1 --> DOORS
  A2 --> DOORS
  A3 --> DOORS
  DOORS --> KIT
  AGENTS -.->|"choose"| LIGHT
  AGENTS -.->|"or"| FLOWS
  PERSON --> FLOWS
  PERSON -.->|"approves"| INBOX
  FLOWS --> INBOX
  KIT --> DB
  KIT -->|"reads only"| CODE
  LIGHT --> TREES
  FLOWS --> TREES
  FLOWS --> FILES
  TREES -->|"branches and commits"| CODE
  METER --> DB
  KIT --> METER

  classDef new fill:#e8f6ee,stroke:#1e8449,color:#145a32
  class HOOK,MCPA,CLI,KNOW,MEM,WHO,TEST,MERGE,LIGHT,METER,TREES new
  classDef project fill:#fff8e1,stroke:#b7950b
  class CODE project
```

Export: [SVG](img/keel-proposal/system-after.svg) · [PNG](img/keel-proposal/system-after.png)

What changed against the picture in 1.1 (green = new):

| Before | After |
|---|---|
| One way in: 131 tools | Three cheap ways in: a brief by hook, 12 tools, or the shell |
| A connected agent can only run the `harness` flow | The agent chooses: the light path (no gate) or a flow (gates) |
| keel knows the project but only its own flows use it | The toolkit answers any connected agent |
| Agents cannot see each other | Who works on what, read from sessions and git; claims that expire |
| Every agent runs the tests | A result for the same code is shared |
| Three branches meet at the end | Conflicts are told early; `keel_land` merges in order and tests the result |
| One Inbox card per call | One OK can cover a kind of call for a flow or an hour |
| `.keel/` and worktrees inside the project | Nothing in the project; keel's own store |
| No numbers | A meter on every tool, and a race that runs every week |

The three servers (api-py, the Kotlin api, the engine) and the plugins stay as they are. The toolkit is new code in
the engine and the api, not a new server.

### 4.2 The same job, after

This is the target, not a measurement.

<!-- diagram: job-after -->
```mermaid
sequenceDiagram
  autonumber
  participant P as Person
  participant A as Agents 1, 2, 3
  participant K as keel
  participant R as Project repo

  Note over A,K: Session start: the hook gives each agent the 5-line brief. No tool call.
  A->>K: keel_workspace(task) (3 times)
  K->>R: 3 branches, 3 worktrees in keel's own folder
  K-->>A: your worktree, the rules file, the test command, who else works on what
  A->>R: read docs/CONVENTIONS.md, write code and tests
  A->>K: keel_test(scope=changed)
  K-->>A: 12 passed (one line)
  K-->>A: note: all 3 branches add to CHANGELOG.md, they will conflict
  A->>K: keel_howto(topic=commit)
  K-->>A: the project's commit form, with its source
  A->>R: each agent commits in its own worktree
  A->>K: keel_land(3 branches)
  K->>R: merge in order into a new branch, keep all 3 CHANGELOG lines
  K->>R: run the tests on the result
  K-->>A: merged 3 of 3, tests pass, nothing pushed
  K-->>P: Agents panel shows the landed branch
  Note over P,R: Target: under 25 s and under 60k tokens, 4 of 4 hidden checks
```

Export: [SVG](img/keel-proposal/job-after.svg) · [PNG](img/keel-proposal/job-after.png)

What is gone: the session protocol text, the wrong work orders, two gates per task, the 90-second waits, and the
merge conflict at the end. What stays from keel's strengths: one worktree per agent, tests run with keel's command,
and a combined result that was tested before anyone sees it. If the person wants to approve each task, the same
job runs as three flows; after the base fixes that path should take seconds plus the person's own time.

### 4.3 Where the data lives

<!-- diagram: data-before-after -->
```mermaid
%%{init: {"flowchart": {"wrappingWidth": 320}}}%%
flowchart LR
  subgraph BEFORE["Before"]
    direction TB
    subgraph BP["In the project folder"]
      B1[".keel: config, rules, specs,<br/>knowledge pages, state"]
      B2[".keel/worktrees"]
      B3["lines in .git/info/exclude"]
    end
    subgraph BK["In keel's data folder"]
      B4[("keel.db: flows, approvals,<br/>facts, tasks, review")]
      B5[("checkpoints.db: flow state")]
      B6["code graph index"]
    end
    subgraph BM["Only in memory"]
      B7["connected sessions"]
      B8["waiting questions"]
    end
  end

  subgraph AFTER["After"]
    direction TB
    subgraph AP["In the project folder"]
      A0["nothing from keel<br/>(export only if the person asks)"]
    end
    subgraph AK["In keel's own store, per project"]
      A1[("keel.db, as before, plus:<br/>agent sessions, claims,<br/>test results, grants,<br/>handoffs, meter")]
      A2["config, rules, specs,<br/>knowledge pages"]
      A3["worktrees, test logs"]
      A4[("checkpoints.db, index")]
    end
    subgraph AG["In .git, as git's own work"]
      A5["branches and commits of the work,<br/>worktree metadata"]
    end
  end

  BEFORE ==> AFTER

  classDef project fill:#fff8e1,stroke:#b7950b
  class B1,B2,B3 project
  classDef new fill:#e8f6ee,stroke:#1e8449,color:#145a32
  class A0,A1,A3 new
```

Export: [SVG](img/keel-proposal/data-before-after.svg) · [PNG](img/keel-proposal/data-before-after.png)

New rows in `keel.db` (all per project, none in the repository):

| Table | Holds | Used by |
|---|---|---|
| `agent_sessions` | a handle keel gives each connected agent, client name, worktree, branch, first and last seen | 7, 12, 23; fixes #219 for good |
| `claims` | paths, reason, who, until when | 13 |
| `facts` (exists) | adds: citation, written by, kind, last checked, replaced by, runs out at | 14, 19 |
| `test_runs` | command, content hash, result, short summary, path of the full log | 10, 11 |
| `handoffs` | branch or task, done, next, open | 18 |
| `grants` | the person's OKs for a kind of call | 17 (from #250) |
| `tool_calls` | tool, client, bytes out, duration, seconds waiting | 1 |

Sessions and waiting questions move from the engine's memory into the store, so a restart of keel does not lose
who is connected.

Every `keel_howto` and `keel_brief` answer is still computed on each call and stored nowhere, as the toolkit plan
says [K2].

## 5. The month, week by week

The chat's plan was: W1 fix the base and no `.keel` folder; W2 the small tool set; W3 `keel_howto`, `keel_brief`,
`keel_test`; W4 shared memory, `keel_who`, `keel_claim`, and the race again. This keeps that order and changes four
things: the meter and the race come first; the brief moves to week 2 because it is cheap and cuts fixed cost; the
light path is added in week 3; merge radar and landing join week 4.

<!-- diagram: roadmap -->
```mermaid
gantt
  title keel, four weeks (30 Nov to 25 Dec 2026)
  dateFormat YYYY-MM-DD
  axisFormat %d %b
  excludes weekends

  section Week 1: base
  Fix 217, 218, 219                     :w1a, 2026-11-30, 3d
  No wasted Inbox cards (250 part A)    :w1b, 2026-11-30, 4d
  No .keel folder, phase 1 (246)        :w1c, 2026-11-30, 5d
  Meter and race harness (1)            :w1d, 2026-11-30, 4d
  Race baseline, 5 runs per arm         :milestone, m1, 2026-12-04, 0d

  section Week 2: cheap
  Agent tool set and hygiene (2, 3)     :w2a, 2026-12-07, 3d
  Slim work orders (4)                  :w2b, 2026-12-07, 2d
  Brief by hook (5)                     :w2c, 2026-12-08, 3d
  Shell path (6)                        :w2d, 2026-12-09, 2d
  One OK for many (17)                  :w2e, 2026-12-07, 5d
  Race run 2 (fixed cost)                :milestone, m2, 2026-12-11, 0d

  section Week 3: knows the project
  keel_howto commit, then topics (8, 9) :w3a, 2026-12-14, 5d
  keel_test and shared results (10, 11) :w3b, 2026-12-14, 5d
  Light path (7)                        :w3c, 2026-12-14, 4d
  Race run 3 (light path)                :milestone, m3, 2026-12-18, 0d

  section Week 4: together
  keel_who and claims (12, 13)          :w4a, 2026-12-21, 3d
  Shared memory (14)                    :w4b, 2026-12-21, 4d
  Merge radar and land (15, 16)         :w4c, 2026-12-21, 4d
  The race must flip                   :milestone, m4, 2026-12-25, 0d
```

Export: [SVG](img/keel-proposal/roadmap.svg) · [PNG](img/keel-proposal/roadmap.png)

| Week | Build | Done when (measured) |
|---|---|---|
| **1** · 30 Nov to 4 Dec · the base | #217, #218, #219 · #250 part A · #246 phase 1 · feature 1 | The race of [K1] is run again on the fixed keel, 5 runs per arm, with keel connected the real way so its tool list is counted. Expected: the flow path falls from 7.6 minutes to under 1 minute with the person bot. This is the baseline for the month. |
| **2** · 7 to 11 Dec · cheap | features 2, 3, 4, 5, 6, 17 | A keel agent's tokens in the flow path are within 2k of a plain agent's. The 12 definitions are under 12 KB. No agent opens `docs/CONVENTIONS.md` without being pointed to it by the brief. The shell arm and the MCP arm are compared. |
| **3** · 14 to 18 Dec · knows the project | features 8, 9, 10, 11, 7 | Three agents on the light path finish in about the time of three plain agents, with 4 of 4 hidden checks, and commit in the project's style on the first try. Each piece of code is tested once. |
| **4** · 21 to 25 Dec · together | features 12, 13, 14, 15, 16 | **The race flips** (section 6). No file is changed by two agents at once. The merged result passes 4 of 4 hidden checks without a hand-made merge. |
| After | features 18 to 27 | Chosen by what the race and the meter show. |

Notes:

- Week 4 ends on 25 December. If that week is short, the race moves to the first week of January; nothing else
  depends on the date.
- The effort letters are for one agent with a reviewer. The squad runs at most three agents at once, so each week
  holds about three parallel pieces. Weeks 1 and 3 are full.
- **What to cut first if a week runs over**, in this order: 16 (keep the radar, land later), 11, 9 (keep `commit`
  and `test`), 6, 13 (keep `keel_who`). **Do not cut:** the base, 1, 2 to 5, 7, 10, 12. Those carry the race.
- keel's feature freeze of 2026-11-18 still stands for everything that is not in this plan. Each week's list needs
  the maintainer's OK before the build starts, one spec at a time.
- A stop rule: if the run at the end of week 2 does not show the fixed cost under the target, week 3 starts with
  that, not with new tools.

## 6. The success test

**The claim to prove:** three agents with keel finish the same job sooner and with fewer tokens than three agents
without keel, and the result is at least as correct.

| Part | How |
|---|---|
| Job | The `shop` project and the three tasks of [K1], unchanged, with its four hidden checks. A second, larger repository (about 1,000 files or more, three separate issues) is added, because on a tiny project there is little to find and so little for keel to save. |
| Arms | **P1** one plain agent, three tasks · **P3** three plain agents, one folder · **K-light** three agents on keel's light path · **K-flow** three agents in keel flows, with the person bot answering gates · in week 2 also **K-shell** against **K-mcp** |
| Same for all | model, client and its version, machine and its limits, prompts. keel is set up in the client's own MCP settings, so tool names, instructions and loaded definitions are counted. |
| Runs | at least 5 per arm, the arms taken in turn, not one arm after the other. Report the median and the lowest and highest run. |
| Measured | wall time from the first prompt to the last commit or landed branch · tokens from the client's own usage report (input, output, cache read and cache write, each on its own) · tool calls · test runs · clicks by the person · hidden checks · files changed by two agents at once |
| **Pass** | K-light's median wall time **and** median tokens are both below P3's, in at least 4 of 5 paired runs · 4 of 4 hidden checks in every K-light run · no file changed by two agents at once |
| Also reported | K-flow against P3. A flow waits for a person, so it is not asked to beat 25 seconds; the target is under twice P3's time with the bot, and no more tokens than P3. |
| Honest line | P1 (17 s, 20k tokens) is expected to stay the cheapest on three tiny tasks. Nothing found in the research says parallel agents win on small work. The report must say so, and must say where keel was worse. |

Where the win has to come from, per agent (our estimate, to be replaced by the week 1 numbers):

| Part | Today | Target |
|---|---|---|
| Tool definitions | about 28k tokens if a client loads all 131 [K1]; not counted in the first race | under 3k if all 12 load; a few hundred where tools load on demand; none on the shell path |
| Fixed text of the session and the work order | 6k to 12k tokens over a plain agent [K1] | under 1k in a flow; none on the light path |
| Finding the rules | one search and one file read per agent | the brief (about 150 tokens) names the file |
| Test output | the whole output, once per agent and once by keel | one line when green; the same code tested once |
| Other agents in the way | 17 tool calls where 12 would do in the plain run [K1] | none: own worktrees, landing in order |
| Waiting | minutes (#217, #218), then gates | none on the light path |

The last two rows are where keel can beat plain agents, not only match them. The first four only remove what keel
itself added.

## 7. Risks

| Risk | What we do about it |
|---|---|
| **The token margin on tiny tasks is thin.** A plain agent uses about 20k tokens, most of it its own system prompt. keel can save only what is wasted. | Measure on two sizes of project. If the tiny one ends level on tokens and ahead on time and checks, say exactly that. |
| **A tool that makes agents cheaper and worse** [R48]. | Hidden checks in every run. keel's tools add to grep and read. `keel_about` and the `where` topic only behind a measurement. |
| **Wrong, old or planted facts** in the shared memory [R29] [R31]. | The rules of section 3.5. Memory is the last item of week 4 and can ship off by default. |
| **Claims that nobody keeps.** | Read from git first; claims expire; count unused claims and drop the tool if they are most of them. |
| **The light path has no gates and no rule guard.** | It is the agent's own process, as without keel. Said plainly in the docs and in the tool's answer. A per-project switch. Flows stay for work that needs approval. The maintainer decides the default. |
| **Hooks touch the person's client settings.** | Print by default, install only when asked, never in the project folder. |
| **Clients differ**: Codex stops a tool call after 60 s by default, Copilot's cloud agent takes tools only, Cursor's hook is fire-and-forget [R39] [R41] [R42]. | Build for the lowest common: fast answers, short results, no push. Test with at least two clients. |
| **Too much for four weeks**; #246 alone touches hundreds of places. | The cut order in section 5. #246 ships in phases; week 1 needs only phase 1. |
| **The MCP specification is moving** (2026-07-28 removes sessions) [R33]. | Agent identity is a handle keel gives out as a plain argument. Nothing new uses sampling, roots or logging. |
| **Noise in the measurements** [R56] [R57]. | Five runs per arm, arms taken in turn, same machine limits; small differences are not reported as wins. |
| **Private text in handoffs, facts and logs.** | Everything stays in keel's local store; values that look like secrets are refused; nothing is exported unless the person asks. |
| **More features against the goal of a stable core.** | Every feature has a number to watch in the meter. A tool nobody calls after a month is removed. |

## 8. Not found, guesses, and what was not checked

- **No independent measurement** of several coding agents sharing one tool, of shared test results, of a conflict
  warning before work starts, or of "tried and failed" memory. The gains here are our inference.
- **The `grite` paper** [R7] reports the largest gain (re-done work from 78% to 0%). It is one author's own tool; we
  read the abstract only. Do not quote it as proof.
- **Not confirmed:** whether Cursor still limits the number of MCP tools; which clients support the MCP Tasks and
  Skills extensions; whether Codex, Cursor, Copilot and Gemini load skills from MCP servers; how Cursor and Codex
  treat server `instructions` beyond what their docs say.
- **Not opened or search summaries only:** OpenHands, Amp, Jules and Devin's multi-agent docs (one page returned
  404); Windsurf's memory page; Serena's memory details; GitHub's dynamic toolsets page (404); the "Agent Trace"
  repository (404); prices of prompt-cache reads and writes.
- **Vendor numbers** (Cursor's semantic search, Augment's context engine, the memory benchmarks) are quoted as
  vendor numbers. The public CLI-against-MCP tests are one task on one server each [R45] [R46]; a third test found
  "more or less the same" for another tool [R47].
- **Guesses in this report:** every target number in sections 5 and 6; the 4,000-character cap (taken from one
  public test); 90 days for facts and 30 for traps; the rank order of the features.
- **Design points to check in the specs:** that `git merge-tree` can keep its temporary objects out of the
  project's `.git`; how a content hash for uncommitted changes is made without writing into the repository; what a
  hook install changes in each client.
- **How the web was read:** most pages through a fetch tool that summarises. For the 27 sources marked ✔✔ a second
  reader opened the page again and compared the quotes; for five of those the raw page text was read. Spot-check any
  other quote before it is used in public text.
- **keel's own state** was read from the code of 0.41.1 on 2026-11-26. `main` moves several times a day; #246 and
  #250 are plans on their branches and not in `main`.

## Sources

Web sources were read on 2026-11-26. "older" = more than a year old on that day. **✔✔** = two readers opened the
page and the quotes matched. **✔** = opened once, through a fetch tool that summarises. Sources seen only as a
search result are not used for any claim above; they are named in section 8.

keel's own:

- [K1] keel 0.40.0 MCP test report, `docs/test-report-mcp-0.40.0.md` (2026-11-24)
- [K2] keel as the agent's toolkit, `docs/research/agent-toolkit-plan.md` (2026-11-25, revised 2026-11-26). Its
  sources [S1] to [S32] are not repeated here.
- The squad's keel experiment log (2026-11-26) is an internal note, not in this repository. Its results are quoted in
  sections 1.3 and 3.4 without a link.

Several agents on one repository:

- [R1] Claude Code docs, agent teams: https://code.claude.com/docs/en/agent-teams (undated) ✔
- [R2] Claude Code docs, manage costs: https://code.claude.com/docs/en/costs (undated) ✔✔
- [R3] Cursor, "Towards self-driving codebases" (Wilson Lin): https://cursor.com/blog/self-driving-codebases (2026-02-05) ✔✔
- [R4] Cognition, "Multi-Agents: What's Actually Working" (Walden Yan): https://cognition.com/blog/multi-agents-working (2026-04-22) ✔✔
- [R5] Anthropic, "How we built our multi-agent research system":
  https://www.anthropic.com/engineering/multi-agent-research-system (2025-06-13, older) ✔
- [R6] Khatua et al., "CooperBench: Why Coding Agents Cannot be Your Teammates Yet": https://arxiv.org/abs/2601.13295 (2026-01-19) ✔✔
- [R7] Sarkar, "Before the Pull Request: Mining Multi-Agent Coordination": https://arxiv.org/abs/2606.19616
  (2026-06-17; abstract only; the author's own tool) ✔✔
- [R8] Xu, Subramanian, Karthik, "AI Agent Pull Requests on GitHub: Frequency, Structure, and Merge Conflict Rates":
  https://arxiv.org/abs/2607.04697 (2026-07-06; abstract) ✔✔
- [R9] Ogenrwot, Businge, "AgenticFlict: A Large-Scale Dataset of Merge Conflicts in AI Coding Agent Pull Requests
  on GitHub": https://arxiv.org/abs/2604.03551 (2026-04-04; abstract) ✔✔
- [R10] Long et al., "ParallelPilot: Supporting Coordination and Monitoring in Parallel AI Coding":
  https://arxiv.org/abs/2609.33113 (2026-09-27; abstract) ✔✔
- [R11] Beads: https://github.com/steveyegge/beads (README, undated) ✔✔
- [R12] mcp_agent_mail: https://github.com/Dicklesworthstone/mcp_agent_mail (README, undated) ✔
- [R13] Gas Town: https://github.com/gastownhall/gastown (README, undated) ✔
- [R14] GitHub, "How to orchestrate agents using mission control":
  https://github.blog/ai-and-ml/github-copilot/how-to-orchestrate-agents-using-mission-control/ (2025-12-01) ✔
- [R15] GitHub docs, managing a merge queue:
  https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue (undated) ✔
- [R16] Git docs, gitattributes (`merge=union`): https://git-scm.com/docs/gitattributes (undated) ✔
- [R17] towncrier docs: https://towncrier.readthedocs.io/en/stable/ (undated) ✔

Memory:

- [R18] GitHub docs, Copilot Memory: https://docs.github.com/en/copilot/concepts/agents/copilot-memory (undated) ✔✔
- [R19] GitHub, "Building an agentic memory system for GitHub Copilot":
  https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/
  (2026-01-15, updated 2026-11-12) ✔✔
- [R20] Claude Code docs, memory: https://code.claude.com/docs/en/memory (undated) ✔
- [R21] Codex docs, memories: https://learn.chatgpt.com/docs/customization/memories (undated) ✔
- [R22] Devin docs, Knowledge: https://docs.devin.ai/product-guides/knowledge (undated) ✔
- [R23] mem0 docs, memory types: https://docs.mem0.ai/core-concepts/memory-types (undated) ✔
- [R24] Letta docs, memory blocks: https://docs.letta.com/guides/core-concepts/memory/memory-blocks (undated) ✔
- [R25] Graphiti: https://github.com/getzep/graphiti (README, undated) ✔
- [R26] Zep, "Lies, Damn Lies, & Statistics: Is Mem0 Really SOTA in Agent Memory?":
  https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ (2025-05-06, older; a vendor about its rival) ✔
- [R27] Letta, "Benchmarking AI agent memory": https://www.letta.com/blog/benchmarking-ai-agent-memory (2025-08-12, older; vendor) ✔
- [R28] MCP reference servers, memory: https://github.com/modelcontextprotocol/servers/tree/main/src/memory (README, undated) ✔
- [R29] Chen et al., "MemDev-Bench: Does Memory Help Code Agents? Benchmarking Memory Systems over Real Repository
  Histories": https://arxiv.org/abs/2610.27524 (2026-10-20; abstract) ✔✔
- [R30] Chen et al., "REM: What to Store, When to Recall, What to Forget in Experience Memory for Coding Agents":
  https://arxiv.org/abs/2610.27377 (2026-10-19; abstract) ✔✔
- [R31] Dash et al., "From Untrusted Input to Trusted Memory: A Systematic Study of Memory Poisoning Attacks in LLM
  Agents": https://arxiv.org/abs/2606.04329 (2026-06-03; abstract) ✔✔
- [R32] Cisco, "Identifying and remediating a persistent memory compromise in Claude Code":
  https://blogs.cisco.com/ai/identifying-and-remediating-a-persistent-memory-compromise-in-claude-code (2026-04-01) ✔

MCP and token cost:

- [R33] MCP specification 2026-07-28, key changes: https://modelcontextprotocol.io/specification/2026-07-28/changelog ✔✔
- [R34] MCP extensions, Tasks: https://modelcontextprotocol.io/extensions/tasks/overview (undated) ✔✔
- [R35] MCP extensions, Skills: https://modelcontextprotocol.io/extensions/skills/overview (undated) ✔
- [R36] Claude Code docs, MCP: https://code.claude.com/docs/en/mcp (undated) ✔✔
- [R37] Claude Code docs, hooks: https://code.claude.com/docs/en/hooks (undated) ✔✔
- [R38] Claude Code docs, channels reference: https://code.claude.com/docs/en/channels-reference (undated; research preview) ✔
- [R39] Codex docs, MCP: https://learn.chatgpt.com/docs/extend/mcp (undated) ✔✔
- [R40] Codex docs, hooks: https://learn.chatgpt.com/docs/hooks (undated) ✔
- [R41] GitHub docs, Copilot hooks configuration: https://docs.github.com/en/copilot/reference/hooks-configuration (undated) ✔
- [R42] Cursor docs, hooks: https://cursor.com/docs/hooks (undated) ✔
- [R43] Claude API docs, tool search tool: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool (undated) ✔✔
- [R44] Claude API docs, tool use with prompt caching:
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching (undated) ✔
- [R45] OpenRouter, "MCP vs CLI vs Skills": https://openrouter.ai/blog/insights/mcp-vs-cli-vs-skills/ (2026-11-11;
  one task, one server) ✔✔
- [R46] Scalekit, "MCP vs CLI": https://www.scalekit.com/blog/mcp-vs-cli-use (2026-03-11; a vendor) ✔
- [R47] Checkly, "MCP vs CLI token efficiency": https://www.checklyhq.com/blog/mcp-vs-cli-token-efficiency/ (2026-07-30) ✔

Project knowledge and measuring:

- [R48] Newell, "mcpbr: Benchmarking Model Context Protocol Servers on Software Engineering Tasks":
  https://zenodo.org/records/18627369 (2026-02-12; a preprint by one author) ✔✔
- [R49] Cursor, semantic search: https://cursor.com/blog/semsearch (2025-11-06, older by three weeks; vendor) ✔
- [R50] Gloaguen et al., "Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?":
  https://arxiv.org/abs/2602.11988 (v1 2026-02-12, v3 2026-09-29) ✔✔ (the full text was checked for [K2])
- [R51] Han et al., "SWE-Skills-Bench: Do Agent Skills Actually Help in Real-World Software Engineering?":
  https://arxiv.org/abs/2603.15401 (v1 2026-03-16, v2 2026-10-27; abstract) ✔✔
- [R52] GitHub, "How we make AI coding more cost efficient without sacrificing task quality":
  https://github.blog/ai-and-ml/github-copilot/how-we-make-ai-coding-more-cost-efficient-without-sacrificing-task-quality/ (2026-09-02) ✔✔
- [R53] Cursor, Bugbot learning: https://cursor.com/blog/bugbot-learning (2026-04-08; vendor) ✔
- [R54] CodeRabbit docs, learnings: https://docs.coderabbit.ai/knowledge-base/learnings (undated) ✔
- [R55] Entire docs, checkpoints: https://docs.entire.com/cli/checkpoints (undated) ✔
- [R56] Bjarnason, Silva, Monperrus, "On Randomness in Agentic Evals": https://arxiv.org/abs/2602.07150
  (2026-02-06, v3 2026-03-25; abstract) ✔✔
- [R57] Anthropic, "Quantifying infrastructure noise in agentic coding evals":
  https://www.anthropic.com/engineering/infrastructure-noise (2026-02-05) ✔✔
