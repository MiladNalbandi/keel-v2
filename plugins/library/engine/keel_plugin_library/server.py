"""The Library plugin's MCP server for KeelBot and agents: `python -m keel_plugin_library.server`, over stdio. Its tools only
read. Each call goes back to the engine with the agent call's key (keel_engine/partmcp.py); the answers come from tools.py."""

from __future__ import annotations

from keel_engine.partmcp import call as _call


def build():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    srv = FastMCP("keel-library", log_level="WARNING", instructions=(
        "The company's policies and rules. Search before you say what the company does. Quote the words you use and name "
        "the source as library:<id>. When the library says nothing, say that."))

    @srv.tool(annotations=ro, structured_output=False)
    def library_search(query: str, limit: int = 5) -> str:
        """Find what the company's documents say about something: the best passages, each with its document and id."""
        return _call("library_search", query=query, limit=limit)

    @srv.tool(annotations=ro, structured_output=False)
    def library_read(doc_id: str) -> str:
        """One whole document, by the id library_search gave."""
        return _call("library_read", doc_id=doc_id)

    return srv


if __name__ == "__main__":
    build().run("stdio")
