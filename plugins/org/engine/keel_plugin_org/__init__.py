"""The Org plugin's engine part (plugins/org): a hierarchy of agents you draw yourself.

An org is roles and links (config.py), kept in the engine's database (store.py) and edited as YAML on the Org page.
You give a mission to a role. The org runner (runner.py) starts the role's flow; its plan step gives work to the roles
it links to (org:delegate, actions.py); each of them runs its own workflow, in its own worktree; when they all end, the
boss reviews their work and writes a report (org:report), and you accept it or send it back (org:close).

    config.py   the org's YAML: roles, links, limits; the link checks
    store.py    orgs, missions, the flows they ran, their history (engine DB, its own tables)
    runner.py   the runner: starts queued missions through keel's api, hears flows end, wakes the boss
    actions.py  org:delegate, org:report, org:close
    routes.py   /org/... (keel's Python api forwards /api/org/* here)
    mcp_tools.py  its tools in keel's MCP server (keel2 mcp): orgs, templates, missions (#98)

keel loads it as a plugin part (KEEL_PLUGIN_ADDONS=keel_plugin_org). This module stays light.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_org.py checks it)
VERSION = "0.1.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"


def _router():
    from .routes import router

    return router


def _errors():
    from .config import OrgError
    from .routes import OrgErr

    return [OrgError, OrgErr]


def _actions():
    from .actions import ACTIONS

    return ACTIONS


def _params():
    from .actions import PARAMS

    return PARAMS


def _docs():
    from .actions import DOCS

    return DOCS


@asynccontextmanager
async def _lifespan(app):
    """The runner lives as long as keel's app: it hears the app's bus and starts flows through keel's api."""
    from .runner import Runner

    runner = Runner(app.state.bus, app.state.engine, getattr(app.state, "org_starter", None))
    app.state.org_runner = runner
    runner.start()
    try:
        yield
    finally:
        app.state.org_runner = None
        await runner.close()


def _keel_mcp(server, api, guard, write: bool) -> None:
    """#98 the Org tools in keel's own MCP server (keel2 mcp): read ones at the read level, acting ones at write."""
    from .mcp_tools import register

    register(server, api, guard, write)


def _fake(req):
    """The fake model's answer for the org's lead (tests and the demo): a plan that gives one mission to each team member
    the request names (KEEL_FAKE_ORG_CHILDREN caps how many, 0 = none), or a report when its team has ended."""
    if req.agent != "org-tech-lead":
        return None
    from .fake import answer

    return None, "", answer(req.prompt), {}


ADDON = {
    "name": "org",
    "title": "Org",
    "version": VERSION,
    "content": CONTENT,
    "fake": _fake,
}

PART = {
    "name": "org",
    "title": "Org",
    "router": _router,
    "errors": _errors,
    "actions": _actions,
    "params": _params,
    "docs": _docs,
    "lifespan": _lifespan,
    "keel_mcp": _keel_mcp,
}
