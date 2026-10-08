"""The Git plugin as a part (keel_engine/extensions.py): its workflow steps (actions.py), its read tools for agents
(tools.py, the keel-git server), its routes (routes.py), its tools in keel2 mcp (mcp_tools.py), and what KeelBot and
"explain a step" say about it. Its code loads only when it is used: this module stays light."""

from __future__ import annotations

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


PART = {
    "name": "git",
    "title": "Git",
    "per_project": True,
    "actions": _actions,
    "params": PARAMS,
    "docs": DOCS,
    "read_tools": ("git_status", "git_diff", "git_log", "git_show", "git_blame", "git_branches", "pr_status"),
    "mcp": {"server": "keel-git", "module": "keel_engine.plugins.server", "args": ["git"], "call": _call},
    "router": _router,
    "errors": _errors,
    "keelbot": {"prompt": KEELBOT, "actions": KEELBOT_ACTIONS},
    "keel_mcp": _keel_mcp,
}
