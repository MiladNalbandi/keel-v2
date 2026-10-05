"""The engine's HTTP API (docs/CONTRACT.md, Engine API). Only the api calls it."""

from __future__ import annotations

import asyncio
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
from .models import usage as provider_usage
from .demo import create_demo, workspace_missing
from .events import EventBus, bus as default_bus
from .models import catalog
from .runtime import mapper, scan
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


class UnlockBody(BaseModel):
    path: str
    phase: str | None = None              # default: the phase the thread is in now
    reason: str | None = None


class Settings(BaseModel):
    gates_mode: Literal["every-ac", "end-of-lane", "end"] = "every-ac"
    cap_tokens: int = 0
    on_cap: Literal["pause", "cheaper", "stop"] = "pause"
    cheaper_model: ModelSpec | None = None
    fix_attempts: int = 3
    simulate_checks: bool | None = None   # default: simulate test runs when every model is fake
    unlocks: list[Unlock] | None = None   # unlocks: that path bypasses the guard matrix in that phase
    sections: list[str] | None = None     # knowledge-refresh: one librarian per section
    spec_check: bool | None = None        # send a spec back once when keel's spec check finds a gap (default: real models only)
    usage_warn: float | None = Field(default=None, ge=0, le=1)    # warn before an agent when its plan window is this used (0.80)
    usage_pause: float | None = Field(default=None, ge=0, le=1)   # pause before an agent at this (0.95)
    provider_windows: list[dict[str, Any]] | None = None          # the api's latest plan windows: [{provider, window, used_pct, resets_at, ...}]


class McpServerSpec(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


KnowledgeSection = Literal["architecture", "domain", "conventions", "data", "integrations", "journeys"]


class AgentKnowledge(BaseModel):
    """What one agent uses (runtime/agent_knowledge.py); a missing field keeps the agent's default."""
    sections: list[KnowledgeSection] | None = None
    code_graph: bool | None = None
    memory: bool | None = None
    strict: bool | None = None


class AgentSettings(BaseModel):
    knowledge: AgentKnowledge | None = None


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
    agents: dict[str, AgentSettings] = Field(default_factory=dict)   # per agent: {knowledge}
    keys: dict[str, str] | None = None   # optional: provider -> key, kept in memory only
    request: str | None = None           # what the user asked for, in their words (every agent gets it)
    data: dict[str, Any] | None = None   # lists a workflow reads with from: / for_each: (state.data), e.g. a seed
    parent: dict[str, Any] | None = None # the thread that started this one (start_flow sets it)


class Ask(BaseModel):
    model: ModelSpec
    system: str = ""
    prompt: str
    keys: dict[str, str] | None = None
    timeout: int = Field(default=300, ge=10, le=1800)


class Resume(BaseModel):
    decision: Literal["approve", "reject"]
    why: str | None = None
    payload: dict[str, Any] | None = None
    keys: dict[str, str] | None = None   # logins again: they live in memory only and a restart forgets them
    root: str | None = None              # the project's folder now (it moves when keel is started another way)


class Continue(BaseModel):
    keys: dict[str, str] | None = None   # the thread's logins (the engine keeps them in memory only)
    root: str | None = None


class Rewind(BaseModel):
    checkpoint_id: str
    keys: dict[str, str] | None = None   # logins again: they live in memory only and a restart forgets them
    root: str | None = None


class YamlBody(BaseModel):
    yaml: str


class EstimateBody(BaseModel):
    yaml: str
    acs: int = 3
    history: list[dict[str, Any]] | None = None
    models: dict[str, ModelSpec] | None = None   # optional: agent -> model, for cost and by_provider
    knowledge_tokens: dict[str, int] | None = None   # optional: agent -> tokens of the knowledge it is given


class ScanBody(BaseModel):
    root: str
    rebuild: bool = False                 # a full re-index instead of an incremental sync of an existing index


class MapBody(BaseModel):
    root: str


class ProviderUsage(BaseModel):
    provider: Literal["claude", "codex", "copilot"]
    key: str | None = None       # the login: codex auth.json content, or the GitHub token; never logged


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
        app.state.scanner = scan.Scanner(bus)
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
        return estimate(r["workflow"], body.acs, body.history, m, body.knowledge_tokens)

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
        data["agents"] = {k: v.model_dump(exclude_none=True) for k, v in body.agents.items()}
        provider_usage.absorb(data["settings"].pop("provider_windows", None))
        tid = await engine(request).start_thread(data)
        return {"thread_id": tid}

    @app.get("/threads/{tid}")
    async def get_thread(tid: str, request: Request):
        return await engine(request).state(tid)

    @app.post("/threads/{tid}/resume")
    async def post_resume(tid: str, body: Resume, request: Request):
        if body.keys:
            await engine(request).set_keys(tid, body.keys)
        await engine(request).set_root(tid, body.root)
        return await engine(request).resume(tid, body.decision, body.why, body.payload)

    @app.post("/threads/{tid}/continue")
    async def post_continue(tid: str, body: Continue, request: Request):
        return await engine(request).continue_after_restart(tid, body.keys, body.root)

    @app.post("/threads/{tid}/stop")
    async def post_stop(tid: str, request: Request):
        return await engine(request).stop(tid)

    @app.get("/threads/{tid}/history")
    async def get_history(tid: str, request: Request):
        return await engine(request).history(tid)

    @app.post("/threads/{tid}/rewind")
    async def post_rewind(tid: str, body: Rewind, request: Request):
        if body.keys:
            await engine(request).set_keys(tid, body.keys)
        await engine(request).set_root(tid, body.root)
        return await engine(request).rewind(tid, body.checkpoint_id)

    @app.get("/threads/{tid}/unlocks")
    async def get_unlocks(tid: str, request: Request):
        return await engine(request).unlocks(tid)

    @app.post("/threads/{tid}/unlocks")
    async def post_unlock(tid: str, body: UnlockBody, request: Request):
        return await engine(request).add_unlock(tid, body.path, body.phase, body.reason)

    def project_root(root: str) -> str:
        if not os.path.isdir(root):
            raise EngineError(400, f"The project folder {root} does not exist.", "Send the folder as the engine sees it.")
        return os.path.realpath(root)

    @app.post("/projects/{pid}/scan")
    async def post_scan(pid: str, body: ScanBody, request: Request):
        """Starts the scan (stack, code graph, map, knowledge sections) and answers at once with the index status."""
        return await request.app.state.scanner.start(pid, project_root(body.root), body.rebuild)

    @app.get("/projects/{pid}/index")
    async def get_index(pid: str):
        return await asyncio.to_thread(scan.status, pid)

    @app.post("/projects/{pid}/map")
    async def post_map(pid: str, body: MapBody):
        return await asyncio.to_thread(mapper.build_and_store, pid, project_root(body.root))

    @app.get("/projects/{pid}/map")
    async def get_map(pid: str):
        m = await asyncio.to_thread(mapper.load, pid)
        return m or {"missing": "No map yet. Build it to draw one."}

    @app.post("/mcp/tools")
    async def post_mcp_tools(body: McpServerSpec):
        return await mcp.list_tools(body.model_dump(exclude_none=True))

    @app.post("/providers/test")
    async def post_provider_test(body: ProviderTest):
        return await models.test_provider(body.provider, body.mode, body.model, body.key)

    @app.post("/providers/usage")
    async def post_provider_usage(body: ProviderUsage):
        """What the provider says is used and what remains now (codex app-server, GitHub quota; claude: its last run)."""
        return await provider_usage.read_live(body.provider, body.key)

    @app.post("/agents/ask")
    async def post_agents_ask(body: Ask):
        return await models.ask(body.model.model_dump(), body.system, body.prompt, body.keys, body.timeout)

    @app.get("/providers/models")
    async def get_models():
        return await asyncio.to_thread(catalog.build_catalog)

    return app

