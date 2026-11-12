"""The Org plugin's engine routes. keel's Python api forwards /api/org/* here (api-py keel_api/org).

    GET    /org/orgs                          the orgs, each with its roles and problems
    GET    /org/example                       an example org's YAML (the page's "Start from an example")
    GET    /org/templates                     the ready-made orgs (Marketplace › Orgs, New org): each with its chart and YAML
    GET    /org/templates/{tid}               one of them
    POST   /org/check {yaml}                  {ok, problems, roles}: checks without saving
    GET    /org/orgs/{oid}                    the org: yaml, roles, links, problems
    PUT    /org/orgs/{oid} {yaml}             saves it (400 with the problems when it has any)
    DELETE /org/orgs/{oid}                    only when no mission of it is open
    GET    /org/orgs/{oid}/missions           the missions you gave (the top of each tree), newest first
    POST   /org/orgs/{oid}/missions {role, project_id, title, request, source?, workflow?, cap_tokens?}
    GET    /org/missions/{mid}                the mission, its tree, its flows and its history
    POST   /org/missions/{mid}/start | stop | accept {why?}
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from . import config, store, templates

router = APIRouter(prefix="/org")
ORG_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class OrgErr(Exception):
    def __init__(self, status: int, error: str, hint: str | None = None):
        self.status = status
        self.hint = hint
        super().__init__(error)


class YamlBody(BaseModel):
    yaml: str = Field(max_length=config.YAML_MAX)


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    project_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    request: str = Field(default="", max_length=20_000)
    source: str | None = Field(default=None, max_length=200)
    workflow: str | None = Field(default=None, max_length=80)
    cap_tokens: int | None = Field(default=None, ge=1000)


class WhyBody(BaseModel):
    why: str = Field(default="", max_length=2000)


def _runner(request: Request):
    r = getattr(request.app.state, "org_runner", None)
    if r is None:
        raise OrgErr(503, "The org runner is not running", "keel starts it with the Org plugin: restart keel.")
    return r


def _public(m: dict | None) -> dict | None:
    if m is None:
        return None
    return {k: v for k, v in m.items() if k != "run_key"}


def _view(row: dict) -> dict:
    try:
        org = config.parse(row["yaml"])
        problems: list[str] = []
    except config.OrgError as exc:
        org, problems = None, exc.problems
    out = {"id": row["id"], "name": row["name"], "yaml": row["yaml"], "updated_at": row["updated_at"], "problems": problems,
           "roles": [], "links": []}
    if org:
        out.update(templates.shape(org))
        out["limits"] = org.limits.model_dump()
        out["projects"] = org.projects
    return out


def _org_or_404(oid: str) -> dict:
    row = store.org_row(oid)
    if not row:
        raise OrgErr(404, f"No org called {oid}", "GET /api/org/orgs lists the orgs.")
    return row


def _mission_or_404(mid: str) -> dict:
    m = store.get(mid)
    if not m:
        raise OrgErr(404, f"No mission {mid}", None)
    return m


def _tree(m: dict) -> dict:
    kids = store.children(m["id"])
    return {**_public(m), "children": [_tree(c) for c in kids]}


def _rollup(t: dict) -> dict:
    """Tokens (with what running flows used so far) and counts over a mission and everything under it."""
    tokens, counts = int(t.get("used_tokens") or 0) + int(t.get("running_tokens") or 0), {}
    todo = list(t.get("children") or [])
    while todo:
        c = todo.pop()
        tokens += int(c.get("used_tokens") or 0) + int(c.get("running_tokens") or 0)
        counts[c["status"]] = counts.get(c["status"], 0) + 1
        todo += c.get("children") or []
    return {"tokens": tokens, "counts": counts}


@router.get("/example")
def example() -> dict:
    return {"yaml": config.EXAMPLE}


@router.get("/templates")
def template_list() -> list[dict]:
    return templates.all_templates()


@router.get("/templates/{tid}")
def template(tid: str) -> dict:
    t = templates.one(tid)
    if t is None:
        raise OrgErr(404, f"No org template called {tid}", "GET /api/org/templates lists them.")
    return t


@router.post("/check")
def check(body: YamlBody) -> dict:
    try:
        org = config.parse(body.yaml)
    except config.OrgError as exc:
        return {"ok": False, "problems": exc.problems, "roles": []}
    return {"ok": True, "problems": [], "roles": list(org.roles)}


@router.get("/orgs")
def orgs() -> list[dict]:
    return [_view(r) for r in store.org_rows()]


@router.get("/orgs/{oid}")
def org(oid: str) -> dict:
    return _view(_org_or_404(oid))


@router.put("/orgs/{oid}")
def save(oid: str, body: YamlBody) -> dict:
    if not ORG_ID.match(oid):
        raise OrgErr(400, "An org id is lowercase letters, digits, - and _ (at most 40)", None)
    try:
        org_ = config.parse(body.yaml)
    except config.OrgError as exc:
        raise OrgErr(400, str(exc), exc.hint) from None
    return _view(store.save_org(oid, org_.name or oid, body.yaml))


@router.delete("/orgs/{oid}")
def delete(oid: str) -> dict:
    _org_or_404(oid)
    if any(m["status"] in store.OPEN for m in store.of_org(oid, top_only=False, limit=10_000)):
        raise OrgErr(409, "This org still has open missions", "Stop them first (Stop on each mission).")
    store.delete_org(oid)
    return {"ok": True}


@router.get("/orgs/{oid}/missions")
async def missions(oid: str, request: Request, limit: int = 50) -> list[dict]:
    await asyncio.to_thread(_org_or_404, oid)
    out = []
    for m in await asyncio.to_thread(store.of_org, oid, True, max(1, min(limit, 200))):
        t = await asyncio.to_thread(_tree, m)
        if m["status"] in store.OPEN:
            await _mark_waiting(request, t)
        out.append({**_public(m), "rollup": _rollup(t), "children_count": len(t["children"]),
                    "waiting": _first_wait(t)})
    return out


def _first_wait(t: dict) -> dict | None:
    todo = [t]
    while todo:
        n = todo.pop(0)
        if n.get("waiting"):
            return {"role": n["role"], "thread_id": n.get("thread_id"), **n["waiting"]}
        todo += n.get("children") or []
    return None


@router.post("/orgs/{oid}/missions")
async def assign(oid: str, body: AssignBody, request: Request) -> dict:
    runner = _runner(request)
    row = _org_or_404(oid)
    try:
        org_ = config.parse(row["yaml"])
    except config.OrgError as exc:
        raise OrgErr(409, "Fix the org before you give it work", "; ".join(exc.problems)) from None
    role = org_.roles.get(body.role)
    if role is None:
        raise OrgErr(400, f"The org has no role {body.role}", f"Its roles: {', '.join(org_.roles)}.")
    if role.human:
        raise OrgErr(400, f"{body.role} is a person: give the mission to an agent role", None)
    humans = config.humans(org_)
    if humans and not any(config.may(org_, h, body.role, "delegate") for h in humans):
        raise OrgErr(400, f"You do not give work to {body.role} in this org",
                     f"Add a link {{ from: {humans[0]}, to: {body.role}, can: [delegate] }}, or give it to a role you lead.")
    if body.workflow and body.workflow not in role.workflows:
        raise OrgErr(400, f"{body.role} may not run {body.workflow}", f"Its workflows: {', '.join(role.workflows)}.")
    if org_.projects and body.project_id not in org_.projects:
        raise OrgErr(400, f"This org does not work on {body.project_id}", f"Its projects: {', '.join(org_.projects)}.")
    m = await runner.assign(oid, org_, role=body.role, project_id=body.project_id, title=body.title.strip(),
                            request=body.request.strip(), source=(body.source or "").strip() or None, workflow=body.workflow,
                            cap_tokens=body.cap_tokens)
    return _public(m)


async def _mark_waiting(request: Request, t: dict) -> None:
    """Each running mission whose flow waits at a gate says so (its step and title): you may be the one it waits for."""
    eng = getattr(request.app.state, "engine", None)
    todo = [t]
    while todo:
        n = todo.pop()
        todo += n.get("children") or []
        n["waiting"] = None
        n["running_tokens"] = 0
        if eng is None or n["status"] != "running" or not n.get("thread_id"):
            continue
        try:
            st = await eng.state(n["thread_id"])
        except Exception:
            continue
        u = st.get("usage") or {}
        n["running_tokens"] = int(u.get("tokens_in") or 0) + int(u.get("tokens_out") or 0)
        w = st.get("waiting")
        if st.get("status") == "waiting" and w:
            n["waiting"] = {"step": w.get("step"), "title": w.get("title"), "person": bool(w.get("person"))}


@router.get("/missions/{mid}")
async def mission(mid: str, request: Request) -> dict:
    m = await asyncio.to_thread(_mission_or_404, mid)
    t = await asyncio.to_thread(_tree, m)
    await _mark_waiting(request, t)
    return {"mission": t, "rollup": _rollup(t), "flows": await asyncio.to_thread(store.threads_of, mid),
            "history": await asyncio.to_thread(store.history, mid)}


@router.post("/missions/{mid}/start")
async def start(mid: str, request: Request) -> dict:
    _mission_or_404(mid)
    return _public(await _runner(request).begin(mid))


@router.post("/missions/{mid}/stop")
async def stop(mid: str, request: Request) -> dict:
    _mission_or_404(mid)
    return _public(await _runner(request).stop(mid))


@router.post("/missions/{mid}/accept")
async def accept(mid: str, request: Request, body: WhyBody | None = None) -> dict:
    m = _mission_or_404(mid)
    if m["status"] != "reported":
        raise OrgErr(409, "Only a reported mission can be accepted", f"It is {m['status']} now.")
    return _public(await _runner(request).accept(mid, (body.why if body else "")))

