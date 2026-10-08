"""The map as a part (keel_engine/extensions.py): what the code is made of, drawn for the Map page. Its code stays in
runtime/mapper.py (and runtime/sqlschema.py) until step 3. It adds the map routes and builds the map at each project
scan (hook on_scan)."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def on_scan(root: str, pid: str) -> dict:
    """Build and store the project's map: {"map": {counts, sha}}, or {"map": {error}} (the scan goes on)."""
    from ...runtime import mapper

    try:
        m = mapper.build_and_store(pid, root)
        return {"map": {"counts": m.get("counts"), "sha": m.get("sha")}}
    except Exception as exc:  # the map is a picture; a bad migration file must not fail the index
        log.warning("map for %s failed: %s", pid, exc)
        return {"map": {"error": f"{type(exc).__name__}: {exc}"[:300]}}


def _router():
    from .routes import router

    return router


PART = {
    "name": "map",
    "title": "Map",
    "router": _router,
    "hooks": {"on_scan": on_scan},
}
