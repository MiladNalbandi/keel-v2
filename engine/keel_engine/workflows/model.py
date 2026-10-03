"""Workflow and Step, as the contract defines them, plus YAML load/dump."""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

StepKind = Literal["agent", "code", "gate", "branch", "parallel"]
OnLimit = Literal["pause", "cheaper", "stop"]


class Lane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    sub: str | None = None          # agent id (agent lane) or action (code lane)
    kind: Literal["agent", "code"]


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
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

    def actions(self) -> list[str]:
        """`verify_green+commit` runs two actions in order."""
        return [a.strip() for a in (self.action or "").split("+") if a.strip()]


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

    @property
    def flow(self) -> str:
        """The keel v1 flow name this workflow runs as (feature, change, fix, init, or its own id)."""
        base = (self.based_on or self.id).split("/")[-1]
        return base

    def step(self, sid: str) -> Step | None:
        return next((s for s in self.steps if s.id == sid), None)


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
