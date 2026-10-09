"""The KeelBot plugin's engine part (plugins/keelbot): its chat sessions and their runner (helper.py), its routes
(routes.py: /helper/*), what it is told about keel and the parts that are on (keelbot.py), and its content (its agent
content/agents/helper.md and keel's own slash commands, content/plugins/core/plugin.yml). Its code loads only when it is
used: this module stays light (the guard's hook reads every part on each tool call).

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_keelbot, written by `keel-engine plugins resolve`): ADDON
says who it is and where its content is, PART holds the keys it had as a built-in part (keel_engine/extensions.py):

    router            its routes, mounted after keel's own
    lifespan          its runner (HelperRunner) starts with keel's app and stops with it: app.state.helper
    open_paths        /helper/permissions/ask skips keel's internal token: the guard's hook asks there with the turn's
                      own ask key, which can only ask, never answer
    pr_body_sections  the commits KeelBot made at a gate, in the PR body and the final review

keel's core keeps "ask a person and wait" (keel_engine/approvals.py, the Inbox, keel2 mcp --write) and KeelBot's old
tables (helper_sessions, helper_messages, helper_files: runtime/migrate.py). KeelBot keeps only its own session rules:
its ask keys, its "always" grants and its panel's events.

    repo:    plugins/keelbot/engine/keel_plugin_keelbot/   ->  plugins/keelbot/content
    plugin:  <plugin>/engine/keel_plugin_keelbot/         ->  <plugin>/content   (keel-plugin.yml, scripts/build-plugin.sh)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_keelbot_part.py checks it)
VERSION = "1.0.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"
ASK_PATH = "/helper/permissions/ask"      # helper.ASK_PATH: where the guard's hook asks


def _router():
    from .routes import router

    return router


@asynccontextmanager
async def _lifespan(app):
    """KeelBot's runner lives as long as keel's app: it stops every turn that still runs when keel stops."""
    from .helper import HelperRunner

    app.state.helper = HelperRunner(app.state.bus)
    try:
        yield
    finally:
        await app.state.helper.close()


def _pr_body_sections(thread_id: str) -> list[str]:
    from .keelbot import pr_body_sections

    return pr_body_sections(thread_id)


ADDON = {
    "name": "keelbot",
    "title": "KeelBot",
    "version": VERSION,
    "content": CONTENT,
}

PART = {
    "name": "keelbot",
    "title": "KeelBot",
    "router": _router,
    "lifespan": _lifespan,
    "open_paths": (ASK_PATH,),
    "hooks": {"pr_body_sections": _pr_body_sections},
}
