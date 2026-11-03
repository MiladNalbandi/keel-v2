"""The Supervisor plugin's engine part (plugins/supervisor, docs/plans/supervisor.md).

A general agent per project: it listens to keel's events (EventBus.listen), looks at each gate a flow waits at with a
strong model and the project's context, and then answers it (mode "act") or only writes what it would do (mode
"shadow", the default). It calls the person when it is unsure, when only a person may answer, when it repeats itself,
or when its turn would pass a budget cap. It never raises a cap, pushes, merges or opens a PR.

    policy.py      which kinds of pause it may answer, the never-kinds, the checks on a verdict
    brief.py       the context it gets before it thinks, and its system prompt
    verdict.py     the model's answer (one JSON object) and the gate answer made from it
    ledger.py      the audit log: every look, answer and escalation with its reason
    past.py        the person's earlier answers to the same kind of gate, for the brief (one read call to the api)
    fix.py         mode fix: the limits keel checks in code after a small fix, and putting it back
    supervisor.py  the watcher, its queue and its turns
    mcp_tools.py   keel_supervisor (read) and keel_supervisor_set (--write) in keel's own MCP server
    routes.py      /supervisor (its decisions and status) and /threads/{id}/supervisor (on, mode, model, effort, caps)

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_supervisor). This module stays light.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_supervisor.py checks it)
VERSION = "0.2.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"   # content/plugins/supervisor/plugin.yml: the per-project list
_SUPERVISOR = None


def _router():
    from .routes import router

    return router


@asynccontextmanager
async def _lifespan(app):
    """The Supervisor lives as long as keel's app: it hears the app's bus and answers through its engine."""
    from .supervisor import Supervisor

    global _SUPERVISOR
    sup = Supervisor(app.state.bus, app.state.engine)
    app.state.supervisor = sup
    _SUPERVISOR = sup
    sup.start()
    try:
        yield
    finally:
        _SUPERVISOR = None
        await sup.close()


def _fake(req):
    """The fake model's verdict for the supervisor agent (tests; KEEL_FAKE_SUPERVISOR scripts it)."""
    from .verdict import fake_fix, fake_verdict

    if req.agent == "supervisor-fix":
        return None, "", fake_fix(req), {}
    if req.agent != "supervisor":
        return None
    return None, "", fake_verdict(req), {}


def _pr_body_sections(thread_id: str) -> list[str]:
    """The gates the Supervisor answered in this flow, for the PR body and the final review: who decided, and why."""
    from .ledger import list_rows

    rows = [r for r in list_rows(thread_id=thread_id, limit=200) if r.get("outcome") == "applied"]
    if not rows:
        return []
    lines = [f"Supervisor answered {len(rows)} gate(s) in this flow:"]
    lines += [f"- {r.get('step')}{' ' + r['ac'] if r.get('ac') else ''}: {r.get('decision')}"
              f"{' (' + r['choice'] + ')' if r.get('choice') else ''}: {r.get('why')}" for r in reversed(rows)]
    return lines


def _keel_mcp(server, api, guard, write: bool) -> None:
    from .mcp_tools import register

    register(server, api, guard, write)


ADDON = {
    "name": "supervisor",
    "title": "Supervisor",
    "version": VERSION,
    "content": CONTENT,
    "fake": _fake,
}

PART = {
    "name": "supervisor",
    "title": "Supervisor",
    "per_project": True,         # a project turns it on (Control › Plugins); a flow then turns it on for itself
    "router": _router,
    "lifespan": _lifespan,
    "keel_mcp": _keel_mcp,
    "hooks": {"pr_body_sections": _pr_body_sections},
}
