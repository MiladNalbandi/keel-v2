"""KeelBot's view of keel itself: the project's workflows, the flows that run now, and the buttons it may give.

The api sends `keel` with every turn (the plugin's api, Helper.kt keelContext): the workflows the project can run and
its recent flows. KeelBot uses them to say which workflow fits a piece of work, to answer questions about a running
flow, and to write a new workflow. It never starts or saves anything itself: it ends its answer with an action block,
and the panel turns that into a button the person presses (the plugin's web, Actions.tsx):

    ```keel-start                      ```keel-workflow
    {"workflow": "feature", ...}       id: ...            (a whole workflow: keel checks it, then offers Save)
    ```                                ```

    ```keel-flow {"do": "approve" | "reject" | "stop" | "mode", ...}```   decide a gate, stop flows, a run mode
    ```keel-flow {"do": "answer", "flow", "answers" | "choice" + "why"}``` propose the answer to a flow's question

    keel_block(keel, question)   the prompt block: workflows, flows, how to give buttons, what each part that is on
                                 says (its `keelbot` words, keel_engine/extensions.py), and (only when the question is
                                 about writing a workflow) the format, the phases, actions and agents to use
    pr_body_sections(thread_id)  its commits in the PR body and the final review (the part's hook, __init__.py)
"""

from __future__ import annotations

import re
from pathlib import Path

from keel_engine import addons, extensions, formats

# One line per keel template (docs/GUIDE.md "Flows in detail"): what it is for.
ABOUT = {
    "feature": "a new feature with a spec: web research when it depends on outside docs → spec (questions) → plan → contract → per criterion a failing test, the code "
               "and a review (you approve each) → security, full review, e2e, smoke → ship",
    "change": "a small change without a spec: trivial (one commit) or 1–3 criteria with tests; too big → hands over to "
              "feature",
    "quick": "the smallest code change (a typo, a small bug, a config value): one agent makes it and a test → keel runs "
             "the tests → you look at the diff once → one commit; a few steps, no spec, no ship",
    "address": "a reviewer's comment, or a change to test code only: one agent changes the code and its tests → keel "
               "runs the tests → you look at the diff once → one commit; no test-first loop",
    "small": "a small feature, test-first, without ship: a short plan with 1–3 criteria you approve → per criterion a "
             "failing test and the code → one review → your one look",
    "implement": "the usual order, not test-first: the code → its tests → keel runs them; pass → one commit and done, "
                 "fail → a fix and the tests again; no gate",
    "fix": "a bug you can describe: reproduce it as a failing test → find the cause → fix → regression test → ship",
    "spike": "research before building: frame → web research of outside docs (search first, 5+ sources) → 2–4 angles researched in parallel → findings with data, sources and one recommendation → spec (a feature flow) or drop",
    "clarify": "ask before you code (the clarify-loop of your ai-coding-toolkit): which task type (6) → which sections → "
               "rounds of up to 6 questions with options from the code → a document in keel's spec format (what will "
               "change, payload changes, architecture, numbered criteria, definition of done, the prompt for any AI "
               "tool) → hand over to feature, fix, change, small or spike, or copy the prompt only",
    "diagnose": "a bug you cannot reproduce yet: 3–4 guesses, one investigator each → hands over to fix or feature",
    "review": "read-only review of the branch, one reviewer per lens (code, security, performance, ...); a report",
    "review-pr": "review someone else's PR or branch against a ticket and the definition of done: build and lint in a "
                 "throwaway worktree → reviewers judge each checklist item met / not met with evidence and hunt bugs → "
                 "a check of every problem → the review; you pick which sections the author sees, nothing is posted "
                 "unless you choose post",
    "lint": "run the project's formatters and linters, fix the findings without changing behaviour, one commit",
    "cover": "test coverage of the changed lines: per gap write a test, delete dead code, or accept it with a reason",
    "ship": "get a branch ready: checks, coverage, dependencies, reviews, final review, the PR body (never pushes)",
    "hunt": "read-only bug hunt: hunters per lens, provers reproduce each candidate, a report",
    "hunt-next": "take the top bug of the last hunt into a fix flow",
    "init": "set a project up for keel: architecture, setup checks, knowledge pages",
    "knowledge-refresh": "refresh the project's knowledge pages (docs/knowledge/ or .keel/knowledge/)",
    "plugin": "build a keel plugin in plugins/<name>/: its spec → keel-plugin new → per criterion a failing test and the "
              "code → keel-plugin lint → the plugin's tests → review",
}

MAX_FLOWS = 8
WRITE_WORDS = re.compile(r"\b(new|make|create|write|generate|build|own|change|edit|save|automat\w*|custom)\b", re.I)
ABOUT_WORKFLOW = re.compile(r"\bwork ?flows?\b", re.I)
ABOUT_RULE = re.compile(r"\brules?\b|\brule ?packs?\b|\bguard\b|\bforbid|\bblock\b", re.I)
ABOUT_AGENT = re.compile(r"\bagents?\b|\bsub-?agents?\b", re.I)
ABOUT_SKILL = re.compile(r"\bskills?\b|\bskill\.md\b", re.I)
ABOUT_STACK = re.compile(r"\bstacks?\b|\bhow (this|the) project (builds|tests)\b", re.I)

QUESTIONS = """- A question for the person, when you need their choice before you can go on (keel 0.18.9): one keel-questions block \
with up to 4 questions, each 2 to 4 options, the recommended one first with "recommended": true, and "multiple": true \
when several options can be true together. The person picks (or writes their own answer) and the answers come back as \
your next message. Whenever the person must choose between options, ask with this block: never write the choice as a \
question in your text ("A or B?"), and do not go on as if they had chosen. Never use it to ask leave to read, search \
or explain ("Read the handler first?"): that needs no OK, do it:
```keel-questions
[{"id": "<short word>", "question": "<the question>", "why": "<what the answer changes>", \
"options": [{"label": "<option>", "description": "<what it costs>", "recommended": true}, {"label": "<option>"}]}]
```
"""

ACTIONS = """Buttons (actions). You never start or save anything yourself: you give the person a button, and nothing \
happens until they press it. Put each one as its own fenced block at the end of your answer:
- Start a flow:
```keel-start
{"workflow": "<an id from the list>", "title": "<one short line>", "request": "<what to build or fix, in the person's \
words, with the details you agreed>", "mode": "<only when the person named a run mode: manual | important | auto | readonly>", \
"branch": "<only \"continue\" when the person chose to work on the branch the project folder is on>"}
```
A start refused because the local base branch and origin's "went their own ways": give the start button again with \
"branch": "continue" when the person chose the current branch (a press of the same button is refused the same way).
When the person asks for a run mode ("in important mode"), put it in the start button's mode: a flow starts in it from \
its first gate (a separate mode button after the start would come too late for gates before it).
- A new workflow (keel checks it and shows Save): the whole workflow YAML in a fenced block with the info string \
keel-workflow.
- A new rule pack (keel checks it, shows what it would have refused lately, then Save): its YAML in a keel-rule block.
- A new agent (Save adds it to the project; workflows use it with `agent: <id>`): its JSON in a keel-agent block.
- A new skill (know-how an agent reads before it works; Save adds it to the project): its whole SKILL.md in a \
keel-skill block; its front matter may name the agents that should load it, as `agents: [<id>, <id>]`.
- A new stack (how this project builds and tests: its lane, the files that detect it, test layers, the project's own \
commands and tools; keel checks it, then Save writes .keel/stacks/<name>.yml): its whole YAML in a keel-stack block.
"""+QUESTIONS+"""- Decide a gate, stop flows, or change a flow's run mode (the flow ids are in the flows list above; the person sees \
the button and presses it, so offer it whenever they ask for one of these, never say you cannot):
```keel-flow
{"do": "approve", "flow": "<flow id>"}
```
```keel-flow
{"do": "reject", "flow": "<flow id>", "why": "<the reason the agent gets>"}
```
```keel-flow
{"do": "stop", "flows": ["<flow id>", "<flow id>"]}
```
```keel-flow
{"do": "mode", "flow": "<flow id>", "mode": "manual | important | auto | readonly"}
```
- Propose the answer to the question a flow waits at (keel 0.18.13; the person sees your answer on a card and presses \
Send, nothing is sent before): for a flow that asks questions, your answers by question id (a label, a list of labels \
for a question that takes several, or your own words); for a gate with named choices, the choice's name, and a "why" \
when that choice needs a reason (a send back always does). Use the ids and names the flows list above shows:
```keel-flow
{"do": "answer", "flow": "<flow id>", "answers": {"<question id>": "<label>", "<question id>": ["<label>", "<label>"]}}
```
```keel-flow
{"do": "answer", "flow": "<flow id>", "choice": "<choice name>", "why": "<the reason, when the choice needs one>"}
```
Before you give an answer button, read the code the question is about (the rule below on advice).
Run modes: manual stops at every gate; important approves a criterion's gate when its checks and review pass; auto \
approves every gate keel can decide (it still stops for money, new dependencies, secrets and failures); readonly lets \
agents read but not change anything. Only flows that run or wait can be stopped. A gate that asks questions or offers \
named choices is answered (the answer button above, the dashboard or /answer), not approved.
When the person asks how to build, change or fix something, say which workflow fits best and why (one or two \
sentences, and when another one fits better), then give the start button with a good title and request."""

# 0.18.23 a person asked KeelBot about a spike's waiting questions: it said "I suggest A for both, smallest and safest",
# and only later "I did not read the code myself, my answers came from the gate text and the explorer notes". Advice on
# a flow's question comes from the code, read in the same turn.
ADVICE = ("Advice on a flow's question. Before you recommend an answer to a question or a choice a flow waits at (an "
          "answer button, or a recommendation in your text), read the code the question is about, in the same turn: "
          "the files the gate names, the flow's evidence and findings, or what a search finds. The gate's text and the "
          "explorer's notes are claims to check, not evidence. Give the reason for each answer with its "
          "`path/to/file.ext:LINE`. When you truly cannot check something, say so in the recommendation itself: "
          "\"not checked in the code: <what and why>\". Never call an option the safest, the smallest or the simplest "
          "without the evidence for it (a file:line, a number, a source).")

FORMAT = formats.WORKFLOW

USEFUL_ACTIONS = ["run:", "commit", "verify_green", "verify_red", "verify_lint", "lint_run", "lint_report",
                  "verify_coverage", "coverage_report", "verify_deps", "audit", "show_diff", "push_check", "report",
                  "review_scope", "review_lenses", "start_flow"]


def _workflow_line(w: dict) -> str:
    wid = str(w.get("id") or "")
    base = str(w.get("based_on") or "").removeprefix("keel/")
    about = ABOUT.get(wid) if w.get("source") == "keel" else None
    steps = [str(s.get("name") or s.get("id")) for s in (w.get("steps") or [])]
    agents = sorted({str(s["agent"]) for s in (w.get("steps") or []) if s.get("agent")})
    if not about:
        own = ["yours"] + ([f"based on {base}"] if base else [])
        if w.get("folder"):
            own.append(f"folder {w['folder']}")
        shown = " → ".join(steps[:10]) + (" → …" if len(steps) > 10 else "")
        about = f"{', '.join(own)}; {len(steps)} steps: {shown}" + (f"; agents: {', '.join(agents)}" if agents else
                                                                       "; no agents")
    last = w.get("last_run") or {}
    run = f" (last run: {last.get('status')}, \"{last.get('title')}\")" if last.get("title") else ""
    return f"- `{wid}` {w.get('name') or wid}: {about}{run}"


def _flow_line(f: dict) -> str:
    bits = [f"\"{f.get('title')}\" ({f.get('workflow') or '?'}, id {f.get('thread_id')}): {f.get('status')}"]
    w = f.get("waiting") or {}
    if f.get("status") == "waiting" and w.get("title"):
        bits.append(f"waits for the person at \"{w['title']}\"" + _asks(w))
    if f.get("step"):
        bits.append(f"step {f['step']}")
    if f.get("phase") and f["phase"] != "none":
        bits.append(f"phase {f['phase']}")
    if f.get("acs_total"):
        bits.append(f"criteria {f.get('acs_done', 0)}/{f['acs_total']} done")
    if f.get("branch"):
        bits.append(f"branch {f['branch']}" + (" (own worktree)" if f.get("where") == "worktree" else ""))
    if f.get("tokens"):
        bits.append(f"{round(int(f['tokens']) / 1000)}k tokens")
    if f.get("error"):
        bits.append(f"error: {str(f['error'])[:200]}")
    if f.get("updated_at"):
        bits.append(f"last change {f['updated_at']}")
    return "- " + "; ".join(bits)


def _choices_of(w: dict) -> list[dict]:
    """The waiting gate's choices with what each does: choice_info (the engine), or the api's choices list of dicts."""
    info = [c for c in w.get("choice_info") or [] if isinstance(c, dict) and c.get("name")]
    if info:
        return info
    return [c if isinstance(c, dict) else {"name": str(c), **({"why": "required"} if c == "reject" else {})}
            for c in w.get("choices") or [] if (isinstance(c, dict) and c.get("name")) or isinstance(c, str)]


REASON_WORDS = {"required": "needs a reason, which is used", "optional": "a reason is used", "used": "a reason is used",
                "none": "a reason is not used"}


def effect_lines(w: dict) -> list[str]:
    """0.18.28 what each choice of the waiting gate really does and whether a reason is used, and what a plain approve
    and a plain reject do there. KeelBot advised "send it back (reject) with this reason" at a gate whose reject only
    stopped the flow and handed the reason to nobody."""
    out = []
    cs = _choices_of(w)
    if cs:
        out.append("Its choices (answer with the choice's name; this is exactly what each does):")
        out += [f"- {c['name']}" + (f" \"{c['label']}\"" if c.get("label") and c.get("label") != c["name"] else "")
                + f": {c.get('description') or '(no description)'}; {REASON_WORDS.get(str(c.get('why')), 'a reason is used')}"
                + ("; the plain approve takes it" if c.get("default") else "") for c in cs]
    plain = w.get("plain") or {}
    for k in ("approve", "reject"):
        e = plain.get(k) if isinstance(plain.get(k), dict) else None
        if e:
            out.append(f"A plain {k} here {e.get('does')}; its reason: {e.get('reason')}.")
    if cs or plain:
        out.append("Before you suggest an answer, check that its effect above is what you say it will do: never suggest "
                   "reject (or a choice) with a reason when that reason is not used, and never say a choice sends work "
                   "back when it stops the flow. Offer the choice that does what you advise.")
    return out


def _asks(w: dict) -> str:
    """What a waiting flow asks, when the api sends it (keel 0.18.13): its questions' ids and options, or its named
    choices (a * marks one that needs a reason), so KeelBot can propose an answer with the right ids and names."""
    qs = [q for q in w.get("questions") or [] if isinstance(q, dict) and q.get("id")]
    if qs:
        return " (questions: " + "; ".join(
            f"{q['id']}{' [several]' if q.get('multiple') else ''}: {q.get('question', '')} ["
            + " | ".join(str(o.get("label", o) if isinstance(o, dict) else o) for o in q.get("options") or []) + "]"
            for q in qs) + ")"
    info = _choices_of(w)
    names = [f"{c['name']}{'*' if c.get('why') == 'required' else ''}"
             + (f" = {c['description'][:120]}" if c.get("description") else "") for c in info]
    plain = (w.get("plain") or {}).get("reject") if isinstance(w.get("plain"), dict) else None
    tail = f"; a plain reject {plain.get('does')}, reason {plain.get('reason')}" if isinstance(plain, dict) and plain.get("does") else ""
    return f" (choices: {(', ' if not any(' = ' in n for n in names) else '; ').join(names)}; * needs a reason{tail})" if names else ""


def _agents() -> list[str]:
    out = []
    for f in addons.agent_files():
        m = re.search(r"^description:\s*(.+)$", f.read_text(), re.M)
        about = (m[1].split(". ")[0] if m else "").strip()
        if len(about) > 100:
            about = about[:100].rsplit(" ", 1)[0] + "…"
        if f.stem != "helper":
            out.append(f"{f.stem}: {about}")
    return out


def format_block(plugins: list[str] | None = None) -> str:
    from keel_engine.rules import PHASES
    from keel_engine.runtime.action_docs import DOCS

    acts = [f"{a} {DOCS[a]['summary']}" if a in DOCS else a for a in USEFUL_ACTIONS]
    parts = [FORMAT, "Phases: " + ", ".join(p for p in PHASES if p != "none") + ".",
             "Actions for code steps (more are in keel's templates):\n" + "\n".join(f"- {a}" for a in acts)]
    mine = extensions.keelbot_actions(plugins)       # each part's step lines (keel_engine/extensions.py)
    if mine:
        parts.append("The project's plugins add these code steps; their settings go in the step's `with:`, for example\n"
                     "  - { id: orphans, kind: code, name: no score without a player, action: db:check, soft: true,\n"
                     "      with: { sql: \"SELECT s.id FROM scores s LEFT JOIN players p ON p.id = s.player_id WHERE p.id IS NULL\", "
                     "expect: none } }\n" + "\n".join(f"- {a}" for a in mine))
    agents = _agents()
    if agents:
        parts.append("Agents for agent steps:\n" + "\n".join(f"- {a}" for a in agents))
    return "\n\n".join(parts)


# 0.31 KeelBot's prompt is in every message. The full block (every workflow with its steps, the flows in detail, every
# button format: about 3.4k tokens) comes only when the question is about keel's work; any other question gets the short
# one (the ids, the flows that wait, the two buttons a coding answer uses: about 0.3k).
ABOUT_KEEL = re.compile(
    r"\b(work ?flows?|flows?|gates?|approve|reject|stop|run ?mode|mode|start|spec|criteri|ship|pull request|prs?|"
    r"wait(s|ing)?|answer|button|rules?|agents?|skills?|stacks?|keel|build|implement|feature|ticket|plan|review|"
    r"deploy|release|which .{0,40}fit|how (do|should|can|would) (i|we)|add|make|create|write|new|fix|bug|change|suggest|recommend|options?|choices?|questions)\b", re.I)

ACTIONS_SHORT = """Buttons: you never start anything yourself; end your answer with a fenced block the person presses.
- Start a flow:
```keel-start
{"workflow": "<an id from the list>", "title": "<one short line>", "request": "<what to build or fix>"}
```
""" + QUESTIONS + """
When the person asks about flows, gates, workflows or how to build something, keel gives you the details and the \
other buttons (approve, answer, stop, a run mode, a new workflow)."""


def keel_block(keel: dict | None, question: str) -> str:
    """What KeelBot knows about keel in this project, for its prompt; "" when the api sent nothing."""
    if not keel:
        return ""
    if not ABOUT_KEEL.search(question or ""):
        return _short_block(keel)
    parts = []
    wfs = [w for w in keel.get("workflows") or [] if w.get("id")]
    if wfs:
        parts.append("The workflows this project can run (a flow is one run of a workflow):\n"
                     + "\n".join(_workflow_line(w) for w in wfs))
    flows = list(keel.get("flows") or [])[:MAX_FLOWS]
    if flows:
        parts.append("This project's flows, newest first (several can run at once, each in its own worktree):\n"
                     + "\n".join(_flow_line(f) for f in flows))
    elif wfs:
        parts.append("No flow has run in this project yet.")
    parts += [ACTIONS, ADVICE]
    on = list(keel.get("plugins") or [])
    parts += [kb["prompt"] for kb in extensions.keelbot(on) if kb.get("prompt")]     # the parts on (Database ...)
    q = question or ""
    if ABOUT_WORKFLOW.search(q) and WRITE_WORDS.search(q):
        parts.append(format_block(on))
    if ABOUT_RULE.search(q) and WRITE_WORDS.search(q):
        parts.append(formats.RULE)
    if ABOUT_STACK.search(q) and WRITE_WORDS.search(q):
        parts.append(formats.STACK)
    if ABOUT_SKILL.search(q) and WRITE_WORDS.search(q):
        parts.append(formats.SKILL)
    elif ABOUT_AGENT.search(q) and WRITE_WORDS.search(q):
        parts.append(formats.AGENT)
    return "\n\n".join(parts)


def _short_block(keel: dict) -> str:
    """The short keel block of a question that is not about keel's work: workflow ids, the flows that wait, two buttons."""
    parts = []
    ids = [str(w["id"]) for w in keel.get("workflows") or [] if w.get("id")]
    if ids:
        parts.append("keel's workflows here (a flow is one run of one): " + ", ".join(ids) + ".")
    flows = list(keel.get("flows") or [])[:MAX_FLOWS]
    busy = [f for f in flows if f.get("status") in ("running", "waiting")]
    if busy:
        parts.append("This project's flows that run or wait now:\n" + "\n".join(_flow_line(f) for f in busy))
    parts.append(ACTIONS_SHORT)
    on = list(keel.get("plugins") or [])
    parts += [kb["prompt"] for kb in extensions.keelbot(on) if kb.get("prompt")]     # the parts on (Database ...)
    return "\n\n".join(parts)


# ------------------------------------------------------------------ KeelBot's hook (its PART, __init__.py)

def pr_body_sections(thread_id: str) -> list[str]:
    """The PR body's and the final review's list of the commits KeelBot made for this flow (Fix at a gate)."""
    from . import helper          # late: KeelBot's module imports most of the runtime
    helped = helper.commits_for(thread_id)
    if not helped:
        return []
    lines = []
    for c in helped:
        files = c["files"]
        more = ", …" if len(files) > 6 else ""
        lines.append(f"- `{c['sha'][:7]}` {c['subject']}" + (f" ({', '.join(files[:6])}{more})" if files else ""))
    return ["## KeelBot changes", "", "Made with KeelBot at a gate, then checked and committed by keel:", ""] + lines + [""]
