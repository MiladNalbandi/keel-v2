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
from .runtime import codegraph_view, helper, hunt, mapper, scan
from .runtime.explain import ExplainError, explain_step
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
    status: str | None = None       # a criterion handed over finished (done, already-met) stays finished


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
    # v0.4.2 project caps (the api turns every cap into these at start; an unknown field would be dropped silently):
    cap_usd: float | None = Field(default=None, ge=0)               # the flow's usage.cost_usd may reach this (0/None = no dollar cap)
    on_cap_usd: Literal["pause", "cheaper", "stop"] | None = None   # what the dollar cap does (default: on_cap)
    step_cap_tokens: int | None = Field(default=None, ge=0)         # every step's token limit; a step's own smaller max_tokens wins
    step_on_cap: Literal["pause", "cheaper", "stop"] | None = None  # what step_cap_tokens does (default: on_cap)
    cheaper_model: ModelSpec | None = None
    stronger_model: ModelSpec | None = None   # escalate_model: the investigator's model after "unconfirmed" (default: one up)
    fix_attempts: int = 3
    simulate_checks: bool | None = None   # default: simulate test runs when every model is fake
    unlocks: list[Unlock] | None = None   # unlocks: that path bypasses the guard matrix in that phase
    sections: list[str] | None = None     # knowledge-refresh: one librarian per section
    spec_check: bool | None = None        # send a spec back once when keel's spec check finds a gap (default: real models only)
    usage_warn: float | None = Field(default=None, ge=0, le=1)    # warn before an agent when its plan window is this used (0.80)
    usage_pause: float | None = Field(default=None, ge=0, le=1)   # pause before an agent at this (0.95)
    provider_windows: list[dict[str, Any]] | None = None          # the api's latest plan windows: [{provider, window, used_pct, resets_at, ...}]
    # flow options (a parent flow's start_flow seed can give the same keys)
    fast: bool | None = None                    # init: no knowledge base unless named, re-use passed rungs; hunt: fast lenses
    fix_attempts_per_rung: int | None = Field(default=None, ge=1, le=10)   # init: setup-doctor rounds before a rung asks
    hunt_mode: Literal["auto", "semi"] | None = None   # hunt: semi stops after the sweep and after the provers
    hunt_scope: str | None = None               # hunt: all | diff | <path,path>
    hunt_lenses: list[str] | None = None        # hunt: the lenses to propose (default: hunt.lenses in .keel/config.yml)
    hunt_run: str | None = None                 # hunt-next: the run to drain (default: the project's latest)
    # v0.4.1 run modes (runtime/run_mode.py): manual stops at every gate, important approves clean AC gates, auto approves
    # every gate keel can decide (the safety stops still stop), readonly lets no agent edit or commit
    run_mode: Literal["manual", "important", "auto", "readonly"] = "manual"


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


class HelperCreate(BaseModel):
    project_id: str
    root: str
    mode: Literal["ask", "fix"] = "ask"
    model: ModelSpec | None = None
    title: str = ""
    thread_id: str | None = None
    flow: dict | None = None        # fix: the waiting flow's context (phase, phases_before ...) for the chat's phase


class HelperCommands(BaseModel):
    root: str | None = None


class HelperAsk(BaseModel):
    """keel's hook asks for the person's OK on a command (runtime/permissions.py); the key is the turn's own."""
    session: str
    key: str
    kind: str = "command"
    command: str = Field(default="", max_length=8000)
    path: str = ""


class HelperAnswer(BaseModel):
    decision: Literal["once", "always", "deny"]
    why: str = ""


class HelperUndo(BaseModel):
    path: str | None = None


class HelperDone(BaseModel):
    flow: dict[str, Any] = Field(default_factory=dict)   # the waiting flow: phase, acs, ac, unlocks, workflow, run_mode
    message: str = ""                                    # the commit's subject, as the person wrote it (else the chat's title)


class HelperPatch(BaseModel):
    title: str | None = None
    model: ModelSpec | None = None


class HelperTurn(BaseModel):
    """One message to the Helper; the api adds the logins, the MCP servers and the project's flow (runtime/helper.py)."""
    text: str = Field(min_length=1, max_length=20_000)
    model: ModelSpec | None = None
    keys: dict[str, str] | None = None
    mcp: list[McpServerSpec] = Field(default_factory=list)
    tools_allow: list[str] = Field(default_factory=list)
    agents: dict[str, AgentSettings] = Field(default_factory=dict)
    skills: dict[str, str] = Field(default_factory=dict)
    flow: dict[str, Any] | None = None          # the flow that runs or waits: title, status, phase, spec, acs, waiting
    mentions: list[dict[str, Any]] = Field(default_factory=list)   # [{kind: file|symbol|ac, value, file?, line?}]
    selection: dict[str, Any] | None = None     # {path, from, to, text}
    open_file: str | None = None
    timeout: int | None = Field(default=None, ge=30, le=3600)


class Resume(BaseModel):
    decision: Literal["approve", "reject"]
    why: str | None = None
    payload: dict[str, Any] | None = None
    keys: dict[str, str] | None = None   # logins again: they live in memory only and a restart forgets them
    root: str | None = None              # the project's folder now (it moves when keel is started another way)


class ModeBody(BaseModel):
    mode: Literal["manual", "important", "auto", "readonly"]


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


class ExplainBody(BaseModel):
    workflow: dict[str, Any] | None = None   # the workflow as JSON (includes allowed); missing = the thread's own
    step_id: str
    root: str | None = None                  # the project folder: real test commands, knowledge files, code graph
    thread_id: str | None = None             # the thread's state fills the prompt; its checkpoints give the last runs
    project_id: str | None = None
    agents: dict[str, AgentSettings] = Field(default_factory=dict)   # per agent: {knowledge}, as in StartThread


class ScanBody(BaseModel):
    root: str
    rebuild: bool = False                 # a full re-index instead of an incremental sync of an existing index


class MapBody(BaseModel):
    root: str


class GraphSearch(BaseModel):
    q: str = ""


class GraphNode(BaseModel):
    id: str
    depth: int = 1                        # 1: who uses it and what it uses; 2: one more step on both sides


class HuntClose(BaseModel):
    id: str                                   # F-001 or G-01
    disposition: Literal["fixed", "accepted", "wontfix"] = Field(alias="as")
    note: str


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
        app.state.helper = helper.HelperRunner(bus)
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
            await app.state.helper.close()
            await engine.close()
            await bus.stop()

    app = FastAPI(title="keel engine", version=config.VERSION, lifespan=lifespan)

    @app.middleware("http")
    async def token_check(request: Request, call_next):
        token = config.internal_token()
        # the hook's permission question carries the turn's own ask key instead (it can only ask, never answer)
        if token and request.url.path not in ("/health", "/helper/permissions/ask"):
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

    @app.post("/steps/explain")
    async def post_explain(body: ExplainBody, request: Request):
        """What one step really does: rules, routes, the real task prompt or the actions in words, and its last runs."""
        data = body.model_dump()
        data["agents"] = {k: v.model_dump(exclude_none=True) for k, v in body.agents.items()}
        try:
            return await explain_step(data, engine(request))
        except ExplainError as exc:
            return _err(exc.status, exc.error)

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

    @app.post("/threads/{tid}/mode")
    async def post_mode(tid: str, body: ModeBody, request: Request):
        """The run mode from the next pause on (manual | important | auto | readonly)."""
        return await engine(request).set_mode(tid, body.mode)

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

    @app.get("/projects/{pid}/graph")
    async def get_graph(pid: str):
        """The code graph for people: groups (packages or folders), units and the uses between them (codegraph_view.py)."""
        return await asyncio.to_thread(codegraph_view.overview, pid)

    @app.post("/projects/{pid}/graph/search")
    async def post_graph_search(pid: str, body: GraphSearch):
        return await asyncio.to_thread(codegraph_view.search, pid, body.q)

    @app.post("/projects/{pid}/graph/node")
    async def post_graph_node(pid: str, body: GraphNode):
        """One symbol: who uses it (left), what it uses (right), its members and how much depends on it."""
        return await asyncio.to_thread(codegraph_view.focus, pid, body.id, body.depth)

    @app.get("/projects/{pid}/hunts")
    async def get_hunts(pid: str):
        """The project's bug hunts, newest first, with their counts (runtime/hunt.py)."""
        return await asyncio.to_thread(hunt.list_view, pid)

    @app.get("/projects/{pid}/hunts/{run}")
    async def get_hunt(pid: str, run: str):
        """One hunt: candidates (with verdicts and recipes), groups, and the rendered report and candidates pages."""
        view = await asyncio.to_thread(hunt.run_view, pid, run)
        if not view:
            raise EngineError(404, f"No hunt {run} in project {pid}.")
        return view

    @app.post("/projects/{pid}/hunts/{run}/close")
    async def post_hunt_close(pid: str, run: str, body: HuntClose):
        """Close a finding or a group as fixed | accepted | wontfix, with a note (never deletes it)."""
        if not await asyncio.to_thread(hunt.get_run, pid, run):
            raise EngineError(404, f"No hunt {run} in project {pid}.")
        ok, msg = await asyncio.to_thread(hunt.close, pid, run, body.id, body.disposition, body.note)
        if not ok:
            raise EngineError(400, msg)
        return await asyncio.to_thread(hunt.run_view, pid, run)

    # ---- the Helper (runtime/helper.py) ------------------------------------------------------------------

    def helper_call(fn, *a, **k):
        try:
            return fn(*a, **k)
        except helper.HelperError as exc:
            raise EngineError(exc.status, str(exc), exc.hint) from exc

    @app.post("/helper/sessions")
    async def post_helper_session(body: HelperCreate):
        return await asyncio.to_thread(helper_call, helper.create, body.project_id, project_root(body.root), body.mode,
                                       body.model.model_dump() if body.model else None, body.title, body.thread_id, body.flow)

    @app.get("/helper/sessions")
    async def get_helper_sessions(project: str):
        return await asyncio.to_thread(helper.list_sessions, project)

    @app.get("/helper/sessions/{sid}")
    async def get_helper_session(sid: str, request: Request):
        s = await asyncio.to_thread(helper_call, helper.get, sid)
        s["busy"] = request.app.state.helper.busy(sid)
        return s

    @app.patch("/helper/sessions/{sid}")
    async def patch_helper_session(sid: str, body: HelperPatch):
        await asyncio.to_thread(helper_call, helper.get, sid, False)
        if body.model:
            await asyncio.to_thread(helper.set_model, sid, body.model.model_dump())
        if body.title is not None and body.title.strip():
            await asyncio.to_thread(helper.update, sid, title=body.title.strip()[:120])
        return await asyncio.to_thread(helper.get, sid)

    @app.delete("/helper/sessions/{sid}")
    async def delete_helper_session(sid: str, request: Request):
        await request.app.state.helper.stop(sid)
        await asyncio.to_thread(helper_call, helper.delete, sid)
        return {"ok": True}

    @app.post("/helper/sessions/{sid}/turn")
    async def post_helper_turn(sid: str, body: HelperTurn, request: Request):
        """Starts one answer and returns at once; its steps and its end come as helper.* events."""
        try:
            return await request.app.state.helper.turn(sid, body.model_dump(exclude_none=True))
        except helper.HelperError as exc:
            raise EngineError(exc.status, str(exc), exc.hint) from exc

    @app.post("/helper/sessions/{sid}/stop")
    async def post_helper_stop(sid: str, request: Request):
        await asyncio.to_thread(helper_call, helper.get, sid, False)
        return await request.app.state.helper.stop(sid)

    @app.post("/helper/permissions/ask")
    async def post_helper_ask(body: HelperAsk, request: Request):
        return await request.app.state.helper.ask(body.session, body.key, body.kind, body.command, body.path)

    @app.get("/helper/permissions")
    async def get_helper_permissions(request: Request, project: str | None = None):
        return request.app.state.helper.pending(project)

    @app.post("/helper/permissions/{qid}")
    async def post_helper_answer(qid: str, body: HelperAnswer, request: Request):
        try:
            return request.app.state.helper.answer(qid, body.decision, body.why)
        except helper.HelperError as exc:
            raise EngineError(exc.status, str(exc), exc.hint) from exc

    @app.get("/helper/sessions/{sid}/changes")
    async def get_helper_changes(sid: str):
        return await asyncio.to_thread(helper_call, helper.changes, sid)

    @app.post("/helper/sessions/{sid}/undo")
    async def post_helper_undo(sid: str, body: HelperUndo, request: Request):
        if request.app.state.helper.busy(sid):
            raise EngineError(409, "The Helper is still working in this chat.", "Wait for it, or stop it, then undo.")
        return await asyncio.to_thread(helper_call, helper.undo, sid, body.path)

    @app.post("/helper/sessions/{sid}/done")
    async def post_helper_done(sid: str, body: HelperDone, request: Request):
        """Run the checks, then keel's commit of the Helper's files; the flow's timeline gets helper.commit."""
        if request.app.state.helper.busy(sid):
            raise EngineError(409, "The Helper is still working in this chat.", "Wait for it, or stop it, then press Done.")
        bus_ = request.app.state.bus
        return await asyncio.to_thread(helper_call, helper.done, sid, body.flow,
                                       lambda t, tid, pid, data: bus_.emit(t, tid, pid, step="helper", data=data), body.message)

    @app.post("/helper/commands")
    async def post_helper_commands(body: HelperCommands):
        """The slash commands of keel's plugins and the project's own (runtime/plugins.py)."""
        root = body.root if body.root and os.path.isdir(body.root) else None
        return await asyncio.to_thread(helper.commands, root)

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

