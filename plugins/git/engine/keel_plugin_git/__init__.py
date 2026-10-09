"""The Git plugin's engine part (plugins/git): its workflow steps (actions.py), its read tools for agents (tools.py,
the keel-git server in server.py), its routes (routes.py), its tools in keel2 mcp (mcp_tools.py), and what KeelBot and
"explain a step" say about it. Its code loads only when it is used: this module stays light (the guard's hook reads
the read tools on each tool call).

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_git, written by `keel-engine plugins resolve`): ADDON says who
it is and where its content is, PART holds the keys it had as a built-in part (keel_engine/extensions.py). Its content
is the per-project plugin of Tools › Plugins: content/plugins/git/plugin.yml (commands, tools, steps).

keel's core keeps what flows need: tools/git.py (the git helper), the GitHub token of Connections › GitHub and "open
the pull request" in ship (runtime/verdict_actions.py). This part uses them, and keel's commit rules (plugin -> core).

    repo:    plugins/git/engine/keel_plugin_git/   ->  plugins/git/content
    plugin:  <plugin>/engine/keel_plugin_git/      ->  <plugin>/content   (keel-plugin.yml, scripts/build-plugin.sh)
"""

from __future__ import annotations

from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_git_part.py checks it)
VERSION = "1.0.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"

PARAMS = {
    "git:branch": {"name": "optional", "pattern": "optional"},
    "git:sync": {},
    "git:push": {},
    "git:pr": {"title": "optional", "draft": "optional"},
    "git:pr-checks": {"minutes": "optional"},
    "git:cleanup": {},
}

DOCS = {
    "git:branch": {
        "summary": "Git plugin: switches to the flow's branch, creating it when it does not exist.",
        "steps": ["with: name, or pattern (default the branch_pattern setting; {slug} is the flow's title)."],
    },
    "git:sync": {
        "summary": "Git plugin: merges the base branch (the remote's when there is one) into the current branch.",
        "steps": ["Needs a clean tree. A conflict is undone at once and fails the step with the files."],
    },
    "git:push": {
        "summary": "Git plugin: pushes the branch, never with force and never to main, master or the base branch.",
        "steps": [
            "Follows Settings › Push and open PR at ship: never skips it, ask needs the gate before it approved (or asks "
            "you here), automatic goes on. The Auto run mode never pushes.",
            "A GitHub remote gets the token from Connections › GitHub.",
        ],
    },
    "git:pr": {
        "summary": "Git plugin: opens the pull request with keel's PR body, or updates its title and body.",
        "steps": ["with: title (default the flow's title) and draft. The branch must be pushed. Same rules as git:push."],
    },
    "git:pr-checks": {
        "summary": "Git plugin: waits for the pull request's CI checks; a failed check fails the step.",
        "steps": ["with: minutes (default 30). Polls every 30 seconds."],
    },
    "git:cleanup": {
        "summary": "Git plugin: deletes local branches already merged into the base, and prunes old worktrees.",
        "steps": ["git branch -d refuses anything that is not merged, so no work is lost."],
    },
}

KEELBOT = """The Git plugin is on. Your tools git_status, git_diff, git_log, git_show, git_blame, git_branches and \
pr_status only read. A commit, push, pull request, branch switch or merge of the base branch is a button the person \
presses, one per block:
```keel-git
{"op": "commit", "message": "fix(prices): round half up\\n\\nThe reviewer asked for it on src/money.ts:14."}
```
ops: commit {message}, push {}, pr {title, body, draft?}, switch {branch, create?}, sync {} (merge the base branch \
in). keel never force-pushes and never pushes to main or master."""

KEELBOT_ACTIONS = ["git:branch  {name? | pattern?}", "git:sync  {}: merge the base branch in", "git:push  {}",
                   "git:pr  {title?, draft?}", "git:pr-checks  {minutes?}: wait for CI", "git:cleanup  {}"]


def _actions() -> dict:
    from .actions import ACTIONS

    return ACTIONS


def _call(c: dict, tool: str, args: dict) -> str:
    from .tools import call

    return call(c, tool, args)


def _router():
    from .routes import router

    return router


def _errors() -> tuple:
    from .core import GitError

    return (GitError,)


def _keel_mcp(server, api, guard, write: bool) -> None:
    from .mcp_tools import register

    register(server, api, guard, write)


ADDON = {
    "name": "git",
    "title": "Git",
    "version": VERSION,
    "content": CONTENT,
}

PART = {
    "name": "git",
    "title": "Git",
    "order": 20,            # after Database (10), before CI/CD and the other parts: as keel 0.15.1 named them
    "per_project": True,
    "actions": _actions,
    "params": PARAMS,
    "docs": DOCS,
    "read_tools": ("git_status", "git_diff", "git_log", "git_show", "git_blame", "git_branches", "pr_status"),
    "mcp": {"server": "keel-git", "module": "keel_plugin_git.server", "call": _call},
    "router": _router,
    "errors": _errors,
    "keelbot": {"prompt": KEELBOT, "actions": KEELBOT_ACTIONS},
    "keel_mcp": _keel_mcp,
}
