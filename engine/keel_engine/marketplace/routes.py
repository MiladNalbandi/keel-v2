"""The marketplace's engine routes (docs/plugins/13-step4-contract.md §5). Only the api calls them, with keel's internal
token, like every other engine route. A refusal answers 4xx {"error", "hint", ...} in plain words.

    GET    /marketplace/search?q=&category=           hits + the sources' state
    GET    /marketplace/plugins/{name}                one plugin: versions, permissions, needs, checks, plan
    POST   /marketplace/refresh?source=               read every source now (or one)
    GET    /marketplace/installed                     installed + image plugins, and what waits for a restart
    POST   /marketplace/install {name, version?}      install (and what it needs)
    POST   /marketplace/installed/{name}/update {version?, allow_more_permissions?}
    POST   /marketplace/installed/{name}/rollback
    PUT    /marketplace/installed/{name} {on}         on or off (dependents go off too)
    DELETE /marketplace/installed/{name}?data=keep|delete
    POST   /marketplace/install-file {path}           a .kplug from /data (unsigned: source "file")
    GET    /marketplace/sources, PUT {sources}        the catalogs
    GET    /marketplace/rules, PUT {rule: bool, ...}  the four rules
    GET    /marketplace/sets                          the sets a core-only keel offers (content/plugin-sets.yml)

Install and update run while the request waits (a download can take a while); their progress also comes as the events
plugin.install.started / done / failed on the bus.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import config
from . import MarketError, catalog, install, local, sources

log = logging.getLogger("keel.marketplace")
router = APIRouter()

EVENT_THREAD = "marketplace"     # the thread id of the plugin.install.* events (they belong to no flow or project)
FIRST_CHECK = 30                 # seconds after the start before keel reads catalogs older than 6 hours
CHECK_EVERY = 3600               # then every hour, again only the ones older than 6 hours


class InstallBody(BaseModel):
    name: str
    version: str | None = None
    by: str = Field(default="person", max_length=200)     # who asked (the api says), kept in installed.json


class UpdateBody(BaseModel):
    version: str | None = None
    allow_more_permissions: bool = False     # a person approved the new permissions (the api's install request)
    by: str = Field(default="person", max_length=200)     # who asked (the api says), kept in installed.json


class OnBody(BaseModel):
    on: bool
    by: str = Field(default="person", max_length=200)     # who asked (the api says), kept in installed.json


class FileBody(BaseModel):
    path: str
    force: bool = False
    by: str = Field(default="person", max_length=200)     # who asked (the api says), kept in installed.json


class SourcesBody(BaseModel):
    sources: list[dict[str, Any]]


def _notify(request: Request):
    """Send plugin.install.* events on the app's bus from the worker thread an install runs in."""
    bus = request.app.state.bus
    loop = asyncio.get_running_loop()

    def notify(kind: str, data: dict) -> None:
        loop.call_soon_threadsafe(lambda: bus.emit(kind, EVENT_THREAD, "", step="plugin", data=data))

    return notify


@router.get("/marketplace/search")
async def get_search(q: str = "", category: str | None = None):
    return await asyncio.to_thread(catalog.search, q, category or None)


def _detail(name: str) -> dict:
    found = catalog.lookup(name)
    if found is None:
        raise MarketError(404, f"There is no plugin {name} in the catalogs keel reads.",
                          "Refresh the catalogs (Control › Plugins › Sources and rules), or check the name.")
    index, entry = found
    have = local.have()
    out = catalog.hit(index, entry, have)
    out["versions"] = [{**v.view(), "revoked": index.why_revoked(name, v.version),
                        "fits": catalog.why_not(index, entry, v, have) is None,
                        "why_not": catalog.why_not(index, entry, v, have)} for v in entry.versions]
    best = entry.version(out["version"]) if out["version"] else None
    out["needs"] = dict(best.plugins) if best else {}
    out["checks"] = list(install.CHECKS)
    try:
        out["plan"], out["refused"] = install.plan(name), None
    except MarketError as exc:
        out["plan"], out["refused"] = None, {"error": str(exc), "hint": exc.hint}
    return out


@router.get("/marketplace/plugins/{name}")
async def get_plugin(name: str):
    return await asyncio.to_thread(_detail, name)


@router.post("/marketplace/refresh")
async def post_refresh(source: str | None = None):
    return {"sources": await asyncio.to_thread(catalog.refresh, source or None)}


@router.get("/marketplace/installed")
async def get_installed():
    return await asyncio.to_thread(install.installed_view)


@router.post("/marketplace/install")
async def post_install(body: InstallBody, request: Request):
    return await asyncio.to_thread(install.install, body.name, body.version or None, by=body.by, notify=_notify(request))


@router.post("/marketplace/installed/{name}/update")
async def post_update(name: str, request: Request, body: UpdateBody | None = None):
    body = body or UpdateBody()
    return await asyncio.to_thread(install.update, name, body.version or None, by=body.by, notify=_notify(request),
                                   allow_more_permissions=body.allow_more_permissions)


@router.post("/marketplace/installed/{name}/rollback")
async def post_rollback(name: str, by: str = "person"):
    return await asyncio.to_thread(install.rollback, name, by=by)


@router.put("/marketplace/installed/{name}")
async def put_installed(name: str, body: OnBody):
    return await asyncio.to_thread(install.set_on, name, body.on, by=body.by)


@router.delete("/marketplace/installed/{name}")
async def delete_installed(name: str, data: Literal["keep", "delete"] = "keep", by: str = "person"):
    return await asyncio.to_thread(install.remove, name, data, by=by)


@router.post("/marketplace/install-file")
async def post_install_file(body: FileBody):
    return await asyncio.to_thread(install.install_file, body.path, by=body.by, force=body.force)


def _sources_view() -> dict:
    by_id = {s["id"]: s for s in catalog.statuses()}
    return {"sources": [{**s, **{k: v for k, v in by_id.get(s["id"], {}).items() if k not in s}}
                        for s in sources.read_sources()]}


@router.get("/marketplace/sources")
async def get_sources():
    return await asyncio.to_thread(_sources_view)


@router.put("/marketplace/sources")
async def put_sources(body: SourcesBody):
    def change():
        sources.write_sources(body.sources)
        return _sources_view()

    return await asyncio.to_thread(change)


@router.get("/marketplace/rules")
async def get_rules():
    return sources.read_rules()


@router.put("/marketplace/rules")
async def put_rules(body: dict[str, Any]):
    return sources.write_rules(body)


# ------------------------------------------------------------------ the sets (content/plugin-sets.yml)

def sets() -> dict:
    """The sets a core-only keel offers ("Start with a set"): each with the plugins it has, the ones keel does not have
    yet (missing: installed from the marketplace) and the ones it has but are off (turned on)."""
    f = config.content_dir() / "plugin-sets.yml"
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        log.warning("marketplace: %s does not read: %s", f, exc)
        data = {}
    have = local.have()
    out = []
    for s in (data or {}).get("sets") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        names = [str(n) for n in s.get("plugins") or []]
        out.append({"id": str(s["id"]), "title": str(s.get("title") or s["id"]), "summary": str(s.get("summary") or ""),
                    "plugins": names, "missing": [n for n in names if n not in have],
                    "off": [n for n in names if n in have and not have[n].on]})
    return {"sets": out}


@router.get("/marketplace/sets")
async def get_sets():
    return await asyncio.to_thread(sets)


# ------------------------------------------------------------------ reading the catalogs while keel runs

async def refresher(first: float = FIRST_CHECK, every: float = CHECK_EVERY) -> None:
    """Read each source whose copy is older than 6 hours: soon after the start, then every hour (while the rule
    "check for updates daily" is on). A failure keeps the last good copy; it never stops keel. An engine without an
    api (KEEL_API_URL=off: the tests) never goes out by itself."""
    if config.api_url() == "off":
        return
    await asyncio.sleep(first)
    while True:
        try:
            if sources.read_rules()["check_daily"]:
                await asyncio.to_thread(catalog.refresh, older_than=catalog.FRESH)
        except Exception as exc:  # noqa: BLE001 - a catalog that does not read is shown in Sources, never a crash
            log.warning("marketplace: reading the catalogs failed: %s", exc)
        await asyncio.sleep(every)
