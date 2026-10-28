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
| 1 | **The spec check's retry dropped the reject note.** The first assemble after a reject used the note; the spec check then sent the outputs back with only its own findings as the feedback; the retry wrote the spec again from the request and the answers, passed the check, and the gate showed the stale spec. | **fixed (0.18.64)**: every reject note is kept (`data.clarify.notes`), shown in the assemble prompt each time, and added to the spec check's send-back (`clarify_flow.notes_of`). Also fixed (0.18.64): every send-back gives the assemble step its last draft (`data.clarify.draft`, the file as it is then) and asks for a patch, not a new answer. |
| 2 | **The gate and the hand-over can differ from the file.** The user edited `specs/clarify/<doc>.md`; the gate text and the web kept the stored copy. Which copy seeds the feature flow is not said. | **fixed (0.18.64)**: the result gate builds its text from the file each time it is shown, says "You edited <path> at <time>; the hand-over uses this file", and the hand-over reads the request, the criteria and the prompt from the file at the choice (`clarify_hand`). |
| 3 | **The spec check does not check citations.** The spec cited `Controller.php:235-245` in a 38-line file and passed. | open: resolve every `path:line` and fail on a missing file or a line out of range (`keel memory check` does this for the knowledge base already). |
| 4 | **The flow read stale code.** It branched from a local base branch that was behind a second remote; the spec said a test harness "does not exist" and that the code had a parallel namespace, both wrong on the newer base. A fetch from the first remote timed out and nobody was told. | open: before a flow starts, say how far the base is behind each remote; say when a fetch fails; offer to start from the newer remote branch (`keel_start_flow` with a base). |
| 5 | **The agent could not read the upstream project.** Its read of the other registered project was refused ("no interactive user response was available"); after the user said "check the project we have connected" it still could not. The spec kept open questions the upstream contract file answered. | open: flows may read other registered projects, read-only (a tool to read one project's file from another); at least a clear "cannot read project X: <why>" error. |
| 6 | **No sanctioned way to bring a flow branch up to its base.** `git reset --hard` and a plain `git commit` are refused during a flow (right), but nothing else does it. | open: a keel command to merge or rebase the flow branch onto a base, the conflict resolution committed by keel. |
| 7 | **Registering a project needs a terminal and restarts keel.** `keel_add_project` said to run `keel2 add` first; `keel2 add` restarted keel and the MCP connection was lost. | open: warn before a restart; the MCP client reconnects by itself. |

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
| `keel_flow_document` | read and write the clarify or spec document of a flow (**added 0.18.64**) |
| `keel_reject_with_document` | reject with a replacement draft (fixes bug 2 from the other side; **added 0.18.64**, with `keel_handover`) |
| `keel_branch_sync` | merge or rebase the flow branch onto a base (bug 6) |
| `keel_fetch_remotes` | fetch, with one result per remote (bug 4) |
| `keel_read_project_file` | read a file of another registered project, read-only (bug 5) |
| `keel_spec_check`, `keel_citations_check` | run the spec check or the citation check on a path (bug 3) |
| `keel_flow_diff` | what a retry changed in a step's output (bug 1 would have shown at once) |
| `keel_agent_prompt` | the prompt a step really got |
| `keel_reconnect` / a health check | after keel restarts (bug 7) |
| `keel_start_flow` with `base` | start from a given branch (bug 4) |

## What the session taught

- **Order**: check the upstream project and the newest remote before the spec is written. Done after, it cost three
  reject rounds.
- **Contract against data**: the upstream contract said an id field is optional while its database column is NOT NULL.
  Checking a contract against the migrations found a real upstream bug. The clarify flow could ask for that check
  when a request touches an external contract.
- **Editing the file by hand** was faster than another reject once a retry had lost the note, but it made bug 2.
