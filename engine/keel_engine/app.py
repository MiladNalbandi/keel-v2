"""The engine's HTTP API (docs/CONTRACT.md, Engine API). Only the api calls it."""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import config, models
from .demo import create_demo, workspace_missing
from .events import EventBus, bus as default_bus
from .models import catalog
from .runtime.service import Engine, EngineError
from .tools import mcp
from .workflows.estimate import estimate
from .workflows.model import WorkflowError, from_dict
from .workflows.templates import templates
from .workflows.validate import validate, validate_yaml

log = logging.getLogger(__name__)


class ModelSpec(BaseModel):
    provider: Literal["fake", "claude", "codex", "copilot"]
    mode: Literal["subscription", "api", "opencode"] = "api"
    model: str = ""
    effort: str | None = None


class AC(BaseModel):
    id: str
    layer: str = "API"
    title: str = ""


class Unlock(BaseModel):
    path: str
    phase: str | None = None


class Settings(BaseModel):
    gates_mode: Literal["every-ac", "end-of-lane", "end"] = "every-ac"
    cap_tokens: int = 0
    on_cap: Literal["pause", "cheaper", "stop"] = "pause"
    cheaper_model: ModelSpec | None = None
    fix_attempts: int = 3
    simulate_checks: bool | None = None   # default: simulate test runs when every model is fake
    unlocks: list[Unlock] | None = None   # keel v1 unlocks: that path bypasses the guard matrix in that phase
    sections: list[str] | None = None     # knowledge-refresh: one librarian per section


class McpServerSpec(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class StartThread(BaseModel):
    project_id: str
    root: str
    workflow: dict[str, Any]
    title: str
    acs: list[AC] | None = None
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    settings: Settings = Field(default_factory=Settings)
    mcp: list[McpServerSpec] = Field(default_factory=list)
    skills: dict[str, str] = Field(default_factory=dict)
    keys: dict[str, str] | None = None   # optional: provider -> key, kept in memory only


class Resume(BaseModel):
    decision: Literal["approve", "reject"]
    why: str | None = None
    payload: dict[str, Any] | None = None


class Rewind(BaseModel):
    checkpoint_id: str


class YamlBody(BaseModel):
    yaml: str


class EstimateBody(BaseModel):
    yaml: str
    acs: int = 3
    history: list[dict[str, Any]] | None = None
    models: dict[str, ModelSpec] | None = None   # optional: agent -> model, for cost and by_provider


class ProviderTest(BaseModel):
    provider: Literal["fake", "claude", "codex", "copilot"]
    mode: Literal["subscription", "api", "opencode"] = "api"
    model: str = ""
    key: str | None = None


def _err(status: int, error: str, hint: str | None = None, errors: list[str] | None = None) -> JSONResponse:
    body: dict = {"error": error}
    if hint:
        body["hint"] = hint
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status)


def create_app(bus: EventBus | None = None, *, resume_running: bool = True) -> FastAPI:
    bus = bus or default_bus

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = Engine(bus)
        app.state.engine = engine
        app.state.bus = bus
        app.state.demo = None
        if workspace_missing() and os.environ.get("KEEL_DEMO", "1") != "0":
            try:
                app.state.demo = str(create_demo())
            except Exception as exc:  # the demo is a convenience; never block startup on it
                log.warning("could not create the demo project: %s", exc)
        bus.start()
        await engine.open(resume_running=resume_running)
        try:
            yield
        finally:
            await engine.close()
            await bus.stop()

    app = FastAPI(title="keel engine", version=config.VERSION, lifespan=lifespan)

    @app.middleware("http")
    async def token_check(request: Request, call_next):
        token = config.internal_token()
        if token and request.url.path != "/health":
            if not secrets.compare_digest(request.headers.get("X-Keel-Token", ""), token):
                return _err(401, "Missing or wrong X-Keel-Token.")
        return await call_next(request)

    @app.exception_handler(EngineError)
    async def engine_error(_req, exc: EngineError):
        return _err(exc.status, exc.error, exc.hint)

    @app.exception_handler(RequestValidationError)
    async def bad_request(_req, exc: RequestValidationError):
        parts = [f"{'.'.join(str(x) for x in e.get('loc', []) if x != 'body')}: {e.get('msg')}" for e in exc.errors()]
        return _err(400, "; ".join(parts) or "Bad request.")

    def engine(request: Request) -> Engine:
        return request.app.state.engine

    @app.get("/health")
    async def health(request: Request):
        out = {"ok": True, "version": config.VERSION, "fake": config.fake()}
        if getattr(request.app.state, "demo", None):
            out["demo"] = request.app.state.demo
        return out

    @app.get("/templates")
    async def get_templates():
        return [t.model_dump() for t in templates()]

    @app.post("/workflows/validate")
    async def post_validate(body: YamlBody):
        r = validate_yaml(body.yaml)
        out = {"ok": r["ok"], "errors": r["errors"]}
        if r.get("workflow") is not None:
            out["workflow"] = r["workflow"].model_dump()
        return out

    @app.post("/workflows/estimate")
    async def post_estimate(body: EstimateBody):
        r = validate_yaml(body.yaml)
        if r.get("workflow") is None:
            return _err(400, "The workflow does not parse.", "; ".join(r["errors"]), r["errors"])
        m = {k: v.model_dump() for k, v in (body.models or {}).items()}
        return estimate(r["workflow"], body.acs, body.history, m)

    @app.post("/threads")
    async def post_thread(body: StartThread, request: Request):
        try:
            wf = from_dict(body.workflow, body.workflow.get("yaml") or None)
        except WorkflowError as exc:
            return _err(400, str(exc), errors=[str(exc)])
        errors = validate(wf)
        if errors:
            return _err(400, "The workflow is not valid.", "; ".join(errors), errors)
        data = body.model_dump()
        data["workflow"] = wf
        data["acs"] = [a.model_dump() for a in body.acs] if body.acs else None
        data["models"] = {k: v.model_dump(exclude_none=True) for k, v in body.models.items()}
        data["settings"] = body.settings.model_dump(exclude_none=True)
        data["mcp"] = [s.model_dump(exclude_none=True) for s in body.mcp]
        tid = await engine(request).start_thread(data)
        return {"thread_id": tid}

    @app.get("/threads/{tid}")
    async def get_thread(tid: str, request: Request):
        return await engine(request).state(tid)

    @app.post("/threads/{tid}/resume")
    async def post_resume(tid: str, body: Resume, request: Request):
        return await engine(request).resume(tid, body.decision, body.why, body.payload)

    @app.post("/threads/{tid}/stop")
    async def post_stop(tid: str, request: Request):
        return await engine(request).stop(tid)

    @app.get("/threads/{tid}/history")
    async def get_history(tid: str, request: Request):
        return await engine(request).history(tid)

    @app.post("/threads/{tid}/rewind")
    async def post_rewind(tid: str, body: Rewind, request: Request):
        return await engine(request).rewind(tid, body.checkpoint_id)

    @app.post("/mcp/tools")
    async def post_mcp_tools(body: McpServerSpec):
        return await mcp.list_tools(body.model_dump(exclude_none=True))

    @app.post("/providers/test")
    async def post_provider_test(body: ProviderTest):
        return await models.test_provider(body.provider, body.mode, body.model, body.key)

    @app.get("/providers/models")
    async def get_models():
        return catalog.MODELS

    return app

