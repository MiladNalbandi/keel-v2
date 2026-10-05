"""Workflow validation: the shape rules the graph compiler relies on, plus keel's locked steps."""

from __future__ import annotations

from ..rules import PHASES
from .model import Workflow, WorkflowError, load_yaml
from .templates import get_template

CODE_ACTIONS = {"verify_red", "verify_green", "verify_release", "verify_coverage", "commit", "push_check", "write_config",
                "ladder", "knowledge_check", "memory_check"}


def _action_ok(action: str) -> bool:
    return action in CODE_ACTIONS or (action.startswith("run:") and len(action) > 4)


def successors(wf: Workflow, i: int) -> list[int]:
    """Where step i can go next (index), following the default order, back and no."""
    ids = {s.id: n for n, s in enumerate(wf.steps)}
    s = wf.steps[i]
    out = []
    if i + 1 < len(wf.steps):
        out.append(i + 1)
    for target in (s.back, s.no):
        if target in ids:
            out.append(ids[target])
    if s.per_ac:  # the loop goes round to its first step
        first = next(n for n, x in enumerate(wf.steps) if x.per_ac)
        out.append(first)
    return out


def validate(wf: Workflow) -> list[str]:
    errors: list[str] = []
    if not wf.steps:
        return ["The workflow has no steps."]
    ids = [s.id for s in wf.steps]
    seen: set[str] = set()
    for sid in ids:
        if sid in seen:
            errors.append(f"Step id '{sid}' is used more than once.")
        seen.add(sid)
    index = {s.id: n for n, s in enumerate(wf.steps)}

    for n, s in enumerate(wf.steps):
        where = f"Step '{s.id}'"
        if s.id.startswith("__"):
            errors.append(f"{where}: ids starting with '__' are reserved.")
        if s.phase and s.phase not in PHASES:
            errors.append(f"{where}: phase '{s.phase}' is not a keel phase.")
        if s.kind == "agent" and not s.agent:
            errors.append(f"{where}: an agent step needs an agent.")
        if s.kind == "code":
            if not s.actions():
                errors.append(f"{where}: a code step needs an action.")
            for a in s.actions():
                if not _action_ok(a):
                    errors.append(f"{where}: unknown action '{a}'.")
        if s.kind == "gate" and s.back:
            if s.back not in index:
                errors.append(f"{where}: back target '{s.back}' does not exist.")
            elif index[s.back] >= n:
                errors.append(f"{where}: back must point to an earlier step, not '{s.back}'.")
        if s.back and s.kind != "gate":
            errors.append(f"{where}: only gates have a back target.")
        if s.kind == "branch":
            if not s.no:
                errors.append(f"{where}: a branch needs a 'no' target.")
            elif s.no not in index:
                errors.append(f"{where}: no target '{s.no}' does not exist.")
        elif s.no:
            errors.append(f"{where}: only branches have a 'no' target.")
        if s.kind == "parallel":
            if not s.lanes and not s.agent:
                errors.append(f"{where}: a parallel step needs an agent or lanes.")
            if s.parallel is not None and s.parallel < 1:
                errors.append(f"{where}: parallel must be 1 or more.")
            for lane in s.lanes or []:
                if lane.kind == "code" and lane.sub and not _action_ok(lane.sub):
                    errors.append(f"{where}: lane '{lane.name}' has unknown action '{lane.sub}'.")
                if lane.kind == "agent" and not (lane.sub or s.agent):
                    errors.append(f"{where}: lane '{lane.name}' needs an agent.")
        if s.max_tokens is not None and s.max_tokens < 0:
            errors.append(f"{where}: max_tokens cannot be negative.")

    loop = [n for n, s in enumerate(wf.steps) if s.per_ac]
    if loop and loop != list(range(loop[0], loop[-1] + 1)):
        errors.append("The 'for each AC' steps must sit together, one after another.")
    if loop:
        for s in wf.steps:
            if s.per_ac and s.back in index and index[s.back] > loop[-1]:
                errors.append(f"Step '{s.id}': back target '{s.back}' is after the AC loop.")

    # Every gate must be reachable from the first step.
    if not errors:
        reach, todo = {0}, [0]
        while todo:
            for m in successors(wf, todo.pop()):
                if m not in reach:
                    reach.add(m)
                    todo.append(m)
        for n, s in enumerate(wf.steps):
            if s.kind == "gate" and n not in reach:
                errors.append(f"Gate '{s.id}' can never be reached.")

    if wf.keel_rules:
        errors += _locked_steps(wf)
    return errors


def _locked_steps(wf: Workflow) -> list[str]:
    """With keel rules on, every locked step of the keel template this is based on must stay."""
    base = (wf.based_on or "").split("/")[-1] if wf.based_on else wf.id
    tpl = get_template(base)
    if not tpl:
        return []
    errs = []
    for t in tpl.steps:
        if not t.lock:
            continue
        mine = wf.step(t.id)
        if not mine:
            errs.append(f"Step '{t.id}' ({t.name}) is a keel rule and cannot be removed while keel_rules is on.")
        elif mine.kind != t.kind or not mine.lock:
            errs.append(f"Step '{t.id}' ({t.name}) is a keel rule: keep it a locked {t.kind} step while keel_rules is on.")
        elif t.action and set(t.actions()) - set(mine.actions()):
            errs.append(f"Step '{t.id}' ({t.name}) is a keel rule: it must keep action '{t.action}'.")
    return errs


def validate_yaml(text: str) -> dict:
    try:
        wf = load_yaml(text)
    except WorkflowError as exc:
        return {"ok": False, "errors": [str(exc)]}
    errors = validate(wf)
    return {"ok": not errors, "errors": errors, "workflow": wf}
