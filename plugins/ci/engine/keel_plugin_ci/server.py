"""The CI/CD plugin's MCP server for KeelBot and agents: `python -m keel_plugin_ci.server`, over stdio (the part's
"mcp" in __init__.py; keel_engine/extensions.py server_specs starts it with this plugin's folder on PYTHONPATH).

Its tools only read. Each call goes back to the engine with the agent call's key (keel_engine/partmcp.py), so no secret
is in this process or its config. The answers come from tools.py, in the engine.
"""

from __future__ import annotations

from keel_engine.partmcp import call as _call


def build():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    srv = FastMCP("keel-ci", log_level="WARNING", instructions=(
        "The project's CI pipelines on GitHub Actions, read only. A re-run or a fix is never started here: give the "
        "person a keel-ci block."))

    @srv.tool(annotations=ro, structured_output=False)
    def ci_runs(branch: str = "", limit: int = 15) -> str:
        """The newest pipeline runs (workflow, branch, commit, status or result, link), for one branch or all."""
        return _call("ci_runs", branch=branch, limit=limit)

    @srv.tool(annotations=ro, structured_output=False)
    def ci_failure(run_id: int = 0, branch: str = "") -> str:
        """Why a run failed: its failed jobs and steps and the end of their log. Without run_id, the newest failed
        run of the branch (default the current one)."""
        return _call("ci_failure", run_id=run_id, branch=branch)

    return srv


if __name__ == "__main__":
    build().run("stdio")
