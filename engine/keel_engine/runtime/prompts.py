"""The system prompt and task prompt for one agent call."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..tools import testcmd
from functools import lru_cache

from .. import addons, agent_names, config, extensions, rules
from . import agent_knowledge, spec_check, stacks, test_scope

# Roles for agents that have no file in content/agents.
BUILTIN_ROLES = {
    "contract-author": "You write or update the contract for the approved spec: only the outside surface the criteria "
                       "change (HTTP routes, wire schemas, public types or interfaces). Keep it short; do not write "
                       "design documents, production code or tests. If nothing outside changes, say so and stop.",
}

ITEM_MAX = 6_000           # a fan-out item's JSON in the prompt; a longer one is cut, with a marker the agent must report
GENERIC_ROLE = "You are a careful software engineer working inside a keel flow. Do the step you are given and nothing else."


@lru_cache(maxsize=64)
def _agent_file(path: str) -> str | None:
    from pathlib import Path

    f = Path(path)
    if not f.is_file():
        return None
    text = f.read_text()
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()


# Steps where an agent does more than its file's role: the explorer only maps code (20 turns), but in the spec and
# triage steps it also writes the spec.
WRITING_TURNS = {("explorer", "spec"): 40, ("explorer", "triage"): 40}


def max_turns(agent: str, phase: str | None = None) -> int | None:
    """maxTurns from the agent file's front matter (explorer: 20), so one step cannot run away."""
    f = addons.agent_path(agent)
    n = None
    if f.is_file():
        m = re.search(r"\A---\n.*?^maxTurns:\s*(\d+)\s*$.*?\n---\n", f.read_text(), flags=re.S | re.M)
        n = int(m.group(1)) if m else None
    more = WRITING_TURNS.get((agent, phase or ""))
    return max(n or 0, more) if more and n is not None else n


# 0.18.20 the web tools an agent file may name in `tools:` (the web-researcher). They write nothing, so the runners give
# them to that agent in any phase: Claude Code's own WebSearch and WebFetch, opencode's websearch and webfetch.
WEB_TOOLS = ("WebSearch", "WebFetch")


def web_tools(agent: str) -> list[str]:
    """The web tools the agent's file names in its `tools:` front matter, in WEB_TOOLS order ([] for no file)."""
    f = addons.agent_path(agent) if agent else None
    if not f or not f.is_file():
        return []
    m = re.search(r"\A---\n.*?^tools:\s*(.+?)\s*$.*?\n---\n", f.read_text(), flags=re.S | re.M)
    named = {t.strip().strip("[]'\"") for t in m.group(1).split(",")} if m else set()
    return [t for t in WEB_TOOLS if t in named]


def role_text(agent: str) -> str:
    return _agent_file(str(addons.agent_path(agent))) or BUILTIN_ROLES.get(agent) or GENERIC_ROLE


def skill_paths(text: str) -> str:
    """`references/x.md` in a skill → its full path in keel's skills folder, so an agent opens it instead of
    searching the whole disk for it (one did: `find / -iname acceptance-criteria.md`). A name two skills share
    stays as written."""
    found: dict[str, list[str]] = {}
    for f in sorted(config.v2_skills().glob("*/references/*.md")):
        found.setdefault(f.name, []).append(str(f))

    def full(m):
        hits = found.get(m.group(1)) or []
        return hits[0] if len(hits) == 1 else m.group(0)
    return re.sub(r"(?<![\w/])references/([\w.-]+\.md)", full, text)


COMMIT_LINE = "Commits are made by the engine after a check, never by you. Do not run git commit."


def system_prompt(agent: str, skills: dict[str, str] | None, commits: str | None = None) -> str:
    """The agent's role, its skills and keel's commit line (`commits`: another line in its place, KeelBot's Edit mode)."""
    parts = [role_text(agent)]
    extra = agent_names.lookup(skills, agent)
    if extra:
        parts.append("## Skills\n\n" + skill_paths(extra) + "\n\nSkill files are under "
                     f"{config.v2_skills()}/; open them by that path, never search the disk for them.")
    parts.append(COMMIT_LINE if commits is None else commits)
    return "\n\n".join(parts)


def _allowed(phase: str) -> str:
    row = rules.MATRIX.get(phase, rules.CLOSED)
    allow = [b for b, r in row.items() if b != "*" and r != "deny"]
    return ", ".join(allow) or "nothing"


def task_prompt(*, agent: str, phase: str, step_name: str, title: str, root: str, ac: dict | None,
                acs: list[dict], feedback: str | None, index: int = 0, spec: str | None = None,
                section: str | None = None, unlocks: list[dict] | None = None, request: str = "",
                knowledge: dict | None = None, graph: bool = False, item: dict | None = None, pid: str = "",
                writes_spec: bool = True, ac_commits: dict | None = None) -> str:
    lines = [f"Project folder: {root}", f"Flow: {title}", f"Step: {step_name} (keel phase: {phase})",
             "How this works: the keel engine runs the tests, makes the commits and moves between phases after you. "
             "Do not run keel commands, do not run git commit, reset, checkout or stash, and do not read or change "
             "anything under .keel/. Do only what this step asks, then stop with a short summary (a few lines).",
             f"Files you may change in this phase: {_allowed(phase)}. Anything else is put back automatically.",
             "Read with care for tokens: find what you need with grep -n (or the code index tools, when you have "
             "them) and read only those lines; never read a large file whole, and never read the same file twice. "
             "When you need several files or ranges, read them together in one turn (several tool calls at once): "
             "every turn sends the whole conversation again."]
    if request:
        lines.append("What the user asked for:\n" + request)
    from .. import layout

    lay = layout.resolve(root)
    # 0.18.57 the project notes are opt-in (knowledge.enabled): off, no prompt names their folder
    from . import knowledge as knowledge_mod

    notes_on = knowledge_mod.enabled(root)
    kdir = next((d for d in lay.knowledge_dirs if (Path(root) / d).is_dir()), None) if notes_on else None
    if knowledge is not None and agent != "librarian" and not section:
        # The agent's own knowledge setting (agent_knowledge.py): the sections it was given, the graph, its memory.
        block = agent_knowledge.prompt_block(root, knowledge, graph)
        if block:
            lines.append(block)
        # what the parts add for this step (keel_engine/extensions.py prompt_context): the code graph's "where to look"
        # lookups for agents with hints on, the places to read first, at no tool call
        query = "\n".join(x for x in (title, request, (ac or {}).get("title") or "", feedback or "",
                                       str((item or {}).get("title") or "")) if x)
        lines += extensions.prompt_context(extensions.Agent(agent, knowledge), root, pid, query)
    elif kdir and not section:
        lines.append(f"keel's memory of this project is in {kdir}/ (start with index.md if it exists, then only the "
                     "section you need). Read it before exploring the code and trust its file:line citations.")
    if not section:
        # 0.18.57 the repo's own rules for coding agents (AGENTS.md, CLAUDE.md, ...), found at init / knowledge refresh
        from . import project_rules as rules_mod

        own = rules_mod.prompt_line(root)
        if own:
            lines.append(own)
    mine = [u["path"] for u in unlocks or [] if u.get("phase") == phase]
    if mine:
        lines.append("Unlocked for this phase by the user: " + ", ".join(mine))
    if section:
        lines.append(f"Knowledge section: {section}. Rewrite {lay.knowledge}/{section}.md from the code, "
                     "with a file:line citation behind every claim. Touch no other file.")
    if agent == "librarian":
        tpl = config.content_dir() / "templates" / "knowledge"
        lines.append(f"Section templates: {tpl}/<section>.md (read only the ones you write). Do not read keel's own "
                     "source code; everything you need is in the project and these templates.")
        chosen = ((rules.load_config(root) or {}).get("init") or {}).get("knowledge_sections")
        if phase == "setup" and not section and isinstance(chosen, list):
            lines.append("Knowledge sections the user chose: " + (", ".join(chosen) if chosen else "none — write nothing")
                         + f". Write only these, one file each under {lay.knowledge}/.")
        if phase == "memory" and not section:
            lines.append("At the end of a flow: update only the knowledge sections this branch changes (see git diff "
                         "against the base branch), and only with facts from this branch. Keep each section short.")
    if spec:
        lines.append(f"Spec: {spec}")
    if acs:
        lines.append("Acceptance criteria:")
        lines += [f"- {a['id']} [{a.get('layer', 'API')}] {a.get('title', '')} ({a.get('status', 'todo')})" for a in acs]
    if ac:
        lines.append(f"Current criterion: {ac['id']} [{ac.get('layer', 'API')}] {ac.get('title', '')}")
    # 0.18.21 only the step that writes the spec (spec_writer: not a `keep` step such as the spike's frame or the web
    # research, not the interview, not a fan-out) gets the "write the spec" and "ask questions" lines
    spec_phase = phase in ("spec", "triage") and writes_spec
    # 0.18.64 a send-back (the spec check, a gate's note) patches the last draft: a retry that wrote the spec again from
    # the request lost what the person had added (a risk the request named)
    draft = spec_phase and bool(feedback and spec) and not str(feedback).startswith("Answers to your questions:")
    if draft:
        lines.append(draft_block(root, spec))
    if spec_phase and acs and not spec and phase == "spec":
        # 0.18.64 criteria handed over (a clarify or change flow) still need the spec's number: without it the agent
        # picked 001 itself while 001-… and 002-… existed
        nnn = next_spec_number(root, lay.specs)
        lines.append(f"Write the spec under {lay.specs}/ as {nnn}-<kebab-name>.md (its id {nnn}: the next free number "
                     "in the project's spec folders).")
    if spec_phase and not acs:
        lines.append(f"Keep the reading short: the request, the README, {f'{kdir}/, ' if kdir else ''}and the files the "
                     "request names. Write the spec file early (before half of your turns are used), then improve it; "
                     "a spec that exists beats a perfect map of the code.")
        lines.append("Before the criteria: if something the code cannot answer would change them (what \"user\" means, where a "
                     "filter runs, two reasonable behaviours), write NO spec and end your answer with one ```keel-questions "
                     "block (max 4 questions, 2-4 options each, the recommended one first, valid JSON; the format is in the "
                     "spec-clarify skill). The user clicks and you continue this conversation. Ask only what changes the criteria.")
        lines.append("Each criterion is a behaviour a test can check from the outside (what a caller or user sees), "
                     "not a step of the work: \"imports are updated\" or \"all calls use the new name\" are steps, and so "
                     "is \"behaviour stays the same\" when the existing tests already prove it. A small change usually "
                     "needs one to three criteria.")
        lines.append("When the work changes no behaviour at all (a refactor, an extract, a rename, test code only), tag "
                     "its criterion [refactor] after the layer (\"- **AC-1** [API] [refactor] ...\"): keel writes no new "
                     "test for it and proves it by the existing tests passing before and after. Never ask for a test that "
                     "reads the source code as text.")
        nnn = next_spec_number(root, lay.specs)
        where = (f"Keep the spec in {spec} (patch it, do not start another file) with numbered criteria" if draft
                 else f"Write the spec under {lay.specs}/ as {nnn}-<kebab-name>.md (its id {nnn}: the next free number there) "
                 f"with numbered criteria" if phase == "spec"
                 else "List the criteria in your answer (no spec file: a small change keeps them inline)")
        lines.append(f"{where}, one per line, in this form:\n"
                     "- **AC-1** [API] <what must be true>\n"
                     "Write real criteria for what the user asked for. If the request is too unclear to write any, write no "
                     "criteria and say in one or two sentences what you need to know.")
        if phase == "spec":
            lines.append(spec_template_line())
    if spec_phase and not stacks.has_web_layer(root):
        lines.append(NO_WEB)
    test_cmd = testcmd.shown(*testcmd.located(root, ac["id"], ac.get("layer", "API"))) if ac and testcmd.command_for(root, ac["id"], ac.get("layer", "API")) else None
    if ac and phase in ("red", "green"):
        # the plan's row for this criterion (files, test layer, what it depends on): the agent does not re-read the plan
        row = plan_row(spec_check.read_spec(root, spec), ac["id"])
        deps = [d for d in ac.get("depends_on") or [] if d]
        if row or deps:
            what = ("for the test layer and where its tests go (the tests still describe behaviour, not the files named "
                    "there)" if phase == "red" else "for the files to change and the pattern to follow")
            lines.append(f"The plan for {ac['id']} ({what}):\n" + (row or "(no row of its own in the plan)")
                         + (f"\nBuilt after: {', '.join(deps)}" if deps else ""))
    if agent == "criterion-reviewer" and ac:
        lines.append(review_commits(ac["id"], ac_commits))
    if phase == "red":
        lines.append("Write the failing test for the current criterion only. "
                     + testcmd.name_hint(root, (ac or {}).get("id", "AC-1")) + " Do not write production code.")
        lines.append(SOURCE_TEST_RULE)
        if test_cmd:
            lines.append(f"The engine runs it with: {test_cmd}  (run it once yourself to see it fail for the right reason).")
    if phase == "green" and ac and test_scope.is_refactor(ac):
        lines.append(test_scope.REFACTOR_NOTE.format(id=ac["id"]) + " Tests are frozen.")
    elif phase == "green":
        lines.append("Write the minimum production code that makes the current criterion's test pass. Tests are frozen.")
        later = [a["id"] for a in acs or [] if ac and a.get("status") in (None, "todo") and a["id"] != ac["id"]
                 and rules.in_ac_loop(a)]
        if later:
            lines.append(f"Do not build behaviour for later criteria ({', '.join(later)}): each gets its own failing test "
                         "first. Edge cases they name (ties, empty input, errors) are not this step's job unless this "
                         "criterion's test needs them.")
        if test_cmd:
            lines.append(f"The engine checks it with: {test_cmd}  (run it to confirm, then stop).")
    if item:
        shown = {k: v for k, v in item.items() if k not in ("status", "whole")}
        head = ("Your work (all of it is yours, no other agent shares it):\n" if item.get("whole")
                else "Your item (work on this one only; other agents have the others):\n")
        text = json.dumps(shown, indent=2, ensure_ascii=False, default=str)
        if len(text) > ITEM_MAX:
            # never silently: an agent that got half its item says what it could not see (keel <= 0.18.56 cut it unmarked)
            text = (text[:ITEM_MAX] + f"\n… [CUT: this item is {len(text)} characters and you got the first {ITEM_MAX}. "
                    "Do the part you got, and name what was cut off in your answer.]")
        lines.append(head + text)
    if index and not section and not item:
        lines.append(f"You are copy {index + 1} of a parallel step; take a different angle from the others.")
    if feedback and feedback.startswith("Answers to your questions:"):
        lines.append(feedback)
    elif feedback:
        lines.append(f"This was sent back. Reason:\n{feedback}")
    return "\n".join(lines)


# item 3 of keel's test rules: a test proves behaviour, never the source text (the red check refuses one that does)
SOURCE_TEST_RULE = ("Test behaviour, never the source code's text: do not write a test that reads a source file "
                    "(file_get_contents, readFileSync, Files.readString, open(...).read(), reflection's getFileName) and "
                    "matches it with a regex or a contains check. keel's red check sends such a test back, unless the "
                    "request explicitly asks for a lint-like check of the code. If the criterion changes no behaviour (a "
                    "refactor, an extract, a rename), write no test and say so: the existing tests are its proof.")


REQUEST_MAX = 8_000         # a started flow's request (start_flow's seed): every agent of that flow reads it
EVIDENCE_MAX = 60_000       # the seed's evidence (a spike's report: findings + each angle's answer) for the spec writer


def evidence_block(seed: dict | None) -> str:
    """The evidence a parent flow handed over (a spike's findings and each angle's answer, a fix's diagnosis), whole up
    to EVIDENCE_MAX, for the steps that write the spec (the interview and the spec step): the request only carries its
    first REQUEST_MAX characters."""
    ev = (seed or {}).get("evidence") if isinstance(seed, dict) else None
    text = "\n".join(f"- {x}" for x in ev) if isinstance(ev, list) else str(ev or "")
    text = text.strip()
    if not text:
        return ""
    cut = len(text) > EVIDENCE_MAX
    return ("Evidence handed over by the flow that started this one" + (f" (cut at {EVIDENCE_MAX} characters)" if cut else "")
            + ". Read all of it. Carry every claim with its file:line or URL into the spec's Findings (evidence), keep "
            "the paths it traced, the tests it named and its draft criteria, and put what it marks unconfirmed under "
            "Unverified and open questions. Fill the spec's first sections from it too (In plain words, For engineers, "
            "What will change, Payload changes, Database changes, Architecture). Do not summarise it away.\n\n"
            + text[:EVIDENCE_MAX])


DRAFT_MAX = 30_000          # the last draft of a sent-back spec, in the spec writer's prompt


def draft_block(root: str, spec: str) -> str:
    """0.18.64 a sent-back spec: patch the last draft (given whole up to DRAFT_MAX), never write it again from the
    request."""
    text = spec_check.read_spec(root, spec).strip()
    head = (f"The spec {spec} exists from the last try and was sent back with the note below. Change that file to "
            "answer the note: patch it, do not write it again from the request. Keep every section, criterion (its id "
            "and words), risk, edge case, decision and out-of-scope item it has; change only what the note names. Read "
            "only what the note needs; do not explore the project again.")
    if not text:
        return head
    cut = len(text) > DRAFT_MAX
    return (f"{head}\nThe draft as it is now" + (f" (its first {DRAFT_MAX} characters)" if cut else "")
            + f":\n````markdown\n{text[:DRAFT_MAX]}\n````")


def seeded_block(where: str) -> str:
    """0.18.64 the flow was handed a spec (a clarify hand-over, a spec pasted into the request, the spec it started
    from): the spec writer keeps all of it and only fills the gaps. keel's spec check names what it dropped."""
    return (f"{where[:1].upper()}{where[1:]} holds a spec (or part of one) already. Keep all of it: every section, "
            "every risk, every acceptance criterion (its id and words), every edge case, decision and out-of-scope item. "
            "Put it into keel's template, fill only what is missing, and mark nothing as done that it does not say. Do "
            "not drop, merge away or summarise anything; keel's spec check compares the spec with it and sends back "
            "what is lost.")


_SPEC_NO = re.compile(r"^(\d{3,4})-")


def spec_folders(root: str | Path, specs: str | None = None) -> list[str]:
    """0.18.64 every spec folder keel knows for the project: `specs` (where a new one goes), the layout's folders and keel
    v1's places (docs/specs, specs, .keel/specs, the config's specs.dir); a folder that does not exist reads as empty."""
    from .. import layout

    try:
        known = list(layout.resolve(root).spec_dirs)
    except Exception:  # noqa: BLE001 - a broken config still gets a number from the folders that exist
        known = []
    return list(dict.fromkeys(x for x in [specs, *known, *layout.REPO_SPEC_DIRS, layout.HIDDEN["specs"]] if x))


def _spec_numbers(root: str | Path, specs: str | None = None, skip: str | None = None) -> dict[int, list[str]]:
    """{number: [the specs with it]} over every spec folder (spec_folders), files and folders named NNN-…; `skip` is a
    spec's own path."""
    used: dict[int, list[str]] = {}
    for d in spec_folders(root, specs):
        try:
            names = sorted(p.name for p in (Path(root) / d).iterdir())
        except OSError:
            continue
        for name in names:
            rel = f"{d.rstrip('/')}/{name}"
            if rel != skip and (m := _SPEC_NO.match(name)):
                used.setdefault(int(m.group(1)), []).append(rel)
    return used


def next_spec_number(root: str | Path, specs: str) -> str:
    """The next free spec number ("001" when the project has none): the highest NNN- name plus one. Before, the agent
    picked a number itself and a project's first spec could be 004. 0.18.64 over every spec folder keel knows (the
    layout's and keel v1's): a new spec got 001 while 001-… and 002-… sat in another one."""
    used = _spec_numbers(root, specs)
    return f"{(max(used) + 1) if used else 1:03d}"


def renumber_spec(root: str | Path, rel: str | None) -> str | None:
    """0.18.64 a spec this flow just wrote whose NNN- number another spec has already (in any spec folder): it gets the
    next free number (the file is renamed, its front matter's `id` with it). Returns its path, the same when nothing
    clashed. Only a file right in a spec folder is renamed."""
    if not rel:
        return rel
    p = Path(root) / rel
    m = _SPEC_NO.match(p.name)
    folder = Path(rel).parent.as_posix()
    if not m or not p.is_file() or folder not in spec_folders(root):
        return rel
    if not _spec_numbers(root, skip=rel).get(int(m.group(1))):
        return rel
    nnn = next_spec_number(root, folder)
    new = p.with_name(nnn + p.name[len(m.group(1)):])
    if new.exists():
        return rel
    text = p.read_text(errors="replace")
    head = re.match(r"\A---\n.*?\n---\n", text, re.S)
    if head:
        fixed = re.sub(rf"(?m)^(id:\s*['\"]?)0*{int(m.group(1))}(['\"]?\s*)$", rf"\g<1>{nnn}\g<2>", head.group(0))
        p.write_text(fixed + text[head.end():])
    p.rename(new)
    return f"{folder}/{new.name}"


def spec_template_line() -> str:
    """keel's spec template (content/templates/spec.md, keel v1's spec with the evidence a spike brings) for the step
    that writes the spec: its path and its sections, which keel's spec check asks for."""
    required = ", ".join(n for n, _p, _w in spec_check.REQUIRED)
    return (f"Write the spec from keel's spec template {spec_check.template_path()} (read it once; keep its front matter "
            f"and headings, in this order: {', '.join(spec_check.SECTION_ORDER)}). keel's spec check sends a spec back "
            f"without: {required}; and without Unverified and open questions when the work relies on facts from outside "
            "the repository. Cite a file:line or URL for every finding, give every criterion its test layers (unit, "
            "functional, integration, smoke, e2e, or why not), turn each interview answer into a sentence under Decisions, "
            "and call a gap that needs a product choice a decision, not a fact. Start with the six sections a reader "
            "scans first: In plain words (5 to 10 short, simple sentences, no file names, one everyday example), For "
            "engineers (8 to 15 precise lines per layer), What will change, Payload changes and Database changes (always "
            "there: \"none: <the reason>\" when nothing changes), Architecture (ASCII, each box new, changed or "
            "unchanged). Cut UI mockup, Request path, Data and "
            "migrations, Assumptions and Plan only when they have nothing to say (the plan step writes the Plan).")


def spec_writer(step) -> bool:
    """Does this agent step write the flow's spec (in a spec or triage phase)? A fan-out (one agent per item), a step
    that asks the person first (`asks`), collects a list (`collect`) or keeps its answer as a document for the next
    steps (`keep`) does something else there: the spike's frame, web research, researchers and findings, the feature's
    interview. Those get no "write the spec" lines, and keel reads no criteria from their answers."""
    return step.kind == "agent" and not (step.items_from or step.asks or step.collect or step.keep)


PLAN_ROW_MAX = 1500


def _names(line: str, ac_id: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(ac_id)}(?![\w.-])", line, re.I) is not None


def plan_row(text: str, ac_id: str) -> str:
    """The lines of the spec's Plan section about one criterion: a heading that names it and the lines under it, a list
    item or table row that names it (with its indented follow-up lines). "" when the plan says nothing about it."""
    # the Plan section with its own sub-headings (### AC-1 ...), up to the next heading of its level or higher
    m = re.search(r"(?im)^(#{1,3})\s+plan\b.*$", text or "")
    if not m:
        return ""
    rest = (text or "")[m.end():]
    end = re.search(rf"(?m)^#{{1,{len(m.group(1))}}}\s", rest)
    plan = re.sub(r"```.*?(```|\Z)", "", rest[:end.start()] if end else rest, flags=re.S)
    out, keep, item = [], False, False
    for line in plan.splitlines():
        stripped = line.strip()
        if re.match(r"#{1,6}\s", stripped):
            keep, item = _names(stripped, ac_id), False
            if keep:
                out.append(stripped)
            continue
        if keep:
            out.append(line.rstrip())
            continue
        top = not line.startswith((" ", "\t"))
        if re.match(r"\**order\b", stripped, re.I):
            continue        # the build order names every criterion; it is not this one's row
        if top and stripped:
            item = _names(stripped, ac_id)
            if item:
                out.append(stripped)
        elif item and stripped:
            out.append(line.rstrip())
        elif not stripped:
            item = False
    row = "\n".join(x for x in out if x.strip()).strip()
    return row if len(row) <= PLAN_ROW_MAX else row[:PLAN_ROW_MAX].rstrip() + " …"


def review_commits(ac_id: str, commits: dict | None) -> str:
    """The AC reviewer's two commits (content/agents/criterion-reviewer.md: "the prompt names the AC and the two shas")."""
    red, green = (commits or {}).get("red"), (commits or {}).get("green")
    lines = [f"RED commit of {ac_id}: {red or 'none found (no test(' + ac_id + ') commit on this branch)'}",
             f"GREEN commit of {ac_id}: {green or 'none found (no feat(' + ac_id + ') or fix(' + ac_id + ') commit on this branch)'}"]
    shown = " and ".join(f"git show {x}" for x in (red, green) if x)
    if shown:
        lines.append(f"Read them with {shown}; stay inside these two commits.")
    return "\n".join(lines)


# 0.18.12 a server-rendered app (Spring MVC + Thymeleaf) has no web tests of its own: its page tests are backend tests,
# which keel's guard puts in the api lane, so a [WEB] criterion could never get its test written in red.
NO_WEB = ("This project has no separate web tests (no web test command, frontend folder or web stack): its pages are "
          "tested by backend tests, which keel runs in the api lane. Tag every criterion [API], pages too (still draw "
          "the page's mockup); [WEB] is only for a project with web tests of its own.")


def step_asks(step, value=None) -> str:
    """What the workflow step itself asks of its agent: its instructions, the list it collects, the markers it reads.
    `{{data.report}}` in the instructions is that value of the flow state (value(path) reads it)."""
    from .markers import REGISTRY

    text = (step.instructions or "").strip()
    if text and value:
        text = re.sub(r"\{\{\s*([\w.-]+)\s*\}\}", lambda m: _shown(value(m.group(1))), text)
    lines = [text] if text else []
    if step.collect:
        lines.append(f"End your answer with a ```json block holding a JSON list of {step.collect}: one object per entry, "
                     "each with a short \"title\" (and any other fields that help the next step).")
    # Registered markers are the agents' own verdict lines (their role says when); the step asks for the others.
    said = [f"{m.upper()}: <one line>" for m in step.markers or [] if m.upper() not in REGISTRY]
    if said:
        lines.append("End your answer with " + ("this line" if len(said) == 1 else "these lines") + ", exactly in this form:\n"
                     + "\n".join(said))
    return "\n".join(lines)


def _shown(v) -> str:
    if v is None or v == "":
        return "(nothing yet)"
    return (v if isinstance(v, str) else json.dumps(v, indent=2, ensure_ascii=False, default=str))[:12000]


# 0.18.17 "**AC-1:** [API]" and "AC-1 - [WEB]" too: a plan with a colon after the id had no criteria for keel
# [E2E] and [SMOKE] are criteria too (keel v1 layer tags): on the spec gate and in the trace, built by the
# e2e and smoke steps, not by the per-criterion RED/GREEN loop (rules.in_ac_loop)
AC_LINE = re.compile(r"\b(AC-\d+)\b[*:.)\s\u2013\u2014-]*\[(API|WEB|E2E|SMOKE)\]\s*(.+)", re.I)


PLACEHOLDER = re.compile(r"^\s*<[^>]*>\s*$|what must be true|^\s*(tbd|todo|\.\.\.|…)\s*$", re.I)


def parse_acs(text: str) -> list[dict]:
    """Numbered criteria from a spec. Examples in code blocks and template placeholders such as
    "<behaviour>" are not criteria (an agent may quote an old template while explaining)."""
    seen, out = set(), []
    text = re.sub(r"```.*?(```|\Z)", "", text or "", flags=re.S)
    # A criterion may wrap over indented follow-up lines; join them so the title is the whole sentence.
    joined, out_lines = [], text.splitlines()
    for line in out_lines:
        if joined and AC_LINE.search(joined[-1]) and line.startswith((" ", "\t")) and line.strip() \
                and not re.match(r"\s*([-*+]|\d+\.)\s", line):
            joined[-1] = joined[-1].rstrip() + " " + line.strip()
        else:
            joined.append(line)
    text = "\n".join(joined)
    for m in AC_LINE.finditer(text):
        aid = m.group(1).upper()
        title = m.group(3).strip().strip("*").strip()
        if aid in seen or PLACEHOLDER.search(title) or len(title) < 3:
            continue
        seen.add(aid)
        ac = {"id": aid, "layer": m.group(2).upper(), "title": title, "status": "todo"}
        # a refactor criterion (tagged [refactor], or worded as one with nothing new to see): no RED test, its proof is
        # the existing tests before and after (runtime/test_scope.py)
        out.append({**ac, "kind": "refactor"} if test_scope.is_refactor(ac) else ac)
    return out
