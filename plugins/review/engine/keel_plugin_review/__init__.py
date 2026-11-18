"""The Code Review plugin's engine part (plugins/review): its content (the Tools › Plugins entry and KeelBot's
/review-branch and /explain-pr commands, content/plugins/review/plugin.yml) and the fake model's answers for its runs
(fake.py: the overview, the findings and the check of findings that the api's ReviewAiService asks KeelBot for). The
review itself (pull requests, diffs, threads, Submit) is the plugin's api part. The engine has one route for it: the
overview's diagrams (diagrams/, routes.py: the Database and System tabs, read from git with no model).

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_review, written by `keel-engine plugins resolve`). Its PART
has one key, keel_mcp (keel 0.18.6): the review's tools in keel's own MCP server (mcp_tools.py), each through the plugin's
api routes, so it still changes nothing in keel's flows or KeelBot's prompt (not per project: the api says when the
plugin is off for a project).

    repo:    plugins/review/engine/keel_plugin_review/   ->  plugins/review/content
    plugin:  <plugin>/engine/keel_plugin_review/         ->  <plugin>/content   (keel-plugin.yml, scripts/build-plugin.sh)
"""

from __future__ import annotations

from pathlib import Path

from .fake import answer as fake_answer

# the same version as ../../keel-plugin.yml (tests/test_review_part.py checks it)
VERSION = "1.3.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"

def _keel_mcp(server, api, guard, write: bool) -> None:
    from .mcp_tools import register

    register(server, api, guard, write)


ADDON = {
    "name": "review",
    "title": "Code Review",
    "version": VERSION,
    "content": CONTENT,
    "fake": fake_answer,
}

def _router():
    from .routes import router

    return router


PART = {
    "name": "review",
    "title": "Code Review",
    "keel_mcp": _keel_mcp,
    "router": _router,
}
