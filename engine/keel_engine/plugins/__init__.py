"""keel's plugin code: what content/plugins/<name>/plugin.yml promises for db and git (runtime/plugins.py loads those).

A project turns a plugin on (Tools › Plugins); the api then sends `plugins: [...]` with every flow and KeelBot turn, and
the plugin's secrets in the call's keys (memory only): `db:<connection>` → {"name","kind","url","env"} as JSON, `github`
→ the token.

    on(settings, name)          is the plugin on for this flow or turn
    connections(keys)           the database connections a call carries
    open_call(...)/close_call   a key for one agent call; the plugin's MCP server sends it back with each tool call
                                (POST /plugins/call), so the engine knows the project, its folder and its connections
                                without writing any of them to disk
    server_specs(names, key)    the MCP servers (plugins/server.py) for that call: read tools only
    call(key, tool, args)       one tool call from such a server
    run_action(action, a)       a workflow code step db:*, git:* or ci:*
"""

from __future__ import annotations

import json
import secrets
import sys
import time

from .. import config

NAMES = ("db", "git", "ci")
SERVERS = {"db": "keel-db", "git": "keel-git", "ci": "keel-ci"}
TITLES = {"db": "Database", "git": "Git", "ci": "CI/CD"}
CALL_TTL = 4 * 3600
_calls: dict[str, dict] = {}


class PluginError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def enabled(settings: dict | None) -> list[str]:
    return [p for p in (settings or {}).get("plugins") or [] if p in NAMES]


def on(settings: dict | None, name: str) -> bool:
    return name in enabled(settings)


def connections(keys: dict | None) -> dict[str, dict]:
    """{name: {name, kind, url, env}} from the call's keys `db:<name>`."""
    out = {}
    for k, v in (keys or {}).items():
        if k.startswith("db:") and v:
            try:
                d = json.loads(v)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(d, dict) and d.get("url"):
                out[k[3:]] = {**d, "name": d.get("name") or k[3:]}
    return out


def github_token(keys: dict | None) -> str | None:
    from ..runtime.verdict_actions import github_token as token

    return token(keys or {})


# ------------------------------------------------------------------ one agent call's key

def _sweep():
    now = time.time()
    for k in [k for k, c in _calls.items() if c["until"] < now]:
        _calls.pop(k, None)


def open_call(*, project: str, root: str, keys: dict | None, plugins: list[str], who: str) -> str:
    """A key for one agent call or KeelBot turn (memory only, at most 4 hours)."""
    _sweep()
    key = "pk_" + secrets.token_urlsafe(24)
    _calls[key] = {"project": project, "root": root, "keys": dict(keys or {}), "plugins": list(plugins), "who": who,
                   "until": time.time() + CALL_TTL}
    return key


def close_call(key: str | None):
    if key:
        _calls.pop(key, None)


def server_specs(names: list[str], key: str) -> list[dict]:
    """The MCP servers of these plugins for one call: each runs plugins/server.py and asks the engine back."""
    url = f"http://127.0.0.1:{config.port()}/plugins/call"
    return [{"name": SERVERS[n], "command": sys.executable, "args": ["-m", "keel_engine.plugins.server", n],
             "env": {"KEEL_PLUGIN_URL": url, "KEEL_PLUGIN_KEY": key}} for n in names if n in SERVERS]


def allow_entries(names: list[str]) -> list[str]:
    return [f"mcp:{SERVERS[n]}:*" for n in names if n in SERVERS]


def call(key: str, tool: str, args: dict) -> str:
    """One tool call from a plugin's MCP server: the answer as text for the model."""
    c = _calls.get(key or "")
    if not c or c["until"] < time.time():
        raise PluginError(401, "This tool call has no valid key: the agent call it belonged to has ended.")
    plugin = "db" if tool.startswith("db_") else "git" if tool.startswith(("git_", "pr_")) else "ci" if tool.startswith("ci_") else ""
    if plugin not in c["plugins"]:
        raise PluginError(403, f"The {plugin or '?'} plugin is off for this project.", "Turn it on in Tools › Plugins.")
    if plugin == "db":
        from .db import tools as db_tools

        return db_tools.call(c, tool, args or {})
    if plugin == "ci":
        from .ci import tools as ci_tools

        return ci_tools.call(c, tool, args or {})
    from .git import tools as git_tools

    return git_tools.call(c, tool, args or {})


# ------------------------------------------------------------------ workflow steps

def _modules():
    from .ci import actions as ci_actions
    from .db import actions as db_actions
    from .git import actions as git_actions

    return {"db": db_actions, "git": git_actions, "ci": ci_actions}


def action_names() -> list[str]:
    return [name for m in _modules().values() for name in m.ACTIONS]


def action_params() -> dict[str, dict]:
    """Every plugin step action with what it takes in `with:`."""
    return {k: v for m in _modules().values() for k, v in m.PARAMS.items()}


async def run_action(action: str, a):
    """A db:* or git:* code step; the plugin must be on for the project."""
    from ..runtime.actions import ActionResult

    plugin = action.split(":", 1)[0]
    if not on(a.settings, plugin):
        return ActionResult(False, f"The {TITLES.get(plugin, plugin)} plugin is off for this project, so {action} cannot run.",
                            "Turn it on in Tools › Plugins, then retry the step.")
    mod = _modules().get(plugin)
    fn = mod.ACTIONS.get(action) if mod else None
    if not fn:
        return ActionResult(False, f"Unknown action {action}.")
    return await fn(a)
