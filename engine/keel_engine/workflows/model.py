"""Workflow and Step, as the contract defines them, plus YAML load/dump."""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

StepKind = Literal["agent", "code", "gate", "branch", "parallel"]
OnLimit = Literal["pause", "cheaper", "stop"]


class Lane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    sub: str | None = None          # agent id (agent lane) or action (code lane)
    kind: Literal["agent", "code"]


class Step(BaseModel):
    # `from` is a Python keyword: the field is items_from, written `from:` in YAML and JSON.
    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)
    id: str
    kind: StepKind
    name: str
    agent: str | None = None
    model: str | None = None
    phase: str | None = None
    action: str | None = None
    per_ac: bool | None = None
    parallel: int | None = None
    lanes: list[Lane] | None = None
    back: str | None = None
    no: str | None = None
    lock: bool | None = None
    max_tokens: int | None = None
    on_limit: OnLimit | None = None
    tools: list[str] | None = None
    # Dynamic fan-out: one agent call per item of a list in the thread's state (state.data[<key>], or acs), at most
    # `cap` of them, `batch` at a time. Results land in state.data["<step id>_results"].
    items_from: str | None = Field(default=None, alias="from")
    cap: int | None = None
    # an int, or "$<state path>" read when the step runs (e.g. "$data.hunt.prove_concurrency")
    batch: int | str | None = None
    # A loop over a list of dicts with an `id` (like per_ac over acs): `for_each` on the loop's first step names the
    # list; every `per_item` step that follows it runs once per item.
    for_each: str | None = None
    per_item: bool | None = None
    # Result markers read from the agent's final text (`REPRO: confirmed`) into state.markers[<step id>].
    markers: list[str] | None = None
    # An agent step whose final text holds a JSON list: it is stored as state.data[<collect>] (for `from`/`for_each`).
    collect: str | None = None
    # branch: `when: {marker: REPRO, equals: confirmed, step: <id, optional>}` instead of a run: command or an agent;
    # `when: {data: hunt.mode, equals: semi}` reads state.data instead. On a gate, `when` says when it pauses: when it
    # does not hold, the gate is approved by the engine and logged as not asked.
    when: dict | None = None
    # start_flow: the workflow to start, its seed (literals, or "$<state path>"), and what this flow does next.
    flow: str | None = None
    seed: dict | None = None
    # A code step's next step: "end" (the flow ends here), "continue" (the next step), or a step id (jump there).
    then: str | None = None
    # Extra task text for the agent of this step (what the step wants that the agent's role does not say); `{{data.x}}`
    # is that value of the flow state.
    instructions: str | None = None
    # A code step whose failing check does not stop the flow: it records markers[<id>].RESULT = pass | fail (and keeps
    # what the check found, like coverage groups) for a branch to read.
    soft: bool | None = None
    # Runs only when work was sent back to it (a failed check after it, or a gate's send-back): skipped otherwise.
    retry_only: bool | None = None
    # How many rounds before keel asks: a code step's fix attempts (instead of settings.fix_attempts), a branch's
    # send-backs to its `no` target (the loop it closes runs at most `rounds` times), a review step's fix rounds.
    rounds: int | None = None
    # A code step: where its failures go once the rounds are used up (instead of asking): "reset and reproduce again".
    after_rounds: str | None = None
    # gate with list choices: the marker (any step) whose value is the default choice, e.g. SIZE from a triage
    recommend: str | None = None
    # A review step: after a fix round, the flow goes on from this step (default: the review step itself).
    redo: str | None = None
    # The opening skip menu (a gate with skip_menu) may turn this step off: deferred = its push gate still waits,
    # optional = nothing downstream needs it. Steps with the same `group` are skipped together (an include's steps).
    skippable: Literal["deferred", "optional"] | None = None
    group: str | None = None
    # Gates: skip_menu asks which skippable steps run (every skip with a reason, in data.ship_skipped); report: verdicts
    # shows the verdict table; choices = one of these per item (payload.choice) or, outside a loop, the gate's named
    # exits (markers[<id>].CHOICE and WHY for a branch), on_skip = {choice, record}: that
    # choice (or payload skip) ends the item as skipped, needs a reason and is appended to data[record].
    # choices as a mapping are named exits instead of approve/reject, on any gate: `{take: take_step, stop: end}`;
    # payload.choice picks one (default: the first), "end" finishes the flow, the answer is kept in
    # data["<gate id>_answer"] ({choice, why, payload}).
    skip_menu: bool | None = None
    report: Literal["verdicts"] | None = None
    choices: list[str] | dict[str, str] | None = None
    on_skip: dict | None = None

    @model_validator(mode="after")
    def _for_each_runs_per_item(self):
        if self.for_each:
            self.per_item = True       # the loop's first step is one of its steps
        return self

    @property
    def looped(self) -> bool:
        """Runs once per AC or per item."""
        return bool(self.per_ac or self.per_item)

    def actions(self) -> list[str]:
        """`verify_green+commit` runs two actions in order."""
        return [a.strip() for a in (self.action or "").split("+") if a.strip()]


# ------------------------------------------------------------------ include

_EXPANDING: list[str] = []      # the includes being expanded right now (a cycle check)


def expand_includes(steps: list) -> list:
    """`{id: ship, kind: include, flow: ship}` becomes the steps of that workflow, in place.

    Included ids get the include's id as a prefix (`ship_verify`); back, no, redo, then, after_rounds and when.step that point at an
    included step follow. With `skippable` on the include, every included step is one skippable unit (group = the
    include's id). A skippable step that has no group keeps its own id as its group name, so a skip menu shows
    `release`, not `ship_release`. Includes nest (ship includes cover); a cycle is an error.
    """
    if not any(isinstance(st, dict) and st.get("kind") == "include" for st in steps or []):
        return steps
    out: list = []
    for st in steps:
        if isinstance(st, dict) and st.get("kind") == "include":
            out += _included(st)
        else:
            out.append(st)
    return out


def _included(inc: dict) -> list[dict]:
    from .templates import get_template      # templates import this module

    name = str(inc.get("flow") or "").strip()
    iid = str(inc.get("id") or name).strip()
    if not name:
        raise ValueError(f"Step '{iid}': an include needs the workflow to include (flow: ship).")
    if name in _EXPANDING:
        raise ValueError(f"Step '{iid}': include cycle {' -> '.join(_EXPANDING + [name])}.")
    _EXPANDING.append(name)
    try:
        tpl = get_template(name)
    finally:
        _EXPANDING.pop()
    if not tpl:
        raise ValueError(f"Step '{iid}': there is no workflow {name} to include.")
    ids = {s.id for s in tpl.steps}

    def ren(x):
        return f"{iid}_{x}" if x in ids else x

    out = []
    for s in tpl.steps:
        d = s.model_dump(exclude_none=True, by_alias=True)
        d["id"] = ren(s.id)
        for k in ("back", "no", "redo", "then", "after_rounds"):
            if d.get(k):
                d[k] = ren(d[k])
        src = (d.get("when") or {}).get("step") if isinstance(d.get("when"), dict) else None
        if src:
            d["when"] = {**d["when"], "step": [ren(x) for x in src] if isinstance(src, list) else ren(src)}
        if inc.get("skippable"):
            d["skippable"], d["group"] = inc["skippable"], iid
        elif d.get("skippable") and not d.get("group"):
            d["group"] = s.id
        out.append(d)
    return out


class Budget(BaseModel):
    max_tokens: int | None = None
    on_limit: OnLimit | None = None


class Workflow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    based_on: str | None = None
    keel_rules: bool = True
    version: int = 1
    budget: Budget | None = None
    steps: list[Step] = Field(default_factory=list)
    yaml: str = ""

    @model_validator(mode="before")
    @classmethod
    def _includes(cls, data):
        if isinstance(data, dict) and isinstance(data.get("steps"), list):
            data = {**data, "steps": expand_includes(data["steps"])}
        return data

    @property
    def flow(self) -> str:
        """The keel v1 flow name this workflow runs as (feature, change, fix, init, or its own id)."""
        base = (self.based_on or self.id).split("/")[-1]
        return base

    def step(self, sid: str) -> Step | None:
        return next((s for s in self.steps if s.id == sid), None)

    def loops(self) -> list[Loop]:
        """The loops in step order: the per-AC steps (over acs), and each run of per_item steps that starts at a
        `for_each` step (over that list). A loop's steps sit together; validate.py says so when they do not."""
        out: list[Loop] = []
        acs = [i for i, s in enumerate(self.steps) if s.per_ac]
        if acs:
            out.append(Loop(id="ac", key="acs", first=acs[0], last=acs[-1]))
        cur: Loop | None = None
        for i, s in enumerate(self.steps):
            if s.for_each:
                cur = Loop(id=s.id, key=s.for_each, first=i, last=i)
                out.append(cur)
            elif s.per_item and cur and cur.last == i - 1:
                cur.last = i
            else:
                cur = None
        return sorted(out, key=lambda lp: lp.first)

    def loop_of(self, i: int) -> Loop | None:
        return next((lp for lp in self.loops() if lp.first <= i <= lp.last), None)


class Loop(BaseModel):
    """Steps first..last run once per entry of state[key] (acs) or state.data[key] (a for_each list)."""
    id: str            # "ac" for the per-AC loop, else the id of the for_each step
    key: str
    first: int
    last: int

    @property
    def per_ac(self) -> bool:
        return self.id == "ac"


class WorkflowError(ValueError):
    pass


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "workflow"


def to_dict(wf: Workflow) -> dict:
    data = {"id": wf.id, "name": wf.name}
    if wf.based_on:
        data["based_on"] = wf.based_on
    data["keel_rules"] = wf.keel_rules
    data["version"] = wf.version
    if wf.budget and (wf.budget.max_tokens or wf.budget.on_limit):
        data["budget"] = wf.budget.model_dump(exclude_none=True)
    data["steps"] = [s.model_dump(exclude_none=True) for s in wf.steps]
    return data


def dump_yaml(wf: Workflow) -> str:
    return yaml.safe_dump(to_dict(wf), sort_keys=False, allow_unicode=True, width=120)


def load_yaml(text: str) -> Workflow:
    """Parse workflow YAML. Raises WorkflowError with a readable message."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"The YAML does not parse: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError("The YAML must be a mapping with name and steps.")
    return from_dict(data, text)


def from_dict(data: dict, text: str | None = None) -> Workflow:
    data = dict(data)
    if not data.get("name"):
        raise WorkflowError("The workflow needs a name.")
    data.setdefault("id", slug(data["name"]))
    data.setdefault("steps", [])
    # YAML 1.1 reads an unquoted `no:` key (a branch's target) as false.
    data["steps"] = [{("no" if k is False else k): v for k, v in st.items()} if isinstance(st, dict) else st
                     for st in data["steps"] or []]
    try:
        wf = Workflow.model_validate({k: v for k, v in data.items() if k != "yaml"})
    except Exception as exc:  # pydantic ValidationError, kept readable
        raise WorkflowError(_pydantic_message(exc)) from exc
    wf.yaml = text if text is not None else dump_yaml(wf)
    return wf


def _pydantic_message(exc: Exception) -> str:
    errs = getattr(exc, "errors", None)
    if not errs:
        return str(exc)
    parts = []
    for e in errs():
        loc = ".".join(str(x) for x in e.get("loc", []))
        parts.append(f"{loc}: {e.get('msg')}")
    return "; ".join(parts)
