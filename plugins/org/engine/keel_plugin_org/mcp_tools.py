"""The Org plugin's tools in keel's own MCP server (keel2 mcp, keel_engine/mcp_server.py), issue #98: through keel's api
(/api/org/*, which keel's Python api passes on to this part's routes), under its MCP levels.

    read    keel_org_list        the orgs: roles, missions open, problems
            keel_org_show        one org: its roles (title, person or agent, workflows, autonomy, team) and links
            keel_org_templates   the ready-made orgs (Marketplace › Orgs)
            keel_org_missions    an org's missions, newest first: status, what waits
            keel_org_mission     one mission as a tree: each role's status, tokens, and what waits for whom
    write   keel_org_save        create or change an org from YAML or a ready-made one (asks the person first)
            keel_org_give_mission  give a mission to a role (starts its flow)
            keel_org_stop_mission  stop a mission and everything under it (asks first)
            keel_org_accept      accept a reported mission (asks first)

A gate a mission's flow waits at (the plan, the report) is answered like any flow's: keel_read_gate, then keel_resume
with the thread id keel_org_mission names. The org's YAML stays out of agents' reach: keel's own agents get keel's MCP
server at the read level only.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from keel_engine.mcp_api import ApiError, KeelApi, _project
from mcp.server.fastmcp import Context

LIMIT = 20


def _q(s: str) -> str:
    return quote(str(s), safe="")


def _role_line(r: dict) -> str:
    kind = "person" if r.get("human") else f"agent · {r.get('autonomy') or 'act'}"
    work = f" · runs {', '.join(r['workflows'])}" if r.get("workflows") else ""
    team = f" · team: {', '.join(r['team'])}" if r.get("team") else ""
    return f"- {r['id']}: {r.get('title') or r['id']} ({kind}){work}{team}"


def _link_line(lk: dict) -> str:
    return f"- {', '.join(lk['from'])} → {', '.join(lk['to'])}: {', '.join(lk['can'])}"


def org_list(api: KeelApi) -> str:
    rows = api.get("/org/orgs") or []
    if not rows:
        return ("No org yet. keel_org_templates lists the ready-made ones; keel_org_save creates one from a template or "
                "from YAML.")
    out = [f"{len(rows)} org(s):"]
    for o in rows:
        bad = f" · PROBLEMS: {'; '.join(o['problems'])}" if o.get("problems") else ""
        people = [r["id"] for r in o.get("roles") or [] if r.get("human")]
        agents = [r["id"] for r in o.get("roles") or [] if not r.get("human")]
        out.append(f"- {o['id']}: {o.get('name') or o['id']} · people: {', '.join(people) or 'none'} · agents: "
                   f"{', '.join(agents) or 'none'}{bad}")
    return "\n".join(out)


def org_show(api: KeelApi, org: str, yaml: bool = False) -> str:
    o = api.get(f"/org/orgs/{_q(org)}")
    out = [f"Org {o['id']}: {o.get('name') or o['id']}"]
    if o.get("problems"):
        out.append("Problems (fix them before giving a mission): " + "; ".join(o["problems"]))
    if o.get("projects"):
        out.append("Works on: " + ", ".join(o["projects"]))
    lim = o.get("limits") or {}
    if lim:
        out.append("Limits: " + ", ".join(f"{k} {v}" for k, v in lim.items() if v not in (None, "", 0)))
    out += ["Roles:", *[_role_line(r) for r in o.get("roles") or []]]
    out += ["Links (what may pass between roles):", *[_link_line(lk) for lk in o.get("links") or []]]
    givers = [r["id"] for r in o.get("roles") or [] if not r.get("human")]
    if givers:
        out.append("Give a mission with keel_org_give_mission (role: an agent role you link to with delegate).")
    if yaml:
        out += ["YAML:", "```yaml", o.get("yaml", "").rstrip(), "```"]
    return "\n".join(out)


def org_templates(api: KeelApi) -> str:
    rows = api.get("/org/templates") or []
    out = ["Ready-made orgs (keel_org_save with template=<id> makes one yours; change any line later):"]
    for t in rows:
        out.append(f"- {t['id']}: {t['title']} ({t['people']} person(s), {t['agents']} agent(s), {t['levels']} levels) — "
                   f"{t['summary']}")
    return "\n".join(out)


def _wait_text(w: dict | None, top_role: str) -> str:
    """What waits in a mission you gave: its own gates are yours; a gate lower in the tree is its boss's to answer."""
    if not w:
        return ""
    who = "you" if w.get("person") or w.get("role") == top_role else f"the boss of {w.get('role')}"
    return (f" · WAITS for {who} at {w.get('step')} ({w.get('title') or ''}) in thread {w.get('thread_id')}: read it with "
            f"keel_read_gate, answer with keel_resume")


def org_missions(api: KeelApi, org: str) -> str:
    rows = api.get(f"/org/orgs/{_q(org)}/missions", limit=LIMIT) or []
    if not rows:
        return f"{org} has no mission yet. Give one with keel_org_give_mission."
    out = [f"{org}'s missions, newest first:"]
    for m in rows:
        tok = (m.get("rollup") or {}).get("tokens") or 0
        out.append(f"- {m['id']}: {m['title']} · to {m['role']} · {m['status']} · {m.get('children_count', 0)} under it · "
                   f"{tok} tokens{_wait_text(m.get('waiting'), m['role'])}")
    return "\n".join(out)


def _tree_lines(n: dict, depth: int = 0, boss: str = "") -> list[str]:
    pad = "  " * depth
    tok = int(n.get("used_tokens") or 0) + int(n.get("running_tokens") or 0)
    w = n.get("waiting")
    wait = ""
    if w:
        # the mission you gave is yours to answer; one lower down is its boss's (unless its gate is a person's)
        who = "the person" if w.get("person") or depth == 0 else f"its boss ({boss})"
        wait = f" · WAITS for {who} at {w.get('step')} ({w.get('title') or ''}), thread {n.get('thread_id')}"
    line = [f"{pad}- {n['role']}: {n['title']} · {n['status']} · {tok} tokens{wait}"]
    for c in n.get("children") or []:
        line += _tree_lines(c, depth + 1, n["role"])
    return line


def org_mission(api: KeelApi, mission: str) -> str:
    v = api.get(f"/org/missions/{_q(mission)}")
    m = v["mission"]
    out = [f"Mission {m['id']} in org {m.get('org_id')}: {m['title']} ({m['status']})",
           f"Tokens so far: {(v.get('rollup') or {}).get('tokens', 0)}"]
    out += _tree_lines(m)
    rep = m.get("report")
    if isinstance(rep, dict) and rep:
        out.append("The boss's report: " + (str(rep.get("summary") or "").strip() or json.dumps(rep)[:1500]))
    if m["status"] == "reported":
        out.append("It waits for your decision: keel_org_accept, or send it back by answering its report gate "
                   "(keel_read_gate, keel_resume with decision reject and a note).")
    return "\n".join(out)


def org_save(api: KeelApi, org: str, yaml: str | None = None, template: str | None = None) -> str:
    text = (yaml or "").strip()
    if not text and template:
        text = api.get(f"/org/templates/{_q(template)}")["yaml"]
    if not text:
        raise ApiError("Give the org's YAML, or a ready-made one's id (template; keel_org_templates lists them).")
    o = api.put(f"/org/orgs/{_q(org)}", {"yaml": text})
    roles = ", ".join(r["id"] for r in o.get("roles") or [])
    return f"Saved org {o['id']} ({o.get('name') or o['id']}): roles {roles}. keel_org_show {o['id']} shows its links."


def org_give_mission(api: KeelApi, org: str, role: str, title: str, request: str = "", project: str | None = None,
                     workflow: str | None = None, cap_tokens: int | None = None) -> str:
    pid = _project(api, project)["id"]
    body = {"role": role, "project_id": pid, "title": title, "request": request or "", "source": "mcp"}
    if workflow:
        body["workflow"] = workflow
    if cap_tokens:
        body["cap_tokens"] = int(cap_tokens)
    m = api.post(f"/org/orgs/{_q(org)}/missions", body)
    return (f"Mission {m['id']} given to {m['role']} in {org} on {pid} ({m['status']}). Follow it with keel_org_mission "
            f"{m['id']}; it waits for you at its plan (when the role proposes) and at its report.")


def org_stop_mission(api: KeelApi, mission: str) -> str:
    m = api.post(f"/org/missions/{_q(mission)}/stop", {})
    return f"Mission {m['id']} is {m['status']}: it and everything under it stopped."


def org_accept(api: KeelApi, mission: str, why: str = "") -> str:
    m = api.post(f"/org/missions/{_q(mission)}/accept", {"why": why or ""})
    return f"Mission {m['id']} accepted: {m['status']}."


def _acting(srv, guard):
    act = getattr(srv, "act", None)

    async def run(ctx, name: str, what: str, fn, *a, confirm: bool = False, **kw) -> str:
        if act is None:
            return guard(fn, *a, **kw)
        return await act(ctx, name, what, fn, *a, confirm=confirm, **kw)

    return run


def register(srv, api: KeelApi, guard, write: bool) -> None:
    """The read tools, or (write) the acting ones."""
    from mcp.types import ToolAnnotations

    if not write:
        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_org_list() -> str:
            """Org plugin: the orgs (hierarchies of agent roles you draw), with their people, agents and problems."""
            return guard(org_list, api)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_org_show(org: str, yaml: bool = False) -> str:
            """Org plugin: one org's roles (person or agent, the workflows each may run, autonomy, team) and its links
            (delegate, report, ask, approve, notify). yaml=true adds the org's YAML."""
            return guard(org_show, api, org, yaml)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_org_templates() -> str:
            """Org plugin: the ready-made orgs (solo developer, small team, bug squad, startup, maintainer, quality team)."""
            return guard(org_templates, api)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_org_missions(org: str) -> str:
            """Org plugin: an org's missions, newest first: who has it, its status, and what waits (and in which thread)."""
            return guard(org_missions, api, org)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_org_mission(mission: str) -> str:
            """Org plugin: one mission as a tree (each role's part, status and tokens), the boss's report, and what waits
            for whom: a gate is answered with keel_read_gate and keel_resume on the thread it names."""
            return guard(org_mission, api, mission)

        return

    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    rx = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
    act = _acting(srv, guard)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_org_save(org: str, yaml: str | None = None, template: str | None = None, ctx: Context | None = None) -> str:
        """Org plugin: create or change an org (id: lowercase letters, digits, - and _) from its YAML, or from a ready-made
        one (template: its id, keel_org_templates). It decides what agents may do, so the person is asked first."""
        return await act(ctx, "keel_org_save", f"save the org {org}", org_save, api, org, yaml, template, confirm=True)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_org_give_mission(org: str, role: str, title: str, request: str = "", project: str | None = None,
                                    workflow: str | None = None, cap_tokens: int | None = None,
                                    ctx: Context | None = None) -> str:
        """Org plugin: give a mission to an agent role of an org (one you link to with delegate). Its flow starts on the
        project: it plans, gives work to its team, checks it and reports. Optional: the workflow (one the role may run)
        and a token cap."""
        return await act(ctx, "keel_org_give_mission", f"give {role} in {org} the mission {title!r}", org_give_mission,
                         api, org, role, title, request, project, workflow, cap_tokens)

    @srv.tool(annotations=rx, structured_output=False)
    async def keel_org_stop_mission(mission: str, ctx: Context | None = None) -> str:
        """Org plugin: stop a mission and every mission under it (their flows stop). The person is asked first."""
        return await act(ctx, "keel_org_stop_mission", f"stop the mission {mission}", org_stop_mission, api, mission,
                         confirm=True)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_org_accept(mission: str, why: str = "", ctx: Context | None = None) -> str:
        """Org plugin: accept a reported mission (the boss's report is the person's to accept). The person is asked first."""
        return await act(ctx, "keel_org_accept", f"accept the mission {mission}", org_accept, api, mission, why,
                         confirm=True)
