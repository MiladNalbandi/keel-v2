"""The code steps of the clarify flow (content/workflows/clarify.yaml): ask before you code.

A port of the clarify-loop skill from the person's own ai-coding-toolkit plugin (skills/clarify-loop: SKILL.md and
references/task-types.md). Its questions, wording and output templates are kept as they are; keel asks them as its
structured questions (runtime/clarify.py) and turns the result into criteria keel can build.

    clarify_start     Step 0, question 1: the task type (six options, the one the request suggests first and
                      recommended: the read step's TASK-TYPE marker), as a clarify form (state.clarify)
    clarify_type      the type answered; Step 0, question 2: which sections (multi-select, the read step's SECTIONS
                      marker ticked, all six without one)
    clarify_sections  the sections answered ("Got it. Running: ..."); the brief for the rounds: the type's questions
                      (section 1), the identity probe when the request names users or auth (question 2.5) and the red
                      flags (section 4). No section that asks: the rounds are skipped
    clarify_doc       writes <specs>/clarify/<date>-<slug>.md from the assemble step's answer: numbered criteria in
                      keel's form (AC-1 [API] Given, when, then), the Definition of Done, the red-flag check, the next
                      step and the assembled prompt; the result gate's text, its choices (the matching keel flow first)
                      and the hand-over seed
    clarify_prompt    "copy the prompt only": the prompt is shown and the flow ends, no child flow

Every value lives in data.clarify: type, sections, brief, template, doc, acs, prompt, request, evidence, flows.
"""

from __future__ import annotations

import asyncio
import datetime
import re
from pathlib import Path

from .. import layout
from . import prompts

SOURCE = ("From the clarify-loop skill of your ai-coding-toolkit plugin (ai-coding-toolkit/skills/clarify-loop), "
          "run as keel's clarify flow.")
RULE = ("The rule: never start any coding task without answering these questions first. Ambiguity at the start costs "
        "10x more than clarity.")

# key -> (label, description, the skill's block): Step 0, question 1, in the skill's order
TYPES: dict[str, tuple[str, str]] = {
    "feature": ("New feature", "Building something that does not exist yet."),
    "bug": ("Bug fix", "Something is broken — find and fix the root cause."),
    "performance": ("Performance", "Too slow, hits a deadline, or costs too much."),
    "refactor": ("Refactor", "Change shape without changing behavior."),
    "api": ("API integration", "Wire up an external service."),
    "architecture": ("Architecture", "Irreversible decision — pick an approach."),
}
# words that name a type in the read step's marker or in an answer in the person's own words
TYPE_WORDS = {"feature": r"feature|new|build", "bug": r"bug|fix|broken|crash|error", "performance": r"perf\w*|slow|fast|latency",
              "refactor": r"refactor\w*|restructure|clean", "api": r"api|integrat\w*|external|webhook|service",
              "architecture": r"architect\w*|design|decision"}

# Step 0, question 2: the sections, in the skill's words
SECTIONS: list[tuple[str, str]] = [
    ("1. Requirement questions", "Walk through a task-specific checklist (scope, affected files, constraints, edge cases). "
                                 "Skip if requirements are already written and unambiguous."),
    ("2. Acceptance criteria (AC-1, AC-2 …)", "Numbered, testable success conditions — each AC maps to a test case. Skip if "
                                              "you already have ACs from a spec."),
    ("3. Definition of Done", "Exit checklist (tests pass, lint passes, PR reviewed, etc.) so 'done' means the same to you "
                              "and the AI. Skip for trivial 1-line fixes."),
    ("4. Red-flag check", "Scan for stop-the-line signals: hidden scope, unclear ownership, untestable ACs, missing auth "
                          "decisions. Skip if scope is clean."),
    ("5. Ready-to-send prompt", "A complete prompt with task + ACs + file list you can paste into a new session. Skip if "
                                "you're handing off to a spec or plan."),
    ("6. Next-step recommendation", "Recommend the next keel flow (feature / fix / change / small / spike) and what context "
                                    "to bring. Skip if you already know what's next."),
]
ASKING = {1, 4}           # the sections whose answers come from the person (the rounds); the others are outputs
# keel's spec needs criteria and a definition of done: 2 and 3 are always in the document (ticked or not)
ALWAYS = {2, 3}

# references/task-types.md: the questions to answer per type, (title, hint)
QUESTIONS: dict[str, list[tuple[str, str]]] = {
    "feature": [
        ("What needs to be built?", "One clear sentence. What is the feature?"),
        ("Which service / module?", "Where does it live in the codebase?"),
        ("Pattern to follow?", "Which existing file or class should this mirror?"),
        ("Tech stack", "Language, framework, key libraries."),
        ("Requirements", "What must be true when this is done? (list each one)"),
        ("Constraints", "What must NOT change or break? Any performance / backward-compat limits?"),
    ],
    "bug": [
        ("Symptom", "What is happening? Be specific about the wrong behavior."),
        ("Expected behavior", "What should happen instead?"),
        ("Steps to reproduce", "Minimal steps to trigger the bug."),
        ("Error message / stack trace", "Paste the full error."),
        ("Recent changes", "What changed recently? (deploys, config, new data patterns)"),
        ("Already tried", "What have you ruled out? (Don't waste AI time on dead ends)"),
    ],
    "performance": [
        ("What is slow?", "Specific endpoint, query, or function."),
        ("Current measured latency", "P50/P95/P99 or average. Use numbers."),
        ("Target", "What is acceptable? Be specific."),
        ("Stack + volume", "Language, DB, request rate, data size."),
        ("Profiling data", "EXPLAIN ANALYZE output, APM trace, or flame graph (if available)."),
    ],
    "refactor": [
        ("What to refactor?", "File + class name + language."),
        ("Goal", "What quality dimension to improve? (readability / testability / performance / structure)"),
        ("Public interface constraints", "What must NOT change from callers' perspective?"),
        ("Pattern to follow", "Existing example or architectural principle to apply."),
        ("Test coverage", "Are there existing tests? Safe to refactor? Or need characterization tests first?"),
    ],
    "api": [
        ("Which API?", "Name + docs URL if available."),
        ("What do you need from it?", "Which endpoints and data."),
        ("Auth mechanism", "API key, OAuth2, Basic Auth, etc."),
        ("Your existing HTTP client pattern", "What to follow for consistency."),
        ("Error handling requirements", "Retry logic, circuit breaker, alerting?"),
    ],
    "architecture": [
        ("What are you designing?", "System, feature, or migration."),
        ("Key requirements", "Functional + non-functional (latency, scale, reliability)."),
        ("Existing stack + constraints", "What you must work within."),
        ("Team + timeline", "Engineers available and deadline."),
        ("Options you're considering", "Your current thinking (even rough)."),
    ],
}

# references/task-types.md: the assembled prompt template per type, word for word (keel numbers criteria AC-1, AC-2)
TEMPLATES: dict[str, str] = {
    "feature": """I need to implement: {{feature description}}

Location: {{service / module}}  |  Stack: {{language, framework}}
Follow the pattern from: {{existing file or class}}

Requirements:
{{list each requirement}}

Constraints:
{{list must-nots and limits}}

---
Before writing any code:
1. Ask me if anything is unclear
2. Turn the requirements into NUMBERED acceptance criteria (AC-1, AC-2, …)
   — each observable, testable, and small enough to map to one test.
3. List every file you will touch and the key design decisions
4. Wait for my approval
Then implement one step at a time, naming tests after the AC they cover.""",
    "bug": """Bug Report

Symptom: {{what is happening}}
Expected: {{what should happen}}

Steps to reproduce:
{{numbered steps}}

Error / stack trace:
```
{{paste full error}}
```

Recent changes: {{what changed}}
Already tried: {{what you've ruled out}}

---
Diagnose this systematically.
Rank hypotheses by likelihood, then walk me through the cheapest test for each one.""",
    "performance": """Performance issue: {{what is slow}}
Current: {{measured latency}}  →  Target: {{acceptable latency}}
Stack: {{language, DB, framework}}  |  Volume: {{req/min, row count}}

Profiling data:
```
{{EXPLAIN ANALYZE / APM trace / flame graph}}
```

---
Identify the bottleneck. For each optimization:
1. Where time is being spent (query / network / compute / serialization)
2. Top 2–3 changes ranked by impact vs effort
3. How to measure that each change actually helped""",
    "refactor": """Refactor: {{file / class + language}}
Goal: {{what to improve and why}}

Public interface constraint: {{what callers expect — must not change}}
Follow pattern: {{existing file or architecture principle}}
Test situation: {{current coverage}}

---
Before refactoring:
1. Summarize what the current code does (so we agree on behavior to preserve)
2. Propose the target structure
3. Wait for my approval
Then refactor in small, behavior-preserving steps.""",
    "api": """API Integration: {{API name}}
Docs: {{URL}}
Need: {{endpoints + data required}}
Auth: {{mechanism}}

Follow client pattern: {{existing file or class}}
Error handling: {{retry policy, circuit breaker, alerting requirements}}

---
Implement a clean integration client:
1. Follow my existing HTTP client pattern exactly
2. Handle all documented error codes with typed exceptions
3. Include request/response logging
4. Make auth injectable for testing""",
    "architecture": """Architecture: {{what you're designing}}
Stack: {{existing tech}}  |  Team: {{size + deadline}}

Requirements:
{{functional and non-functional requirements}}

Options I'm considering:
{{Option A: description}}
{{Option B: description}}

Constraints:
{{hard limits}}

---
Ask me clarifying questions first. Then:
1. Analyze tradeoffs of each option against my requirements
2. Recommend one with explicit reasoning
3. Identify the top 3 risks and how to mitigate them""",
}

# the skill's notes after a template
UPGRADE = {
    "feature": "Upgrade path: the moment you write numbered ACs, you're doing spec-driven development. For anything "
               "non-trivial, hand these criteria to keel's feature flow (it writes the spec around them).",
    "architecture": "For a full architecture loop (constraints → options → tradeoffs → ADR → phasing), keel's spike "
                    "flow researches the options before a feature is built.",
}

RED_FLAGS = [
    "You are guessing what the user/product owner actually wants",
    "The feature touches more than 3 services or modules",
    "You're not sure which of two patterns to follow",
    'The requirements contain the word "later", "eventually", or "maybe"',
    "You're about to change a public interface or database schema",
    "The task feels bigger than 1 day of work without a plan",
]

META_RULE = [
    ("What exactly am I building?", "Prevents scope creep and building the wrong thing"),
    ("Where does it live?", "Avoids architectural misfits"),
    ("What pattern should I follow?", "Keeps the codebase consistent"),
    ("What must not break?", "Prevents regressions"),
    ("How will I know it's done?", "Defines a clear exit condition"),
]

# Definition of Done (adopted from spec-driven-development), word for word
DOD = [
    "Requirements captured as **numbered acceptance criteria** (AC-1, AC-2, …), each observable and testable.",
    "Tests exist, map back to ACs by name, and **failed before** implementation (red-first).",
    "Input validation is explicit and tested.",
    "Authentication, authorization, and **ownership** rules are explicit and tested.",
    "Sensitive/internal fields are not exposed; error responses are intentional.",
    "For HTTP features: the API contract was updated **before** the code and matches real behavior.",
    "Formatter, static analysis, full test suite, and a smoke test all pass.",
    "No behavior outside the agreed ACs was added (**YAGNI**).",
    "Any non-obvious decision has an ADR.",
]

# Question 2.5: identity vs. authentication probe (auto-trigger)
IDENTITY_WORDS = re.compile(r"\b(users?|auth|login|register|sign[ -]?in|sign[ -]?up|account|member|role|permission)s?\b", re.I)
IDENTITY = [
    ("A · Named entities only", "A `users` table with name/email, no login flow. Tasks are linked to a user record. No "
                                "session, no token, no password. → Small scope. Adds a FK, a user creation endpoint, maybe a "
                                "list endpoint."),
    ("B · Authentication", "Users can log in, get a token/session, and their identity gates what they can see or do. "
                           "Password storage, token lifecycle, auth middleware. → Large scope. Adds auth layer, protected "
                           "routes, session management."),
    ("C · Authorization only", "Auth already exists, you want to control who can do what (roles, ownership checks, "
                               "permission rules). → Medium scope. No new login flow, just guard existing routes."),
]

# the result gate: the keel flows each type hands over to, the recommended one first (feature -> spec, ...)
FLOWS = {"feature": ["feature", "small"], "bug": ["fix"], "performance": ["change", "spike"], "refactor": ["change", "small"],
         "api": ["feature", "spike"], "architecture": ["spike", "feature"]}
HANDS = ("feature", "fix", "change", "small", "spike")
ABOUT_FLOW = {
    "feature": "hand over to the feature flow: the criteria and the answers are its seed; it writes the spec around the "
               "criteria (no second interview), then builds them test-first",
    "fix": "hand over to the fix flow: the bug report is its request; it reproduces the bug as a failing test, finds the "
           "cause and fixes it",
    "change": "hand over to the change flow: the prompt and the criteria are its request; it sizes the work (trivial, "
              "small or a feature)",
    "small": "hand over to the small flow: the criteria are its plan's criteria; test-first, one review, your one look",
    "spike": "hand over to the spike flow: research the options from evidence and the web first, then decide: build or drop",
}


def _result(ok: bool, note: str, detail: str = "", update: dict | None = None):
    from .actions import ActionResult
    return ActionResult(ok, note, detail, update or {})


def _state(a) -> dict:
    return dict(a.data.get("clarify") or {})


def _data(a, **changes) -> dict:
    return {**a.data, "clarify": {**_state(a), **changes}}


def _markers(a, step: str) -> dict:
    return ((a.state.get("markers") or {}).get(step) or {})


def type_of(text: str | None) -> str | None:
    """The type a marker or an answer names: a key, a label or a word for it ("bug — it crashes" -> bug)."""
    t = str(text or "").strip().lower()
    if not t:
        return None
    for key, (label, _d) in TYPES.items():
        if t == key or t.startswith(label.lower()) or re.match(rf"{key}\b", t):
            return key
    for key, words in TYPE_WORDS.items():
        if re.search(rf"\b({words})\b", t):
            return key
    return None


def type_question(recommended: str) -> dict:
    """Step 0, question 1: "What kind of task is this?" with the six types, the recommended one first."""
    keys = [recommended] + [k for k in TYPES if k != recommended]
    return {"id": "task_type", "question": "What kind of task is this?",
            "why": "Task type: the questions and the prompt template follow it (the clarify-loop of your ai-coding-toolkit).",
            "options": [{"label": TYPES[k][0], "description": TYPES[k][1], "recommended": k == recommended} for k in keys]}


def sections_of(value) -> list[int]:
    """Section numbers in a marker or an answer: "1, 2, 5", "all", or the ticked labels ["1. Requirement questions", ...]."""
    out: list[int] = []
    for x in value if isinstance(value, list) else [value]:
        s = str(x or "").strip().lower()
        if not s:
            continue
        if s in ("all", "all 6", "every section"):
            return list(range(1, 7))
        label = re.match(r"([1-6])\.\s", s)            # a ticked option: its number
        nums = [int(label.group(1))] if label else [int(n) for n in re.findall(r"(?<![\w-])([1-6])(?![\w])", s)]
        if not nums:
            nums = [n for n, (lab, _d) in enumerate(SECTIONS, 1) if lab.split(". ", 1)[1].split(" (")[0].lower() in s]
        out += [n for n in nums if n not in out]
    return sorted(out)


def sections_question(kind: str, recommended: list[int]) -> dict:
    """Step 0, question 2: which sections to run (several may be ticked; the sensible ones are recommended)."""
    asks = "; ".join(t for t, _h in QUESTIONS[kind])
    opts = []
    for n, (label, about) in enumerate(SECTIONS, 1):
        extra = f" For {TYPES[kind][0]}: {asks}." if n == 1 else ""
        opts.append({"label": label, "description": (about + extra)[:300], "recommended": n in recommended})
    return {"id": "sections", "question": "Which sections should I run for this clarify-loop?",
            "why": "Sections: tick the ones to run. Without an answer the recommended ones run. 2 and 3 are always in "
                   "the document: it is a keel spec, and a spec needs its criteria and its definition of done.",
            "options": opts, "multiple": True}


def _answer(a, gate: str, qid: str):
    """The latest answer to question qid at a clarify gate (data.<gate>_clarify: every round's questions and answers)."""
    rounds = a.data.get(f"{gate}_clarify") or []
    if not rounds:
        return None, ""
    last = rounds[-1]
    return (last.get("answers") or {}).get(qid), str(last.get("note") or "")


def running_text(chosen: list[int]) -> str:
    """The skill's confirmation before it runs ("Got it. Running: ✅ ... ⬜ ...")."""
    lines = ["Got it. Running:", ""]
    lines += [f"  ✅ {label}" for n, (label, _a) in enumerate(SECTIONS, 1) if n in chosen]
    lines += [f"  ⬜ {label}" for n, (label, _a) in enumerate(SECTIONS, 1) if n not in chosen]
    return "\n".join(lines)


def brief(kind: str, chosen: list[int], request: str) -> str:
    """What the rounds ask: the type's questions (section 1), the identity probe when the task names users or auth, the
    red flags (section 4)."""
    out = [f"Task type: {TYPES[kind][0]}.", running_text(chosen), ""]
    if 1 in chosen:
        out.append(f"Section 1, requirement questions for {TYPES[kind][0]} (the questions to answer):")
        out += [f"{n}. **{t}** — {h}" for n, (t, h) in enumerate(QUESTIONS[kind], 1)]
        hit = IDENTITY_WORDS.search(request or "")
        if hit:
            out += ["", f'Identity vs. authentication probe: the task mentions "{hit.group(0)}". Before the acceptance '
                    'criteria, ask what "users" means here, as one question with exactly these three options (recommend '
                    "the one the code points to; a mix is an answer in the person's own words), id \"identity\":"]
            out += [f"   - {label}: {about}" for label, about in IDENTITY]
    if 4 in chosen:
        out += ["", "Section 4, red-flag check: for each red flag that may hold here, ask the question that clears it "
                "(skip the ones the code or the answers already rule out):"]
        out += [f"- {f}" for f in RED_FLAGS]
    out += ["", "The meta-rule: before any coding task these five must have an answer:"]
    out += [f"- {q} ({why})" for q, why in META_RULE]
    return "\n".join(out)


# ------------------------------------------------------------------ the actions

async def clarify_start(a):
    return await asyncio.to_thread(_start, a)


def _start(a):
    said = _markers(a, "read").get("TASK-TYPE")
    kind = type_of(said) or "feature"
    q = type_question(kind)
    data = _data(a, recommended_type=kind, recommended_sections=sections_of(_markers(a, "read").get("SECTIONS")) or list(range(1, 7)))
    return _result(True, f"task type: {TYPES[kind][0]} recommended" + ("" if said else " (the read step named none)"),
                   update={"data": data, "feedback": None,
                           "clarify": {"questions": [q], "step": "type_set", "title": "What kind of task is this?"}})


async def clarify_type(a):
    return await asyncio.to_thread(_type, a)


def _type(a):
    st = _state(a)
    raw, note = _answer(a, "task_type", "task_type")
    kind = type_of(raw) or type_of(note) or st.get("recommended_type") or "feature"
    q = sections_question(kind, st.get("recommended_sections") or list(range(1, 7)))
    from . import spec_check

    data = _data(a, type=kind, type_label=TYPES[kind][0], template=TEMPLATES[kind], spec_template=spec_check.template_path())
    return _result(True, f"task type: {TYPES[kind][0]}" + ("" if raw else " (recommended)"),
                   update={"data": data, "feedback": None,
                           "clarify": {"questions": [q], "step": "sections_set", "title": "Which sections should I run?"}})


async def clarify_sections(a):
    return await asyncio.to_thread(_sections, a)


def _sections(a):
    st = _state(a)
    kind = st.get("type") or "feature"
    raw, note = _answer(a, "sections", "sections")
    chosen = sections_of(raw) or sections_of(note) or st.get("recommended_sections") or list(range(1, 7))
    chosen = sorted(set(chosen) | ALWAYS)
    asks = bool(ASKING & set(chosen))
    text = running_text(chosen)
    data = _data(a, sections=chosen, running=text, ask="yes" if asks else "no", brief=brief(kind, chosen, a.request or a.title),
                 red_flags="\n".join(f"- {f}" for f in RED_FLAGS) if 4 in chosen else "(not chosen)")
    # the rounds have their own count of question rounds (the two Step 0 questions were rounds too)
    return _result(True, " · ".join([f"sections {', '.join(map(str, chosen))}", "rounds of questions next" if asks
                                     else "no section asks: straight to the outputs"]), text,
                   update={"data": data, "feedback": None, "clarify": {}, "clarify_rounds": 0})


_PROMPT = re.compile(r"^(`{4,})[ \t]*prompt[^\n]*\n(.*?)\n\1[ \t]*$", re.S | re.M)
_HEAD = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)


def split_answer(text: str) -> dict[str, str]:
    """The assemble step's answer by its `## ` headings (lower case) -> body."""
    out: dict[str, str] = {}
    marks = list(_HEAD.finditer(text or ""))
    for n, m in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(text)
        out[m.group(1).strip().lower()] = text[m.end():end].strip()
    return out


def prompt_of(text: str) -> str:
    m = _PROMPT.search(text or "")
    return m.group(2).strip() if m else ""


def _part(parts: dict[str, str], *names: str) -> str:
    for name in names:
        for k, v in parts.items():
            if k.startswith(name):
                return v
    return ""


def doc_path(root: str, title: str) -> str:
    """<specs>/clarify/<date>-<slug>.md in the project's layout (docs/specs or .keel/specs). A folder of its own: the
    spec readers list only the spec folder's own files, and a feature started from it finds its spec by its title."""
    day = datetime.date.today().isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "clarify").lower()).strip("-")[:50] or "clarify"
    folder = f"{layout.resolve(root).specs}/clarify"
    rel, n = f"{folder}/{day}-{slug}.md", 2
    while (Path(root) / rel).exists():
        rel, n = f"{folder}/{day}-{slug}-{n}.md", n + 1
    return rel


def _report_part(prompt: str) -> str:
    """The prompt's part before its "---": what the person reported, without the lines addressed to an AI tool."""
    return re.split(r"^---[ \t]*$", prompt, maxsplit=1, flags=re.M)[0].strip() if prompt else ""


# The document is a keel spec (content/templates/spec.md, the spec-writing skill; spec_check.check passes it): the six
# sections a reader scans first (In plain words ... Architecture), then the spec's sections in the template's order,
# the clarify-loop's own outputs, and the assembled prompt as the last appendix.
FIRST = (("In plain words", ("in plain words",)), ("For engineers", ("for engineers",)),
         ("What will change", ("what will change",)), ("Payload changes", ("payload change",)),
         ("Database changes", ("database change",)), ("Architecture", ("architecture",)))
SPEC = (("Goal", ("goal",)), ("User stories", ("user stor",)), ("Decisions", ("decision",)),
        ("Findings (evidence)", ("findings",)), ("Acceptance criteria", ("acceptance criteria",)),
        ("Test layers", ("test layer",)), ("UI mockup", ("ui mockup", "mockup")), ("Request path", ("request path",)),
        ("Data and migrations", ("data and migration", "data")),
        ("Validation and security rules", ("validation",)), ("Edge cases", ("edge case",)), ("Risks and cost", ("risk",)),
        ("Assumptions", ("assumption",)), ("Unverified and open questions", ("unverified", "open question")),
        ("Out of scope", ("out of scope",)))
OPTIONAL = {"UI mockup", "Request path"}       # only for [WEB] / [API] criteria (the spec check asks for them then)


def spec_frontmatter(title: str, slug: str, day: str) -> list[str]:
    return ["---", f"id: clarify-{day}", f"slug: {slug}", f"title: {title}", "status: draft", f"created: {day}",
            'approved: ""', 'frozen: ""', 'superseded_by: ""', "contract: see Payload changes", "source: clarify", "---", ""]


async def clarify_doc(a):
    return await asyncio.to_thread(_doc, a)


def notes_of(a) -> list[str]:
    """The person's notes from the clarify result's reject, oldest first (0.18.64). Each one stays in the assemble step's
    prompt ({{data.clarify.notes}}): a send-back by the spec check, or a later reject, used to replace the note in the
    feedback, and the outputs were written again from the request and the answers alone."""
    notes = [str(x) for x in _state(a).get("notes") or [] if str(x).strip()]
    ans = a.data.get("result_answer") or {}
    why = str(ans.get("why") or "").strip()
    if ans.get("choice") == "reject" and why and why not in notes:
        notes.append(why)
    return notes


def _doc(a):
    from . import spec_check

    st = _state(a)
    notes = notes_of(a)
    kind = st.get("type") or "feature"
    chosen = st.get("sections") or list(range(1, 7))
    said = str(a.data.get("assemble_text") or a.state.get("last_answer") or "")
    parts = split_answer(said)
    acs = prompts.parse_acs(_part(parts, "acceptance criteria") or said.split("````", 1)[0])
    prompt = prompt_of(said) if 5 in chosen else ""
    rel = st.get("doc") or doc_path(a.root, a.title)          # a second write (spec check, send-back) keeps the file
    day = datetime.date.today().isoformat()
    slug = Path(rel).stem
    lines = spec_frontmatter(a.title, slug, day)
    lines += [f"# {a.title}", "", f"> {SOURCE}", f"> {RULE}", "",
              f"Task type: {TYPES[kind][0]}  |  Flow: clarify  |  {day}", "", running_text(chosen), ""]
    for name, keys in FIRST:
        lines += [f"## {name}", "", _part(parts, *keys), ""]
    request = (a.request or a.title).strip()
    lines += ["## Request", "", request, "", "Clarified with keel's clarify flow (the clarify-loop of your ai-coding-toolkit).", ""]
    for name, keys in SPEC:
        body = _part(parts, *keys)
        if name == "Acceptance criteria" and acs:
            body = "\n".join(f"- **{x['id']}** [{x['layer']}] {x['title']}" for x in acs)
        if body or name not in OPTIONAL:
            lines += [f"## {name}", "", body or "", ""]
    lines += ["## Definition of done", "", "A change is mergeable only when **all** of these hold:", ""]
    lines += [f"- [ ] {x}" for x in DOD] + [""]
    if 4 in chosen:
        lines += ["## Red-flag check", "", _part(parts, "red-flag", "red flag") or "none", ""]
    if 6 in chosen:
        lines += ["## Next step", "", _part(parts, "next step") or f"The {FLOWS[kind][0]} flow.", ""]
    record = str(a.data.get("rounds_text") or "").strip()
    answers = str(a.data.get("rounds_answers") or "").strip()
    if record or answers:
        lines += ["## Appendix: questions and answers", "", *([record, ""] if record else []), *([answers, ""] if answers else [])]
    if 5 in chosen:
        lines += ["## Appendix: ready-to-send prompt", "", "Paste it into any AI coding tool.", "", "````text",
                  prompt or "(the outputs held no prompt)", "````", ""]
        if UPGRADE.get(kind):
            lines += [f"> {UPGRADE[kind]}", ""]
    text = "\n".join(lines)
    path = Path(a.root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

    # keel's spec check: something clear missing -> the outputs are written again once, then the gate shows the rest
    found = spec_check.check(text, acs, spec_check.outside_facts(a.request, a.data), root=a.root)
    fixes = [f for f in found if f["level"] == "fix"]
    if fixes and not st.get("checked"):
        data = _data(a, doc=rel, checked=True, notes=notes)
        mk = dict(a.state.get("markers") or {})
        mk[a.step or "write_doc"] = {"SPEC-CHECK": "fix"}
        note = spec_check.revision_note(fixes).replace("Change the existing file to fix them",
                                                       "Write your whole answer again with them fixed")
        if notes:
            note += ("\n\nThe person's notes on the outputs still apply, all of them: keep every correction your last "
                     "answer made for them.\n" + "\n".join(f"- {x}" for x in notes))
        return _result(True, f"{rel}: the spec check sends the outputs back once ({len(fixes)} missing)",
                       update={"data": data, "markers": mk, "feedback": note})

    # the hand-over: what the person reported, the criteria as AC lines (change and small read them from the request)
    ac_lines = "\n".join(f"- **{x['id']}** [{x['layer']}] {x['title']}" for x in acs)
    handed = "\n\n".join(x for x in [request, _report_part(prompt),
                                     f"Acceptance criteria (from the clarify flow, {rel}):\n{ac_lines}" if ac_lines else ""] if x)
    flows = list(FLOWS[kind])
    without = [f for f in HANDS if f not in flows] + ([] if prompt else ["prompt"])
    order = flows + ["prompt", "reject", "stop"]
    about = {**ABOUT_FLOW, "prompt": "copy the prompt only: it is shown here and in the document; no flow starts",
             "reject": "send it back with a note: the outputs are written again with it",
             "stop": "stop here: the document stays, nothing else happens"}
    choice_lines = [f"- {name}: {about[name]}" + (" (recommended)" if name == flows[0] else "")
                    for name in order if name not in without]
    detail = "\n".join([f"Clarify document: {rel} ({len(acs)} acceptance criteria)", "", "Choose one:", *choice_lines, "",
                        spec_check.describe(found), "", "---", "", text])
    data = _data(a, doc=rel, acs=acs or None, prompt=prompt, request=handed, evidence=text, flows=flows, checked=False,
                 spec_check=[f["text"] for f in found], notes=notes)
    data.update(result_detail=detail, result_without=without, result_order=order)
    mk = dict(a.state.get("markers") or {})
    mk[a.step or "write_doc"] = {"SPEC-CHECK": "pass"}
    return _result(True, f"{rel}: {len(acs)} criteria" + (", a prompt" if prompt else "") + f"; next: the {flows[0]} flow",
                   update={"data": data, "markers": mk, "feedback": None})


async def clarify_prompt(a):
    return await asyncio.to_thread(_prompt, a)


def _prompt(a):
    st = _state(a)
    prompt = st.get("prompt") or ""
    if not prompt:
        return _result(True, f"no prompt was written; the document is {st.get('doc')}")
    return _result(True, f"the prompt is ready to copy ({st.get('doc')}); no flow started", prompt,
                   update={"show": prompt})


ACTIONS = {"clarify_start": clarify_start, "clarify_type": clarify_type, "clarify_sections": clarify_sections,
           "clarify_doc": clarify_doc, "clarify_prompt": clarify_prompt}
