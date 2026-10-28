# Field report: a clarify flow on a library project (0.18.62, October 2026)

A user's report from one real session, with every name made neutral. The project: a library that must accept a new
webhook payload from an upstream service (another project registered in the same keel). The work went through the
clarify flow, with a planned hand-over to the feature flow. keel was driven through `keel2 mcp` (read, then
`--write`), not the web.

```
 clarify: read → questions → assemble → write_doc (spec check) → result gate
                                 ▲            │ SPEC-CHECK: fix
                                 └────────────┘ sends the outputs back once
 result gate: reject + note ──► assemble (with the note) ──► spec check fails ──► assemble (note lost) ──► gate
```

## Bugs

| # | what happened | status |
|---|---|---|
| 1 | **The spec check's retry dropped the reject note.** The first assemble after a reject used the note; the spec check then sent the outputs back with only its own findings as the feedback; the retry wrote the spec again from the request and the answers, passed the check, and the gate showed the stale spec. | **fixed (0.18.64)**: every reject note is kept (`data.clarify.notes`), shown in the assemble prompt each time, and added to the spec check's send-back (`clarify_flow.notes_of`). Still open: give the retry the last draft and ask for a patch, not a new answer. |
| 2 | **The gate and the hand-over can differ from the file.** The user edited `specs/clarify/<doc>.md`; the gate text and the web kept the stored copy. Which copy seeds the feature flow is not said. | open: read the file again at the gate (or lock it while the gate waits), and say in the gate which copy the hand-over uses. |
| 3 | **The spec check does not check citations.** The spec cited `Controller.php:235-245` in a 38-line file and passed. | **fixed (0.18.64)**: the spec check resolves every `path:line` (also `path:a-b`, `path#La`) that names a project file; a missing file or a line past the end is a fix finding, so the spec is sent back once. One parser with `keel memory check` (`runtime/citations.py`). |
| 4 | **The flow read stale code.** It branched from a local base branch that was behind a second remote; the spec said a test harness "does not exist" and that the code had a parallel namespace, both wrong on the newer base. A fetch from the first remote timed out and nobody was told. | open: before a flow starts, say how far the base is behind each remote; say when a fetch fails; offer to start from the newer remote branch (`keel_start_flow` with a base). |
| 5 | **The agent could not read the upstream project.** Its read of the other registered project was refused ("no interactive user response was available"); after the user said "check the project we have connected" it still could not. The spec kept open questions the upstream contract file answered. | open: flows may read other registered projects, read-only (a tool to read one project's file from another); at least a clear "cannot read project X: <why>" error. |
| 6 | **No sanctioned way to bring a flow branch up to its base.** `git reset --hard` and a plain `git commit` are refused during a flow (right), but nothing else does it. | open: a keel command to merge or rebase the flow branch onto a base, the conflict resolution committed by keel. |
| 7 | **Registering a project needs a terminal and restarts keel.** `keel_add_project` said to run `keel2 add` first; `keel2 add` restarted keel and the MCP connection was lost. | open: warn before a restart; the MCP client reconnects by itself. |

Found later in the same project (the feature flow after clarify):

| # | what happened | status |
|---|---|---|
| 8 | **The feature flow's spec dropped part of the request** (a risk the request named); the user added it back by hand. Same root as bug 1: a seed or a retry writes the spec again instead of patching it. | open |
| 9 | **A leftover keel v1 state file blocked commits.** `.keel/state.json` of an old v1 flow (phase full-review) blocked `git commit` although the v2 flow had stopped; only `keel state close` cleared it. The refusal did not say which flow blocked it. | open: v2 finds leftover v1 state and offers to archive it; a blocked commit names the flow (v1 or v2) that blocks it. |
| 10 | **The clarify-to-feature hand-over was not used**: the gate showed the stale copy, so the user stopped clarify and pasted the spec into a new feature request; keel's history does not link the two flows. | open: the hand-over takes the file on disk and records the link (bug 2). |
| 11 | **Spec numbers collide**: a new spec got 001 while `001-…` and `002-…` already existed. | open |
| 12 | **Tool feedback**: `keel_answer` with reject + choice edit printed "(reject)" though it ran as an edit; `keel_start_flow` returned nothing within the client's timeout (the user polled); a wrong model or effort said only "no effort setting" and nothing lists the valid ones; "planning" maps to no agent name (it is the explorer, and changing the explorer project-wide also changes clarify and contract). | open |
| 13 | **Confirmations**: `keel_stop_flow` and `keel_agent_model` ask for a confirmation a client without forms cannot show; the user ran `keel2 mcp --admin --yes`, which skips every confirmation. | open: a confirmation a plain client can give (a second call with a token keel returned). |

Smaller ones:

- **Default project**: `keel_flows` without a project answered for another, unrelated project. It should refuse or ask
  when it cannot tell which project is meant.
- **Dirty tree**: the start's 409 listed keel's own `.keel/docs/` and `.keel/specs/` files. keel's own output should
  not block its own flows.
- **Knowledge on a branch**: the knowledge lives in `.keel/docs/knowledge/`; on a feature branch `.gitignore` made
  `keel memory check` report all five sections missing.
- **Time zones**: the timeline shows UTC; files and the shell show local time. Show both, or local time with the zone.

## MCP tools the user missed

| tool | for |
|---|---|
| `keel_flow_document` | read and write the clarify or spec document of a flow |
| `keel_reject_with_document` | reject with a replacement draft (fixes bug 2 from the other side) |
| `keel_branch_sync` | merge or rebase the flow branch onto a base (bug 6) |
| `keel_fetch_remotes` | fetch, with one result per remote (bug 4) |
| `keel_read_project_file` | read a file of another registered project, read-only (bug 5) |
| `keel_spec_check`, `keel_citations_check` | run the spec check or the citation check on a path (bug 3); **added (0.18.64)** |
| `keel_flow_diff` | what a retry changed in a step's output (bug 1 would have shown at once) |
| `keel_agent_prompt` | the prompt a step really got |
| `keel_reconnect` / a health check | after keel restarts (bug 7) |
| `keel_start_flow` with `base` | start from a given branch (bug 4) |
| `keel_models` | the models and efforts keel can use, per provider (bug 12) |
| `keel_agent_model` with `thread_id` and `step` | a model for one flow or one step, not the whole project (bug 12) |
| `keel_state` | keel v1's leftover state: show and close (bug 9) |
| `keel_handover` | hand a flow's document over to the next flow, linked (bug 10) |

## What the session taught

- **Order**: check the upstream project and the newest remote before the spec is written. Done after, it cost three
  reject rounds.
- **Contract against data**: the upstream contract said an id field is optional while its database column is NOT NULL.
  Checking a contract against the migrations found a real upstream bug. The clarify flow could ask for that check
  when a request touches an external contract.
- **Reading the source, not only the contract** (the upstream code and its migrations) is what found the real gap.
- **Re-reading the document after every gate** caught lost corrections three times.
- **Editing the file by hand** was faster than another reject once a retry had lost the note, but it made bug 2.
