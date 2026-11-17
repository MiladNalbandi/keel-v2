"""The marketplace's engine routes (docs/plugins/13-step4-contract.md §5). Only the api calls them, with keel's internal
token, like every other engine route. A refusal answers 4xx {"error", "hint", ...} in plain words.

    GET    /marketplace/search?q=&category=&kind=     hits + the sources' state (kind: plugin, workflow, rules, skills, stacks or all)
    GET    /marketplace/workflows?q=&category=        workflows (21-workflow-marketplace.md §9)
    GET    /marketplace/workflows/{name}              one workflow: versions, review, needs, checks
    POST   /marketplace/workflows/{name}/fetch {version?}   its verified YAML (the api installs it)
    GET    /marketplace/rule-packs?q=&category=       rule packs (23-rules-in-marketplace.md)
    GET    /marketplace/rule-packs/{name}             one rule pack: versions, review, checks
    POST   /marketplace/rule-packs/{name}/fetch {version?}  its verified YAML (the api installs it with the rules api)
    GET    /marketplace/skills?q=&category=           skills (keel 0.18.6)
    GET    /marketplace/skills/{name}                 one skill: versions, review, checks
    POST   /marketplace/skills/{name}/fetch {version?}      its verified SKILL.md (the api saves it as a project skill)
    GET    /marketplace/stacks?q=&category=           stacks (keel 0.18.9)
    GET    /marketplace/stacks/{name}                 one stack: versions, review, checks
    POST   /marketplace/stacks/{name}/fetch {version?}      its verified YAML (the api saves it in .keel/stacks)
    GET    /marketplace/plugins/{name}                one plugin: versions, permissions, needs, checks, plan
    POST   /marketplace/refresh?source=               read every source now (or one)
    GET    /marketplace/installed                     installed + image plugins, and what waits for a restart
    POST   /marketplace/install {name, version?}      install (and what it needs)
    POST   /marketplace/installed/{name}/update {version?, allow_more_permissions?}
    POST   /marketplace/installed/{name}/rollback
    PUT    /marketplace/installed/{name} {on}         on or off (dependents go off too)
    DELETE /marketplace/installed/{name}?data=keep|delete
    POST   /marketplace/install-file {path}           a .kplug from /data (unsigned: source "file")
    POST   /marketplace/apply-live {names?}           apply the web/content-only changes now (live.py); the api calls
                                                      it after each change, then reads resolved.json again
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
from . import MarketError, catalog, install, live, local, rulepacks, skills, sources, stacks, workflows

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


class LiveBody(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=200)     # the plugins the change touched


class SourcesBody(BaseModel):
    sources: list[dict[str, Any]]


def _notify(request: Request):
    """Send plugin.install.* events on the app's bus from the worker thread an install runs in."""
    bus = request.app.state.bus
    loop = asyncio.get_running_loop()

    def notify(kind: str, data: dict) -> None:
        loop.call_soon_threadsafe(lambda: bus.emit(kind, EVENT_THREAD, "", step="plugin", data=data))

    return notify


def _search(q: str, category: str | None, kind: str) -> dict:
    """kind plugin (the default): step 4's answer as it was; workflow: the workflows' answer; rules: the rule packs'
    answer; skills: the skills' answer; all: every list, with the other kinds' categories as workflow_categories,
    rules_categories and skills_categories."""
    if kind == "workflow":
        return workflows.search(q, category)
    if kind == "rules":
        return rulepacks.search(q, category)
    if kind == "skills":
        return skills.search(q, category)
    if kind == "stacks":
        return stacks.search(q, category)
    out = catalog.search(q, category)
    if kind == "all":
        found = workflows.search(q, category)
        out["workflows"] = found["workflows"]
        out["workflow_categories"] = found["categories"]
        packs = rulepacks.search(q, category)
        out["rules"] = packs["rules"]
        out["rules_categories"] = packs["categories"]
        found = skills.search(q, category)
        out["skills"] = found["skills"]
        out["skills_categories"] = found["categories"]
        found = stacks.search(q, category)
        out["stacks"] = found["stacks"]
        out["stacks_categories"] = found["categories"]
    return out


@router.get("/marketplace/search")
async def get_search(q: str = "", category: str | None = None,
                     kind: Literal["plugin", "workflow", "rules", "skills", "stacks", "all"] = "plugin"):
    return await asyncio.to_thread(_search, q, category or None, kind)


# ------------------------------------------------------------------ workflows (21-workflow-marketplace.md)

class FetchBody(BaseModel):
    version: str | None = None


@router.get("/marketplace/workflows")
async def get_workflows(q: str = "", category: str | None = None):
    return await asyncio.to_thread(workflows.search, q, category or None)


@router.get("/marketplace/workflows/{name}")
async def get_workflow(name: str):
    return await asyncio.to_thread(workflows.detail, name)


@router.post("/marketplace/workflows/{name}/fetch")
async def post_workflow_fetch(name: str, body: FetchBody | None = None):
    return await asyncio.to_thread(workflows.fetch, name, (body.version if body else None) or None)


# ------------------------------------------------------------------ rule packs (23-rules-in-marketplace.md §2)
# /marketplace/rules is the four marketplace rules (13-step4-contract.md §12), so the packs are /marketplace/rule-packs.
# No install route: a pack goes into a project through the rules api (the api's /rule-packs/market/install), which takes
# the YAML fetch gives back.

@router.get("/marketplace/rule-packs")
async def get_rule_packs(q: str = "", category: str | None = None):
    return await asyncio.to_thread(rulepacks.search, q, category or None)


@router.get("/marketplace/rule-packs/{name}")
async def get_rule_pack(name: str):
    return await asyncio.to_thread(rulepacks.detail, name)


@router.post("/marketplace/rule-packs/{name}/fetch")
async def post_rule_pack_fetch(name: str, body: FetchBody | None = None):
    return await asyncio.to_thread(rulepacks.fetch, name, (body.version if body else None) or None)


# ------------------------------------------------------------------ skills (keel 0.18.6)
# No install route: a skill goes into a project through the api's skills (/projects/{pid}/skills/market/install), which
# takes the text fetch gives back.

@router.get("/marketplace/skills")
async def get_skills(q: str = "", category: str | None = None):
    return await asyncio.to_thread(skills.search, q, category or None)


@router.get("/marketplace/skills/{name}")
async def get_skill(name: str):
    return await asyncio.to_thread(skills.detail, name)


@router.post("/marketplace/skills/{name}/fetch")
async def post_skill_fetch(name: str, body: FetchBody | None = None):
    return await asyncio.to_thread(skills.fetch, name, (body.version if body else None) or None)


# ------------------------------------------------------------------ stacks (keel 0.18.9)
# No install route: a stack goes into a project through the api (/projects/{pid}/stacks/market/install), which saves
# the YAML fetch gives back with the engine's stack save (runtime/stack_edit.py).

@router.get("/marketplace/stacks")
async def get_stacks(q: str = "", category: str | None = None):
    return await asyncio.to_thread(stacks.search, q, category or None)


@router.get("/marketplace/stacks/{name}")
async def get_stack(name: str):
    return await asyncio.to_thread(stacks.detail, name)


@router.post("/marketplace/stacks/{name}/fetch")
async def post_stack_fetch(name: str, body: FetchBody | None = None):
    return await asyncio.to_thread(stacks.fetch, name, (body.version if body else None) or None)


def _detail(name: str) -> dict:
    found = catalog.lookup(name)
    if found is None:
        raise MarketError(404, f"There is no plugin {name} in the catalogs keel reads.",
                          "Refresh the catalogs (Settings › Plugins › Sources and rules), or check the name.")
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


@router.post("/marketplace/apply-live")
async def post_apply_live(body: LiveBody | None = None):
    return await asyncio.to_thread(live.apply, (body or LiveBody()).names)


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
