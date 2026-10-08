"""A part moved out of core into a plugin, for the registry's tests (tests/test_extensions.py): ADDON says who it is and
where its content is, PART keeps the keys it had as a built-in part (a lazy router, a hook, read tools), like plugins/map's
keel_plugin_map and plugins/ci's keel_plugin_ci. Its content holds a per-project plugin of Tools › Plugins
(content/plugins/moved/plugin.yml) with its own workflow, as plugins/ci/content does."""

from pathlib import Path


def _router():
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/moved/hello")
    async def hello():
        return {"hello": "moved"}

    return router


def on_scan(root, pid):
    return {"moved": {"seen": pid}}


ADDON = {"name": "moved", "version": "1.0.0", "content": Path(__file__).parent / "content"}

PART = {"name": "moved", "title": "Moved", "per_project": True, "router": _router, "hooks": {"on_scan": on_scan},
        "read_tools": ("moved_runs",), "mcp": {"server": "keel-moved"}}
