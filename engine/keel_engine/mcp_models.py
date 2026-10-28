"""keel 0.18.9: the model and thinking effort of an agent, or of KeelBot, through keel's MCP tools: how a model reads
in the tools' answers (model_text), and keel_agent_model, which changes them (it asks the person first).

The effort choices come from keel's catalog (GET /providers/models): a model's own `efforts`, else its provider's;
[] means the model has no effort setting (keel ignores one). "default" is no effort: the engine's own default.

0.18.64 keel_models lists every provider with its models and the efforts each takes, and which modes are set up; a wrong
provider, mode, model or effort is refused with the ones there are; keel_agent_model takes a role word (planning: the
explorer in the spec phase) and changes one flow (thread_id) or one step of it (step) without the project.
"""

from __future__ import annotations

from typing import Any

from . import agent_names
from .mcp_api import q
from .mcp_server import ApiError, KeelApi, _project

KEELBOT = ("keelbot", "helper")     # KeelBot's model is the helper agent's (the Agents page)


_q = q


def model_text(m: dict | None, effort_default: bool = True) -> str:
    """claude/sonnet (subscription) · effort high; "· effort default" when none is set."""
    if not m:
        return "the default model"
    out = f"{m.get('provider')}/{m.get('model')}" + (f" ({m['mode']})" if m.get("mode") else "")
    if m.get("effort"):
        return out + f" · effort {m['effort']}"
    return out + (" · effort default" if effort_default else "")


def efforts_of(catalog: dict, m: dict) -> list[str]:
    """The model's effort choices in keel's catalog: its own list, else its provider's ([] = no effort setting)."""
    prov = (catalog or {}).get(m.get("provider") or "") or {}
    items = ((prov.get("modes") or {}).get(m.get("mode") or "") or [])
    own = next((x for x in items if x.get("id") == m.get("model")), None)
    if own is not None and "efforts" in own:
        return list(own.get("efforts") or [])
    return list(prov.get("efforts") or [])


def _models_of(catalog: dict, provider: str, mode: str | None) -> list[str]:
    """The model ids keel's catalog lists for this provider in this mode (every mode when none is given)."""
    modes = ((catalog or {}).get(provider) or {}).get("modes") or {}
    picked = [modes.get(mode) or []] if mode else list(modes.values())
    out: list[str] = []
    for items in picked:
        for x in items or []:
            if x.get("id") and x["id"] not in out:
                out.append(x["id"])
    return out


def _check_model(catalog: dict, nxt: dict) -> None:
    """0.18.64 a wrong mode or model is refused with the ones there are (a wrong model said only "no effort setting")."""
    modes = ((catalog or {}).get(nxt["provider"]) or {}).get("modes") or {}
    if nxt.get("mode") and modes and nxt["mode"] not in modes:
        raise ApiError(f'{nxt["mode"]} is not a mode of {nxt["provider"]}; pick one of: {", ".join(modes)}.')
    ids = _models_of(catalog, nxt["provider"], nxt.get("mode"))
    if ids and nxt["model"] not in ids:
        other = [m for m, items in modes.items() if any(x.get("id") == nxt["model"] for x in items or [])]
        where = f" (it is one in the {other[0]} mode: pass mode {other[0]})" if other else ""
        mode = f" in the {nxt['mode']} mode" if nxt.get("mode") else ""
        raise ApiError(f'{nxt["model"]} is not a model of {nxt["provider"]}{mode}{where}; pick one of: {", ".join(ids)}. '
                       "(A model keel does not list: the dashboard's model picker takes any id, under Other model.)")


def effort_problem(m: dict, choices: list[str], want: str) -> str:
    """0.18.64 the refusal of an effort this model does not take, with the ones it does (or that it has none)."""
    name = f"{m['provider']}/{m['model']}"
    if not choices:
        return f"{name} has no effort setting; it has none. Leave effort out (or say default)."
    return f'{name} has no effort setting "{want}"; it takes: {", ".join(choices)}, or default.'


def _next_model(catalog: dict, now: dict, provider: str | None, mode: str | None, model: str | None,
                effort: str | None) -> dict:
    """The current model with the given parts changed. Another provider starts on its default mode and model; a new
    model keeps the effort when it has it; effort "default" clears it. 0.18.64 refuses a provider, mode, model or
    effort keel's catalog does not have, naming the ones it has (keel_models lists them all)."""
    nxt = {k: v for k, v in (now or {}).items() if k in ("provider", "mode", "model", "effort") and v}
    if provider and provider != nxt.get("provider"):
        prov = (catalog or {}).get(provider)
        if prov is None:
            raise ApiError(f'{provider} is not a provider keel knows; pick one of: {", ".join(catalog or {}) or "none"}.')
        d = prov.get("default") or {}
        nxt = {"provider": provider, "mode": d.get("mode") or "api", "model": d.get("model") or ""}
        if mode and not model and mode != nxt["mode"]:
            # another mode of the new provider: its first model there (the default mode's model may not be in it)
            nxt["model"] = next(iter(_models_of(catalog, provider, mode)), nxt["model"])
    if mode:
        nxt["mode"] = mode
    if model:
        nxt["model"] = model
    if not nxt.get("provider") or not nxt.get("model"):
        raise ApiError("say which model: provider and model (keel_models lists them).")
    if provider or mode or model:
        _check_model(catalog, nxt)          # only what changes: a model set by id in the dashboard keeps working
    choices = efforts_of(catalog, nxt)
    want = (effort or "").strip().lower()
    if want in ("default", "none"):
        nxt.pop("effort", None)
    elif want:
        if want not in choices:
            raise ApiError(effort_problem(nxt, choices, want))
        nxt["effort"] = want
    elif nxt.get("effort") and nxt["effort"] not in choices:
        nxt.pop("effort")                          # the new model does not have the old effort: its default
    return nxt


def _roles_line() -> str:
    return "; ".join(agent_names.role_text(agent_names.role(k)) for k in agent_names.ROLES)


def _agent_of(api: KeelApi, pid: str, name: str) -> tuple[str, dict | None, dict]:
    """(agent id, the role word it came from or None, the agent's row): an agent id, an old name (agent_names.RENAMED),
    keelbot, or 0.18.64 a role word (planning: the explorer in the spec phase; agent_names.ROLES)."""
    rows = api.get(f"/projects/{pid}/agents") or []
    key = name.strip()
    aid = "helper" if key.lower() in KEELBOT else (agent_names.canonical(key) or "")   # old name: new agent
    row = next((a for a in rows if a.get("id") == aid), None)
    if row is not None:
        return aid, None, row
    r = agent_names.role(key)
    row = next((a for a in rows if r and a.get("id") == r["agent"]), None)
    if row is not None:
        return r["agent"], r, row
    if aid == "helper":
        raise ApiError("KeelBot is not on in this project (Plugins › KeelBot).")
    raise ApiError(f'no agent "{name}". The agents: {", ".join(str(a.get("id")) for a in rows) or "none"}. '
                   f"Role words: {_roles_line()}.")


def agent_model(api: KeelApi, agent: str | None, provider: str | None = None, mode: str | None = None, model: str | None = None,
                effort: str | None = None, chat: str | None = None, project: str | None = None,
                thread_id: str | None = None, step: str | None = None) -> str:
    """Change an agent's (or KeelBot's) model and effort for the project; with `chat`, only that KeelBot chat's;
    0.18.64 with `thread_id` or `step`, only that flow's (an agent, a role's steps, one step; the project keeps its own)."""
    if not any((provider, mode, model, effort)):
        raise ApiError("say what to change: provider, mode, model or effort (\"default\" clears the effort).")
    p = _project(api, project)
    catalog: dict[str, Any] = api.get("/providers/models") or {}
    if chat:
        s = api.get(f"/projects/{p['id']}/helper/sessions/{_q(chat)}") or {}
        nxt = _next_model(catalog, s.get("model") or {}, provider, mode, model, effort)
        api.patch(f"/projects/{p['id']}/helper/sessions/{_q(chat)}", {"model": nxt})
        return f"KeelBot's chat {chat} now answers with {model_text(nxt)}."
    if thread_id or step:
        return _flow_model(api, p, catalog, agent, thread_id, step, provider, mode, model, effort)
    if not (agent or "").strip():
        raise ApiError("say which agent (keel_agent_definitions lists them), or a step of a flow (step, thread_id).")
    aid, r, row = _agent_of(api, p["id"], agent or "")
    nxt = _next_model(catalog, row.get("model") or {}, provider, mode, model, effort)
    saved = api.put(f"/projects/{p['id']}/agents/{_q(aid)}", {"model": nxt}) or {}
    who = "KeelBot" if aid == "helper" else f"the agent {aid}" + (f" ({agent_names.role_text(r)})" if r else "")
    out = (f"{who} now uses {model_text(saved.get('model') or nxt)} on {p['id']}"
           + (" (new chats; keel_agent_model with chat changes an open one)." if aid == "helper" else " (from its next run)."))
    if r and r.get("phase"):
        # a role is not an agent of its own: the project's explorer changed for every phase it works in
        out += (f" This is the {aid} of the whole project, in every phase, not only {r['phase']}; for one flow pass "
                "thread_id (only its steps in that phase change), for one step pass step.")
    return out


def _flow_model(api: KeelApi, p: dict, catalog: dict, agent: str | None, thread_id: str | None, step: str | None,
                provider: str | None, mode: str | None, model: str | None, effort: str | None) -> str:
    """0.18.64 a model for one flow only (POST /api/threads/{id}/models): an agent in it, a role's steps (planning: the
    explorer's spec steps) or one step. The project's Agents page and every other flow keep theirs."""
    from .mcp_api import _flow

    if not thread_id:
        thread, _ = _flow(api, p["id"])
        if not thread:
            raise ApiError("no flow on this project: pass thread_id (keel_flows lists them).")
        thread_id = thread["thread_id"]
    view = api.get(f"/threads/{_q(thread_id)}/models") or {}
    steps, table = view.get("steps") or [], view.get("models") or {}
    body: dict[str, Any] = {}
    r = None
    name = (agent or "").strip()
    if name:
        runs = {agent_names.canonical(s.get("agent")) for s in steps}
        r = agent_names.role(name) if agent_names.canonical(name) not in runs else None
        body["agent"] = r["agent"] if r else agent_names.canonical(name)
        if r and r.get("phase") and not step:
            body["phase"] = r["phase"]
    if step:
        body["step"] = step
    if not body:
        raise ApiError("say which agent or which step (keel_agent_definitions lists the agents).")
    first = next((s for s in steps if (s.get("id") == step if step else
                                       agent_names.canonical(s.get("agent")) == body["agent"]
                                       and (not body.get("phase") or s.get("phase") == body["phase"]))), None)
    now = (first or {}).get("model") or table.get(body.get("agent") or "") or table.get("default") or {}
    nxt = _next_model(catalog, now, provider, mode, model, effort)
    res = api.post(f"/threads/{_q(thread_id)}/models", {**body, "model": nxt}) or {}
    changed = [str(c) for c in res.get("changed") or []]
    names = [c.removeprefix("step:") for c in changed if c.startswith("step:")]
    what = (f"the step {names[0]}" if len(names) == 1 else f"the steps {', '.join(names)}" if names
            else f"the agent {body.get('agent')}")
    role = f" ({agent_names.role_text(r)})" if r else ""
    return (f"flow {thread_id}: {what}{role} now {'use' if len(names) > 1 else 'uses'} {model_text(nxt)}, from the next "
            "agent call. Only this flow changed: the project's agents and the other flows keep their models.")


def models(api: KeelApi) -> str:
    """0.18.64 keel_models: every provider keel knows, each mode's models with the efforts each takes, and whether that
    mode is set up (keel has its login or API key; GET /api/connections says ready, never the secret)."""
    catalog: dict[str, Any] = api.get("/providers/models") or {}
    ready: dict[tuple[str, str], tuple[bool, str]] = {}
    try:
        for pv in (api.get("/connections") or {}).get("providers") or []:
            for m in pv.get("modes") or []:
                ready[(pv.get("id"), m.get("id"))] = (bool(m.get("ready")), str(m.get("detail") or ""))
    except ApiError:
        ready = {}                       # an older keel or no connections page: the models without the set-up state
    out = ["keel's models: each provider's modes, their models and the efforts each takes. \"set up\": keel has the "
           "login or API key for that mode (no secret is shown)."]
    for pid, prov in catalog.items():
        d = prov.get("default") or {}
        out.append(f"{pid} ({prov.get('label') or pid}) · default {d.get('mode')}/{d.get('model')}"
                   + (f" · effort {d['effort']}" if d.get("effort") else ""))
        for mode, items in (prov.get("modes") or {}).items():
            st = ready.get((pid, mode))
            state = ("set up" if st[0] else f"not set up: {st[1]}" if st[1] else "not set up") if st else \
                ("set up" if pid == "fake" else "set-up state unknown")
            groups: dict[tuple, list[str]] = {}
            for x in items or []:
                eff = tuple(efforts_of(catalog, {"provider": pid, "mode": mode, "model": x.get("id")}))
                groups.setdefault(eff, []).append(str(x.get("id")))
            parts = [f"{', '.join(ids)} (effort {', '.join(e) if e else 'none'})" for e, ids in groups.items()]
            out.append(f"  {mode} [{state}]: " + ("; ".join(parts) or "no models listed"))
    out.append("Change one with keel_agent_model(agent, provider, mode, model, effort): the project's agent; with thread_id "
               "one flow only; with step one step of it. effort default = the engine's own.")
    out.append("Role words keel_agent_model takes: " + _roles_line() + ".")
    return "\n".join(out)
