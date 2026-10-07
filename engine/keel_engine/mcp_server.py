"""keel v2's own MCP server (stdio): the flow's status, timeline, next action and phase rules, read over the api.

    python -m keel_engine.mcp [--read-only | --write]

Agents get it read-only (the builtin `keel` server in tools/mcp.py and the api's McpService): an agent never
approves its own gate. A user's Claude Code / Claude Desktop gets it through `keel2 mcp`; `--write` (or
KEEL_MCP_WRITE=1 without --read-only) adds keel_approve_gate and keel_resume. Read-only mode does not register
them at all, so a client cannot even see them.

Every tool takes an optional `project` id. Without it: KEEL_PROJECT, else the project whose root holds the
working directory (an agent runs in its project), else the only (or first) project.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from typing import Any, Literal

import httpx

from . import rules

API_TIMEOUT = 15
BUCKETS = ["api-main", "api-test", "web-src", "web-test", "contract", "specs", "migration", "e2e", "smoke", "other"]
BUCKET_TEXT = {
    "api-main": "backend production code", "api-test": "backend tests", "web-src": "frontend production code",
    "web-test": "frontend tests", "contract": "the API contract, openapi", "specs": "spec files", "migration": "database migrations",
    "e2e": "end-to-end tests", "smoke": "smoke tests", "other": "any other file: docs, build files",
}
TIMELINE_FILTERS = ("all", "steps", "agents", "failed")


class ApiError(Exception):
    pass


class KeelApi:
    """The keel v2 api over HTTP (KEEL_API_URL). Public /api routes; the api only listens on 127.0.0.1."""

    def __init__(self, url: str | None = None, transport: httpx.BaseTransport | None = None):
        self.url = (url or os.environ.get("KEEL_API_URL") or "http://127.0.0.1:8080").rstrip("/")
        headers = {"X-Keel-Token": os.environ["KEEL_INTERNAL_TOKEN"]} if os.environ.get("KEEL_INTERNAL_TOKEN") else {}
        self.http = httpx.Client(base_url=self.url + "/api", timeout=API_TIMEOUT, headers=headers, transport=transport)

    def _send(self, method: str, path: str, **kw) -> Any:
        try:
            res = self.http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ApiError(f"keel's api does not answer at {self.url} ({type(exc).__name__}). Is keel running? (keel2 status)") from exc
        if res.status_code >= 400:
            try:
                body = res.json()
                msg = body.get("error") or res.text
                if body.get("hint"):
                    msg += f" ({body['hint']})"
            except ValueError:
                msg = res.text[:300]
            raise ApiError(f"{method} {path}: {res.status_code} {msg}")
        return res.json() if res.content else None

    def get(self, path: str, **params) -> Any:
        return self._send("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, body: dict) -> Any:
        return self._send("POST", path, json=body)


# ---------------------------------------------------------------- helpers

def _project(api: KeelApi, project: str | None) -> dict:
    projects = api.get("/projects") or []
    want = project or os.environ.get("KEEL_PROJECT") or ""
    if want:
        for p in projects:
            if want in (p.get("id"), p.get("name")):
                return p
        raise ApiError(f'no project "{want}". keel knows: {", ".join(p["id"] for p in projects) or "none yet"}')
    if not projects:
        raise ApiError("keel has no project yet. Add one in the dashboard (Projects › Add).")
    cwd = os.path.realpath(os.getcwd())
    inside = [p for p in projects if p.get("root") and (cwd == p["root"] or cwd.startswith(p["root"].rstrip("/") + "/"))]
    if inside:
        return max(inside, key=lambda p: len(p["root"]))
    return projects[0]


def _flow(api: KeelApi, pid: str) -> tuple[dict | None, dict | None]:
    view = api.get(f"/projects/{pid}/flow") or {}
    return view.get("thread"), view.get("workflow")


def _n(x: Any) -> str:
    try:
        return f"{int(x or 0):,}"
    except (TypeError, ValueError):
        return str(x)


def _when(at: str | None) -> datetime:
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.min


def _clock(at: str | None) -> str:
    d = _when(at)
    return d.strftime("%m-%d %H:%M:%S") if d != datetime.min else "--"


def _flow_name(thread: dict, workflow: dict | None) -> str:
    wid = thread.get("workflow_id") or "?"
    name = (workflow or {}).get("name")
    return f"{name} ({wid})" if name and name != wid else wid


def _waiting_lines(w: dict) -> list[str]:
    labels = w.get("labels") or {}
    opts = " | ".join(f"{o}" + (f' ("{labels[o]}")' if labels.get(o) else "") for o in w.get("options") or [])
    out = [f"waiting   {w.get('kind', 'gate')}: {w.get('title', '')}", f"          options: {opts or '-'}"]
    for q in w.get("questions") or []:
        choices = " / ".join(o.get("label", "") + (" (recommended)" if o.get("recommended") else "") for o in q.get("options") or [])
        out.append(f"          {q.get('id')}: {q.get('question')}  [{choices}]")
    detail = (w.get("detail") or "").strip()
    if detail:
        out.append("          " + detail[:600].replace("\n", "\n          "))
    return out


# ---------------------------------------------------------------- read tools

def status(api: KeelApi, project: str | None = None) -> str:
    p = _project(api, project)
    thread, workflow = _flow(api, p["id"])
    head = f"project   {p['id']} · branch {p.get('branch') or '-'}"
    if not thread:
        return f"{head}\nflow      none. Start one in the dashboard (Flow › Start a flow)."
    out = [head, f"flow      {_flow_name(thread, workflow)} · \"{thread.get('title', '')}\" · {thread.get('status')} · thread {thread.get('thread_id')}",
           f"step      {thread.get('current') or '-'} · phase {thread.get('phase') or 'none'}" + (f" · {thread['ac']}" if thread.get("ac") else "")
           + (f" · run mode {thread['run_mode']}" if thread.get("run_mode") not in (None, "", "manual") else "")]
    acs = thread.get("acs") or []
    if acs:
        done = sum(1 for a in acs if a.get("status") in ("done", "already-met"))
        out.append(f"criteria  {done}/{len(acs)} done")
        out += [f"  {('[' + str(a.get('status')) + ']'):<14}{a.get('id')} {a.get('layer', '')}  {a.get('title', '')}" for a in acs]
    if thread.get("waiting"):
        out += _waiting_lines(thread["waiting"])
    u = thread.get("usage") or {}
    total = int(u.get("tokens_in") or 0) + int(u.get("tokens_out") or 0)
    cap = f" of cap {_n(u['cap_tokens'])}" if u.get("cap_tokens") else " (no cap)"
    cap_usd = f" of cap ${float(u['cap_usd']):.2f}" if u.get("cap_usd") else ""
    out.append(f"usage     {_n(total)} tokens{cap} · in {_n(u.get('tokens_in'))} · out {_n(u.get('tokens_out'))} · cached "
               f"{_n(u.get('tokens_cached'))} · ${float(u.get('cost_usd') or 0):.2f}{cap_usd} · {_n(u.get('premium_requests'))} premium requests")
    if thread.get("error"):
        out.append(f"error     {thread['error']}")
    for b in thread.get("blockers") or []:
        out.append(f"blocker   {b.get('gate')}: {b.get('why')} -> {b.get('fix')}")
    return "\n".join(out)


def projects(api: KeelApi) -> str:
    rows = api.get("/projects") or []
    if not rows:
        return "keel has no project yet. Add one in the dashboard (Projects › Add)."
    out = []
    for p in rows:
        acs = p.get("acs") or [0, 0]
        out.append(f"{p['id']}  branch {p.get('branch') or '-'} · flow {p.get('flow') or 'none'} · phase {p.get('phase') or 'none'} · "
                   f"criteria {acs[0]}/{acs[1] if len(acs) > 1 else 0} · waiting {'yes' if p.get('waiting') else 'no'}"
                   + (f" · {p['running']} running" if p.get("running") else ""))
    return "\n".join(out)


def timeline(api: KeelApi, project: str | None = None, limit: int = 20, filter: str = "all") -> str:
    if filter not in TIMELINE_FILTERS:
        raise ApiError(f'filter must be one of: {", ".join(TIMELINE_FILTERS)}')
    limit = max(1, min(int(limit or 20), 200))
    p = _project(api, project)
    thread, _ = _flow(api, p["id"])
    events: list[tuple[datetime, str]] = []
    if filter in ("all", "steps") and thread:
        for c in api.get(f"/threads/{thread['thread_id']}/history") or []:
            events.append((_when(c.get("at")), f"{_clock(c.get('at'))}  step   {c.get('step')}" + (f" · {c['note']}" if c.get("note") else "")))
    if filter in ("all", "agents", "failed"):
        for j in api.get("/jobs", project=p["id"], limit=limit) or []:
            if filter == "failed" and j.get("status") not in ("failed", "guard"):
                continue
            tokens = int(j.get("tokens_in") or 0) + int(j.get("tokens_out") or 0)
            where = " ".join(x for x in (j.get("step"), j.get("ac")) if x)
            events.append((_when(j.get("started_at")), f"{_clock(j.get('started_at'))}  agent  {j.get('agent')} {j.get('status')} · {where} · "
                                                         f"{_n(tokens)} tokens · {j.get('provider')}/{j.get('model')} · job {j.get('id')}"))
    if filter == "failed" and thread and thread.get("error"):
        events.append((_when(thread.get("updated_at")), f"{_clock(thread.get('updated_at'))}  flow   failed · {thread['error']}"))
    if not events:
        return "nothing yet: no flow has run on this project." if not thread else f"no {filter} events."
    events.sort(key=lambda e: e[0], reverse=True)
    return "\n".join(line for _, line in events[:limit])


def next_action(api: KeelApi, project: str | None = None) -> str:
    p = _project(api, project)
    thread, workflow = _flow(api, p["id"])
    if not thread:
        return "no flow: start one in the dashboard (Flow › Start a flow: feature, change, fix, diagnose, review, cover, ship, hunt, lint, init …)."
    st, w = thread.get("status"), thread.get("waiting")
    ac = f" for {thread['ac']}" if thread.get("ac") else ""
    if st == "waiting" and w:
        kind = w.get("kind")
        if kind == "clarify":
            n = len(w.get("questions") or [])
            line = f"waiting at {w.get('title')}: answer the {n} question(s), then send them (approve with answers)."
        elif kind == "budget":
            line = f"waiting at {w.get('title')}: approve to go past the token cap, or reject to stop."
        elif kind == "fix":
            line = f"waiting at {w.get('title')}: approve to try again, or reject to stop the flow."
        else:
            labels = w.get("labels") or {}
            line = (f"waiting at {w.get('step')} ({w.get('title')}){ac}: approve"
                    + (f' ("{labels["approve"]}")' if labels.get("approve") else "")
                    + (" or send back with a reason" + (f' ("{labels["reject"]}")' if labels.get("reject") else "")
                       if "reject" in (w.get("options") or []) else "") + ".")
        out = [line, "A human decides in the dashboard (or a client with keel2 mcp --write: keel_approve_gate)."]
    elif st == "running":
        out = [f"running {thread.get('current') or 'the next step'} (phase {thread.get('phase') or 'none'}){ac}: nothing to do, keel is working."]
    elif st == "done":
        out = [f"flow {_flow_name(thread, workflow)} is done. Review the branch {p.get('branch') or ''} and open a PR, or start a new flow."]
    elif st == "failed":
        out = [f"flow failed at {thread.get('current')}: {thread.get('error') or 'see keel_timeline filter=failed'}. Rewind or start it again from the dashboard."]
    elif st == "stopped":
        out = [f"flow stopped at {thread.get('current')}. Rewind to a checkpoint in the dashboard, or start a new flow."]
    else:
        out = [f"flow is {st} at {thread.get('current')}."]
    blockers = thread.get("blockers") or []
    if blockers:
        out += ["", "blocking a push:"] + [f"  {b.get('gate')}: {b.get('why')} -> {b.get('fix')}" for b in blockers]
    return "\n".join(out)


def explain(api: KeelApi | None, phase: str | None = None, project: str | None = None) -> str:
    if not phase:
        thread = _flow(api, _project(api, project)["id"])[0] if api else None
        phase = (thread or {}).get("phase") or "none"
    if phase not in rules.PHASES:
        return f'no phase "{phase}". keel knows: {", ".join(rules.PHASES)}'
    out = [f"phase  {phase}"]
    for flow, names in rules.RAILS.items():
        if phase in names:
            i = names.index(phase)
            out += ["", f"on the {flow} rail, step {i + 1} of {len(names)}:", "  " + " - ".join(f"[{n}]" if j == i else n for j, n in enumerate(names))]
    row = rules.MATRIX.get(phase) or rules.CLOSED
    fallback = row.get("*", "deny")
    allowed, refused, other = [], [], []
    for b in BUCKETS:
        rule = row.get(b, fallback)
        item = f"{b} ({BUCKET_TEXT[b]})"
        (allowed if rule == "allow" else refused if rule == "deny" else other).append(item if rule in ("allow", "deny") else f"{item}: {rule}")
    out += ["", "may edit:", *[f"  {x}" for x in allowed or ["nothing"]], "refused:", *[f"  {x}" for x in refused or ["nothing"]]]
    if other:
        out += ["conditional:", *[f"  {x}" for x in other]]
    out += ["always refused:", *[f"  {b}: {why}" for b, why in rules.ALWAYS_DENY.items()]]
    if phase in rules.LANE_SCOPED_PHASES:
        out.append(f"in {phase}, an agent edits only its criterion's lane (api or web).")
    hint = rules._hint(phase, {"red": "api-main", "green": "api-test"}.get(phase, "other")).strip()
    if hint:
        out += ["", hint]
    ctype = rules.commit_type_for(phase)
    cr = rules.COMMIT_RULES.get(ctype)
    if cr:
        out += ["", f"commits here are '{ctype}': may hold {', '.join(cr.get('allow') or []) or '-'}; never {', '.join(cr.get('deny') or []) or '-'}."]
    if phase != "none":
        out += ["shell: commits, git reset --hard and interactive rebase are refused during a flow (the engine commits); "
                "adding a dependency needs the spec gate; shell writes follow the edit rules above."]
    out += ["an unlock (dashboard › Repo › Unlock) lets one path through in one phase."]
    nxt = rules.TRANSITIONS.get(phase) or []
    if nxt:
        out.append(f"next phases: {', '.join(nxt)}")
    return "\n".join(out)


# ---------------------------------------------------------------- write tools (only with --write)

def approve_gate(api: KeelApi, decision: str, project: str | None = None, why: str | None = None,
                 answers: dict[str, str] | None = None) -> str:
    if decision not in ("approve", "reject"):
        raise ApiError("decision must be approve or reject")
    p = _project(api, project)
    thread, _ = _flow(api, p["id"])
    if not thread or thread.get("status") != "waiting" or not thread.get("waiting"):
        return f"nothing is waiting on {p['id']}: {next_action(api, p['id']).splitlines()[0]}"
    w = thread["waiting"]
    if decision not in (w.get("options") or ["approve", "reject"]):
        return f"{w.get('title')} takes only: {', '.join(w.get('options') or [])}"
    payload = {"answers": answers} if answers else None
    if w.get("kind") == "clarify" and not answers:
        ids = ", ".join(q.get("id", "") for q in w.get("questions") or [])
        return f"{w.get('title')}: pass answers, a map of question id to the chosen label or your own words ({ids})."
    return _resume(api, thread["thread_id"], decision, why, payload, before=w)


def resume(api: KeelApi, decision: str, thread_id: str | None = None, project: str | None = None, why: str | None = None,
           payload: dict | None = None) -> str:
    if decision not in ("approve", "reject"):
        raise ApiError("decision must be approve or reject")
    if not thread_id:
        thread, _ = _flow(api, _project(api, project)["id"])
        if not thread:
            return "no flow on this project, so nothing to resume."
        thread_id = thread["thread_id"]
    return _resume(api, thread_id, decision, why, payload)


def _resume(api: KeelApi, tid: str, decision: str, why: str | None, payload: dict | None, before: dict | None = None) -> str:
    body: dict[str, Any] = {"decision": decision}
    if why:
        body["why"] = why
    if payload:
        body["payload"] = payload
    state = api.post(f"/threads/{tid}/resume", body) or {}
    what = f" {before.get('title')}" if before else ""
    after = f"{state.get('status')} at {state.get('current') or '-'} (phase {state.get('phase') or 'none'})"
    if state.get("waiting"):
        after += f"; now waiting: {state['waiting'].get('title')}"
    done = "approved" if decision == "approve" else "sent back"
    return f"{done}{what} on thread {tid}. The flow is {after}."


# ---------------------------------------------------------------- server

# ---- v0.10.0 plugins (Tools › Plugins): the Database and Git plugins' tools, through the api and keel's rules

ASK_WAIT = 600          # an acting tool waits this long for the person's answer in keel's Inbox
ASK_POLL = 3


def _plugin_on(api: KeelApi, pid: str, name: str):
    on = [p["name"] for p in api.get(f"/projects/{pid}/plugins") or [] if p.get("enabled")]
    if name not in on:
        title = {"db": "Database", "git": "Git"}[name]
        raise ApiError(f"The {title} plugin is off for {pid}: turn it on in keel (Tools › Plugins).")


def db_schema(api: KeelApi, project: str | None = None, connection: str | None = None, table: str | None = None) -> str:
    from .plugins.db.tools import _schema_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    return _schema_text(api.get(f"/projects/{pid}/db/schema", connection=connection), table or "")


def db_query(api: KeelApi, sql: str, project: str | None = None, connection: str | None = None) -> str:
    from .plugins.db.tools import query_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    return query_text(api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "mask": True}))


def git_status(api: KeelApi, project: str | None = None) -> str:
    from .plugins.git.tools import status_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "git")
    return status_text(api.get(f"/projects/{pid}/git/status"))


def pr_status(api: KeelApi, project: str | None = None) -> str:
    from .plugins.git.tools import pr_text

    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "git")
    return pr_text((api.get(f"/projects/{pid}/git/pr") or {}).get("pr"))


def _ask_person(api: KeelApi, pid: str, title: str, command: str, sleep=time.sleep) -> tuple[bool, str]:
    """Ask the person in keel's Inbox and wait for the answer (Claude Code cannot show keel's card)."""
    q = api.post(f"/projects/{pid}/plugins/ask", {"title": title, "command": command})
    deadline = time.monotonic() + ASK_WAIT
    while time.monotonic() < deadline:
        a = api.get(f"/plugins/asks/{q['id']}")
        if not a.get("waiting"):
            return a.get("decision") == "allow", a.get("why") or ""
        sleep(ASK_POLL)
    return False, "Nobody answered in keel's Inbox in 10 minutes."


def db_change(api: KeelApi, sql: str, project: str | None = None, connection: str | None = None, sleep=time.sleep) -> str:
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "db")
    dry = api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "change": True})
    ok, why = _ask_person(api, pid, f"Claude Code: change {dry['changed']} row(s) in {dry['connection']}?", dry["sql"], sleep)
    if not ok:
        return f"The person said no in keel's Inbox: {why}".strip()
    r = api.post(f"/projects/{pid}/db/query", {"sql": sql, "connection": connection or "", "change": True, "confirm": True})
    return f"{r['changed']} row(s) changed in {r['connection']}."


def git_act(api: KeelApi, op: str, project: str | None = None, sleep=time.sleep, **body) -> str:
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "git")
    what = {"commit": f"commit: {body.get('message', '')}", "push": "push the branch",
            "pr": f"open the pull request: {body.get('title', '')}"}[op]
    ok, why = _ask_person(api, pid, f"Claude Code: {what.split(':')[0]}?", what, sleep)
    if not ok:
        return f"The person said no in keel's Inbox: {why}".strip()
    r = api.post(f"/projects/{pid}/git/{op}", body)
    if op == "commit":
        return f"Committed {r['sha'][:7]} {r['subject']} ({len(r['files'])} file(s))."
    if op == "push":
        return f"Pushed {r['branch']} ({r['sha'][:7]})."
    return f"{'Updated' if r.get('updated') else 'Opened'} the pull request: {r.get('url')}"


def build_server(write: bool = False, api: KeelApi | None = None):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    api = api or KeelApi()
    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    srv = FastMCP("keel", instructions="keel v2: the flow on each project (phase, criteria, gates), what happened, what to do next, "
                                         "and what each phase permits." + (" Write tools can decide a waiting gate." if write else ""),
                  log_level="WARNING")

    def guard(fn, *a, **kw) -> str:
        try:
            return fn(*a, **kw)
        except ApiError as exc:
            return f"keel: {exc}"

    @srv.tool(annotations=ro, structured_output=False)
    def keel_status(project: str | None = None) -> str:
        """The project's flow: branch, workflow, current step and phase, every acceptance criterion with its status,
        what is waiting on a human (title, kind, options, questions), token usage against the cap, and blockers."""
        return guard(status, api, project)

    @srv.tool(annotations=ro, structured_output=False)
    def keel_projects() -> str:
        """Every keel project, one line each: id, branch, flow, phase, criteria done, and whether it waits on a human.
        Pass an id as `project` to the other keel tools."""
        return guard(projects, api)

    @srv.tool(annotations=ro, structured_output=False)
    def keel_timeline(project: str | None = None, limit: int = 20, filter: Literal["all", "steps", "agents", "failed"] = "all") -> str:
        """Recent events, newest first: flow checkpoints (steps) and agent runs (status, tokens, model).
        filter: all, steps, agents or failed. limit: 1-200 (default 20)."""
        return guard(timeline, api, project, limit, filter)

    @srv.tool(annotations=ro, structured_output=False)
    def keel_next(project: str | None = None) -> str:
        """The single next action for a human or an agent (e.g. a gate to approve, a step running), plus what blocks a push."""
        return guard(next_action, api, project)

    @srv.tool(annotations=ro, structured_output=False)
    def keel_explain(phase: str | None = None, project: str | None = None) -> str:
        """What a phase permits and refuses (which files may be edited, commit rules, shell rules) and where it sits on
        each flow's rail. phase: e.g. spec, red, green, gate, hunt-sweep; default the project's current phase."""
        return guard(explain, api, phase, project)

    @srv.tool(annotations=ro, structured_output=False)
    def keel_db_schema(project: str | None = None, connection: str | None = None, table: str | None = None) -> str:
        """Database plugin: the project's tables with columns, primary keys (*) and foreign keys; with `table`, one table."""
        return guard(db_schema, api, project, connection, table)

    @srv.tool(annotations=ro, structured_output=False)
    def keel_db_query(sql: str, project: str | None = None, connection: str | None = None) -> str:
        """Database plugin: one read-only query (SELECT, EXPLAIN, SHOW) under keel's rules: at most 200 rows and 15
        seconds; columns named like a secret show as •••."""
        return guard(db_query, api, sql, project, connection)

    @srv.tool(annotations=ro, structured_output=False)
    def keel_git_status(project: str | None = None) -> str:
        """Git plugin: the project's branch, how far it is from the base branch and its remote, and what changed."""
        return guard(git_status, api, project)

    @srv.tool(annotations=ro, structured_output=False)
    def keel_pr_status(project: str | None = None) -> str:
        """Git plugin: the branch's pull request: state, review, CI checks and review comments."""
        return guard(pr_status, api, project)

    if write:
        rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

        @srv.tool(annotations=rw, structured_output=False)
        def keel_db_change(sql: str, project: str | None = None, connection: str | None = None) -> str:
            """Database plugin: change data (INSERT, UPDATE, DELETE) on a local or test database. keel counts the rows,
            then waits up to 10 minutes for the person's OK in keel's Inbox."""
            return guard(db_change, api, sql, project, connection)

        @srv.tool(annotations=rw, structured_output=False)
        def keel_git_commit(message: str, project: str | None = None) -> str:
            """Git plugin: commit every change (keel's secret check, the author from keel's settings), after the person's
            OK in keel's Inbox."""
            return guard(git_act, api, "commit", project, message=message)

        @srv.tool(annotations=rw, structured_output=False)
        def keel_git_push(project: str | None = None) -> str:
            """Git plugin: push the branch (never with force, never to main or master), after the person's OK."""
            return guard(git_act, api, "push", project)

        @srv.tool(annotations=rw, structured_output=False)
        def keel_pr_create(title: str, body: str = "", project: str | None = None) -> str:
            """Git plugin: open the branch's pull request (or update its title and body), after the person's OK."""
            return guard(git_act, api, "pr", project, title=title, body=body)

        @srv.tool(annotations=rw, structured_output=False)
        def keel_approve_gate(decision: Literal["approve", "reject"], project: str | None = None, why: str | None = None,
                              answers: dict[str, str] | None = None) -> str:
            """Decide the gate the project's flow waits at. approve, or reject (send back) with `why`.
            For a clarify gate pass `answers`: question id -> the chosen option label or your own words."""
            return guard(approve_gate, api, decision, project, why, answers)

        @srv.tool(annotations=rw, structured_output=False)
        def keel_resume(decision: Literal["approve", "reject"], thread_id: str | None = None, project: str | None = None,
                        why: str | None = None, payload: dict[str, Any] | None = None) -> str:
            """Resume a waiting flow thread as the dashboard does (POST /api/threads/{id}/resume with decision, why,
            payload). thread_id defaults to the project's current flow."""
            return guard(resume, api, decision, thread_id, project, why, payload)

    return srv


def write_mode(argv: list[str]) -> bool:
    """--read-only always wins (agents get it), so KEEL_MCP_WRITE=1 in an env cannot give an agent the write tools."""
    if "--read-only" in argv:
        return False
    return "--write" in argv or os.environ.get("KEEL_MCP_WRITE", "0") == "1"


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="python -m keel_engine.mcp", description="keel v2 MCP server (stdio)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--read-only", action="store_true", help="only the read tools (default; what agents get)")
    group.add_argument("--write", action="store_true", help="also keel_approve_gate and keel_resume")
    parser.parse_args(argv)
    build_server(write=write_mode(argv)).run("stdio")
