"""Workflow validation: the shape rules the graph compiler relies on, plus keel's locked steps."""

from __future__ import annotations

from .. import extensions
from ..rules import PHASES
from ..runtime.findings import REVIEWERS
from .model import Workflow, WorkflowError, load_yaml
from .templates import get_template

CODE_ACTIONS = {"verify_red", "verify_green", "verify_release", "verify_coverage", "commit", "push_check", "write_config",
                "ladder", "knowledge_check", "memory_check",
                # verdict actions (runtime/verdict_actions.py), the PR body, and the hand-off to another workflow
                "verify_fast", "verify_module", "verify_deps", "audit", "trace", "trace_strict", "arch", "pr", "open_pr",
                "start_flow",
                # ship and cover (runtime/ship.py)
                "review_lenses", "coverage_report",
                # flow helpers: review scope, reports, the bug and change flows (runtime/flow_actions.py), model escalation
                "review_scope", "report", "investigation_note", "bug_intake", "reset", "change_size", "change_start",
                "escalate_model",
                # the feature flow (runtime/feature_actions.py)
                "preflight", "explore_areas", "spec_sync", "spec_freeze", "spec_restart", "amend_start", "spec_amendment",
                "spec_amend_commit", "show_diff", "security_scope", "e2e_scope", "e2e_unrun", "verify_e2e", "smoke_scope",
                "verify_smoke", "close_flow",
                # static checks (runtime/lint_actions.py)
                "lint_scope", "lint_run", "lint_report", "verify_lint"}
# the hunt and hunt-next flows (runtime/hunt_actions.py) and init's extra steps (runtime/init_actions.py)
FLOW_ACTIONS = {"hunt_start", "hunt_deps", "hunt_confirm", "hunt_ingest", "hunt_verdicts", "hunt_group", "hunt_report",
                "hunt_commit", "hunt_close", "hunt_take", "arch_detect", "arch_set", "ladder_soft", "ladder_retry",
                "rung_apply"}
END = "end"          # a branch's no, or a gate choice, may finish the flow


def _plugin_params() -> dict[str, dict]:
    """The parts' step actions (db, git, ci ...) with what each needs in `with:` (keel_engine/extensions.py)."""
    return extensions.action_params()


def _action_ok(action: str) -> bool:
    return (action in CODE_ACTIONS or action in FLOW_ACTIONS or action in _plugin_params()
            or (action.startswith("run:") and len(action) > 4) or extensions.has_action(action))


def _with_errors(where: str, s) -> list[str]:
    """A plugin step's `with:`: the settings it needs are there, and it has none it does not know."""
    plugin = [a for a in s.actions() if a in _plugin_params()]
    if not plugin:
        if s.params and any(extensions.has_action(a) for a in s.actions()):
            return []          # a part's action without declared params (an add-on's) reads its own `with:`
        kinds = ", ".join(f"{p}:..." for p in extensions.param_prefixes())
        which = f" ({kinds})" if kinds else ""          # no plugin loaded (a core-only keel): no empty brackets
        return [f"{where}: only a plugin step{which} takes `with`."] if s.params else []
    errs, known = [], {}
    for a in plugin:
        known.update(_plugin_params()[a])
    for a in plugin:
        for key, need in _plugin_params()[a].items():
            if need == "required" and not str((s.params or {}).get(key) or "").strip():
                errs.append(f"{where}: {a} needs `with: {{{key}: ...}}`.")
    errs += [f"{where}: {', '.join(plugin)} does not know `with: {k}`." for k in (s.params or {}) if k not in known]
    return errs


def successors(wf: Workflow, i: int) -> list[int]:
    """Where step i can go next (index), following the default order, back and no."""
    ids = {s.id: n for n, s in enumerate(wf.steps)}
    s = wf.steps[i]
    out = []
    if i + 1 < len(wf.steps):
        out.append(i + 1)
    exits = s.choices.values() if isinstance(s.choices, dict) else ()
    for target in (s.back, s.no, s.then, s.after_rounds, *exits):
        if target in ids:
            out.append(ids[target])
    loop = wf.loop_of(i)
    if loop:  # the loop goes round to its first step
        out.append(loop.first)
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
            if not s.actions() and not s.then:
                errors.append(f"{where}: a code step needs an action (or a 'then').")
            for a in s.actions():
                if not _action_ok(a):
                    errors.append(f"{where}: unknown action '{a}'.")
            errors += _with_errors(where, s)
        elif s.params:
            errors.append(f"{where}: only a code step takes `with`.")
        if s.kind in ("gate", "code") and s.back:
            # a code step's back: where a failed check goes (with the failure as feedback) instead of a retry
            if s.back not in index:
                errors.append(f"{where}: back target '{s.back}' does not exist.")
            elif index[s.back] >= n and s.kind == "gate":
                errors.append(f"{where}: back must point to an earlier step, not '{s.back}'.")
        review = s.kind in ("agent", "parallel") and (s.agent or "") in REVIEWERS and not s.per_ac and not s.lanes
        if s.back and s.kind != "gate":
            if s.kind != "code" and not review:
                errors.append(f"{where}: only gates, code steps and review steps have a back target.")
            elif s.back not in index or (index[s.back] >= n and (s.kind != "code" or index[s.back] == n)):
                # a code step may send its failure forward too (hunt: a refused close goes back to the triage gate), never to itself
                errors.append(f"{where}: back target '{s.back}' must be an earlier step.")
        if s.redo is not None:
            if not review:
                errors.append(f"{where}: only a review step has redo (where the flow goes on after a fix).")
            elif s.redo not in index:
                errors.append(f"{where}: redo target '{s.redo}' does not exist.")
        if s.rounds is not None:
            if s.rounds < 1:
                errors.append(f"{where}: rounds must be 1 or more.")
            if s.kind not in ("code", "branch") and not review:
                errors.append(f"{where}: rounds belongs to a code step, a branch or a review step.")
        if s.soft and s.kind != "code":
            errors.append(f"{where}: only a code step is soft.")
        if s.retry_only and s.kind not in ("agent", "code"):
            errors.append(f"{where}: only agent and code steps are retry_only.")
        if (s.skip_menu or s.report or s.choices or s.on_skip) and s.kind != "gate":
            errors.append(f"{where}: skip_menu, report, choices and on_skip belong to a gate.")
        if isinstance(s.choices, dict):
            if not s.choices:
                errors.append(f"{where}: choices needs at least one exit.")
            for name, target in s.choices.items():
                if target != END and target not in index:
                    errors.append(f"{where}: choice '{name}' goes to '{target}', which does not exist.")
        if s.on_skip is not None:
            if not s.per_item:
                errors.append(f"{where}: on_skip belongs to a gate inside a for_each loop.")
            if s.on_skip.get("choice") and s.on_skip["choice"] not in (s.choices or []):
                errors.append(f"{where}: on_skip choice '{s.on_skip['choice']}' is not one of the choices.")
        if isinstance(s.choices, list) and (not s.choices or len(set(s.choices)) != len(s.choices)):
            errors.append(f"{where}: choices must be a list of different names.")
        if s.recommend is not None and (s.kind != "gate" or not isinstance(s.choices, list)):
            errors.append(f"{where}: recommend belongs to a gate with a list of choices.")
        if s.after_rounds is not None and (s.kind != "code" or s.after_rounds not in index):
            errors.append(f"{where}: after_rounds belongs to a code step and names an existing step.")
        if s.then and s.then not in ("end", "continue"):
            if s.kind != "code":
                errors.append(f"{where}: only code steps have a 'then'.")
            elif s.then not in index:
                errors.append(f"{where}: then target '{s.then}' does not exist.")
        if s.kind == "branch":
            if not s.no:
                errors.append(f"{where}: a branch needs a 'no' target.")
            elif s.no not in index and s.no != END:
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
        if s.items_from and s.kind != "parallel":
            errors.append(f"{where}: only a parallel step takes 'from' (one agent per item).")
        if s.items_from and s.lanes:
            errors.append(f"{where}: 'from' and lanes do not go together.")
        if s.root and not (s.kind == "parallel" and s.items_from):
            errors.append(f"{where}: 'root: item' needs a parallel step with 'from' (each item names its folder).")
        if s.asks and s.kind != "agent":
            errors.append(f"{where}: only an agent step takes 'asks' (its questions go to the next gate).")
        if s.keep and s.kind != "agent":
            errors.append(f"{where}: only an agent step takes 'keep' (its whole answer, for the next steps).")
        for name, v in (("cap", s.cap), ("batch", s.batch)):
            if isinstance(v, str):
                if not v.startswith("$"):
                    errors.append(f"{where}: {name} is a number or a \"$<state path>\".")
            elif v is not None and v < 1:
                errors.append(f"{where}: {name} must be 1 or more.")
        if s.batch is not None and s.kind != "parallel":
            errors.append(f"{where}: only a parallel step takes batch.")
        if s.per_ac and (s.per_item or s.for_each):
            errors.append(f"{where}: a step runs per AC or per item, not both.")
        if s.collect and s.kind not in ("agent", "parallel"):
            errors.append(f"{where}: only agent steps collect a list.")
        if s.when is not None:
            if s.kind not in ("branch", "gate", "agent", "parallel", "code"):
                errors.append(f"{where}: a {s.kind} step has no 'when'.")
            elif not str(s.when.get("marker") or s.when.get("data") or s.when.get("state") or "").strip():
                errors.append(f"{where}: when needs a marker name (when: {{marker: REPRO, equals: confirmed}}), "
                              "a data path (when: {data: hunt.mode, equals: semi}) or a state path (when: {state: acs}).")
            else:
                src = s.when.get("step")
                for name in (src if isinstance(src, list) else [src] if src else []):
                    if name not in index:
                        errors.append(f"{where}: when.step '{name}' does not exist.")
        if "start_flow" in s.actions():
            if not s.flow:
                errors.append(f"{where}: start_flow needs the workflow to start (flow: fix).")
        elif s.flow or s.seed:
            errors.append(f"{where}: flow and seed belong to a start_flow step.")

    acs = [n for n, s in enumerate(wf.steps) if s.per_ac]
    if acs and acs != list(range(acs[0], acs[-1] + 1)):
        errors.append("The 'for each AC' steps must sit together, one after another.")
    loops = wf.loops()
    for n, s in enumerate(wf.steps):
        if s.per_item and not s.for_each and not any(lp.first <= n <= lp.last for lp in loops if not lp.per_ac):
            errors.append(f"Step '{s.id}': a per_item step must follow a for_each step (or another per_item step) directly.")
    for lp in loops:
        what = "the AC loop" if lp.per_ac else f"the '{wf.steps[lp.first].id}' loop"
        for s in wf.steps[lp.first:lp.last + 1]:
            if s.back in index and index[s.back] > lp.last:
                errors.append(f"Step '{s.id}': back target '{s.back}' is after {what}.")

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
