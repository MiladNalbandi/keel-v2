"""0.37 (#191) agents use the Git plugin's tools: the main ones are always in the tool list, the system prompt says what
they are for and to use them over Bash, and a Bash git command that one of them does ends with a hint naming it."""

import asyncio

import pytest

from keel_engine import extensions
from keel_engine.harness.tools import ToolSet, deferrable
from keel_engine.tools.agent_tools import ToolBox
from keel_engine.tools import mcp


def test_the_main_git_tools_are_never_deferred_the_others_are():
    assert {"mcp__keel-git__git_status", "mcp__keel-git__git_diff", "mcp__keel-git__git_log",
            "mcp__keel-git__pr_status"} <= extensions.base_tools()
    assert not deferrable("mcp__keel-git__git_diff") and deferrable("mcp__keel-git__git_blame")
    assert deferrable("mcp__other__thing") and not deferrable("mcp__codegraph__codegraph_search")


@pytest.mark.parametrize("command,tool", [
    ("git status", "git_status"), ("cd src && git diff --stat", "git_diff"), ("git log --oneline -5", "git_log"),
    ("git show abc123", "git_show"), ("git blame -L 1,5 app.py", "git_blame"), ("git branch -a", "git_branches"),
    ("gh pr view 12 --comments", "pr_status"), ("gh pr checks", "pr_status"),
])
def test_bash_commands_the_git_tools_do(command, tool):
    hits = [t for pat, t in extensions.bash_prefer() if pat.search(command)]
    assert hits[:1] == [f"mcp__keel-git__{tool}"], hits


@pytest.mark.parametrize("command", ["git commit -m x", "git branch new-feature", "echo git status is fine",
                                     "git push", "npm test", "legit diff"])
def test_bash_commands_that_are_not_theirs(command):
    assert not [t for pat, t in extensions.bash_prefer() if pat.search(command) and "keel-git" in t]


def _git_tools(repo):
    key = extensions.open_call(project="demo", root=str(repo), keys={}, plugins=["git"], who="keelbot")
    tools, _ = asyncio.run(mcp.langchain_tools(extensions.server_specs(["git"], key), ["mcp:keel-git:*"]))
    return key, tools


def test_a_bash_git_command_names_the_tool_once(repo):
    key, mcp_tools = _git_tools(repo)
    box = ToolSet(ToolBox(str(repo), "none"), mcp=mcp_tools, lazy=True)
    assert "mcp__keel-git__git_diff" in [s.name for s in box.specs()]          # in the list from the start
    assert "mcp__keel-git__git_blame" not in [s.name for s in box.specs()]     # loaded when needed
    bash = box.tools["Bash"]
    first = asyncio.run(bash.run({"command": "git status"}))
    assert first.rstrip().endswith("use it next time it fits.)") and "mcp__keel-git__git_status" in first
    again = asyncio.run(bash.run({"command": "git status"}))
    assert "keel:" not in again                                              # once per tool in a call
    box.close()
    extensions.close_call(key)


def test_without_the_git_tools_bash_says_nothing(repo):
    box = ToolSet(ToolBox(str(repo), "none"), lazy=True)
    assert "keel:" not in asyncio.run(box.tools["Bash"].run({"command": "git status"}))
    box.close()


def test_the_system_prompt_names_the_tools_and_says_use_them_over_bash(repo):
    key, mcp_tools = _git_tools(repo)
    names = [t.name for t in mcp_tools]
    later = {n for n in names if deferrable(n)}
    guide = extensions.tools_guide(names, later)
    assert guide.startswith("## keel's tools for this project")
    assert "instead of the same thing in Bash" in guide
    line = next(x for x in guide.splitlines() if x.startswith("- keel-git"))
    assert "In your tool list: git_status, git_diff, git_log, pr_status." in line
    assert "Load when needed: git_show, git_blame, git_branches." in line and "against_base" in line
    assert extensions.tools_guide(["Read", "mcp__codegraph__codegraph_search"], set()) == ""
    extensions.close_call(key)
