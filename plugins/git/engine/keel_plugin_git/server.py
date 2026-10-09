"""The Git plugin's MCP server for KeelBot and agents: `python -m keel_plugin_git.server`, over stdio (the part's "mcp"
in __init__.py; keel_engine/extensions.py server_specs starts it with this plugin's folder on PYTHONPATH).

Its tools only read. Each call goes back to the engine with the agent call's key (keel_engine/partmcp.py), so no secret
is in this process or its config. The answers come from tools.py, in the engine.
"""

from __future__ import annotations

from keel_engine.partmcp import call as _call


def build():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    srv = FastMCP("keel-git", log_level="WARNING", instructions=(
        "git and the pull request, read only. A commit, push, pull request or branch switch is never run here: give "
        "it to the person as a keel-git block."))

    @srv.tool(annotations=ro, structured_output=False)
    def git_status() -> str:
        """The branch, how far it is from the base branch and its remote, and the uncommitted changes."""
        return _call("git_status")

    @srv.tool(annotations=ro, structured_output=False)
    def git_diff(path: str = "", staged: bool = False, against_base: bool = False) -> str:
        """The diff of the uncommitted changes (or only the staged ones, or the whole branch against its base),
        for one file or all; at most 40k characters."""
        return _call("git_diff", path=path, staged=staged, against_base=against_base)

    @srv.tool(annotations=ro, structured_output=False)
    def git_log(n: int = 20, path: str = "", range: str = "") -> str:
        """Recent commits (sha, date, author, subject), for one file or a range like main..HEAD."""
        return _call("git_log", n=n, path=path, range=range)

    @srv.tool(annotations=ro, structured_output=False)
    def git_show(sha: str) -> str:
        """One commit: its message, files and diff."""
        return _call("git_show", sha=sha)

    @srv.tool(annotations=ro, structured_output=False)
    def git_blame(path: str, start: int = 1, end: int = 0) -> str:
        """Who last changed these lines of a file, and in which commit."""
        return _call("git_blame", path=path, start=start, end=end)

    @srv.tool(annotations=ro, structured_output=False)
    def git_branches() -> str:
        """The local branches, newest first, with how far each is from the base branch."""
        return _call("git_branches")

    @srv.tool(annotations=ro, structured_output=False)
    def pr_status() -> str:
        """The branch's pull request on GitHub: state, review, CI checks and the review comments."""
        return _call("pr_status")

    return srv


if __name__ == "__main__":
    build().run("stdio")
