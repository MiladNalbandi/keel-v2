"""The Map plugin's engine part: what the code is made of, drawn for the Map page (mapper.py, sqlschema.py).

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_map, written by `keel-engine plugins resolve`): ADDON says
who it is, PART holds the keys it had as a built-in part (keel_engine/extensions.py). It adds the map routes and builds
the map at each project scan (hook on_scan). The map lives in the engine DB (table project_map, made by core).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# the same version as ../../keel-plugin.yml (tests/test_map_part.py checks it)
VERSION = "1.0.0"


def on_scan(root: str, pid: str) -> dict:
    """Build and store the project's map: {"map": {counts, sha}}, or {"map": {error}} (the scan goes on)."""
    from . import mapper

    try:
        m = mapper.build_and_store(pid, root)
        return {"map": {"counts": m.get("counts"), "sha": m.get("sha")}}
    except Exception as exc:  # the map is a picture; a bad migration file must not fail the index
        log.warning("map for %s failed: %s", pid, exc)
        return {"map": {"error": f"{type(exc).__name__}: {exc}"[:300]}}


def _router():
    from .routes import router

    return router


ADDON = {
    "name": "map",
    "title": "Map",
    "version": VERSION,
}

PART = {
    "name": "map",
    "title": "Map",
    "router": _router,
    "hooks": {"on_scan": on_scan},
}
