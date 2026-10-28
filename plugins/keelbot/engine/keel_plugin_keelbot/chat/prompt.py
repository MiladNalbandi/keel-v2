"""KeelBot's prompt: the mode texts, the flow block, images, the conversation so far and /compact's summary prompt (a part of helper.py)."""

from __future__ import annotations

from pathlib import Path

from keel_engine.runtime import agent_knowledge, plugins
from keel_engine import extensions

from .. import keelbot
from .sessions import AGENT, SUMMARY_HEAD, TITLE_ASK, fix_phase, get, mode_note, workdir

TRANSCRIPT_CHARS = 6000                  # the conversation so far, for the engines that cannot continue a session
COMPACT_WINDOW = 128_000                 # 0.18.19 the window assumed for /compact when the model's is unknown
COMPACT_SHARE = 0.5                      # of the window (as characters, about 4 a token) the chat to summarise may fill
MODE_TEXT = {
    "ask": ("Mode: Ask. Read only: you change nothing (no edits, no new files, no command that changes files or git). "
            "When the person asks for a change (edit a file, git checkout or switch a branch, install, build, commit), do "
            "not try it and do not try other ways around it: say in one line that Ask mode is read only, and give the "
            "exact command they asked for (for example `git checkout main`), so they can run it themselves. Then offer "
            "the ways forward as a keel-questions block: switch to Edit (recommended; give that option \"mode\": \"edit\" "
            "and a label that says what you will do, for example \"Switch to Edit and fix it\": picking it switches "
            "this chat to Edit and sends their answer, so the work starts at once, as `/mode edit` in keel TUI or the "
            "dashboard's mode menu do; it works in the project folder and every command that changes something waits for their OK), a side session (`/mode side`: its own copy on "
            "its own branch), or run the command themselves. Never offer a plain \"I will switch\" option without "
            "\"mode\": it switches nothing. When a message arrives in Edit after you offered it, do the work they "
            "asked for in that turn; do not ask them to send it again."),
    "fix": ("Mode: Fix at a gate. The flow waits for the person at a gate, and the person asked you to change something. "
            "Edit only what the request needs, with the smallest change; keel's guard refuses files the flow's phase "
            "({phase}) does not allow. Reading, searching and running the tests need no OK; any other command that changes "
            "something waits for the person's OK. Never commit: when the person presses Done, keel runs the checks and "
            "commits your change. End with one short line per file you changed."),
    "edit": ("Mode: Edit. You work in the project folder itself, with no flow: edit what the person asks, with the smallest "
             "change; keel's always-on rules still apply (no secrets, never write .git by hand, no applied migration). "
             "Reading, searching and running the tests need no OK; any other command that changes something waits for the "
             "person's OK, and keel shows them each command first. Git work on the person's branch is yours to do when they "
             "ask for it: git status, git stash and git stash pop, git checkout or git switch, git fetch and git pull, and "
             "git commit of the person's own changes when they choose that (you write the message, they OK the command). "
             "A command that throws work away (git checkout -- ., git restore, git reset --hard, git clean, git stash "
             "drop) only when the person picked exactly that, and say first what is lost. Never commit your own file "
             "changes and never push: when the person keeps your change, keel runs the checks and commits it. End with "
             "one short line per file you changed."),
    "side": ("Mode: Side session. You work in your own copy of the project: a git worktree on branch {branch}, not the "
             "main folder. Edit freely there, but only there (keel refuses a file outside it); nothing you do touches the "
             "main folder or a flow running in it. Reading, searching and running the tests need no OK; any other command "
             "that changes something (git stash, git pull, git checkout in this worktree) waits for the person's OK. Never "
             "commit your own changes: the person keeps your work (keel runs the checks and commits it on the branch), "
             "hands it over as a task or a flow, or throws it away. End with one short line per file you changed."),
}
# keel 0.18.18 when something fails (every mode). A person asked to check out main with uncommitted changes in the
# folder; `git checkout main` failed, and KeelBot ran it again on every "fix it" and ended with "I cannot". The way
# opencode does it (and now keel): read the error, say why in one line, offer the ways forward, do what the person picks.
FAILURES = ("A network error is not a result: a timeout, a TLS handshake timeout, a connection reset or refused, a DNS "
            "failure, or an HTTP 502, 503 or 504. Run that same call once more; only when it fails again treat it as "
            "the failure below.\n"
            "When a command or a tool fails, or keel refuses it: never run the same thing again unchanged (keel refuses an "
            "exact repeat while nothing in the folder changed). Read the error, and say in one line why it failed, quoting "
            "the error's own words. Then offer 2 to 4 ways forward as a keel-questions block, the safe one first and "
            "recommended, each with what it does and what it costs, and a way to leave it as it is; then stop and wait. "
            "Never end with only \"I cannot\": there is always a next step to offer. After the person's answer, do what "
            "they picked, step by step (each command that changes something waits for their OK). If they deny a command, "
            "their reason comes back to you: go on from what they said, do not ask for the same command again.\n"
            "For example `git checkout main` fails with \"Your local changes to the following files would be overwritten "
            "by checkout\": say that the folder has uncommitted changes in those files, and offer: stash them, check out "
            "main, pull, then git stash pop (recommended: nothing is lost); commit them first on this branch; discard them "
            "(git checkout -- <files>: they are lost); or leave it as it is.")
# 0.18.23 KeelBot asked a question card for leave to read code ("What should I do next? → Read the handler and entity,
# then confirm the rule fits (recommended)"). Reading needs nobody's OK, in Ask mode too: only the person's own choices
# are asked.
READING = ("Reading is always allowed, in every mode (Ask mode too): reading files, searching the code and explaining "
           "need nobody's OK. Never ask the person before you read, search or explain, and never offer it as an option "
           "in a keel-questions block (\"Read the handler first?\"): do it, in the same turn. Ask only for choices that "
           "are the person's: a decision, a change to a file, a command that changes something. When the person asks "
           "whether you read the code (\"did you read the code?\"), read it now and answer from it in the same turn, "
           "with file:line.")

# 0.18.38 the dashboard renders a light answer syntax (web Markdown `answer`): a bold lead-in as a small heading with a
# verdict chip, an "Open items:" note box, and a keel-questions block with the one id "next" as a row of buttons.
STYLE = ("Answer format (optional): a point may start with a bold lead-in ending in : or ? and, when you judge it, a tag "
         "after it: `**Comment 1: keep x?** [valid]` (valid | partly | not valid | optional | unclear). Start what is "
         "still open with \"Open items:\". Never offer next steps in prose (\"I can draft those replies\"): give 2 to 4 "
         "as a keel-questions block with one question, id \"next\", or a keel-start button for a change flow.")


# ------------------------------------------------------------------ the prompt

def _pointing(mentions: list[dict], selection: dict | None, open_file: str | None) -> str:
    lines = []
    for m in mentions or []:
        kind, value = str(m.get("kind") or ""), str(m.get("value") or "")
        if not value:
            continue
        where = f" ({m['file']}:{m['line']})" if m.get("file") and m.get("line") else ""
        lines.append(f"- {kind or 'item'} {value}{where}")
    if selection and selection.get("path") and selection.get("text"):
        lo, hi = selection.get("from"), selection.get("to")
        span = f":{lo}-{hi}" if lo and hi and lo != hi else (f":{lo}" if lo else "")
        lines.append(f"- selected lines {selection['path']}{span}:\n```\n{str(selection['text'])[:4000]}\n```")
    if open_file:
        lines.append(f"- the file open in the editor: {open_file}")
    return ("The person points at:\n" + "\n".join(lines)) if lines else ""


def _flow_block(flow: dict | None) -> str:
    """The flow that runs or waits in this project: its title, phase, spec, criteria and the waiting gate."""
    if not flow or not flow.get("title"):
        return ""
    lines = [f"A flow {flow.get('status') or 'runs'} in this project: \"{flow['title']}\" (phase {flow.get('phase') or '?'})."]
    if flow.get("spec"):
        lines.append(f"Its spec: {flow['spec']}")
    acs = flow.get("acs") or []
    if acs:
        lines.append("Its acceptance criteria:")
        lines += [f"- {a.get('id')} [{a.get('layer', '')}] {a.get('title', '')} ({a.get('status', '')})" for a in acs[:30]]
    w = flow.get("waiting") or {}
    if w.get("title"):
        lines.append(f"It waits for the person at a gate: {w['title']}" + (f" (the step {w['name']!r})" if w.get("name") else "") + ".")
        if w.get("detail"):
            lines.append(f"The gate shows:\n{str(w['detail'])[:6000]}")
        lines += keelbot.effect_lines(w)
    if flow.get("current"):
        lines.append(f"The step it is at now: {flow['current']}.")
    jobs = flow.get("jobs") or []
    if jobs:
        # 0.18.15 the newest agents and code steps (agent "keel") of this flow and what each ended with
        lines.append("Its newest jobs (newest first; agent \"keel\" is a code step keel runs itself):")
        for j in jobs[:8]:
            head = f"- {j.get('agent') or '?'} at {j.get('step') or '?'}: {j.get('status') or '?'}"
            res = str(j.get("result") or "").strip()
            lines.append(head + (f"\n  ended with: {res[:1200]}" if res else ""))
    return "\n".join(lines)


def flows_in_folder(keel: dict | None) -> list[dict]:
    """The project's flows that run or wait in the project folder itself (not in a worktree of their own): a branch
    switch there changes the files they work on."""
    return [f for f in (keel or {}).get("flows") or [] if isinstance(f, dict)
            and f.get("status") in ("running", "waiting") and (f.get("where") or "folder") != "worktree"]


def flow_branches(keel: dict | None) -> set[str]:
    """The branches of the project's flows that run or wait: theirs, not KeelBot's to check out."""
    return {str(f["branch"]) for f in (keel or {}).get("flows") or [] if isinstance(f, dict) and f.get("branch")
            and f.get("status") in ("running", "waiting")}


def _failed_block(failed: list[dict] | None) -> str:
    """keel 0.18.18 what failed or was refused in KeelBot's last answer: the next turn starts from the error, not from
    the same command again."""
    rows = [f for f in failed or [] if f.get("command") or f.get("said")]
    if not rows:
        return ""
    lines = [f"- `{f['command']}`" + (f": {f['said']}" if f.get("said") else "") if f.get("command") else f"- {f['said']}"
             for f in rows[-6:]]
    return ("In your last answer these failed or were refused:\n" + "\n".join(lines) + "\nDo not run them again "
            "unchanged. If the person now says \"fix it\" or \"try again\", offer the ways forward (a keel-questions block) "
            "unless they already picked one.")


def _transcript(hist: list[tuple[str, str]], limit: int = TRANSCRIPT_CHARS) -> str:
    """The conversation so far, newest kept, for engines that cannot continue their own session. 0.18.19 a /compact
    summary (history()'s first message) always stays: the newest messages share the rest of `limit`."""
    parts, used = [], 0
    if hist and hist[0][0] == "user" and hist[0][1].startswith(SUMMARY_HEAD):
        parts.append(f"Person: {hist[0][1].strip()}")
        hist = hist[1:]
    keep = parts[:]
    parts = []
    for role, text in reversed(hist):
        line = f"{'Person' if role == 'user' else 'KeelBot'}: {text.strip()}"
        if used + len(line) > limit:
            break
        parts.append(line)
        used += len(line)
    parts = keep + list(reversed(parts))
    return ("The conversation so far (oldest first):\n" + "\n\n".join(parts)) if parts else ""


SHELL_OUTPUT_MAX = 8000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
IMAGE_MAX = 10 * 1024 * 1024
IMAGES_MAX = 8


def sees_images(model: dict) -> bool:
    """0.18.18 Claude Code (the claude CLI) reads an image file with its Read tool and sees it. keel's other runners
    give the model text only: an API-key model's read_file reads text, the other CLIs get no picture."""
    return model.get("provider") == "claude" and model.get("mode", "api") != "api"


def attach_images(s: dict, model: dict, paths: list[str]) -> dict:
    """0.18.18 The images the person names with a turn (keel TUI: a path ending .png/.jpg/.jpeg/.gif/.webp, an
    @mention of one, a pasted one): each a file of the chat's folder (its worktree, or the project folder), relative
    or absolute. Answers {images: their full paths, once, for a model that sees images; skipped: [{path, why}]}."""
    roots = [Path(r).resolve() for r in dict.fromkeys(x for x in (workdir(s), s.get("root")) if x)]
    ok: list[str] = []
    skipped: list[dict] = []
    for raw in list(dict.fromkeys(str(p).strip() for p in paths or [] if str(p).strip()))[:IMAGES_MAX * 2]:
        p = Path(raw).expanduser()
        full = (p if p.is_absolute() else roots[0] / p).resolve()
        if not any(full == r or r in full.parents for r in roots):
            why = "it is outside the project folder (keel reads only the project's files): copy it into the project"
        elif full.suffix.lower() not in IMAGE_EXT:
            why = f"it is not an image KeelBot can send ({', '.join(IMAGE_EXT)})"
        elif not full.is_file():
            why = "it does not exist in the project folder"
        elif full.stat().st_size > IMAGE_MAX:
            why = f"it is larger than {IMAGE_MAX // (1024 * 1024)} MB"
        elif not sees_images(model):
            why = (f"the model {model.get('provider', '?')}/{model.get('model', '?')} cannot see images here: only "
                   "Claude Code (the claude CLI) does; /model picks it")
        elif len(ok) >= IMAGES_MAX:
            why = f"at most {IMAGES_MAX} images go with one question"
        else:
            if str(full) not in ok:
                ok.append(str(full))
            continue
        skipped.append({"path": raw, "why": why})
    return {"images": ok, "skipped": skipped}


def _images_block(images: list[str] | None) -> str:
    if not images:
        return ""
    return ("The person attached these images. Look at each with your Read tool (it shows you the picture) before "
            "you answer:\n" + "\n".join(f"- {i}" for i in images))


def _shell_block(shell: dict | None) -> str:
    """0.18.18 The output of a command the person ran themselves (keel TUI's `!command`), for their next question."""
    if not shell or not str(shell.get("command") or "").strip():
        return ""
    code = shell.get("exit")
    how = f"exit {code}" if code is not None else "refused"
    out = str(shell.get("output") or "")[-SHELL_OUTPUT_MAX:]
    return f"The person ran `{shell['command']}` in the project folder ({how}); its output:\n```\n{out.rstrip()}\n```"


def build_prompt(*, mode: str, root: str, question: str, know: dict, graph: bool, flow: dict | None,
                 mentions: list[dict] | None, selection: dict | None, open_file: str | None, transcript: str,
                 branch: str = "", pid: str = "", keel: dict | None = None, shell: dict | None = None,
                 images: list[str] | None = None, failed: list[dict] | None = None, was: str = "",
                 title: bool = False, folder: list[str] | None = None) -> str:
    where = f"Your folder (the worktree): {root}" if mode == "side" or (mode == "ask" and branch) else \
        f"The project folder: {root}"
    note = mode_note(was, mode)            # 0.18.19 the mode changed since the last answer: say so first
    parts = [*([note] if note else []), MODE_TEXT[mode].format(phase=fix_phase(flow), branch=branch or "?"), READING, FAILURES, STYLE, where,
             *(folder or [])]           # 0.18.57 the folder's branch, and a switch since the last answer (folder_branch.py)
    if mode == "edit":
        busy = flows_in_folder(keel)
        if busy:
            parts.append("A flow works in this project folder now (" + "; ".join(f"\"{f.get('title')}\" {f.get('status')}"
                         for f in busy) + "): a branch switch, a pull or a stash changes the files it works on. Say so "
                         "before you offer one, and recommend a side session (`/mode side`) or waiting for the flow.")
    lost = _failed_block(failed)
    if lost:
        parts.append(lost)
    block = agent_knowledge.prompt_block(root, know, graph)
    if block:
        parts.append(block)
    # what the parts add for this question (keel_engine/extensions.py prompt_context): the code graph's "where to look"
    # lookups when hints are on, the places to read first, at no tool call
    parts += extensions.prompt_context(extensions.Agent(AGENT, know, mentions, open_file, selection), root, pid, question)
    ctx = plugins.context_files(root, (keel or {}).get("plugins"))
    if ctx:
        parts.append("Read these when they help: " + ", ".join(ctx))
    for extra in (_flow_block(flow), keelbot.keel_block(keel, question), transcript,
                  _pointing(mentions or [], selection, open_file), _shell_block(shell), _images_block(images)):
        if extra:
            parts.append(extra)
    if title:
        parts.append(TITLE_ASK)            # 0.18.19 a new chat: its first answer names it
    parts.append(f"Question: {question.strip()}")
    return "\n\n".join(parts)


def last_mode(sid: str, before_n: int) -> str:
    """0.18.19 The mode KeelBot's last answer (or stopped turn) before message `before_n` ran in; "" when none says."""
    msgs = [m for m in get(sid)["messages"] if m["n"] < before_n and m["role"] in ("helper", "note")
            and (m.get("data") or {}).get("mode")]
    return str(msgs[-1]["data"]["mode"]) if msgs else ""


def last_failures(sid: str, before_n: int) -> list[dict]:
    """What failed or was refused in KeelBot's last answer before message `before_n` (its stored `failed`)."""
    msgs = [m for m in get(sid)["messages"] if m["n"] < before_n and m["role"] in ("helper", "note")]
    return list((msgs[-1].get("data") or {}).get("failed") or []) if msgs else []


# ------------------------------------------------------------------ /compact

# 0.18.19 the summary's form and rules, from opencode (MIT), core/src/session/compaction.ts: what the person wants, what
# was decided, what is done, active or blocked, the next move and the files to open, so a fresh session goes on alone
SUMMARY_TEMPLATE = """You MUST use this format for your response (you may omit sections that aren't applicable). Do not \
include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the person is trying to accomplish]

## Requirements
- [constraints, preferences, requirements, and scope boundaries stated by the person, or "(none)"]

## Decisions
- [decisions already made and why, or "(none)"]

## Work State
Break the objective into smaller goals and report which are completed, which are being worked on, and which are blocked.
### Completed
- [goals that have been completed; otherwise "(none)"]

### Active
- [goals currently being worked on; otherwise "(none)"]

### Blocked
- [anything blocking progress, and why; otherwise "(none)"]

## Next Move
1. [ordered list of next actions, or "(none)"]

## Relevant Files
List the files and directories, other than the project folder itself, that another agent would need to open to \
continue this work. Include at most 15, most important first. Do not list every file that was read or changed. If none, \
write "(none)".
- `[file or directory path]`: [brief reason it matters]

## Important Context
- [facts the next agent cannot continue without and cannot easily find on its own; or "(none)"]
</template>"""
SUMMARY_RULES = """Rules:
- Keep each section concise. Use terse, single-line bullets, not prose paragraphs or nested lists.
- Prefer short references over detailed restatement. It is fine to leave out information the next agent can recover \
from the code or the files listed above.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers.
- Carry forward only the person's questions or requests that remain unanswered or require further action. Do not \
repeat ones that newer history has answered or resolved. Preserve exact wording when carrying one forward.
- Preserve consequential workflow state, including whether changes are uncommitted, committed, pushed, under review, or \
merged (keel keeps KeelBot's own changes until the person keeps them: say which are kept and which wait).
- Do not mention the summary process or that context was compacted."""


def summary_prompt(update: bool) -> str:
    """0.18.19 What /compact asks after the chat (opencode's buildPrompt): a new summary, or, when the chat starts from
    an earlier one, that summary updated with what came after it."""
    shared = ["Summarize only what the person and KeelBot said and did. Leave out instructions and setup KeelBot was "
              "given rather than told by the person (keel's rules, the mode, the project's knowledge): the next session "
              "receives current versions of these separately.",
              SUMMARY_TEMPLATE, SUMMARY_RULES, "Do not continue the task or call tools.",
              "Return only the structured summary in the requested format. Do not include a preamble, explanation, or "
              "other commentary."]
    if not update:
        return "\n\n".join(["You MUST summarize the conversation above into a structured summary that will be given to "
                             "another agent to resume the work.", *shared])
    return "\n\n".join([
        "Update the existing checkpoint in the conversation above into one consolidated summary.",
        "Newer history always takes precedence over the existing checkpoint. Preserve previous information unless newer "
        "history clearly contradicts, supersedes, resolves, or makes it stale. If something is no longer relevant to "
        "continuing the work, you may remove it.",
        "Incorporate newer requirements, decisions, progress, and context. Reconcile Work State and Next Move: move "
        "completed work out of Active, remove resolved blockers and answered questions, and preserve unresolved or "
        "pending work.",
        "Return only the updated Markdown sections. Do not reproduce the `<conversation-checkpoint>` or `<summary>` "
        "wrapper tags from the previous checkpoint.", *shared])
