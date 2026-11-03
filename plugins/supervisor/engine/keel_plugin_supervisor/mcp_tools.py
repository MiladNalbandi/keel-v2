"""The Supervisor's tools in keel's own MCP server (keel2 mcp, keel_engine/mcp_server.py), through keel's api.

    keel_supervisor       read: its switches (keel-wide, project, plugin, the flow), mode and tokens, and its last
                          decisions with their reasons
    keel_supervisor_set   --write: turn it on or off keel-wide, for the project or for the flow now (on: null = follow),
                          or change the flow's mode; "act" asks the person first (it lets the Supervisor answer gates);
                          "fix" also needs the admin level (the api refuses it below that).
                          The never-kinds cannot be changed from here.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from keel_engine.mcp_server import KeelApi, _project

RESULT = {"applied": "answered", "suggested": "suggested", "escalated": "asked the person", "stale": "too late"}


def _thread(api: KeelApi, pid: str, thread_id: str | None) -> str | None:
    if thread_id:
        return thread_id
    t = (api.get(f"/projects/{pid}/flow") or {}).get("thread") or {}
    return t.get("thread_id")


def decisions_text(rows: list[dict]) -> str:
    if not rows:
        return "The Supervisor has not looked at anything yet."
    out = []
    for r in rows:
        what = RESULT.get(r.get("outcome") or "", r.get("outcome") or "")
        if r.get("outcome") == "applied":
            what += f" {r.get('choice') or r.get('decision') or ''}".rstrip()
        why = r.get("summary") or r.get("why") or ""
        code = f" [{r['code']}]" if r.get("code") else ""
        out.append(f"- {r.get('at', '')[:19]} {r.get('step') or ''} {r.get('ac') or ''}: {what}{code}"
                   f" ({r.get('confidence') or '-'}, {int(r.get('tokens') or 0):,} tokens) {why}".replace("  ", " "))
    return "\n".join(out)


SCOPES = ("keel", "project", "flow")


def _yes(v) -> str:
    return "on" if v else "off"


def supervisor_status(api: KeelApi, project: str | None = None, thread_id: str | None = None) -> str:
    pid = _project(api, project)["id"]
    tid = _thread(api, pid, thread_id)
    sw = api.get(f"/projects/{pid}/supervisor/switch") or {}
    own = sw.get("own")
    head = [f"Supervisor in {pid}: {_yes(sw.get('on'))} (keel-wide {_yes(sw.get('keel'))}, project "
            f"{_yes(sw.get('project'))}{'' if own is not None else ' following keel-wide'}, plugin {_yes(sw.get('plugin'))})."]
    if tid:
        v = api.get(f"/projects/{pid}/flows/{tid}/supervisor") or {}
        s = v.get("settings") or {}
        used = int((v.get("used") or {}).get("tokens") or 0)
        off_by = (s.get("switches") or {}).get("off_by")
        head.append(f"Flow {tid}: Supervisor {'on' if s.get('on') else 'off'}{f' (off by the {off_by} switch)' if off_by else ''}, "
                    f"mode {s.get('mode', 'shadow')}, {used:,} of {int(s.get('cap_tokens') or 0):,} tokens used.")
    rows = (api.get(f"/projects/{pid}/supervisor/decisions", threadId=tid, limit=20) or {}).get("decisions") or []
    return "\n".join(head + [decisions_text(rows)])


def supervisor_set(api: KeelApi, on: bool | None = None, mode: str | None = None, project: str | None = None,
                   thread_id: str | None = None, scope: str = "project", follow: bool = False) -> str:
    """scope keel: the keel-wide switch; project: the project's (follow: it follows keel-wide again); flow: the flow
    now's switch (follow: it follows the project again) and its mode."""
    if scope not in SCOPES:
        return f"scope is one of {', '.join(SCOPES)}."
    if mode is not None and scope != "flow":
        scope = "flow"                          # a mode belongs to a flow
    if scope == "keel":
        if on is None:
            return "Say on=true or on=false."
        v = api.post("/supervisor/switch", {"on": bool(on)}) or {}
        return f"Supervisor keel-wide: {_yes(v.get('on'))}."
    pid = _project(api, project)["id"]
    if scope == "project":
        if on is None and not follow:
            return "Say on=true, on=false, or follow=true."
        v = api.post(f"/projects/{pid}/supervisor/switch", {"on": None if follow else bool(on)}) or {}
        return (f"Supervisor in {pid}: {_yes(v.get('on'))} (keel-wide {_yes(v.get('keel'))}, project {_yes(v.get('project'))}"
                f"{', following keel-wide' if v.get('own') is None else ''}, plugin {_yes(v.get('plugin'))}).")
    tid = _thread(api, pid, thread_id)
    if not tid:
        return "No flow runs in this project: start one first."
    body: dict = {"on": None} if follow else {}
    if on is not None and not follow:
        body["on"] = bool(on)
    if mode is not None:
        body["mode"] = mode
    if not body:
        return "Say on, off, follow or a mode (shadow or act)."
    v = api.post(f"/projects/{pid}/flows/{tid}/supervisor", body) or {}
    s = v.get("settings") or {}
    off_by = (s.get("switches") or {}).get("off_by")
    return (f"Flow {tid}: Supervisor {'on' if s.get('on') else 'off'}{f' (off by the {off_by} switch)' if off_by else ''}, "
            f"mode {s.get('mode', 'shadow')}.")


def register(srv, api: KeelApi, guard, write: bool) -> None:
    from mcp.types import ToolAnnotations

    if not write:
        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_supervisor(project: str | None = None, thread_id: str | None = None) -> str:
            """Supervisor plugin: is it on for the flow, its mode and tokens, and its last decisions with their reasons
            (answered, suggested, asked the person)."""
            return guard(supervisor_status, api, project, thread_id)

        return
    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    act = getattr(srv, "act", None)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_supervisor_set(on: bool | None = None, scope: str = "project", follow: bool = False,
                                  mode: str | None = None, project: str | None = None, thread_id: str | None = None,
                                  ctx: Context | None = None) -> str:
        """Supervisor plugin: turn the Supervisor on or off. scope "keel" = everywhere (off: nothing runs), "project" =
        this project (follow=true: it follows keel-wide again), "flow" = the flow now (follow=true: it follows the
        project). mode (flow only): shadow (it only suggests), retry, or act (it answers gates when it is sure; asks the
        person first). Mode fix needs keel2 mcp --admin. It never answers a cap, the final review or the PR gate, whatever the
        mode."""
        if act is None:
            return guard(supervisor_set, api, on, mode, project, thread_id, scope, follow)
        return await act(ctx, "keel_supervisor_set", "change the Supervisor's switch", supervisor_set, api, on, mode, project,
                         thread_id, scope, follow, confirm=(mode in ("act", "fix") or (on is True and scope == "keel")))
