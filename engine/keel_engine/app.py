"""The engine's HTTP API (docs/CONTRACT.md, Engine API). Only the api calls it."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import addons, approvals, config, diagrams, extensions, models
from .models import usage as provider_usage
from .demo import create_demo, workspace_missing
from .events import EventBus, bus as default_bus
from .marketplace import MarketError
from .models import catalog
from .runtime import evals, hunt, rules_view, scan, stack_setup, task_pick
from .rules.packs import normalize as packs_normalize
from .runtime.explain import ExplainError, explain_step
from .runtime.service import Engine, EngineError
from .runtime.stack_edit import StackError
from .tools import git, mcp, worktrees
from .workflows import editor as wf_editor
from .workflows.estimate import estimate
from .workflows.model import WorkflowError, from_dict
from .workflows.templates import templates
from .workflows.validate import validate, validate_yaml

log = logging.getLogger(__name__)


class ModelSpec(BaseModel):
    provider: Literal["fake", "claude", "codex", "copilot", "local", "vertex"]
    mode: Literal["subscription", "api", "opencode"] = "api"
    model: str = ""
    effort: str | None = None


class AC(BaseModel):
    id: str
    layer: str = "API"
    title: str = ""
    status: str | None = None       # a criterion handed over finished (done, already-met) stays finished


class DiagramBody(BaseModel):
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
    width: int = 100
    max_lines: int = 60
    zoom: str | None = None


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
    sendback_limit: int | None = Field(default=None, ge=1, le=100)   # send-backs of one gate in a row before keel asks (5)
    simulate_checks: bool | None = None   # default: simulate test runs when every model is fake
    unlocks: list[Unlock] | None = None   # unlocks: that path bypasses the guard matrix in that phase
    sections: list[str] | None = None     # knowledge-refresh: one librarian per section
    spec_check: bool | None = None        # send a spec back once when keel's spec check finds a gap (default: real models only)
    usage_warn: float | None = Field(default=None, ge=0, le=1)    # warn before an agent when its plan window is this used (0.80)
    usage_pause: float | None = Field(default=None, ge=0, le=1)   # pause before an agent at this (0.95)
    provider_windows: list[dict[str, Any]] | None = None          # the api's latest plan windows: [{provider, window, used_pct, resets_at, ...}]
    # flow options (a parent flow's start_flow seed can give the same keys)
    fast: bool | None = None                    # init: no knowledge base unless named, re-use passed rungs; hunt: fast lenses
    fix_attempts_per_rung: int | None = Field(default=None, ge=1, le=10)   # unused since 0.18.22: the rung gate asks after each doctor round
    hunt_mode: Literal["auto", "semi"] | None = None   # hunt: semi stops after the sweep and after the provers
    hunt_scope: str | None = None               # hunt: all | diff | <path,path>
    hunt_lenses: list[str] | None = None        # hunt: the lenses to propose (default: hunt.lenses in .keel/config.yml)
    hunt_run: str | None = None                 # hunt-next: the run to drain (default: the project's latest)
    # v0.4.1 run modes (runtime/run_mode.py): manual stops at every gate, important approves clean AC gates, auto approves
    # every gate keel can decide (the safety stops still stop), readonly lets no agent edit or commit
    run_mode: Literal["manual", "important", "auto", "readonly"] = "manual"
    # who keel's commits are by and whether they name KeelBot (runtime/actions.py commit_args): "Name <email>" (default:
    # the project's git name, else KeelBot), and the Co-Authored-By: KeelBot line (default on)
    commit_author: str | None = None
    commit_coauthor: bool | None = None
    # v0.10.0 plugins (Control › Plugins): the ones on for the project; git:push / git:pr follow push_pr; git:branch the pattern
    plugins: list[str] | None = None
    push_pr: Literal["ask", "auto", "never"] | None = None
    branch_pattern: str | None = None
    # the Supervisor plugin (plugins/supervisor): {on, mode, model, effort, cap_tokens, max_answers, policy}; its own
    # route checks and changes it during a run
    supervisor: dict[str, Any] | None = None


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
    # 0.18.57 the "where to look" lookups (agent_knowledge.py): without this field pydantic dropped it, so hints on in
    # the Agents drawer never reached a flow's agent or KeelBot
    hints: bool | None = None


class AgentSettings(BaseModel):
    knowledge: AgentKnowledge | None = None


class OtherProject(BaseModel):
    """keel 0.18.64 another project registered in this keel: the flow's agents may read it, never change it."""
    id: str
    name: str = ""
    root: str


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
    # keel 0.18.64 every other project registered in this keel (runtime/other_projects.py): read only for the agents
    other_projects: list[OtherProject] = Field(default_factory=list)


class Ask(BaseModel):
    model: ModelSpec
    system: str = ""
    prompt: str
    keys: dict[str, str] | None = None
    timeout: int = Field(default=300, ge=10, le=1800)


class TaskPick(BaseModel):
    """Task automations: the ticket and the workflows to pick from (runtime/task_pick.py)."""
    model: ModelSpec | None = None
    keys: dict[str, str] | None = None
    timeout: int = Field(default=120, ge=10, le=600)
    kind: str = "flow"
    ticket: dict = Field(default_factory=dict)
    workflows: list[dict] = Field(default_factory=list)
    default: str = ""
    facts: list[str] = Field(default_factory=list)


class PluginCall(BaseModel):
    """A tool call from a plugin's MCP server (keel_plugin_<name>.server): the agent call's key, the tool, its
    arguments."""
    key: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class PluginAsk(BaseModel):
    project: str
    title: str
    command: str


class EvalPrepare(BaseModel):
    name: str
    dest: str                           # a new folder under $KEEL_DATA/evals


class WorktreeAdd(BaseModel):
    root: str
    name: str
    branch: str
    start: str | None = None            # the commit or branch it starts from (default: the folder's HEAD)


class RemotesFetch(BaseModel):
    """0.18.64 fetch every remote of a project (keel_fetch_remotes, the api's flow start)."""
    root: str
    keys: dict[str, str] | None = None   # keel's GitHub login, for a remote on its host only (tools/git_auth.py)
    start: str | None = None             # a ref: the report says whether it is the newest copy of the base


class BranchSync(BaseModel):
    """0.18.64 keel merges or rebases a flow's branch onto a base (runtime/branch_sync.py)."""
    onto: str | None = None              # default: the base branch's remote copy (origin/main), else the local base
    mode: Literal["merge", "rebase"] = "merge"
    action: Literal["start", "continue", "abort"] = "start"
    root: str | None = None              # the project's folder now (it moves when keel is started another way)


class WorktreeRemove(BaseModel):
    root: str
    name: str
    branch: str | None = None           # also delete this branch (a flow's is kept for its PR)


class RootBody(BaseModel):
    root: str


class Resume(BaseModel):
    decision: Literal["approve", "reject"]
    why: str | None = None
    payload: dict[str, Any] | None = None
    keys: dict[str, str] | None = None   # logins again: they live in memory only and a restart forgets them
    root: str | None = None              # the project's folder now (it moves when keel is started another way)
    question_id: str | None = None       # the id of the question the answer is for: a flow that moved on answers 409, not the new one


class ModeBody(BaseModel):
    mode: Literal["manual", "important", "auto", "readonly"]


class ModelsBody(BaseModel):
    """0.18.64 a model for this flow only: an agent, a step, or an agent's steps in one phase (runtime/thread_models.py)."""
    agent: str | None = None
    step: str | None = None
    phase: str | None = None
    model: dict[str, Any] | None = None   # {provider, mode, model, effort?}; null takes the pick back
    keys: dict[str, str] | None = None    # the new model's login or API key (memory only, like a start's keys)


class Continue(BaseModel):
    keys: dict[str, str] | None = None   # the thread's logins (the engine keeps them in memory only)
    root: str | None = None


class DocumentBody(BaseModel):
    text: str
    root: str | None = None              # the project's folder now (as Resume)


class Rewind(BaseModel):
    checkpoint_id: str
    keys: dict[str, str] | None = None   # logins again: they live in memory only and a restart forgets them
    root: str | None = None


class YamlBody(BaseModel):
    yaml: str


class RulesConfigBody(BaseModel):
    root: str
    patch: dict[str, Any]


class RulePackBody(BaseModel):
    root: str
    yaml: str
    replace: str | None = None     # the id of the pack being edited


class RuleInstallBody(BaseModel):
    root: str
    source: Literal["library", "file", "marketplace"]
    name: str | None = None        # the pack's name (its id); a library install needs it
    version: str | None = None     # optional: install exactly this version
    yaml: str | None = None        # the pack itself, for a file or a marketplace install
    replace: bool = False          # another version is installed: replace it (upgrade or downgrade)


class RuleUninstallBody(BaseModel):
    root: str
    name: str


class RuleCheckBody(BaseModel):
    yaml: str
    calls: list[dict[str, Any]] = Field(default_factory=list)   # recorded tool calls for the dry-run


class EstimateBody(BaseModel):
    yaml: str
    acs: int = 3
    history: list[dict[str, Any]] | None = None
    models: dict[str, ModelSpec] | None = None   # optional: agent -> model, for cost and by_provider
    knowledge_tokens: dict[str, int] | None = None   # optional: agent -> tokens of the knowledge it is given
    # 0.18.57 optional: the project folder; for the knowledge-refresh flow (building the project notes) a librarian
    # with no history then also counts what it reads of the code (runtime/knowledge.py librarian_read_tokens)
    root: str | None = None


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


class HuntClose(BaseModel):
    id: str                                   # F-001 or G-01
    disposition: Literal["fixed", "accepted", "wontfix"] = Field(alias="as")
    note: str


class ProviderUsage(BaseModel):
    provider: Literal["claude", "codex", "copilot"]
    key: str | None = None       # the login: codex auth.json content, or the GitHub token; never logged


class ProviderTest(BaseModel):
    provider: Literal["fake", "claude", "codex", "copilot", "local", "vertex"]
    mode: Literal["subscription", "api", "opencode"] = "api"
    model: str = ""
    key: str | None = None
    effort: str | None = None    # the agent's thinking effort: the test runs with it, as its flows do


class CatalogKeys(BaseModel):
    """POST /providers/models: the connections of the providers the catalog asks a server for (`local`: the local model
    servers, `vertex`: project and region), as the api's SecretService.engineKeys gives them. Never logged."""
    keys: dict[str, str] | None = None


class LocalEndpoint(BaseModel):
    """POST /providers/local/test: one local model server, with its key (never logged)."""
    name: str = Field(default="test", pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    kind: Literal["ollama", "lmstudio", "vllm", "llamacpp", "openai-compatible"] = "ollama"
    url: str = Field(pattern=r"^https?://\S+$", max_length=500)
    key: str | None = None
    context: int | None = Field(default=None, ge=256, le=10_000_000)


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
        # keel 0.18.22 the system packages every registered project's stacks need (setup.apt): in the background, so
        # keel starts at once; a flow that needs them waits (runtime/stack_setup.py)
        stack_setup.ASKER = stack_setup.make_asker(bus)
        stack_setup.SYSTEM.request()
        from .marketplace.routes import refresher   # the marketplace's catalogs, read while keel runs

        catalogs = asyncio.create_task(refresher())
        try:
            # the parts' own lifespans (keel_engine/extensions.py): they start now and stop first, in reverse order
            async with extensions.lifespan(app):
                yield
        finally:
            catalogs.cancel()
            await engine.close()
            await bus.stop()

    app = FastAPI(title="keel engine", version=config.VERSION, lifespan=lifespan)

    @app.middleware("http")
    async def token_check(request: Request, call_next):
        token = config.internal_token()
        # a part's open path (KeelBot's /helper/permissions/ask: the hook's question carries the turn's own ask key
        # instead, which can only ask, never answer) and a part's tool call (its own key) need no internal token
        # No token set: the engine refuses (a missing token must not open every route, /mcp/tools spawns commands),
        # unless KEEL_ENGINE_OPEN=1 says so on purpose.
        if request.url.path not in ("/health", "/plugins/call", *extensions.open_paths()):
            if not token:
                if not config.engine_open():
                    return _err(401, "The engine has no KEEL_INTERNAL_TOKEN, so it refuses requests.",
                                "Start it with KEEL_INTERNAL_TOKEN set (keel-start does), or KEEL_ENGINE_OPEN=1 for local development.")
            elif not secrets.compare_digest(request.headers.get("X-Keel-Token", ""), token):
                return _err(401, "Missing or wrong X-Keel-Token.")
        return await call_next(request)

    @app.exception_handler(EngineError)
    async def engine_error(_req, exc: EngineError):
        return _err(exc.status, exc.error, exc.hint)

    @app.exception_handler(MarketError)
    async def market_error(_req, exc: MarketError):
        return JSONResponse(exc.body(), status_code=exc.status)
    @app.exception_handler(rules_view.RulesError)
    async def rules_error(_req, exc: rules_view.RulesError):
        return _err(exc.status, exc.error, "; ".join(exc.errors) or None, exc.errors or None)

    @app.exception_handler(StackError)
    async def stack_error(_req, exc: StackError):
        return _err(exc.status, exc.error, exc.hint, exc.errors or None)

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

    @app.get("/addons")
    async def get_addons():
        """The add-ons this engine loaded (KEEL_ADDONS), and the ones it left out with the reason."""
        return addons.info()

    @app.get("/templates")
    async def get_templates():
        # keel's templates, then the plugins' own workflows ("plugin": the plugin that brings it; the api lists one only
        # for the projects that turned that plugin on)
        from .runtime import plugins as manifests

        return [t.model_dump() for t in templates()] + manifests.plugin_workflows()

    @app.post("/workflows/validate")
    async def post_validate(body: YamlBody):
        r = validate_yaml(body.yaml)
        # lines: each error at its line (the editor marks it); formatted: the file as keel saves it (the api stores it)
        out = {"ok": r["ok"], "errors": r["errors"], "lines": wf_editor.error_lines(body.yaml, r["errors"])}
        if r.get("notes"):
            out["notes"] = r["notes"]          # soft: an old agent name (it still works)
        if r.get("workflow") is not None:
            out["workflow"] = r["workflow"].model_dump()
            out["formatted"] = wf_editor.normalize(body.yaml)
        return out

    @app.get("/workflows/starter")
    async def get_starter(name: str = "My workflow", id: str | None = None, keel_rules: bool = True):
        """A new workflow's YAML: small, valid as it is, in keel's style (Build › Workflows › New workflow)."""
        return {"yaml": wf_editor.starter(name, id, keel_rules)}

    @app.post("/workflows/estimate")
    async def post_estimate(body: EstimateBody):
        r = validate_yaml(body.yaml)
        if r.get("workflow") is None:
            return _err(400, "The workflow does not parse.", "; ".join(r["errors"]), r["errors"])
        m = {k: v.model_dump() for k, v in (body.models or {}).items()}
        kt = dict(body.knowledge_tokens or {})
        if body.root and os.path.isdir(body.root) and r["workflow"].id == "knowledge-refresh":
            from .runtime import knowledge as knowledge_mod

            kt["librarian"] = kt.get("librarian", 0) + await asyncio.to_thread(knowledge_mod.librarian_read_tokens, body.root)
        return estimate(r["workflow"], body.acs, body.history, m, kt)

    @app.post("/diagrams/ascii")
    async def post_diagram(body: DiagramBody):
        """keel's ASCII diagrams (diagrams.py), for the api's POST /api/diagrams/ascii: keel TUI draws with it."""
        try:
            text = diagrams.render(body.kind, body.data, body.width, body.max_lines, body.zoom)
        except diagrams.DiagramError as exc:
            return _err(400, str(exc), "kinds: " + ", ".join(diagrams.KINDS))
        return {"kind": body.kind, "text": text, "lines": len(text.splitlines())}

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
        source = engine(request).sources.pop(tid, None)
        note = engine(request).base_notes.pop(tid, None)
        return {"thread_id": tid, **({"source": source} if source else {}), **({"base_note": note} if note else {})}

    @app.get("/threads/{tid}")
    async def get_thread(tid: str, request: Request):
        return await engine(request).state(tid)

    @app.post("/threads/{tid}/resume")
    async def post_resume(tid: str, body: Resume, request: Request):
        if body.keys:
            await engine(request).set_keys(tid, body.keys)
        await engine(request).set_root(tid, body.root)
        return await engine(request).resume(tid, body.decision, body.why, body.payload, body.question_id)

    @app.post("/threads/{tid}/mode")
    async def post_mode(tid: str, body: ModeBody, request: Request):
        """The run mode from the next pause on (manual | important | auto | readonly)."""
        return await engine(request).set_mode(tid, body.mode)

    @app.get("/threads/{tid}/models")
    async def get_models(tid: str, request: Request):
        """0.18.64 the flow's models: the default, each agent's, each agent step's model now, the person's picks."""
        return await engine(request).models_of(tid)

    @app.post("/threads/{tid}/models")
    async def post_models(tid: str, body: ModelsBody, request: Request):
        """0.18.64 a model for this flow only (an agent, a step, an agent's steps in a phase), from its next agent call."""
        return await engine(request).set_models(tid, body.model, body.agent, body.step, body.phase, body.keys)

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

    # 0.18.64 keel brings the flow's branch up to a base: merge (default) or rebase; a conflict stops with the files
    @app.post("/threads/{tid}/sync")
    async def post_sync(tid: str, body: BranchSync, request: Request):
        await engine(request).set_root(tid, body.root)
        return await engine(request).sync_branch(tid, body.onto, body.mode, body.action)

    @app.get("/threads/{tid}/unlocks")
    async def get_unlocks(tid: str, request: Request):
        return await engine(request).unlocks(tid)

    # ---- 0.18.64 a flow's document (its clarify document or spec), from its file; replaced while the flow waits
    @app.get("/threads/{tid}/document")
    async def get_document(tid: str, request: Request):
        return await engine(request).document(tid)

    # 0.18.64 the AC gate's code for the web: the criterion's RED and GREEN commits with each file's diff (cut)
    @app.get("/threads/{tid}/ac-code")
    async def get_ac_code(tid: str, request: Request, ac: str = "", sha: str = "", path: str = ""):
        return await engine(request).ac_code(tid, ac, sha or None, path or None)

    @app.put("/threads/{tid}/document")
    async def put_document(tid: str, body: DocumentBody, request: Request):
        await engine(request).set_root(tid, body.root)
        return await engine(request).set_document(tid, body.text)

    # ---- v0.17.2 rules: the Rules page and "Rules now" (runtime/rules_view.py)
    @app.get("/threads/{tid}/rules-now")
    async def get_rules_now(tid: str, request: Request):
        return await engine(request).rules_now(tid)

    @app.get("/rules")
    async def get_rules(root: str):
        return await asyncio.to_thread(rules_view.view, project_root(root))

    @app.put("/rules/config")
    async def put_rules_config(body: RulesConfigBody):
        return await asyncio.to_thread(rules_view.set_config, project_root(body.root), body.patch)

    @app.get("/rules/library")
    async def get_rules_library():
        return await asyncio.to_thread(rules_view.library)

    @app.get("/rules/library/{pack_id}")
    async def get_rules_library_pack(pack_id: str, version: str | None = None):
        return {"yaml": await asyncio.to_thread(rules_view.library_pack, pack_id, version)}

    @app.post("/rules/check")
    async def post_rules_check(body: RuleCheckBody):
        out = rules_view.check_pack(body.yaml)
        if out["ok"] and body.calls:
            out["dry_run"] = await asyncio.to_thread(rules_view.dry_run, body.yaml, body.calls)
        return out

    # the marketplace's preview of a pack: keel's own reading, each rule with who enforces it (no project needed)
    @app.post("/rules/read")
    async def post_rules_read(body: RuleCheckBody):
        return await asyncio.to_thread(rules_view.reading, body.yaml)

    @app.post("/rules/packs")
    async def post_rule_pack(body: RulePackBody):
        root = project_root(body.root)
        # the Rules page's save (New rule, Edit, Import): written tidy (rules/packs.py normalize); installs keep the file
        pack = await asyncio.to_thread(rules_view.save_pack, root, packs_normalize(body.yaml), body.replace,
                                       None if body.replace else "file")
        return {"pack": pack, "view": await asyncio.to_thread(rules_view.view, root)}

    # v0.17.2 rule packs as installable items (a marketplace install calls these through the api)
    @app.get("/rules/packs")
    async def get_rule_packs(root: str):
        return await asyncio.to_thread(rules_view.installed, project_root(root))

    @app.post("/rules/packs/install")
    async def post_rule_pack_install(body: RuleInstallBody):
        root = project_root(body.root)
        pack = await asyncio.to_thread(rules_view.install, root, body.source, body.name, body.version, body.yaml, body.replace)
        return {"pack": pack, "installed": await asyncio.to_thread(rules_view.installed, root)}

    @app.post("/rules/packs/uninstall")
    async def post_rule_pack_uninstall(body: RuleUninstallBody):
        root = project_root(body.root)
        await asyncio.to_thread(rules_view.remove_pack, root, body.name)
        return {"installed": await asyncio.to_thread(rules_view.installed, root)}

    @app.get("/rules/packs/{pack_id}")
    async def get_rule_pack(pack_id: str, root: str):
        return {"yaml": await asyncio.to_thread(rules_view.pack_text, project_root(root), pack_id)}

    @app.delete("/rules/packs/{pack_id}")
    async def delete_rule_pack(pack_id: str, root: str):
        root = project_root(root)
        await asyncio.to_thread(rules_view.remove_pack, root, pack_id)
        return await asyncio.to_thread(rules_view.view, root)

    @app.post("/threads/{tid}/unlocks")
    async def post_unlock(tid: str, body: UnlockBody, request: Request):
        return await engine(request).add_unlock(tid, body.path, body.phase, body.reason)

    def project_root(root: str) -> str:
        if not os.path.isdir(root):
            raise EngineError(400, f"The project folder {root} does not exist.", "Send the folder as the engine sees it.")
        return os.path.realpath(root)

    @app.post("/projects/{pid}/scan")
    async def post_scan(pid: str, body: ScanBody, request: Request):
        """Starts the scan (stack, code graph, map, knowledge sections) and answers at once with the index status. A new
        project's stacks may need system packages: keel looks (in the background) too."""
        root = project_root(body.root)
        stack_setup.SYSTEM.request(root, pid)
        return await request.app.state.scanner.start(pid, root, body.rebuild)

    # keel 0.18.22 stack setup: what keel installs for the projects' stacks (setup.apt / setup.run), for the dashboard
    @app.get("/setup/status")
    async def get_setup_status():
        return stack_setup.SYSTEM.status()

    @app.post("/setup/run")
    async def post_setup_run():
        """Look again now (after a stack pack changed by hand, say). Answers at once; the run is in the background."""
        return stack_setup.SYSTEM.request()

    @app.get("/projects/{pid}/index")
    async def get_index(pid: str):
        return await asyncio.to_thread(scan.status, pid)

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

    @app.post("/projects/{pid}/actions/{name}")
    async def post_action(pid: str, name: str, body: dict, request: Request):
        """v0.16.0 one engine action outside a flow (keel_run_action): only an action with a level (action_docs
        ACTION_LEVELS), on a blank flow input for the project's folder. The MCP level is enforced by keel's MCP server
        (mcp_more.run_action); like the rest of keel's api on 127.0.0.1, this route has no login of its own.
        0.18.65 args.thread_id: the action runs in that flow's folder (its worktree), a flow of this project only; the
        other args are the action's params (keel_ci_run: jobs)."""
        from .runtime import action_docs
        from .runtime.actions import ActionInput, run_action

        if name not in action_docs.dispatch_names():
            raise EngineError(404, f"No action {name}.")
        if action_docs.action_level(name) == "flow-only":
            raise EngineError(409, f"{name} only runs inside a flow.", "Start a flow instead.")
        root = str(body.get("root") or "")
        if not root or not Path(root).is_dir():
            raise EngineError(400, "root is missing or not a folder.")
        args = dict(body.get("args") or {}) if isinstance(body.get("args"), dict) else {}
        tid = str(args.pop("thread_id", "") or "")
        if tid:
            row = await engine(request)._row(tid)
            if row["project_id"] != pid:
                raise EngineError(404, f"No flow {tid} in this project.")
            if not Path(row["root"]).is_dir():
                raise EngineError(409, f"The flow's folder is gone: {row['root']}.")
            root = row["root"]
        a = ActionInput(root=root, phase="none", title=f"{name} (MCP)", ac=None, acs=[], fake=False, flow="mcp", project=pid,
                        settings=dict(body.get("settings") or {}), params=args)
        r = await run_action(name, a)
        # a check's output matters at its end; a CI report starts with its table (0.18.65)
        detail = (r.detail or "")[:12000] if name in ("ci_pipeline", "ci_report") else (r.detail or "")[-6000:]
        return {"ok": r.ok, "note": r.note, "detail": detail}

    from .approvals_routes import router as approvals_router   # core: ask a person and wait (keel_engine/approvals.py)
    app.include_router(approvals_router)
    from .marketplace.routes import router as marketplace_router   # core: find, install and update plugins (step 4)
    app.include_router(marketplace_router)
    from .runtime.stack_routes import router as stack_router   # keel 0.18.9 a project's own stacks (runtime/stack_edit.py)
    app.include_router(stack_router)
    from .runtime.spec_routes import router as spec_router   # keel 0.18.64 the spec check and the citation check on a file
    app.include_router(spec_router)

    # v0.8.0 quality runs: the eval sets (content/evals) and a fresh copy of a set's project for one case
    @app.get("/evals")
    async def get_evals():
        return await asyncio.to_thread(evals.load_all)

    @app.post("/evals/prepare")
    async def post_eval_prepare(body: EvalPrepare):
        home = (config.data_dir() / "evals").resolve()
        dest = Path(body.dest).resolve()
        if not dest.is_relative_to(home) or dest == home:
            raise EngineError(400, "An eval project is made under keel's data folder only.", f"Use a new folder under {home}.")
        try:
            return await asyncio.to_thread(evals.prepare, body.name, str(dest))
        except evals.EvalError as exc:
            raise EngineError(exc.status, str(exc), exc.hint) from exc

    # a flow next to other flows works in its own worktree (the api makes it at the start, removes it when it ends)
    @app.post("/worktrees")
    async def post_worktree(body: WorktreeAdd):
        try:
            return await asyncio.to_thread(worktrees.add, project_root(body.root), body.name, body.branch, body.start)
        except worktrees.WorktreeError as exc:
            raise EngineError(409, str(exc), exc.hint) from exc

    # 0.18.64 fetch every remote at once (git.FETCH_EACH s each at most): one result per remote, and how far the base
    # branch is behind each remote's copy (runtime/branch_sync.py). Never fails because a remote cannot be reached.
    @app.post("/remotes/fetch")
    async def post_remotes_fetch(body: RemotesFetch):
        from .runtime import branch_sync

        root = project_root(body.root)
        fetched = await asyncio.to_thread(branch_sync.fetch_all, root, body.keys)
        start = await asyncio.to_thread(git.rev, root, body.start) if body.start else None
        return {**await asyncio.to_thread(branch_sync.report, root, fetched, start), "fetched": fetched}

    # 0.18.57 what Start a flow says before a start: can a worktree flow run the tests, is the base behind its remote
    @app.get("/start-check")
    async def get_start_check(root: str):
        from .runtime import container

        return await asyncio.to_thread(container.start_check, project_root(root))

    # 0.18.57 the configured commands that cannot run as written (keel2 doctor, the project's doctor)
    @app.get("/config-check")
    async def get_config_check(root: str):
        from .runtime import config_check

        found = await asyncio.to_thread(config_check.stale, project_root(root))
        return {"stale": found, "lines": config_check.lines(found)}

    # 0.18.64 keel v1's leftover state file (.keel/state.json): what it says, and archiving it (runtime/v1_state.py)
    @app.get("/keel-state")
    async def get_keel_state(root: str):
        from .runtime import v1_state

        return await asyncio.to_thread(v1_state.show, project_root(root))

    @app.post("/keel-state/close")
    async def post_keel_state_close(body: RootBody):
        from .runtime import v1_state

        try:
            return await asyncio.to_thread(v1_state.close, project_root(body.root))
        except FileNotFoundError as exc:
            raise EngineError(404, str(exc), "Nothing to archive: keel_state shows what keel knows.") from exc

    @app.post("/worktrees/remove")
    async def post_worktree_remove(body: WorktreeRemove):
        root = project_root(body.root)
        try:
            path = worktrees.path_of(root, body.name)
        except worktrees.WorktreeError as exc:
            raise EngineError(400, str(exc), exc.hint) from exc
        await asyncio.to_thread(worktrees.remove, root, str(path), body.branch)
        return {"ok": True}

    # ---- v0.10.0 plugins (keel_engine/plugins): each part's own routes are its router (extensions.mount, below) --

    @app.get("/plugins")
    async def get_plugins():
        """keel's installable plugins with what each adds, and what each step action takes in `with:`."""
        from .runtime import plugins as manifests
        from .runtime.action_docs import describe

        params = extensions.action_params()
        out = []
        for p in manifests.catalog():
            acts = [{"name": a, "with": params.get(a, {}), "summary": describe(a)["summary"]} for a in p["actions"]]
            out.append({**p, "actions": acts})
        return out

    @app.post("/plugins/ask")
    async def post_plugin_ask(body: PluginAsk, request: Request):
        """keel2 mcp --write: an acting tool (change data, commit, push, open a PR) asks the person in the Inbox."""
        return approvals.of(request.app.state.bus).ask_person(body.project, body.title[:200], body.command[:4000])

    @app.get("/plugins/ask/{qid}")
    async def get_plugin_ask(qid: str, request: Request):
        try:
            return approvals.of(request.app.state.bus).asked(qid)
        except approvals.ApprovalError as exc:
            raise EngineError(exc.status, str(exc), exc.hint) from exc

    @app.post("/plugins/call")
    async def post_plugin_call(body: PluginCall):
        """A tool call from a part's MCP server, with its agent call's key (keel_engine/extensions.py call)."""
        return {"text": await asyncio.to_thread(extensions.call, body.key, body.tool, body.args)}

    @app.post("/mcp/tools")
    async def post_mcp_tools(body: McpServerSpec):
        return await mcp.list_tools(body.model_dump(exclude_none=True))

    @app.post("/providers/test")
    async def post_provider_test(body: ProviderTest):
        return await models.test_provider(body.provider, body.mode, body.model, body.key, body.effort)

    @app.post("/providers/usage")
    async def post_provider_usage(body: ProviderUsage):
        """What the provider says is used and what remains now (codex app-server, GitHub quota; claude: its last run)."""
        return await provider_usage.read_live(body.provider, body.key)

    @app.post("/agents/ask")
    async def post_agents_ask(body: Ask):
        return await models.ask(body.model.model_dump(), body.system, body.prompt, body.keys, body.timeout)

    @app.post("/tasks/pick")
    async def post_tasks_pick(body: TaskPick):
        """Task automations: the best flow for a ticket (one cheap model call, the default flow without a model)."""
        data = body.model_dump()
        data["model"] = body.model.model_dump() if body.model else None
        return await task_pick.pick(data)

    @app.get("/providers/models")
    async def get_models():
        return await asyncio.to_thread(catalog.build_catalog)

    @app.post("/providers/models")
    async def post_models(body: CatalogKeys):
        """The catalog with the providers the api has set up (local model servers, Vertex AI)."""
        return await asyncio.to_thread(catalog.build_catalog, body.keys or {})

    @app.post("/providers/local/test")
    async def post_local_test(body: LocalEndpoint):
        """Can keel reach this local model server, and which models does it have (Connections › Local models)."""
        from .models import local

        return await asyncio.to_thread(local.test_endpoint, body.model_dump(exclude_none=True))

    # the parts' routes last (built-in parts first, then add-ons), so they cannot shadow one of keel's own; what their
    # routes raise (PartError, DbError, GitError ...) is answered as {error, hint} with its status
    async def part_error(_req, exc: Exception):
        return _err(getattr(exc, "status", 400), str(exc), getattr(exc, "hint", "") or None)

    for kind in extensions.errors():
        app.add_exception_handler(kind, part_error)
    extensions.mount(app)
    return app

