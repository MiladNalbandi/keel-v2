"""The Slack plugin's engine part: only the free chat (**Let people ask keel in Slack**, off until the person turns it on).

The api part matches commands with no model. A message that is no command, when the chat is on, comes here (POST /slack/chat) and gets one model turn:

    ToolBox(readonly=True): no write, no shell command that changes files or git, for every kind of model (the hook reads the same flag)
    no MCP server and no keel tool: it cannot start a flow, make a task or answer a gate
    Slack's words go in as data, in a block of their own

Its tokens are an agent call (slack.chat.started / slack.chat.finished; the api's SlackChatEvents), so the Budget page counts them.

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_slack). This module stays light.
"""

from __future__ import annotations

# the same version as ../../keel-plugin.yml (tests/test_slack_package.py checks it)
VERSION = "0.2.0"


def _router():
    from .chat import router

    return router


def _fake(req):
    """The fake model's answer for the chat (tests and the stand-in; KEEL_FAKE_SLACK_CHAT scripts what it tries)."""
    if req.agent != "slack-chat":
        return None
    from .chat import fake_answer

    return None, "", fake_answer(req), {}


ADDON = {
    "name": "slack",
    "title": "Slack",
    "version": VERSION,
    "fake": _fake,
}

PART = {
    "name": "slack",
    "title": "Slack",
    "router": _router,
}
