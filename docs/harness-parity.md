# keel's own agent harness: feature checklist (#101)

keel runs every real model in its own harness (`engine/keel_engine/harness/`), on the model's API with an API key. No
coding-agent CLI runs any more. This page lists what a coding agent like Claude Code does, taken from its public
documentation (features only: nothing is copied or decompiled), and where keel's harness stands on each.

Status: **done** (built and tested) · **partial** (works, with a gap named) · **batch B / C** (planned, in order) ·
**keel's way** (keel already has its own form of it) · **not here** (does not fit a harness inside keel, and why).

Tests: `engine/tests/test_harness.py`, `test_harness_parity.py`, `test_harness_providers.py`, `test_harness_flow.py`.
They run with a scripted model, no key and no network. Flow tests that need a model use `tests/scripted_model.py`.

## The agent loop

| Feature | Status | In keel |
|---|---|---|
| Streamed answers and thinking | done | `providers/anthropic.py`, `providers/openai_compat.py`; the flow shows them live |
| Tool calls, several per turn | done | read-only calls run side by side (`tools.run_calls`) |
| Tool input checked before it runs | done | JSON Schema check (`tools.check_input`); a bad input goes back to the model as an error |
| Retries on rate limits and server errors | done | the SDKs retry; the harness retries a whole turn too, honouring `retry-after` |
| Turn limit | done | the agent file's `maxTurns`, at most 150 (`KEEL_HARNESS_MAX_TURNS`) |
| Stops a loop of the same call | done | a note after 3 repeats, a stop after 6 |
| Refusal and max-tokens stops | done | reported to the flow in plain words |
| Usage and cost while it runs | done | `agent.usage` after every turn; the token cap warns while the step runs. 0.32: cache writes and a provider's compaction tokens are counted and priced; GitHub Copilot's own AI credits (from `copilot_usage.total_nano_aiu`, shown as "about") |
| Cache health | done (0.32) | each turn's `context:` line says new / written to the cache / read from it, a **cache break** (the cached part fell to under a tenth of the last call's), when the fixed start is below the model's cache minimum (Anthropic 512–4,096 by model, OpenAI 1,024), and a compaction's tokens |
| Prompt caching | done | Anthropic: cached system + automatic tail, 1-hour TTL for KeelBot chats (0.32), 5 minutes for flow steps; OpenAI: stable prefix and one `prompt_cache_key` per keel session (0.32); GitHub Copilot: one live session per chat, so the next message goes on where Copilot is (0.31) |
| Tools loaded on demand | done (0.31, cache-safe 0.32) | Task, Skill, WebFetch, NotebookEdit, background shells and every MCP/plugin tool load when needed. 0.32: Anthropic (4.5 and later, also on Vertex) and GitHub Copilot get every tool from the first request, the deferrable ones marked (`defer_loading` + Anthropic's BM25 tool search; Copilot `defer="auto"` + its own tool search), so the tool list never changes and the cache stays valid; an endpoint that refuses tool search gets every tool plainly. OpenAI-compatible providers keep the small base set (Read, Write, Edit, Glob, Grep, Bash, RunTests, TodoWrite) plus `LoadTools`, loaded tools joining the end of the list |
| What each turn sends | done (0.31) | a `context:` step per model call: system, tools, chat, new text and the provider's new / cached tokens |
| One output policy for every tool | done (0.32) | a result longer than its cap (about 10k tokens; a command, a page or a plugin tool 30k characters) keeps its start and its end, and the whole output is saved to a file the model reads with Read (offset / limit) or Grep; kept 7 days. A Read is cut instead (the file is there to read in parts) |
| Old tool results cleared | done (0.32) | as Anthropic's context editing and OpenCode's prune, for every provider: the newest 40k tokens of tool results (a KeelBot chat: 12k) and the last 3 stay; older ones become a one-line stub ("Read src/x.py (240 lines): run it again if you need it"), only in batches of at least 20k (chat: 6k) so the cached start changes seldom; kept in the session |
| Effort / thinking | done | Anthropic adaptive thinking + `output_config.effort`; OpenAI `reasoning_effort` |
| Fallback model on a refusal | done | Anthropic server-side fallback on the newest models |
| A step that fails does not end the flow | done (part 3) | the flow doctor (`flowdoctor/`): a busy, limited or unreachable provider is waited out, a conversation too long runs again in a new session, a stale git lock is removed; else the flow waits with what happened and what to do |
| Crash log and improvement report | done (part 3) | `$KEEL_DATA/doctor/crashes.jsonl`; Watch › Flow doctor, `GET /api/flow-doctor/report`, MCP `keel_flow_doctor` |

## Tools

| Tool | Status | Notes |
|---|---|---|
| Read (text, line ranges) | done | through keel's read rules (secrets, phases) |
| Read an image | done | PNG, JPEG, GIF, WebP go to the model as images |
| Read a PDF | done | `pdftotext` (poppler-utils in the image); `pages` picks pages |
| Read a notebook | done | cells with their outputs |
| Write, Edit (`replace_all`) | done | through keel's edit rules, per phase and unlock |
| NotebookEdit | done | replace, insert, delete a cell |
| Glob, Grep | done | ripgrep; Grep has `output_mode`, `-A/-B/-C`, `-i`, `multiline`, `type`, `head_limit` |
| Bash | done | keel's command rules first; the folder stays between calls (inside the project) |
| Bash in the background, BashOutput, KillShell | done | every background shell stops when the agent call ends |
| TodoWrite | done | kept in the session; open items are recalled every 10 turns |
| Task (helpers) with agent types | done | keel's agents as types; a helper writes only when its agent and the step may. A helper has its own conversation (none of the parent's) and answers in about 2k tokens (0.32: longer is cut, saved whole) |
| Skill | done | keel's skills + the project's `.claude/skills` and `.keel/skills`, loaded on demand |
| WebSearch | done | Anthropic's server tool; not on the OpenAI-compatible providers |
| WebFetch | partial | public pages only (no private network); the `prompt` is not yet run over the page by a small model |
| MCP tools | done | the step's MCP servers, judged by keel's MCP rules first |
| AskUserQuestion | batch B | through keel's approvals: a card in the Inbox / Flow, the answer goes back to the agent |
| Plan mode / ExitPlanMode | keel's way + batch B | keel's plan gate does this per flow; batch B lets an agent ask for it |
| MCP resources (list, read) | batch B | |
| RunTests | keel's way | keel's own tool: the project's test command under keel's rules |

## Context

| Feature | Status | In keel |
|---|---|---|
| CLAUDE.md / AGENTS.md at the start, with `@` imports | done | `context.project_notes`; imports go through the read rules |
| A folder's CLAUDE.md / AGENTS.md with its first Read | done | `context.FolderNotes` |
| Git state at the start | done | branch, changed files, last 5 commits |
| Environment (folder, date, platform) | done | |
| Note when a file changed since the agent read it | done | changes by the agent's own Write/Edit do not count |
| Compaction of a long conversation | done | one owner: Claude on the server, other models keel's own summary at 85% of the window (0.32; peers 80–95%). KeelBot chats: from 40k tokens, keeping about the last 16k tokens whole (from a question on). The summary uses a structured template (goal, instructions, discoveries, files, decisions, done, next), is asked with the conversation's own system and tools (it reads the cached start), and keel adds what it knows itself: the task list, the files changed, the files read last and the project's notes |
| Memory the person adds (`#` in Claude Code) | keel's way | keel's knowledge and agent memory |
| Output styles | not here | keel's agents have their own role text |

## Sessions

| Feature | Status | In keel |
|---|---|---|
| Sessions saved and continued | done | append-only JSONL in `$KEEL_DATA/harness/sessions`; a send-back or restart continues the same session |
| Same-model replay of thinking | done | an assistant turn goes back exactly as it came |
| Old sessions cleaned up | done | after 14 days |
| Checkpoints and rewind | keel's way + batch C | keel's step snapshots and git; batch C: rewind one agent call |

## Safety

| Feature | Status | In keel |
|---|---|---|
| Permission rules (allow / ask / deny) | keel's way | keel's rules, rule packs, phases and unlocks; refusals are `guard.refused` events |
| Permission modes | keel's way | keel's run modes and gates |
| Hooks (PreToolUse, PostToolUse, Stop, ...) | keel's way + batch C | keel checks every call itself; batch C: run a project's own hooks if it has them |
| Sandbox | keel's way | agents run as their own user, in keel's container |

## Extending

| Feature | Status | In keel |
|---|---|---|
| Skills | done | see Tools |
| Subagents (`.claude/agents`) | done | keel's agent files, add-ons' and plugins' |
| MCP servers | done | per project and step, through keel's gateway |
| Slash commands (`.claude/commands`) | batch C | as skills the agent can load |
| Plugins | keel's way | keel's plugins |

## Not here

The terminal UI, vim mode, the status line, IDE extensions and `/` menu commands belong to an interactive CLI. In keel the
web, the TUI and the IDE plugins show the harness's work instead.

## Providers

| keel name | Shown as | API | Key |
|---|---|---|---|
| claude | Anthropic | Anthropic Messages API | Anthropic API key |
| codex | OpenAI | OpenAI chat API | OpenAI API key |
| copilot | GitHub Copilot (your plan) | GitHub's Copilot SDK (`github-copilot-sdk`): keel's tools as the session's only tools | GitHub sign-in, or a gho_/ghu_/fine-grained token with Copilot Requests |
| vertex | Vertex AI | Claude via Vertex; Gemini via its OpenAI-compatible endpoint | Google credentials |
| local | a local model server | OpenAI-compatible (Ollama, LM Studio, vLLM, llama.cpp) | optional |
| fake | the fake model | none (keel's own) | none |

A subscription login (Claude Pro/Max, ChatGPT) is not used: those plans allow their login only in their own apps and
approved partners. GitHub Copilot is the exception: GitHub's Copilot SDK is made for apps that make Copilot requests for a
person on their own plan, so the copilot row runs on it (GitHub retired GitHub Models, the old copilot row, on 2026-07-30).
keel's harness keeps its own loop: Copilot's own tools are off, keel's tools are the session's custom tools, and each
call returns to keel's loop, where keel's rules run it (`harness/providers/copilot_sdk.py`). The models are the plan's own
(Settings › Models & keys › Test lists them); `auto` lets Copilot pick one. Part 2 removed the coding-agent apps (Claude Code, Codex, Copilot CLI, OpenCode) from the
image, their login pages and their plan windows; a model saved for one of them runs on its provider's API key.
