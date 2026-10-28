"""keel 0.18.5: a new workflow, rule pack or agent, made through keel's MCP tools (and keel TUI's /create): the format
first (keel_format), a check that saves nothing (keel_workflow_check, keel_rule_check), then the save, which asks the
person first. keel checks everything again when it saves; what it refuses is said line by line.
"""

from __future__ import annotations

import json
from typing import Any

from .formats import AGENT_TOOLS, FORMATS
from .mcp_api import q
from .mcp_server import ApiError, KeelApi, _project


_q = q


def fmt(kind: str) -> str:
    if kind not in FORMATS:
        raise ApiError(f"kind is one of {', '.join(FORMATS)}.")
    return FORMATS[kind]


def _review(r: dict) -> str:
    out = [f"{r.get('name') or '?'}: {r.get('steps', 0)} steps, {r.get('gates', 0)} gates"
           + (f"; agents {', '.join(r['agents'])}" if r.get("agents") else "")
           + (f"; commands {', '.join(r['commands'])}" if r.get("commands") else "")
           + ("; it changes files" if r.get("edits_files") else "; it changes no file")]
    lines = {e.get("message"): e.get("line") for e in r.get("lines") or []}
    out += [f"  error{f' (line {lines[e]})' if lines.get(e) else ''}: {e}" for e in r.get("errors") or []]
    out += [f"  warning: {w}" for w in r.get("warnings") or []]
    return "\n".join(out)


def check_workflow(api: KeelApi, yaml_text: str, project: str | None = None) -> str:
    p = _project(api, project)
    r = api.post(f"/projects/{p['id']}/workflows/check", {"yaml": yaml_text}) or {}
    return ("keel can run it. " if r.get("valid") and not r.get("errors") else "keel cannot save it yet. ") + _review(r)


def create_workflow(api: KeelApi, yaml_text: str, folder: str | None = None, project: str | None = None) -> str:
    p = _project(api, project)
    r = api.post(f"/projects/{p['id']}/workflows/check", {"yaml": yaml_text}) or {}
    if r.get("errors") or r.get("valid") is False:
        return "keel did not save it:\n" + _review(r)
    res = api.post(f"/projects/{p['id']}/workflows/import", {"yaml": yaml_text, **({"folder": folder} if folder else {})}) or {}
    w = res.get("workflow") or {}
    return (f"saved the workflow {w.get('id')} on {p['id']}" + (f" in the folder {folder}" if folder else "")
            + f" ({len(w.get('steps') or [])} steps). Start it: keel_start_flow(type={w.get('id')!r}, idea=...).")


def check_rule(api: KeelApi, yaml_text: str, project: str | None = None) -> str:
    p = _project(api, project)
    r = api.post(f"/projects/{p['id']}/rules/check", {"yaml": yaml_text}) or {}
    if not r.get("ok"):
        return "keel cannot save it yet:\n" + "\n".join(f"  {e}" for e in r.get("errors") or [])
    dry = r.get("dry_run") or {}
    blocked = dry.get("blocked") or []
    return (f"keel can save it. On the recent tool calls it would have refused {len(blocked)}"
            + (f" of {dry.get('checked', 0)}:\n" + "\n".join(f"  {b.get('kind')}: {str(b.get('target') or '')[:120]}"
                                                             + (f" ({b['agent']})" if b.get("agent") else "") for b in blocked[:10])
               if blocked else f" of {dry.get('checked', 0)}."))


def create_rule(api: KeelApi, yaml_text: str, project: str | None = None) -> str:
    p = _project(api, project)
    pid = _q(p["id"])
    check = api.post(f"/projects/{pid}/rules/check", {"yaml": yaml_text}) or {}
    if not check.get("ok"):
        return "keel did not save it:\n" + "\n".join(f"  {e}" for e in check.get("errors") or [])
    r = api.post(f"/projects/{pid}/rules/packs", {"yaml": yaml_text}) or {}
    blocked = len(((check.get("dry_run") or {}).get("blocked")) or [])
    name = (r.get("pack") or {}).get("id") or "the pack"
    return (f"saved the rule pack {name} on {p['id']} (.keel/rules/{name}.yaml); it applies from the next tool call. "
            f"On the recent tool calls it would have refused {blocked}.")


def agent_definitions(api: KeelApi, project: str | None = None) -> str:
    p = _project(api, project)
    rows = api.get(f"/projects/{p['id']}/agents") or []
    if not rows:
        return "no agent."
    from .mcp_models import _roles_line, model_text
    return "\n".join([f"- {a.get('id')}  {a.get('label')} · {'yours' if a.get('custom') else 'keel'}"
                      + ("" if a.get("enabled", True) else " · off")
                      + f" · model {model_text(a.get('model'))}"
                      + f" · tools {', '.join(a.get('tools') or [])} · phases {', '.join(a.get('phases') or [])}"
                      + (f"\n    {a['about']}" if a.get("about") else "") for a in rows]
                     # 0.18.64 the words people use for a job ("planning" named no agent), as keel_agent_model takes them
                     + [f"Role words: {_roles_line()}."])


def create_agent(api: KeelApi, name: str, prompt: str, about: str = "", tools: list[str] | None = None,
                 phases: list[str] | None = None, project: str | None = None, provider: str | None = None,
                 mode: str | None = None, model: str | None = None, effort: str | None = None) -> str:
    if not name.strip() or not prompt.strip():
        raise ApiError("an agent needs a name and its instructions (prompt).")
    tools = tools or ["Read", "Grep", "Glob"]
    bad = [t for t in tools if t not in AGENT_TOOLS]
    if bad:
        raise ApiError(f"unknown tools {', '.join(bad)}: pick from {', '.join(AGENT_TOOLS)}.")
    p = _project(api, project)
    body: dict[str, Any] = {"name": name.strip(), "about": about.strip(), "prompt": prompt.strip(), "tools": tools,
                            "phases": phases or ["any"]}
    if any((provider, mode, model, effort)):
        # its model and thinking effort: from the project's default model, the given parts changed (keel_agent_model's rules)
        from .mcp_models import _next_model
        settings = api.get(f"/projects/{p['id']}/settings") or {}
        base = (settings.get("effective") or {}).get("default_model") or {}
        body["model"] = _next_model(api.get("/providers/models") or {}, base, provider, mode, model, effort)
    a = api.post(f"/projects/{p['id']}/agents", body) or {}
    from .mcp_models import model_text
    return (f"saved the agent {a.get('id')} on {p['id']} (tools {', '.join(a.get('tools') or tools)}; "
            f"model {model_text(a.get('model'))}). "
            f"A workflow uses it in an agent step: agent: {a.get('id')}.")


def agent_from_json(text: str) -> dict:
    """A keel-agent block's JSON as create_agent's arguments (keel TUI and KeelBot's button)."""
    v = json.loads(text)
    if not isinstance(v, dict):
        raise ApiError("an agent is a JSON object: name, about, prompt, tools, phases.")
    return {"name": str(v.get("name") or v.get("label") or ""), "about": str(v.get("about") or ""), "prompt": str(v.get("prompt") or ""),
            "tools": list(v.get("tools") or []) or None, "phases": list(v.get("phases") or []) or None}
