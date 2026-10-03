"""How many tokens a workflow will likely use.

Per agent call: tokens = median(in + out) from this project's history, or the agent's default.
A step runs (ACs if per_ac) x (parallel copies) x (1 + retry rate) times. The range is 0.7x to 1.6x.

Prices follow each agent's model: API mode pays per token (catalog price table); subscription and
opencode modes are paid by the plan, so they cost $0 per token (Copilot still counts premium requests).
"""

from __future__ import annotations

from statistics import mean, median

from ..models import catalog
from .model import Step, Workflow

PROVIDERS = ["fake", "claude", "codex", "copilot"]


def _model_for(step: Step, agent: str, models: dict) -> tuple[str, str, str]:
    """(provider, model id, mode) for an agent step."""
    if step.model and step.model != "default":
        if step.model in models:
            m = models[step.model]
            return m["provider"], m.get("model", ""), m.get("mode", "api")
        prov = catalog.provider_of(step.model)
        if prov:
            return prov, step.model, "api"
    m = models.get(agent) or models.get("default")
    if m:
        return m["provider"], m.get("model", ""), m.get("mode", "api")
    return "fake", "fake", "api"


def _agent_calls(step: Step) -> list[str]:
    """The agents one run of this step calls."""
    if step.kind == "agent":
        return [step.agent or ""]
    if step.kind == "parallel":
        if step.lanes:
            out = []
            for lane in step.lanes:
                if lane.kind == "agent":
                    out += [lane.sub or step.agent or ""] * max(1, step.parallel or 1)
            return out
        return [step.agent or ""] * max(1, step.parallel or 1)
    return []


def estimate(wf: Workflow, acs: int, history: list[dict] | None = None, models: dict | None = None) -> dict:
    models = {k: (v if isinstance(v, dict) else v.model_dump()) for k, v in (models or {}).items()}
    by_agent: dict[str, list[dict]] = {}
    for h in history or []:
        by_agent.setdefault(h.get("agent", ""), []).append(h)
    table = catalog.prices()

    total, cost, premium = 0.0, 0.0, 0.0
    by_provider = {p: 0 for p in PROVIDERS}
    cost_by_provider = {p: 0.0 for p in PROVIDERS}
    per_step = []
    for step in wf.steps:
        step_tokens = 0.0
        for agent in _agent_calls(step):
            hist = by_agent.get(agent)
            if hist:
                tin = median(h.get("tokens_in", 0) for h in hist)
                tout = median(h.get("tokens_out", 0) for h in hist)
                retry = mean(h.get("retries", 0) for h in hist)
            else:
                k_in, k_out, retry = catalog.AGENT_DEFAULTS.get(agent, catalog.FALLBACK_AGENT)
                tin, tout = k_in * 1000, k_out * 1000
            times = (max(acs, 0) if step.per_ac else 1) * (1 + retry)
            provider, model, mode = _model_for(step, agent, models)
            p = catalog.price(provider, model, table)
            tokens = (tin + tout) * times
            step_tokens += tokens
            by_provider[provider] = by_provider.get(provider, 0) + int(round(tokens))
            c = 0.0 if mode in ("subscription", "opencode") else (tin * times * p.get("in", 0) + tout * times * p.get("out", 0)) / 1e6
            cost += c
            cost_by_provider[provider] = cost_by_provider.get(provider, 0.0) + c
            premium += times * p.get("premium", 0)
        total += step_tokens
        per_step.append({"step": step.id, "tokens": int(round(step_tokens))})

    tokens = int(round(total))
    return {
        "tokens": tokens,
        "low": int(round(tokens * 0.7)),
        "high": int(round(tokens * 1.6)),
        "cost_usd": round(cost, 4),
        "premium_requests": int(round(premium + 0.4999)) if premium else 0,
        "by_provider": by_provider,
        "cost_by_provider": {k: round(v, 4) for k, v in cost_by_provider.items()},
        "per_step": per_step,
    }
