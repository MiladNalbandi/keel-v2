"""The CI/CD plugin's engine part (plugins/ci): its workflow steps (actions.py), its read tools for agents (tools.py,
the keel-ci server in server.py), its routes (routes.py), its tools in keel2 mcp (mcp_tools.py), and what KeelBot and
"explain a step" say about it. Its code loads only when it is used: this module stays light (the guard's hook reads
the read tools on each tool call).

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_ci, written by `keel-engine plugins resolve`): ADDON says who
it is and where its content is, PART holds the keys it had as a built-in part (keel_engine/extensions.py). Its content
is the per-project plugin of Tools › Plugins: content/plugins/ci/plugin.yml (commands, tools, steps) and its own
workflow ci-fix (runtime/plugins.py reads both from a loaded add-on's content/plugins).

    repo:    plugins/ci/engine/keel_plugin_ci/   ->  plugins/ci/content
    plugin:  <plugin>/engine/keel_plugin_ci/     ->  <plugin>/content   (keel-plugin.yml, scripts/build-plugin.sh)
"""

from __future__ import annotations

from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_ci_part.py checks it)
VERSION = "1.0.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"

PARAMS = {
    "ci:status": {"branch": "optional"},
    "ci:wait": {"minutes": "optional"},
    "ci:logs": {"run": "optional"},
    "ci:rerun": {"run": "optional"},
}

DOCS = {
    "ci:status": {
        "summary": "CI/CD plugin: the newest pipeline runs of this commit (or a branch); passes when all are done and green.",
        "steps": ["with: branch (default: the runs of HEAD). A run that is still going fails the step (soft: a branch reads it)."],
    },
    "ci:wait": {
        "summary": "CI/CD plugin: waits for the pipelines of this commit after a push; a failed run fails the step.",
        "steps": ["with: minutes (default 30). The failed run's jobs, steps and log go to data.ci_failure."],
    },
    "ci:logs": {
        "summary": "CI/CD plugin: reads why the newest failed pipeline of this branch failed, into data.ci_failure.",
        "steps": ["with: run (a run id; default the newest failed run of the branch). No failed run fails the step."],
    },
    "ci:rerun": {
        "summary": "CI/CD plugin: runs the failed jobs of a pipeline again (for a flaky failure).",
        "steps": ["with: run (default the newest failed run of the branch)."],
    },
}

KEELBOT = """The CI/CD plugin is on. Your tools ci_runs and ci_failure only read the project's pipelines (GitHub \
Actions). When the person asks why CI failed, read ci_failure, say the cause in plain words with file:line where the log \
points at code, and what fixes it. You never re-run or fix it yourself: give a button, one per block:
```keel-ci
{"op": "fix"}
```
ops: fix {run?} (starts the ci-fix flow on this branch: read the failure, fix, commit, push, wait for CI), rerun {run?} \
(runs the failed jobs again; for a flaky failure: a timeout, the network)."""

KEELBOT_ACTIONS = ["ci:status  {branch?}: the pipelines of this commit pass", "ci:wait  {minutes?}: wait for CI after a push",
                   "ci:logs  {run?}: why the newest failed run failed, into data.ci_failure", "ci:rerun  {run?}"]


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
    from .core import CiError

    return (CiError,)


def _keel_mcp(server, api, guard, write: bool) -> None:
    from .mcp_tools import register

    register(server, api, guard, write)


ADDON = {
    "name": "ci",
    "title": "CI/CD",
    "version": VERSION,
    "content": CONTENT,
}

PART = {
    "name": "ci",
    "title": "CI/CD",
    "per_project": True,
    "actions": _actions,
    "params": PARAMS,
    "docs": DOCS,
    "read_tools": ("ci_runs", "ci_failure"),
    "mcp": {"server": "keel-ci", "module": "keel_plugin_ci.server", "call": _call},
    "router": _router,
    "errors": _errors,
    "keelbot": {"prompt": KEELBOT, "actions": KEELBOT_ACTIONS},
    "keel_mcp": _keel_mcp,
}
