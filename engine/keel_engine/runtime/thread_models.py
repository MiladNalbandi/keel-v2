"""0.18.64 a model for one flow, or for one step of it, without changing the project (POST /threads/{id}/models).

A flow keeps its models in its start body (`models`, ThreadContext.models): "default", one per agent id (what the
project's Agents page said when it started) and now "step:<step id>" for a step the person gave its own model. A pick
for an agent in this flow replaces that agent's entry; a pick for a step adds its "step:" entry. Each pick keeps what
was there before (`model_picks`), so clearing it (model null) puts that back. Other flows and the project never change.

Which model a step gets (Compiler._model): the cheaper model of a budget cap, then the step's own pick, then a stronger
model after escalate_model, then the workflow step's `model:` name, the agent's entry, the flow's default.
"""

from __future__ import annotations

from typing import Any

from .. import agent_names
from ..workflows.model import Workflow
from . import review_config

STEP = "step:"


def step_key(step_id: str) -> str:
    return STEP + step_id


def static_model(models: dict | None, step, agent: str) -> dict | None:
    """The model a step's agent gets from the flow's table alone (no cap, no escalation): its step pick, the step's
    `model:` name, the agent's entry, the default."""
    models = models or {}
    if step is not None and models.get(step_key(step.id)):
        return models[step_key(step.id)]
    if step is not None and step.model and step.model != "default" and step.model in models:
        return models[step.model]
    return agent_names.lookup(models, agent) or models.get("default")


def agent_steps(wf: Workflow) -> list:
    """The workflow's steps that run an agent (an agent step, a fan-out of one agent)."""
    return [s for s in wf.steps if s.agent]


def targets(wf: Workflow, agent: str | None, step: str | None, phase: str | None) -> tuple[list[str], str | None]:
    """(the keys a pick changes, an error). step: that step only; phase (with agent): every step of that agent in that
    phase (a role such as planning: the explorer's spec steps); agent alone: the agent everywhere in this flow."""
    steps = agent_steps(wf)
    agent = agent_names.canonical((agent or "").strip()) or None
    if step:
        s = next((x for x in wf.steps if x.id == step), None)
        if s is None or not s.agent:
            ids = ", ".join(x.id for x in steps) or "none"
            return [], f'this flow has no agent step "{step}". Its agent steps: {ids}.'
        if agent and agent_names.canonical(s.agent) != agent:
            return [], f'the step "{step}" runs {s.agent}, not {agent}.'
        return [step_key(step)], None
    if not agent:
        return [], "say which agent or which step."
    used = sorted({agent_names.canonical(s.agent) for s in steps})
    if agent not in used:
        return [], f"this flow does not run {agent}. Its agents: {', '.join(used) or 'none'}."
    if phase:
        hit = [s.id for s in steps if agent_names.canonical(s.agent) == agent and (s.phase or "") == phase]
        if not hit:
            return [], f"{agent} has no step in the phase {phase} in this flow."
        return [step_key(i) for i in hit], None
    return [agent], None


def apply(models: dict, picks: dict, keys: list[str], model: dict | None) -> tuple[dict, dict]:
    """(models, picks) with `model` set on these keys, or each pick taken back (model None)."""
    models, picks = dict(models or {}), dict(picks or {})
    for k in keys:
        if model is None:
            if k in picks:
                before = picks.pop(k)
                if before:
                    models[k] = before
                else:
                    models.pop(k, None)
            continue
        if k not in picks:
            picks[k] = models.get(k)        # what the project gave the flow at its start (None: nothing)
        models[k] = {x: model[x] for x in ("provider", "mode", "model", "effort") if model.get(x)}
    return models, picks


def shown_model(models: dict | None, step, agent: str, root: str | None) -> dict | None:
    """What a step runs on, for the table: its static model, and (0.18.65) a final review's stronger model
    (Compiler._model: Settings › Review's review.final) when the person did not pick one for the step."""
    m = static_model(models, step, agent)
    if root and m and not (models or {}).get(step_key(step.id)) and review_config.final_step(step, agent):
        return review_config.final_model(root, m)
    return m


def view(models: dict, picks: dict, wf: Workflow, root: str | None = None) -> dict[str, Any]:
    """The flow's model table: the default, each agent's entry, each step's model now and which ones the person picked
    for this flow."""
    steps = [{"id": s.id, "name": s.name, "agent": s.agent, "phase": s.phase or "none",
              "model": shown_model(models, s, s.agent or "", root),
              "picked": step_key(s.id) in (picks or {}) or agent_names.canonical(s.agent) in (picks or {})}
             for s in agent_steps(wf)]
    return {"models": dict(models or {}), "picks": sorted((picks or {}).keys()), "steps": steps}
