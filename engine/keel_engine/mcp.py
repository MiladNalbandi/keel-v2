"""`python -m keel_engine.mcp [--read-only|--write]`: keel v2's MCP server on stdio (see mcp_server.py)."""

from keel_engine.mcp_server import main

if __name__ == "__main__":
    main()
