"""The code graph as a part (keel_engine/extensions.py). Its code stays where it is until step 3: the CodeGraph CLI and
its index (tools/codegraph.py), the Graph page's view (runtime/codegraph_view.py), keel's "where to look" lookups
(runtime/graph_hints.py). What it adds through the hooks:

    on_scan            index the project (init, sync or rebuild): the index row's files and symbols
    on_commit          sync the index in the background after each keel commit
    on_thread_start    sync it when a flow starts (edits made since the last index)
    mcp_specs          its MCP server, once the project's index is ready (only agents with code_graph keep it)
    prompt_context     the "where to look" lookups for agents with hints on, at no tool call
    index_available    is CodeGraph installed
"""

from __future__ import annotations

from ...tools import codegraph

# What an agent with the code graph server is told (runtime/agent_knowledge.py prompt_block).
PROMPT = ("Code graph: codegraph_search finds where a class, function or route is (file:line, no code); then read only "
          "those lines (Read with offset and limit) instead of whole files. codegraph_callers, codegraph_callees and "
          "codegraph_impact list who calls what and what a change touches.")


def on_scan(root: str, pid: str, rebuild: bool = False) -> dict:
    return {"index": codegraph.index(pid, root, rebuild)}


def on_commit(root: str) -> None:
    codegraph.sync_later(root)          # the code graph follows every keel commit (best effort, in the background)


def on_thread_start(root: str, thread: str) -> None:
    codegraph.sync_later(root)          # the code graph catches up with edits made since the last index (background)


def mcp_specs(agent, root: str) -> list[dict]:
    spec = codegraph.mcp_spec(root)
    return [spec] if spec else []


def prompt_context(agent, root: str, pid: str, query: str) -> str:
    """keel's own lookups in the code graph for this step or question: the places to read first, at no tool call."""
    if not pid or not (agent.knowledge or {}).get("hints", False):
        return ""
    from ...runtime import graph_hints

    return graph_hints.where_to_look(pid, query, mentions=agent.mentions, open_file=agent.open_file,
                                     selection=agent.selection)


def index_available() -> bool:
    return codegraph.binary() is not None


def _router():
    from .routes import router

    return router


PART = {
    "name": "graph",
    "title": "Code graph",
    "mcp": {"server": "codegraph", "prompt": PROMPT},
    "router": _router,
    "hooks": {"on_scan": on_scan, "on_commit": on_commit, "on_thread_start": on_thread_start, "mcp_specs": mcp_specs,
              "prompt_context": prompt_context, "index_available": index_available},
}
