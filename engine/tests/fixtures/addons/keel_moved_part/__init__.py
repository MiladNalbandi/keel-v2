"""A part moved out of core into a plugin, for the registry's tests (tests/test_extensions.py): ADDON says who it is,
PART keeps the keys it had as a built-in part (a lazy router, a hook), like plugins/map's keel_plugin_map."""


def _router():
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/moved/hello")
    async def hello():
        return {"hello": "moved"}

    return router


def on_scan(root, pid):
    return {"moved": {"seen": pid}}


ADDON = {"name": "moved", "version": "1.0.0"}

PART = {"name": "moved", "title": "Moved", "router": _router, "hooks": {"on_scan": on_scan}}
