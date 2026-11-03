"""keel 0.18.5: what keel's terminal app (keel TUI, cli/) can do, as MCP tools too, so Claude Code, OpenCode, Codex or xal
can do it for the person: stop flows, a flow's run mode, go on with a stopped flow, the agents at work and the ones
the person starts, KeelBot (ask it, its chats, the files it changed, keep, undo) and keel's ASCII diagrams.

Plain functions of the api (mcp_server.KeelApi), like mcp_more.py; mcp_server.py registers them at their level
(TOOLS) and asks the person first where CONFIRM says so.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from . import mcp_ask as ask_mod
from .mcp_models import model_text
from .runtime import clarify
from .mcp_api import q
from .mcp_server import ApiError, KeelApi, _flow, _n, _project, cut

RUN_MODES = {
    "manual": "stops at every gate",
    "important": "approves a criterion's gate when its checks and review pass; stops at everything else",
    "auto": "approves every gate keel can decide; still stops for money, new dependencies, secrets and failures",
    "readonly": "stops at every gate; agents may read but not edit, write or commit",
}
OPEN = ("running", "waiting")


_q = q


def _runs(api: KeelApi, pid: str, limit: int = 30) -> list[dict]:
    return api.get(f"/projects/{pid}/runs", limit=limit) or []


# ---------------------------------------------------------------- flows: stop, run mode, go on

def stop_flows(api: KeelApi, project: str | None = None, flows: list[str] | None = None, all_open: bool = False) -> str:
    """Stop flows: the given ids (or a start of one, 6+ characters), every running or waiting one (`all_open`), or the
    project's current one."""
    p = _project(api, project)
    rows = _runs(api, p["id"])
    if all_open:
        chosen = [r for r in rows if r.get("status") in OPEN]
        if not chosen:
            return "no running or waiting flow on this project."
    elif flows:
        chosen = []
        for f in flows:
            hit = [r for r in rows if r.get("thread_id") == f or (len(f) >= 6 and str(r.get("thread_id", "")).startswith(f))]
            if not hit:
                raise ApiError(f"no flow {f} on {p['id']} (keel_flows lists them).")
            chosen.append(hit[0])
    else:
        thread, _ = _flow(api, p["id"])
        if not thread or thread.get("status") not in OPEN:
            return "no running or waiting flow on this project."
        chosen = [{"thread_id": thread["thread_id"], "title": thread.get("title"), "status": thread.get("status")}]
    done, skipped, failed = [], [], []
    for r in chosen:
        name = f'"{r.get("title") or r["thread_id"]}"'
        if r.get("status") not in OPEN:
            skipped.append(f"{name} ({r.get('status')})")
            continue
        try:
            api.post(f"/threads/{_q(r['thread_id'])}/stop", {})
            done.append(name)
        except ApiError as exc:
            failed.append(f"{name}: {exc}")
    out = []
    if done:
        out.append(f"stopped {', '.join(done)}. Every finished step stays saved; keel_resume_flow goes on later.")
    if skipped:
        out.append(f"not running: {', '.join(skipped)}.")
    if failed:
        out.append("could not stop: " + "; ".join(failed))
    return "\n".join(out)


def run_mode(api: KeelApi, mode: str | None = None, project: str | None = None, thread_id: str | None = None) -> str:
    """A flow's run mode (no `mode`: what it is now, and the four)."""
    p = _project(api, project)
    if thread_id:
        view = api.get(f"/projects/{p['id']}/flows/{_q(thread_id)}") or {}
        thread = view.get("thread")
    else:
        thread, _ = _flow(api, p["id"])
    if not thread:
        return "no flow on this project."
    now = thread.get("run_mode") or "manual"
    if not mode:
        return f'"{thread.get("title")}" runs in {now} mode.\n' + "\n".join(f"  {m}{' (now)' if m == now else ''}: {about}"
                                                                         for m, about in RUN_MODES.items())
    if mode not in RUN_MODES:
        raise ApiError(f"a run mode is one of {', '.join(RUN_MODES)}.")
    if thread.get("status") not in OPEN:
        return f'"{thread.get("title")}" is {thread.get("status")}: a run mode is for a running or waiting flow.'
    if mode == now:
        return f'"{thread.get("title")}" runs in {mode} mode already.'
    api.post(f"/threads/{_q(thread['thread_id'])}/mode", {"mode": mode})
    return (f'"{thread.get("title")}" (thread {thread["thread_id"]}) runs in {mode} mode from its next gate on: it '
            f'{RUN_MODES[mode]}.')


def resume_flow(api: KeelApi, project: str | None = None, thread_id: str | None = None) -> str:
    """Go on with a stopped or failed flow from its last saved step (the dashboard's Resume, keel TUI's /resume)."""
    p = _project(api, project)
    if thread_id:
        thread = (api.get(f"/projects/{p['id']}/flows/{_q(thread_id)}") or {}).get("thread")
    else:
        thread, _ = _flow(api, p["id"])
    if not thread:
        return "no flow on this project."
    st = thread.get("status")
    if st in OPEN:
        return f"the flow is {st}: nothing to resume." + (" Answer its gate: keel_approve_gate." if st == "waiting" else "")
    if st == "done":
        return "the flow is done: start a new one with keel_start_flow."
    hist = api.get(f"/threads/{_q(thread['thread_id'])}/history") or []
    if not hist:
        return "this flow has no saved step yet: start it again with keel_start_flow."
    last = max(hist, key=lambda c: c.get("n") or 0)
    api.post(f"/threads/{_q(thread['thread_id'])}/rewind", {"checkpoint_id": last.get("id")})
    return f'resumed "{thread.get("title")}" after "{last.get("step")}": the next step runs now.'


# ---------------------------------------------------------------- agents

def _step_line(s: dict, width: int = 140) -> str:
    kind = s.get("kind") or "?"
    what = s.get("tool") or kind
    text = re.sub(r"\s+", " ", str(s.get("text") or s.get("path") or "")).strip()
    mark = "" if s.get("ok") is None else (" ✓" if s.get("ok") else " ✗")
    return cut(f"{what}{mark} {text}".strip(), width)


def _job_model(j: dict) -> str:
    """An agent call's model as the tools show it: claude/sonnet · effort high (the effort it ran with, when one was set)."""
    if not j.get("provider"):
        return ""
    return f"{j.get('provider')}/{j.get('model')}" + (f" · effort {j['effort']}" if j.get("effort") else "")


def agents(api: KeelApi, project: str | None = None) -> str:
    """The agents at work on the project's flows, and the person's own (KeelBot chats in their own worktree)."""
    p = _project(api, project)
    jobs = api.get("/jobs", project=p["id"], status="running", limit=20) or []
    chats = api.get(f"/projects/{p['id']}/helper/sessions") or []
    mine = [s for s in chats if s.get("mode") == "side" or s.get("busy") or s.get("status") == "running"][:12]
    out = []
    for j in jobs:
        out.append(f"- {j.get('id')}  ▶ {j.get('agent')} (flow) · {' '.join(x for x in (j.get('step'), j.get('ac')) if x)} · "
                   + "".join(f"{x} · " for x in [_job_model(j)] if x) + f"{_n((j.get('tokens_in') or 0) + (j.get('tokens_out') or 0))} tokens")
    for s in mine:
        busy = s.get("busy") or s.get("status") == "running"
        out.append(f"- {s.get('id')}  {'▶ at work' if busy else s.get('status')} · \"{s.get('title') or s.get('id')}\" · {s.get('mode')}"
                   + (f" · {model_text(s['model'], effort_default=False)}" if s.get("model") else "")
                   + (f" · branch {s['branch']}" if s.get("branch") else "") + f" · ${float(s.get('cost_usd') or 0):.2f}")
    if not out:
        return "no agent at work, and none of yours yet (keel_agent_start starts one)."
    return "\n".join(out) + "\nkeel_agent <id> shows one: its steps, or its chat."


def agent(api: KeelApi, agent_id: str, project: str | None = None, steps: int = 15) -> str:
    """One agent: a flow's agent call (its last steps, live) or one of the person's (its chat's last messages)."""
    try:
        r = api.get(f"/jobs/{_q(agent_id)}/steps") or {}
        rows = [s for s in r.get("steps") or [] if s.get("kind") != "thinking"][-max(1, steps):]
        head = "at work" if r.get("running") else "finished"
        try:
            job = api.get(f"/jobs/{_q(agent_id)}") or {}
            head += "".join(f" · {x}" for x in (job.get("agent"), _job_model(job)) if x)
        except ApiError:
            pass
        return f"agent call {agent_id} · {head}\n" + ("\n".join(f"{s.get('n'):>4} {_step_line(s)}" for s in rows) or "no steps yet.")
    except ApiError:
        pass
    p = _project(api, project)
    s = api.get(f"/projects/{p['id']}/helper/sessions/{_q(agent_id)}") or {}
    busy = s.get("busy") or s.get("status") == "running"
    out = [f"agent \"{s.get('title')}\" · {s.get('mode')}" + (f" · branch {s['branch']}" if s.get("branch") else "")
           + (f" · {model_text(s['model'], effort_default=False)}" if s.get("model") else "")
           + f" · {'at work' if busy else s.get('status')}"]
    for m in (s.get("messages") or [])[-4:]:
        who = {"user": "you", "helper": "agent"}.get(m.get("role"), m.get("role"))
        out.append(f"\n[{who}]\n{cut(str(m.get('text') or ''), 3000)}")
    if not busy and s.get("mode") == "side":
        out.append("\nits changes: keel_keelbot_changes; keep them: keel_keelbot_keep; put them back: keel_keelbot_undo.")
    return "\n".join(out)


def agent_start(api: KeelApi, task: str, mode: str = "side", project: str | None = None, force: bool = False,
                model: str | None = None) -> str:
    """An agent of the person's own: a KeelBot chat in its own worktree (side) or one that only reads (ask). 0.18.57 a
    side agent on a task a flow or another side agent works on is refused with who; force starts it anyway. 0.22.0 the
    answer names the model it runs on and where that came from; `model` picks another one for this agent (an alias
    such as opus, or an exact id such as claude-opus-5-5)."""
    if mode not in ("side", "ask"):
        raise ApiError("mode is side (its own worktree and branch) or ask (it only reads).")
    if not task.strip():
        raise ApiError("say what the agent should do.")
    p = _project(api, project)
    try:
        s = api.post(f"/projects/{p['id']}/helper/sessions",
                     {"mode": mode, "title": task.strip()[:60], **({"force": True} if force else {})}) or {}
    except ApiError as exc:
        if exc.status == 409 and exc.body.get("owners"):
            who = "; ".join(f"{o.get('who')} ({o.get('why')})" for o in exc.body["owners"])
            raise ApiError(f"refused: someone already works on this: {who}. Wait for it or stop it; to start anyway call "
                           "keel_agent_start again with force=true.", exc.status, exc.body) from exc
        raise
    runs = s.get("model") or {}
    why = ("KeelBot's model: the helper agent's on the Agents page, else the project's default model; another one: "
           "keel_agent_start(..., model='opus' or an exact id)")
    if model:
        from .mcp_models import _next_model

        runs = _next_model(api.get("/providers/models") or {}, runs, None, None, model.strip(), None)
        api.patch(f"/projects/{p['id']}/helper/sessions/{_q(s['id'])}", {"model": runs})
        why = "asked for in this call"
    api.post(f"/projects/{p['id']}/helper/sessions/{_q(s['id'])}/turn", {"text": task.strip()})
    on = f" It runs on {model_text(runs, effort_default=False)} ({why})." if runs else ""
    return (f"agent {s['id']} started: \"{s.get('title')}\" ({'its own worktree and branch' if mode == 'side' else 'it only reads'}).{on} "
            f"keel_agent {s['id']} shows how it does; keel_agents lists every one.")


# ---------------------------------------------------------------- KeelBot

# KeelBot's failure rule (plugins/keelbot content/agents/helper.md "When something fails"), for the client too
FAILURE_RULE = ("after a failure these are KeelBot's ways forward: the person picks one (the safe one is recommended); "
                "never run the failed command again unchanged yourself, and never pick for the person.")
FLOW_BLOCK = re.compile(r"```keel-(flow|start)\s*\n(.*?)\n```", re.S)
SKILL_BLOCK = re.compile(r"```keel-skill\s*\n(.*?)\n```", re.S)
STACK_BLOCK = re.compile(r"```keel-stack\s*\n(.*?)\n```", re.S)


def _buttons_hint(text: str, chat: str | None = None) -> str:
    """KeelBot's buttons, as the MCP tool that does each (the person's client presses them by calling it)."""
    hints = []
    # 0.19.0 KeelBot's questions (```keel-questions```): the person picks, the answers go back as the next message
    asked = clarify.parse_questions(text or "")
    if asked:
        recommended = {q["id"]: ([o["label"] for o in q["options"] if o.get("recommended")] if q.get("multiple")
                                 else next((o["label"] for o in q["options"] if o.get("recommended")), ""))
                       for q in asked}
        hints.append("questions (ask the person, then send their answers as the next message; the recommended ones "
                     f"are filled in here): keel_keelbot_ask(chat={chat!r}, text={clarify.answers_message(asked, recommended)!r})\n"
                     + "\n".join(ask_mod.question_lines(asked, "    "))
                     + "\n    " + FAILURE_RULE)
    for kind, body in FLOW_BLOCK.findall(text or ""):
        try:
            b = json.loads(body)
        except ValueError:
            continue
        if kind == "start":
            hints.append(f"start: keel_start_flow(type={b.get('workflow')!r}, idea={b.get('request') or b.get('title')!r})")
        elif b.get("do") == "stop":
            hints.append(f"stop: keel_stop_flows(flows={b.get('flows') or [b.get('flow')]!r})")
        elif b.get("do") in ("approve", "reject"):
            hints.append(f"{b['do']}: keel_resume(decision={b['do']!r}, thread_id={b.get('flow')!r}"
                         + (f", why={b.get('why')!r}" if b.get("why") else "") + ")")
        elif b.get("do") == "mode":
            hints.append(f"run mode: keel_run_mode(mode={b.get('mode')!r}, thread_id={b.get('flow')!r})")
    # 0.18.6 a new skill: its whole SKILL.md is the tool's text
    for body in SKILL_BLOCK.findall(text or ""):
        name = re.search(r"^name:\s*(\S+)", body, re.M)
        hints.append(f"save the skill {name[1] if name else ''}: keel_skill_create(text=<the keel-skill block, whole>)")
    # 0.18.9 a new stack: its whole YAML is the tool's text
    for body in STACK_BLOCK.findall(text or ""):
        name = re.search(r"^name:\s*(\S+)", body, re.M)
        hints.append(f"save the stack {name[1] if name else ''}: keel_stack_create(yaml=<the keel-stack block, whole>)")
    return ("\n\nKeelBot's buttons (each happens only when called, after the person agrees):\n" + "\n".join(f"- {h}" for h in hints)) if hints else ""


def keelbot_ask(api: KeelApi, text: str, project: str | None = None, chat: str | None = None, mode: str = "ask",
                wait: int = 120) -> str:
    """Ask KeelBot (a new chat, or `chat` to go on with one) and wait up to `wait` seconds for its answer."""
    if mode not in ("ask", "edit", "fix", "side"):
        raise ApiError("mode is ask (it reads), edit (it changes the project folder, no flow needed), fix (at a gate, under "
                       "its phase's rules) or side (its own worktree).")
    p = _project(api, project)
    base = f"/projects/{p['id']}/helper/sessions"
    sid = chat or (api.post(base, {"mode": mode, "title": text.strip()[:60]}) or {})["id"]
    before = len((api.get(f"{base}/{_q(sid)}") or {}).get("messages") or [])
    api.post(f"{base}/{_q(sid)}/turn", {"text": text})
    end = time.monotonic() + max(5, min(wait, 600))
    while time.monotonic() < end:
        time.sleep(2)
        s = api.get(f"{base}/{_q(sid)}") or {}
        msgs = s.get("messages") or []
        if not (s.get("busy") or s.get("status") == "running") and len(msgs) > before + 1:
            last = msgs[-1]
            if last.get("role") != "user":
                return f"[KeelBot · chat {sid}]\n{last.get('text')}" + _buttons_hint(str(last.get("text") or ""), sid)
    return f"KeelBot is still at it (chat {sid}). keel_agent {sid} shows its answer when it is there."


def keelbot_chats(api: KeelApi, project: str | None = None, limit: int = 20) -> str:
    p = _project(api, project)
    rows = sorted(api.get(f"/projects/{p['id']}/helper/sessions") or [], key=lambda s: s.get("updated_at") or "", reverse=True)
    if not rows:
        return "no KeelBot chat yet (keel_keelbot_ask starts one)."
    return "\n".join(f"- {s.get('id')}  \"{s.get('title') or '(no title)'}\" · {s.get('mode')} · {s.get('turns', 0)} turns · "
                     f"{s.get('status')} · {s.get('updated_at')}" for s in rows[:limit])


def keelbot_changes(api: KeelApi, chat: str, project: str | None = None, diffs: bool = True) -> str:
    p = _project(api, project)
    rows = api.get(f"/projects/{p['id']}/helper/sessions/{_q(chat)}/changes") or []
    if not rows:
        return "KeelBot changed no file in this chat (or they are kept or undone already)."
    out = [f"{len(rows)} file(s), not kept yet:"]
    for c in rows:
        out.append(f"{c.get('status')} {c.get('path')}  +{c.get('added', 0)} -{c.get('removed', 0)}")
        if diffs and c.get("diff"):
            out.append(cut(str(c["diff"]), 4000))
    return "\n".join(out)


def keelbot_keep(api: KeelApi, chat: str, paths: list[str] | None = None, message: str = "", project: str | None = None) -> str:
    p = _project(api, project)
    body: dict[str, Any] = {}
    if paths:
        body["paths"] = paths
    if message.strip():
        body["message"] = message.strip()
    d = api.post(f"/projects/{p['id']}/helper/sessions/{_q(chat)}/done", body, timeout=600) or {}
    return (f"kept {len(d.get('files') or [])} file(s): {d.get('message')} ({str(d.get('sha') or '')[:7]})"
            + (f" · checks: {d['checks']}" if d.get("checks") else ""))


def keelbot_undo(api: KeelApi, chat: str, path: str | None = None, project: str | None = None) -> str:
    p = _project(api, project)
    left = api.post(f"/projects/{p['id']}/helper/sessions/{_q(chat)}/undo", {"path": path} if path else {}) or []
    return (f"put back {path}" if path else "put back every file KeelBot changed") + f"; {len(left)} file(s) still changed."


# ---------------------------------------------------------------- diagrams

DIAGRAMS = ("flow", "workflow", "map-er", "map-system", "map-modules", "graph")


def diagram(api: KeelApi, kind: str, arg: str | None = None, project: str | None = None, width: int = 120, max_lines: int = 200) -> str:
    """keel's ASCII diagrams (the dashboard's and keel TUI's /diagram): the flow, a workflow, the map's ER, system or
    modules level, or a symbol's code graph."""
    if kind not in DIAGRAMS:
        raise ApiError(f"keel draws: {', '.join(DIAGRAMS)}.")
    p = _project(api, project)
    data: dict[str, Any]
    zoom = None
    if kind == "flow":
        thread, workflow = _flow(api, p["id"])
        if not thread or not workflow:
            return "no flow on this project."
        hist = api.get(f"/threads/{_q(thread['thread_id'])}/history") or []
        data = {"workflow": workflow, "thread": thread, "visited": [c.get("step") for c in hist]}
        zoom = arg
    elif kind == "workflow":
        if not arg:
            raise ApiError("which workflow? keel_workflows lists them.")
        rows = api.get(f"/projects/{p['id']}/workflows") or []
        w = next((x for x in rows if x.get("id") == arg), None)
        if not w:
            raise ApiError(f"no workflow {arg} (keel_workflows lists them).")
        data = {"workflow": w}
    elif kind == "graph":
        if not arg:
            raise ApiError("which symbol? diagram graph needs its name.")
        hits = api.get(f"/projects/{p['id']}/graph/search", q=arg) or {}
        if not hits.get("available"):
            return f"the code graph is not ready: {hits.get('reason') or 'build it in the dashboard (Code › Graph)'}."
        res = hits.get("results") or []
        hit = next((h for h in res if h.get("name") == arg), res[0] if res else None)
        if not hit:
            return f'no symbol "{arg}" in the code graph.'
        data = api.get(f"/projects/{p['id']}/graph/node", id=hit["id"], depth=2) or {}
    else:
        m = api.get(f"/projects/{p['id']}/map") or {}
        if m.get("missing"):
            return f"no map yet: {m['missing']}. The Map page in the dashboard builds it."
        if kind == "map-er":
            data = {"schema": m.get("schema") or {"tables": [], "relations": []}, **({"focus": arg} if arg else {})}
        else:
            level = (m.get("levels") or {}).get("system" if kind == "map-system" else "modules")
            if not level:
                return f"the map has no {kind[4:]} level."
            data = {"level": level}
            zoom = arg
    body = {"kind": kind, "data": data, "width": max(60, min(width, 160)), "max_lines": max_lines, **({"zoom": zoom} if zoom else {})}
    return (api.post("/diagrams/ascii", body) or {}).get("text") or "(empty)"
