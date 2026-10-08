"""What one workflow step really does: POST /steps/explain (docs/CONTRACT.md, "v0.4.1: explain a step").

    common    id, name, kind, phase (+ what the phase means), the phase's file rules per bucket, the shell commands it
              refuses, the commit type it uses, the loop it is in, where it goes next (by step name)
    agent     the agent, how its model is picked, the step's instructions, markers, collect, and the real task prompt
              (prompts.task_prompt + step_asks), rendered with the thread's state or with «placeholders»
    code      every chained action (`verify_red+commit`) in plain words (action_docs.py)
    gate      what approve / send back / each choice does and where it goes
    branch    the condition in words and its yes / no targets
    last_runs with a thread: what the step did the last times it ran, read from the thread's checkpoints

Nothing here runs a step or changes the thread; it only reads.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .. import addons, rules
from ..tools import git
from ..workflows.model import Step, Workflow, from_dict
from ..workflows.templates import get_template, templates
from . import action_docs, agent_knowledge, prompts
from .findings import REVIEWERS
from .markers import REGISTRY

# What each keel phase is for, in one plain sentence.
PHASE_MEANING = {
    "none": "No keel phase yet: no file rules apply.",
    "setup": "keel init: set the project up (config, run ladder, knowledge base). Only non-code files change.",
    "preflight": "Get ready: check the repo and the test command, move to the flow's own branch. No code changes.",
    "workspace": "Prepare the working copy (branch, worktree). No code changes.",
    "triage": "Size a small change and list its criteria. No code changes.",
    "spec": "Write the spec: what must be true, as numbered acceptance criteria. Only the spec and docs change.",
    "contract": "Write the outside surface (routes, schemas, public types) the criteria change. Only the contract changes.",
    "red": "Test first: write a failing test for the current criterion. Tests only, no production code.",
    "green": "Make the current criterion's failing test pass with the least production code. Tests are frozen.",
    "ac": "Work on one criterion with tests and code together.",
    "refactor": "Improve the code without changing behaviour. Tests are frozen.",
    "gate": "A review or approval point: nothing is edited.",
    "review-fix": "Fix what a review found: code and tests may change, migrations only as new files.",
    "integration": "Wire the real parts together once every criterion is green. Production code only, tests frozen.",
    "e2e": "Write the end-to-end tests. Only the e2e folder changes.",
    "smoke": "Write the smoke checks. Only the smoke folder changes.",
    "lint-fix": "Fix what the static checks found, without changing behaviour or adding suppressions. Code and tests "
                "may change; migrations, the contract and specs may not.",
    "coverage-fix": "Raise coverage: add tests; production code may only lose unreachable lines.",
    "trivial": "A change no test could notice: code may change, tests only as new files.",
    "small-change": "A small change being prepared. No code changes.",
    "bug-report": "Take in a bug report. No code changes.",
    "bug-repro": "Reproduce the bug with a failing test. Tests only.",
    "bug-investigate": "Find the root cause from evidence. Read only: production code is locked until Gate F.",
    "gate-r": "Gate R: you confirm the bug is reproduced. Nothing is edited.",
    "gate-f": "Gate F: you approve the fix plan. Nothing is edited.",
    "bug-fix": "Fix the bug: production code (migrations only as new files), tests frozen.",
    "reset": "Put back uncommitted work so the next try starts clean.",
    "security": "Security review of the branch. Nothing in the code is edited.",
    "ship": "Get the branch ready to push: checks, reviews, PR. No code changes in this phase itself.",
    "final-review": "The final review before the PR. Nothing is edited.",
    "memory": "Update keel's knowledge base (docs/knowledge/). No code changes.",
    "close": "Close the flow: ADRs and the closing note. Only specs and docs change.",
    "hunt-scope": "Bug hunt: choose what to look at. The hunt never changes code.",
    "hunt-sweep": "Bug hunt: hunters propose candidate bugs. The hunt never changes code.",
    "hunt-prove": "Bug hunt: provers try to reproduce each candidate. The hunt never changes code.",
    "hunt-report": "Bug hunt: write the report. The hunt never changes code.",
    "hunt-triage": "Bug hunt: decide what to fix first. The hunt never changes code.",
    "review": "A read-only review: nothing at all may be edited.",
}

# The buckets keel sorts every file into (rules.classify), in the order the rules table shows them.
BUCKETS = [
    ("api-main", "backend production code"), ("api-test", "backend tests"), ("web-src", "frontend code"),
    ("web-test", "frontend tests"), ("migration", "database migrations"), ("contract", "the API contract (openapi)"),
    ("specs", "specs"), ("e2e", "end-to-end tests"), ("smoke", "smoke checks"),
    ("other", "everything else: docs, config, build files"), ("protected-env", ".env files (secrets)"),
    ("generated", "generated code"),
]
MAY = {"allow": ("edit", "edit"), "new-only": ("new-only", "create new files only"),
       "delete-only": ("delete-only", "delete lines only"), "deny": ("read-only", "read only")}
COMMIT_TYPE_WORDS = {
    "red": "the failing test", "green": "the code that makes it pass", "fix": "a fix", "coverage": "added tests",
    "contract": "the contract", "e2e": "end-to-end tests", "smoke": "smoke checks", "trivial": "a trivial change",
    "docs": "the spec or docs", "ac": "one criterion", "memory": "the knowledge base", "setup": "keel init's files",
    "refactor": "a refactor", "lint": "static-check fixes",
}

CRITERION = "«the current criterion»"


class ExplainError(Exception):
    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status, self.error = status, error


# ------------------------------------------------------------------ entry point

async def explain_step(body: dict, engine=None) -> dict:
    """The structured explanation of body.step_id in body.workflow (or the thread's own workflow)."""
    tid = str(body.get("thread_id") or "").strip() or None
    ctx = state = None
    if tid:
        if engine is None:
            raise ExplainError(400, "This engine cannot read threads here.")
        ctx = await engine._context(tid)           # 404 (EngineError) for an unknown thread
        snap = await (await engine._graph(tid)).aget_state(engine._cfg(tid))
        state = dict(snap.values or {})
    wf = _workflow(body, ctx)
    step = wf.step(str(body.get("step_id") or ""))
    if not step:
        raise ExplainError(404, f"No step {body.get('step_id')!r} in workflow {wf.id}.")
    root = (ctx.root if ctx else None) or str(body.get("root") or "") or None
    agents = (ctx.agents if ctx else None) or body.get("agents") or {}
    i = wf.steps.index(step)
    out = await asyncio.to_thread(_explain, wf, i, root, agents, ctx, state)
    if tid:
        out["last_runs"] = await last_runs(engine, tid, step, wf)
    return out


def _workflow(body: dict, ctx) -> Workflow:
    raw = body.get("workflow")
    if not raw:
        if ctx:
            return ctx.workflow
        raise ExplainError(400, "Send the workflow (or a thread_id) to explain a step of it.")
    try:
        return from_dict(raw, raw.get("yaml") or None) if isinstance(raw, dict) else raw
    except Exception as exc:
        raise ExplainError(400, f"The workflow does not parse: {exc}") from exc


def _explain(wf: Workflow, i: int, root: str | None, agents: dict, ctx, state: dict | None) -> dict:
    step = wf.steps[i]
    phase = phase_of(wf, i)
    real_root = root if root and Path(root).is_dir() else None
    cfg = rules.load_config(real_root)
    out = {
        "id": step.id, "name": step.name, "kind": step.kind, "phase": phase,
        "phase_meaning": PHASE_MEANING.get(phase, f"A phase keel has no rules for ({phase}): only non-code files may "
                                                   "change."),
        "phase_inherited": not step.phase,
        "lock": bool(step.lock),
        "workflow": {"id": wf.id, "name": wf.name},
        "rules": phase_rules(phase, cfg, step),
        "loop": loop_info(wf, i),
        "next": routes(wf, i),
        "thread": bool(state is not None),
    }
    origin = included_from(wf, step)
    if origin:
        out["included_from"] = origin
    if step.when and step.kind in ("agent", "parallel", "code"):
        out["runs_only_when"] = when_text(step.when)
    if step.retry_only:
        out["runs_only_when"] = "work was sent back to it (a failed check after it, or a gate's send-back); skipped otherwise"
    if step.skippable:
        out["skippable"] = {"band": step.skippable, "unit": step.group or step.id,
                            "text": ("the opening skip menu may turn it off; " +
                                     ("its push gate still waits for a fresh verdict" if step.skippable == "deferred"
                                      else "nothing later needs it"))}
    if step.kind in ("agent", "parallel"):
        out["agent"] = agent_info(wf, i, phase, real_root or root, agents, ctx, state)
    if step.kind == "branch" and step.agent:
        out["agent"] = agent_info(wf, i, phase, real_root or root, agents, ctx, state)
    if step.kind == "code":
        out["code"] = code_info(wf, i, phase, cfg, real_root, state)
    if step.kind == "gate":
        out["gate"] = gate_info(wf, i, phase)
    if step.kind == "branch":
        out["branch"] = branch_info(wf, i)
    return out


# ------------------------------------------------------------------ phase + rules

def phase_of(wf: Workflow, i: int) -> str:
    """The step's phase; a step without one keeps the phase of the step before it (as the engine does)."""
    for s in reversed(wf.steps[: i + 1]):
        if s.phase:
            return s.phase
    return "none"


def phase_rules(phase: str, cfg: dict, step: Step) -> dict:
    """Per bucket what an agent may do in this phase, the shell commands keel refuses, and the commit type."""
    flow = phase not in (None, "", "none")
    row = rules.MATRIX.get(phase, rules.CLOSED) if flow else {"*": "allow"}
    buckets = []
    for b, what in BUCKETS:
        rule = row.get(b) or row.get("*") or "deny"
        may, label = MAY.get(rule, MAY["deny"])
        note = None
        if b == "protected-env":
            may, label, note = "no-access", "never read or written", "it holds secrets; only you change it"
        elif b == "generated":
            may, label, note = "read-only", "read only", "change its source and run the generator instead"
        elif b == "migration" and may == "edit":
            may, label, note = "new-only", "create new files only", "an existing migration never changes"
        item = {"bucket": b, "what": what, "may": may, "label": label}
        if note:
            item["note"] = note
        buckets.append(item)
    guards = cfg["guards"]
    shell = [f'"{p}" (it skips checks keel relies on)' for p in guards["bash_deny_always"]]
    shell.append("printing a .env file (cat, grep, head, … .env): secrets stay out of the conversation")
    if flow:
        shell.append("git commit: the engine's commit step makes every commit")
        shell += [f'"{p}" (blocked during a keel flow)' for p in guards["bash_deny_in_flow"] if p != "git commit"]
        shell.append("adding a dependency (npm install x, pip install x, …): it goes through the spec and a gate")
        shell.append("shell writes (>, >>, sed -i, tee) to a file this phase may not edit")
    out = {"phase": phase, "buckets": buckets, "shell_refused": shell, "read_only": rules.read_only(phase),
           "lane_scoped": phase in rules.LANE_SCOPED_PHASES,
           "reads_blocked": ".env files, and build output (" + ", ".join(guards["read_block"]) + ")"}
    if out["lane_scoped"]:
        out["lane_note"] = "Only the current criterion's lane may change: an [API] criterion cannot touch web files, a [WEB] one " \
                           "cannot touch api files."
    if flow:
        out["commit"] = commit_info(phase, step)
    return out


def commit_info(phase: str, step: Step, ac: dict | None = None, title: str | None = None, flow: str = "") -> dict:
    """The commit a commit action makes in this phase: type, message shape, what it may hold."""
    ctype = rules.commit_type_for(phase)
    rule = rules.COMMIT_RULES[ctype]
    bug = ctype in ("fix", "red") or (ctype == "e2e" and flow == "fix")
    named = {"review-fix": "review", "integration": "integration"}.get(phase)
    if rule.get("noId"):
        ident = ""
    elif step.per_ac:
        ident = (ac or {}).get("id") or "AC-n"
    else:
        ident = named or ("BUG" if bug else "")
    subject = "keel init" if ctype == "setup" else ((ac or {}).get("title") if step.per_ac and ac else None) or title \
        or ("«the criterion's title»" if step.per_ac else "«the flow's title»")
    prefix = rules.commit_prefix(ctype, ident)
    extra = []
    if rule.get("trivial"):
        extra.append("may not edit existing tests, the contract or migrations")
    if rule.get("deleteOnlyProd"):
        extra.append("may only delete production lines, never add any, and never touch .keel/config.yml")
    if rule.get("memoryOnly"):
        extra.append("may only touch docs/knowledge/ and .keel/")
    if rule.get("setupOnly"):
        extra.append("may only touch .keel/, docs/RUNNING.md, docs/knowledge/, CLAUDE.md and .gitignore")
    return {"type": ctype, "about": COMMIT_TYPE_WORDS.get(ctype, ctype), "prefix": prefix, "message": f"{prefix}: {subject}",
            "author": "you, with KeelBot as co-author (Settings › Git)", "may_contain": list(rule.get("allow") or []), "may_not_contain": list(rule.get("deny") or []),
            "extra": extra}


# ------------------------------------------------------------------ loops + routes

def loop_info(wf: Workflow, i: int) -> dict | None:
    step = wf.steps[i]
    lp = wf.loop_of(i)
    out: dict = {}
    if lp:
        first, last = wf.steps[lp.first], wf.steps[lp.last]
        out.update(kind="per_ac" if lp.per_ac else "for_each", over="acs" if lp.per_ac else lp.key,
                   first=first.name, first_id=first.id, last=last.name, last_id=last.id,
                   text=(f"Runs once per acceptance criterion: the steps from {first.name} to {last.name} repeat for each "
                         "criterion that is not done yet." if lp.per_ac else
                         f"Runs once per item of {lp.key}: the steps from {first.name} to {last.name} repeat for each item "
                         "(todo until done, skipped or failed)."))
    if step.items_from:
        cap = f", at most {step.cap}" if step.cap else ""
        batch = f", {step.batch} at a time" if step.batch else ", all at the same time"
        who = step.agent or "the agent"
        out["fan_out"] = {"from": step.items_from, "cap": step.cap, "batch": step.batch,
                          "text": f"One {who} per item of {step.items_from}{cap}{batch}; the answers are kept in "
                                  f"data.{step.id}_results."}
    elif step.kind == "parallel" and not step.lanes:
        out["fan_out"] = {"copies": step.parallel or 1,
                          "text": f"{step.parallel or 1} copies of {step.agent or 'the agent'} at the same time, each told to "
                                  "take a different angle."}
    if step.lanes:
        out["lanes"] = [ln.model_dump(exclude_none=True) for ln in step.lanes]
    return out or None


def _name(wf: Workflow, sid: str | None) -> str:
    if not sid:
        return ""
    if sid == "end":
        return "the end of the flow"
    s = wf.step(sid)
    return s.name if s else sid


def _to(wf: Workflow, sid: str | None) -> dict:
    return {"to": sid, "to_name": _name(wf, sid)}


def default_next(wf: Workflow, i: int) -> dict:
    """Where the flow goes after step i when nothing else decides: the next step, or round the loop."""
    lp = wf.loop_of(i)
    if lp and i == lp.last:
        after = wf.steps[lp.last + 1] if lp.last + 1 < len(wf.steps) else None
        what = "criterion" if lp.per_ac else "item"
        return {"label": "next", "to": wf.steps[lp.first].id, "to_name": wf.steps[lp.first].name,
                "text": f"back to {wf.steps[lp.first].name} for the next {what}; after the last one: "
                        f"{after.name if after else 'the end of the flow'}"}
    if i + 1 < len(wf.steps):
        nxt = wf.steps[i + 1]
        if nxt.for_each or (nxt.per_ac and not wf.steps[i].per_ac):
            what = "the first criterion" if nxt.per_ac else f"the first item of {nxt.for_each}"
            return {"label": "next", "to": nxt.id, "to_name": nxt.name, "text": f"{nxt.name} (the loop starts with {what})"}
        return {"label": "next", "to": nxt.id, "to_name": nxt.name, "text": nxt.name}
    return {"label": "next", "to": "end", "to_name": "the end of the flow", "text": "the end of the flow"}


def retry_target(wf: Workflow, i: int) -> str | None:
    """The agent step a failed check sends its work back to (compiler.Nav.retry_target: never across a loop's edge)."""
    here = (wf.loop_of(i).id if wf.loop_of(i) else "")
    for k in range(i - 1, -1, -1):
        if (wf.loop_of(k).id if wf.loop_of(k) else "") != here:
            break
        if wf.steps[k].kind in ("agent", "parallel"):
            return wf.steps[k].id
    return None


def routes(wf: Workflow, i: int) -> list[dict]:
    """Every way out of step i, by step name."""
    s = wf.steps[i]
    nxt = default_next(wf, i)
    out: list[dict] = []
    if s.kind == "code":
        if s.then == "end":
            out.append({"label": "then", **_to(wf, "end"), "text": "the flow ends here after its actions"})
        elif s.then and s.then != "continue":
            out.append({"label": "then", **_to(wf, s.then), "text": f"jumps to {_name(wf, s.then)}"})
        else:
            out.append({**nxt, "label": "passes"})
        fail = s.back or retry_target(wf, i)
        tries = s.rounds if s.rounds is not None else "fix_attempts (default 3)"
        if s.soft:
            out.append({"label": "fails", **({"to": nxt["to"], "to_name": nxt["to_name"]}),
                        "text": f"goes on anyway (soft): markers[{s.id}].RESULT = fail, what it said is kept in "
                                f"data.{s.id}_output"})
        elif fail:
            out.append({"label": "fails", **_to(wf, fail), "text": f"back to {_name(wf, fail)} with the output, at most {tries} "
                                                                   "time(s); then keel asks you (approve = try again, reject "
                                                                   "= stop)"})
        else:
            out.append({"label": "fails", "to": None, "to_name": "", "text": "keel asks you: approve = try again, reject = "
                                                                             "stop the flow"})
        if s.after_rounds:
            out.append({"label": "after the rounds", **_to(wf, s.after_rounds),
                        "text": f"once the rounds are used up, on to {_name(wf, s.after_rounds)} instead of asking"})
    elif s.kind == "gate":
        out += gate_info(wf, i, phase_of(wf, i))["answers"]
    elif s.kind == "branch":
        out += branch_info(wf, i)["routes"]
    else:
        out.append(nxt)
        if s.kind in ("agent", "parallel") and (s.agent or "") in REVIEWERS and not rules.read_only(s.phase) \
                and not s.per_ac and not s.lanes:
            if s.back:
                out.append({"label": "blocking findings", **_to(wf, s.back),
                            "text": f"sent to {_name(wf, s.back)} as feedback without asking, {s.rounds or 2} round(s); "
                                    "then keel asks: send back once more or go on (dismissed, shown at the final review)"})
            else:
                out.append({"label": "blocking findings", "to": None, "to_name": "",
                            "text": "keel asks: fix them (the implementer fixes, the tests run, the fix is committed, then "
                                    f"{_name(wf, s.redo) or s.name} again) or go on with a reason"})
    if s.when and s.kind in ("agent", "parallel", "code"):
        out.append({"label": "skipped", "to": nxt["to"], "to_name": nxt["to_name"],
                    "text": f"when {when_text(s.when)} does not hold, it does not run and the flow goes on"})
    return out


def when_text(when: dict) -> str:
    """A `when` in words: `{marker: AMEND, step: red}` → "the red step's answer has an AMEND line"."""
    if when.get("data") or when.get("state"):
        path = f"data.{when['data']}" if when.get("data") else str(when["state"])
        subject = path
    else:
        src = when.get("step")
        steps = ", ".join(src) if isinstance(src, list) else (src or "any earlier step")
        subject = f"marker {str(when.get('marker')).upper()} from {steps}" + (" (or any of its items)" if when.get("any") else "")
    if "equals" in when:
        return f"{subject} is {when['equals']}"
    if when.get("in"):
        return f"{subject} is one of {', '.join(map(str, when['in']))}"
    return f"{subject} is set"


def included_from(wf: Workflow, step: Step) -> dict | None:
    """The workflow an included step came from (`ship_verify` ← ship): the step's own included_from (set when the
    include was expanded, outermost include first), else a guess from an include's prefix on its id."""
    if step.included_from:
        name = step.included_from.split("/")[0]
        inner = step.id[len(name) + 1:] if step.id.startswith(name + "_") else step.id
        return {"flow": name, "step": inner, "text": f"Included from the {name} workflow (its step {inner}); edit it there."}
    names = {t.id for t in templates()}
    for name in sorted(names, key=len, reverse=True):
        if step.id.startswith(name + "_") and name != wf.id:
            tpl = get_template(name)
            inner = step.id[len(name) + 1:]
            if tpl and tpl.step(inner):
                return {"flow": name, "step": inner, "text": f"Included from the {name} workflow (its step {inner}); "
                                                             f"edit it there."}
    return None


# ------------------------------------------------------------------ agent

def _front_matter(agent: str) -> dict:
    from .. import config
    f = addons.agent_path(agent)
    if not f.is_file():
        return {}
    return agent_knowledge._front_matter(str(f), f.stat().st_mtime)


def agent_info(wf: Workflow, i: int, phase: str, root: str | None, agents: dict, ctx, state: dict | None) -> dict:
    step = wf.steps[i]
    agent = step.agent or (step.lanes[0].sub if step.lanes else "") or ""
    fm = _front_matter(agent)
    know = agent_knowledge.for_agent(agent, agents)
    out = {
        "id": agent, "about": fm.get("description") or prompts.BUILTIN_ROLES.get(agent, ""),
        "custom": not fm and agent not in prompts.BUILTIN_ROLES,
        "tools": fm.get("tools"), "max_turns": prompts.max_turns(agent, phase),
        "model": model_info(step, agent, fm, ctx, state),
        "knowledge": know,
        "instructions": (step.instructions or "").strip() or None,
        "markers": [{"name": m.upper(), "values": list(REGISTRY.get(m.upper(), ())), "registered": m.upper() in REGISTRY,
                     "text": (f"the agent's own verdict line, one of {', '.join(REGISTRY[m.upper()])}" if REGISTRY.get(m.upper())
                              else "free text the agent writes only when it applies" if m.upper() in REGISTRY
                              else "the step asks the agent to end with this line")} for m in step.markers or []],
        "collect": ({"key": step.collect, "text": f"the JSON list at the end of the answer becomes data.{step.collect}"}
                    if step.collect else None),
        "mcp_tools": list(step.tools or []),
        "role": prompts.role_text(agent),
    }
    out.update(task_prompt(wf, i, phase, agent, root, know, ctx, state))
    return out


def model_info(step: Step, agent: str, fm: dict, ctx, state: dict | None) -> dict:
    rule = ("1. a cheaper model when the budget cap switched to one (on_cap: cheaper); 2. a stronger model after "
            "escalate_model; 3. the step's own model when it is not \"default\" and is in the model table; 4. the "
            f"model set for {agent or 'the agent'} in Agents; 5. the default model in Settings.")
    out = {"step": step.model or "default", "agent_file": fm.get("model"), "effort": fm.get("effort"), "rule": rule}
    if ctx is not None:
        models = ctx.models or {}
        m = ((state or {}).get("model_override") or ((state or {}).get("agent_models") or {}).get(agent)
             or (models.get(step.model) if step.model and step.model != "default" else None) or models.get(agent)
             or models.get("default"))
        if m:
            out["now"] = {k: m.get(k) for k in ("provider", "mode", "model", "effort") if m.get(k)}
    return out


def _walk(state: dict, path: str):
    cur = state
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def task_prompt(wf: Workflow, i: int, phase: str, agent: str, root: str | None, know: dict, ctx, state: dict | None) -> dict:
    """The task text keel would send this step's agent: prompts.task_prompt + step_asks, as in Compiler._run_agent.

    With a thread, its state fills it (title, request, criteria, the current criterion or item, spec, unlocks);
    without one, «placeholders» stand where the flow's values go."""
    step = wf.steps[i]
    placeholders = state is None
    st = dict(state or {})
    acs = list(st.get("acs") or [])
    has_spec_before = any(s.phase in ("spec", "triage") for s in wf.steps[:i])
    if placeholders and any(s.per_ac for s in wf.steps) and phase not in ("spec", "triage", "preflight", "bug-report"):
        acs = [{"id": "«AC-n»", "layer": "API", "title": "«each criterion of the spec»", "status": "todo"}]
    ac = None
    if step.per_ac:
        ac = next((a for a in acs if a["id"] == st.get("ac")), None) or \
            next((a for a in acs if a.get("status") not in ("done", "already-met")), None)
        if not ac or placeholders:
            ac = {"id": "«AC-n»", "layer": "API", "title": CRITERION}
    item = None
    if step.per_item or step.items_from:
        key = step.items_from or (wf.loop_of(i).key if wf.loop_of(i) else "")
        items = (st.get("data") or {}).get(key) if isinstance((st.get("data") or {}).get(key), list) else []
        item = next((x for x in items if isinstance(x, dict) and str(x.get("id")) == str(st.get("item"))), None) \
            or (items[0] if items and isinstance(items[0], dict) else None)
        if not item:
            item = {"id": "«item id»", "title": f"«one item of {key}»"}
    real = bool(root and Path(root).is_dir())
    graph = False
    if real and know.get("code_graph"):
        try:
            from ..tools import mcp
            graph = bool(mcp.codegraph_server_spec(root))
        except Exception:
            graph = False
    spec = st.get("spec") or ("«the spec file»" if placeholders and has_spec_before else None)
    text = prompts.task_prompt(
        agent=agent, phase=phase, step_name=step.name, title=(ctx.title if ctx else "«the flow's title»"),
        root=root or "«the project folder»", ac=ac, acs=acs, feedback=None, index=0, spec=spec,
        unlocks=st.get("unlocks") or [], request=(ctx.request if ctx else "«what the user asked for»"),
        knowledge=know, graph=graph, item=item)
    cur = {**st, "request": ctx.request if ctx else "", "item": item}

    def value(path: str):
        if placeholders:
            return f"«{path}: filled in from the flow state when the step runs»"
        return _walk(cur, path)
    asks = prompts.step_asks(step, value)
    if asks:
        text = f"{text}\n{asks}"
    notes = ["When work is sent back (a gate's note, a failed check), the reason is added at the end: "
             "\"This was sent back. Reason: …\".",
             "When this agent ran this step before (memory on), keel continues its session or adds a summary of its last try "
             "at the top."]
    if step.kind == "parallel" and not step.items_from:
        notes.append("Each parallel copy also gets: \"You are copy N of a parallel step; take a different angle from the "
                     "others.\"")
    return {"prompt": text, "placeholders": placeholders, "prompt_notes": notes,
            "system": "\n\n".join([prompts.role_text(agent), "Commits are made by the engine after a check, never by you. "
                                                             "Do not run git commit."])}


# ------------------------------------------------------------------ code

def code_info(wf: Workflow, i: int, phase: str, cfg: dict, root: str | None, state: dict | None) -> dict:
    step = wf.steps[i]
    names = step.actions()
    acs = list((state or {}).get("acs") or [])
    ac = next((a for a in acs if a["id"] == (state or {}).get("ac")), None) if step.per_ac else None
    actions = []
    for n, name in enumerate(names):
        d = action_docs.describe(name)
        if name == "commit":
            c = commit_info(phase, step, ac, None, wf.flow)
            d["for_this_step"] = (f"In phase {phase} this is a {c['type']} commit ({c['about']}): {c['message']}. It may "
                                  f"hold {', '.join(c['may_contain']) or 'nothing'}" +
                                  (f"; it may not hold {', '.join(c['may_not_contain'])}" if c["may_not_contain"] else "") +
                                  "." + (" It " + "; ".join(c["extra"]) + "." if c["extra"] else ""))
            d["commit"] = c
        elif name in ("verify_red", "verify_green") and root:
            from ..tools import testcmd
            layer = (ac or {}).get("layer", "API")
            cmd = testcmd.command_for(root, (ac or {}).get("id") or ("AC-n" if step.per_ac else None), layer) \
                if step.per_ac or name == "verify_red" else testcmd.command_for(root, None, layer)
            d["for_this_step"] = f"Test command here: {cmd}" if cmd else "No test command found in this project yet: the step " \
                                                                          "fails until .keel/config.yml has one."
        elif name.startswith("run:"):
            v = rules.check_bash(phase, name[4:].strip(), cfg)
            d["for_this_step"] = f"Runs: {name[4:].strip()}" + ("" if v.ok else f" — refused in phase {phase}: {v.reason}")
        elif name == "start_flow":
            d["for_this_step"] = f"Starts the {step.flow} flow" + (f" with seed {step.seed}" if step.seed else "") + \
                                 (" and this flow ends." if step.then == "end" else ".")
        if n:
            d["after"] = f"runs only when {names[n - 1]} passed"
        actions.append(d)
    out = {"actions": actions, "chain": " → ".join(names) or None}
    if not names:
        out["text"] = "No action: it only moves the flow" + (f" (then: {_name(wf, step.then)})." if step.then else ".")
    if step.soft:
        out["soft"] = "A failing check does not stop the flow: markers[" + step.id + "].RESULT = pass | fail for a branch."
    if step.rounds is not None:
        out["rounds"] = f"{step.rounds} fix attempt(s) before keel asks (instead of settings.fix_attempts)."
    return out


# ------------------------------------------------------------------ gate + branch

def gate_info(wf: Workflow, i: int, phase: str) -> dict:
    s = wf.steps[i]
    nxt = default_next(wf, i)
    back = s.back or (wf.steps[i - 1].id if i else None)
    answers: list[dict] = []
    notes: list[str] = []
    lp = wf.loop_of(i)
    if s.skip_menu:
        answers.append({"label": "approve", **{"to": nxt["to"], "to_name": nxt["to_name"]},
                        "text": "run the listed steps; payload skip {unit: reason} turns some off (each skip needs a reason, "
                                "kept in data.ship_skipped, shown in the PR body); payload lenses picks the review lenses"})
    elif isinstance(s.choices, dict):
        for name, target in s.choices.items():
            answers.append({"label": name, **_to(wf, target),
                            "text": ("finishes the flow" if target == "end" else f"goes to {_name(wf, target)}") +
                                    "; your note is kept for that step"})
        notes.append(f"A plain approve picks {next(iter(s.choices))}"
                     + (", a plain send back picks reject." if "reject" in s.choices else "."))
    elif isinstance(s.choices, list) and lp and not lp.per_ac:
        for c in s.choices:
            answers.append({"label": c, "to": nxt["to"], "to_name": nxt["to_name"], "text": f"stores {c} on the item and goes on"})
        if s.on_skip:
            notes.append(f"{s.on_skip.get('choice')} ends the item as skipped and needs a reason (kept in "
                         f"data.{s.on_skip.get('record')}).")
    elif isinstance(s.choices, list):
        for c in s.choices:
            answers.append({"label": c, "to": nxt["to"], "to_name": nxt["to_name"],
                            "text": f"approve with {c}: markers[{s.id}].CHOICE = {c} for the branches after it"})
        answers.append({"label": "send back", **_to(wf, back), "text": f"back to {_name(wf, back)} with your note"})
        notes.append(f"A plain approve picks {s.choices[0]}" + (f" (or the {s.recommend} marker's value)" if s.recommend else "") + ".")
    else:
        answers.append({"label": "approve", "to": nxt["to"], "to_name": nxt["to_name"],
                        "text": f"goes on to {nxt['text']}" + (" and the criterion is done" if s.per_ac else "")})
        answers.append({"label": "send back", **_to(wf, back),
                        "text": f"back to {_name(wf, back)}; your note becomes that agent's feedback"
                                + (" and the criterion is todo again" if s.per_ac else "")})
    if s.per_ac:
        notes.append("Asked per criterion according to gates_mode: every-ac (each one), end-of-lane (the last of its lane), end "
                     "(only the last one); otherwise it approves itself and logs why.")
    if s.when:
        notes.append(f"Pauses only when {when_text(s.when)}; otherwise keel approves it and logs \"not asked\".")
    if s.lock:
        notes.append("A keel rule: it cannot be removed while keel rules are on.")
    if s.report == "verdicts":
        notes.append("Shows the final report: exceptions first, then the verdicts for HEAD, the trace and the diff.")
    if phase in ("integration", "e2e", "smoke"):
        notes.append("The no_gates option waives this gate (it approves itself and the PR body lists it).")
    notes.append("Any answer may carry payload.unlock {path, phase}: that file passes the phase's rules from then on.")
    return {"answers": answers, "notes": notes, "costs": "Waits for you. No tokens."}


def branch_info(wf: Workflow, i: int) -> dict:
    s = wf.steps[i]
    nxt = default_next(wf, i)
    if s.when:
        cond = when_text(s.when)
    elif s.action and s.action.startswith("run:"):
        cond = f"the command `{s.action[4:].strip()}` exits 0"
    elif s.agent:
        cond = f"the {s.agent} agent's answer does not start with \"no\""
    else:
        cond = "always yes (no condition set)"
    no = s.no or None
    out = {"condition": cond, "routes": [
        {"label": "yes", "to": nxt["to"], "to_name": nxt["to_name"], "text": nxt["text"]},
        {"label": "no", **_to(wf, no), "text": _name(wf, no) or "no target set: the flow goes on to the next step"},
    ]}
    if s.rounds:
        out["rounds"] = (f"The loop it closes runs at most {s.rounds} time(s): after that, keel asks (approve = go on anyway, "
                         "with a reason; reject = stop the flow). A yes starts the count again.")
    return out


# ------------------------------------------------------------------ what it did

def _node(wf: Workflow, nid: str) -> str:
    if nid in ("__finish",):
        return "the end of the flow"
    if nid.startswith("__ac_") or nid.startswith("__each_"):
        return "the loop (next criterion or item)"
    if nid.endswith("__fix"):
        return f"{_name(wf, nid[:-5])}: fix findings"
    return _name(wf, nid)


def _delta(a: dict, b: dict, key: str) -> int:
    return int((a or {}).get(key) or 0) - int((b or {}).get(key) or 0)


async def last_runs(engine, tid: str, step: Step, wf: Workflow, limit: int = 5) -> dict:
    """What step did the last times it ran in this thread, from its checkpoints: a run is a checkpoint whose parent was
    about to run the step (next = the step) and that the step itself wrote (current = the step)."""
    graph = await engine._graph(tid)
    ctx = await engine._context(tid)
    snaps = [s async for s in graph.aget_state_history(engine._cfg(tid))]
    snaps.reverse()                                             # oldest first
    runs = []
    for prev, cur in zip(snaps, snaps[1:]):
        if step.id not in (prev.next or ()) or (cur.values or {}).get("current") != step.id:
            continue
        runs.append(await asyncio.to_thread(_run_of, wf, step, prev, cur, ctx.root))
    now = None
    if snaps and step.id in (snaps[-1].next or ()):
        waiting = any(getattr(t, "interrupts", None) for t in snaps[-1].tasks or ())
        now = "waiting for you here" if waiting else ("running" if tid in engine.tasks else "next to run")
    calls = _calls(engine, tid, step.id)
    out = {"count": len(runs), "runs": list(reversed(runs))[:limit], "now": now}
    if calls:
        out["calls"] = calls[-limit:]
    return out


def _run_of(wf: Workflow, step: Step, prev, cur, root: str) -> dict:
    v, pv = cur.values or {}, prev.values or {}
    run: dict = {"checkpoint": cur.config["configurable"]["checkpoint_id"], "at": cur.created_at, "note": v.get("note") or ""}
    if step.per_ac and v.get("ac"):
        run["ac"] = v["ac"]
    if step.per_item and v.get("item"):
        run["item"] = v["item"]
    failed = v.get("status") in ("failed", "stopped") or (step.kind == "code" and bool(v.get("last_failure")) and
                                                         v.get("last_failure") != pv.get("last_failure"))
    run["ok"] = not failed
    nxt = [n for n in (cur.next or ())]
    if nxt:
        run["went_to"] = ", ".join(_node(wf, n) for n in nxt)
    if step.kind == "code":
        if failed:
            run["output"] = str(v.get("feedback") or v.get("last_failure") or "")[:1500]
        elif step.soft and (v.get("data") or {}).get(f"{step.id}_output"):
            run["output"] = str(v["data"][f"{step.id}_output"])[:1500]
        elif v.get("output"):
            run["output"] = str(v["output"])[:1500]
    head, before = v.get("git_head"), pv.get("git_head")
    if head and head != before and git.is_repo(root):
        r = git.git(root, "log", "-1", "--format=%s", head)
        run["commit"] = {"sha": head[:12], "subject": r.stdout.strip() if r.returncode == 0 else ""}
    if step.kind in ("agent", "parallel") or (step.kind == "branch" and step.agent):
        ans = str(v.get("last_answer") or "")
        if ans and ans != pv.get("last_answer"):
            run["answer"] = ans[:1500]
        run["tokens"] = {"in": _delta(v.get("usage"), pv.get("usage"), "tokens_in"),
                         "out": _delta(v.get("usage"), pv.get("usage"), "tokens_out"),
                         "step": _delta(v.get("step_tokens"), pv.get("step_tokens"), step.id)}
    mk = (v.get("markers") or {}).get(step.id)
    if mk and mk != (pv.get("markers") or {}).get(step.id):
        run["markers"] = mk
    log_now, log_before = (v.get("gates") or {}).get("log") or [], (pv.get("gates") or {}).get("log") or []
    added = log_now[len(log_before):] if log_now[: len(log_before)] == log_before else []
    if added:
        run["decided"] = added[-3:]
    return run


def _calls(engine, tid: str, sid: str) -> list[dict]:
    """The agent calls of this step the engine still remembers (its recent events): agent, model, tokens, result head."""
    bus = getattr(engine, "bus", None)
    if not bus:
        return []
    started = {e.get("call_id"): e for e in bus.of(tid, "agent.started") if e.get("step") == sid}
    out = []
    for e in bus.of(tid, "agent.finished"):
        if e.get("step") != sid:
            continue
        d, s = e.get("data") or {}, (started.get(e.get("call_id")) or {}).get("data") or {}
        out.append({"at": e.get("at"), "agent": d.get("agent"), "provider": s.get("provider"), "model": s.get("model"),
                    "status": d.get("status"), "tokens_in": d.get("tokens_in", 0), "tokens_out": d.get("tokens_out", 0),
                    "result": str(d.get("result") or "")[:600]})
    refused = [e for e in bus.of(tid, "guard.refused") if e.get("step") == sid]
    if refused and out:
        out[-1]["guard_refused"] = len(refused)
    return out

