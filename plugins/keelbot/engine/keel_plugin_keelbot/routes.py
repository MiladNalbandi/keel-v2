"""KeelBot's engine routes (helper.py). Only the api calls them, with keel's internal token; the guard's hook asks at
/helper/permissions/ask with the turn's own ask key instead (the part's open_paths, __init__.py). keel mounts them after
its own routes (keel_engine/extensions.py mount). The runner is app.state.helper (the part's lifespan).

    /helper/sessions[/{sid}]                 the chats of a project: create, list, read, rename or change the model, delete
    /helper/sessions/{sid}/turn | stop       one answer (it returns at once; helper.* events follow), stop it
    /helper/sessions/{sid}/changes | undo    Fix and side: the files KeelBot changed, put one or all back
    /helper/sessions/{sid}/done              the checks, then keel's commit of KeelBot's files
    /helper/sessions/{sid}/handover|release  a side session's branch, handed over to a task or a change flow
    /helper/permissions[/ask | /{qid}]       a command that waits for the person's OK (core approvals; old aliases)
    /helper/commands                         the slash commands: keel's, the project's plugins' and the project's own
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from keel_engine.app import AgentSettings, McpServerSpec, ModelSpec
from keel_engine.runtime.service import EngineError

from . import helper

router = APIRouter()


class HelperCreate(BaseModel):
    project_id: str
    root: str
    mode: Literal["ask", "fix", "side"] = "ask"
    model: ModelSpec | None = None
    title: str = ""
    thread_id: str | None = None
    flow: dict | None = None        # fix: the waiting flow's context (phase, phases_before ...) for the chat's phase


class HelperCommands(BaseModel):
    root: str | None = None
    plugins: list[str] = Field(default_factory=list)    # the project's plugins add their commands (/sql, /commit ...)


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
    commit: dict[str, Any] = Field(default_factory=dict)  # the project's commit_author / commit_coauthor settings


class HelperPatch(BaseModel):
    title: str | None = None
    model: ModelSpec | None = None
    folder: str | None = None           # v0.15.2 a folder id of the chat's project; "" takes the chat out of its folder


class HelperFolder(BaseModel):
    """v0.15.2 a folder for KeelBot's chats, in one project."""
    project_id: str
    name: str = ""


class HelperFolderPatch(BaseModel):
    name: str = ""


class HelperTurn(BaseModel):
    """One message to KeelBot; the api adds the logins, the MCP servers and the project's flow (helper.py)."""
    text: str = Field(min_length=1, max_length=20_000)
    model: ModelSpec | None = None
    keys: dict[str, str] | None = None
    mcp: list[McpServerSpec] = Field(default_factory=list)
    tools_allow: list[str] = Field(default_factory=list)
    agents: dict[str, AgentSettings] = Field(default_factory=dict)
    skills: dict[str, str] = Field(default_factory=dict)
    flow: dict[str, Any] | None = None          # the flow that runs or waits: title, status, phase, spec, acs, waiting
    keel: dict[str, Any] | None = None          # KeelBot's view of keel: {workflows, flows} (keelbot.py)
    plugins: list[str] = Field(default_factory=list)   # the plugins on for the project: KeelBot gets their read tools
    mentions: list[dict[str, Any]] = Field(default_factory=list)   # [{kind: file|symbol|ac, value, file?, line?}]
    selection: dict[str, Any] | None = None     # {path, from, to, text}
    open_file: str | None = None
    timeout: int | None = Field(default=None, ge=30, le=3600)


def project_root(root: str) -> str:
    if not os.path.isdir(root):
        raise EngineError(400, f"The project folder {root} does not exist.", "Send the folder as the engine sees it.")
    return os.path.realpath(root)


def helper_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except helper.HelperError as exc:
        raise EngineError(exc.status, str(exc), exc.hint) from exc


def runner(request: Request) -> helper.HelperRunner:
    return request.app.state.helper


@router.post("/helper/sessions")
async def post_helper_session(body: HelperCreate):
    return await asyncio.to_thread(helper_call, helper.create, body.project_id, project_root(body.root), body.mode,
                                   body.model.model_dump() if body.model else None, body.title, body.thread_id, body.flow)


@router.get("/helper/sessions")
async def get_helper_sessions(project: str):
    return await asyncio.to_thread(helper.list_sessions, project)


@router.get("/helper/sessions/{sid}")
async def get_helper_session(sid: str, request: Request):
    s = await asyncio.to_thread(helper_call, helper.get, sid)
    s["busy"] = runner(request).busy(sid)
    return s


@router.patch("/helper/sessions/{sid}")
async def patch_helper_session(sid: str, body: HelperPatch):
    await asyncio.to_thread(helper_call, helper.get, sid, False)
    if body.model:
        await asyncio.to_thread(helper.set_model, sid, body.model.model_dump())
    if body.title is not None and body.title.strip():
        await asyncio.to_thread(helper.update, sid, title=body.title.strip()[:120])
    if body.folder is not None:
        await asyncio.to_thread(helper_call, helper.set_folder, sid, body.folder)
    return await asyncio.to_thread(helper.get, sid)


@router.delete("/helper/sessions/{sid}")
async def delete_helper_session(sid: str, request: Request):
    await runner(request).stop(sid)
    await asyncio.to_thread(helper_call, helper.delete, sid)
    return {"ok": True}


# v0.15.2 folders for KeelBot's chats: a chat's `folder` names one (PATCH the session); deleting one keeps its chats
@router.get("/helper/folders")
async def get_helper_folders(project: str):
    return await asyncio.to_thread(helper.folders, project)


@router.post("/helper/folders")
async def post_helper_folder(body: HelperFolder):
    return await asyncio.to_thread(helper_call, helper.create_folder, body.project_id, body.name)


@router.patch("/helper/folders/{fid}")
async def patch_helper_folder(fid: str, body: HelperFolderPatch):
    return await asyncio.to_thread(helper_call, helper.rename_folder, fid, body.name)


@router.delete("/helper/folders/{fid}")
async def delete_helper_folder(fid: str):
    return await asyncio.to_thread(helper_call, helper.delete_folder, fid)


@router.get("/helper/sessions/{sid}/handover")
async def get_helper_handover(sid: str):
    """A side session's branch, the commits kept on it, what is not kept yet, and its last answer."""
    return await asyncio.to_thread(helper_call, helper.handover, sid)


@router.post("/helper/sessions/{sid}/release")
async def post_helper_release(sid: str, request: Request):
    """A side session's worktree goes; its branch and commits stay for a change flow (the api checks it out)."""
    if runner(request).busy(sid):
        raise EngineError(409, "KeelBot is still working in this chat.", "Wait for it, or stop it, then hand it over.")
    return await asyncio.to_thread(helper_call, helper.release, sid)


@router.post("/helper/sessions/{sid}/turn")
async def post_helper_turn(sid: str, body: HelperTurn, request: Request):
    """Starts one answer and returns at once; its steps and its end come as helper.* events."""
    try:
        return await runner(request).turn(sid, body.model_dump(exclude_none=True))
    except helper.HelperError as exc:
        raise EngineError(exc.status, str(exc), exc.hint) from exc


@router.post("/helper/sessions/{sid}/stop")
async def post_helper_stop(sid: str, request: Request):
    await asyncio.to_thread(helper_call, helper.get, sid, False)
    return await runner(request).stop(sid)


@router.post("/helper/permissions/ask")
async def post_helper_ask(body: HelperAsk, request: Request):
    return await runner(request).ask(body.session, body.key, body.kind, body.command, body.path)


@router.get("/helper/permissions")
async def get_helper_permissions(request: Request, project: str | None = None):
    return runner(request).pending(project)


@router.post("/helper/permissions/{qid}")
async def post_helper_answer(qid: str, body: HelperAnswer, request: Request):
    try:
        return runner(request).answer(qid, body.decision, body.why)
    except helper.HelperError as exc:
        raise EngineError(exc.status, str(exc), exc.hint) from exc


@router.get("/helper/sessions/{sid}/changes")
async def get_helper_changes(sid: str):
    return await asyncio.to_thread(helper_call, helper.changes, sid)


@router.post("/helper/sessions/{sid}/undo")
async def post_helper_undo(sid: str, body: HelperUndo, request: Request):
    if runner(request).busy(sid):
        raise EngineError(409, "KeelBot is still working in this chat.", "Wait for it, or stop it, then undo.")
    return await asyncio.to_thread(helper_call, helper.undo, sid, body.path)


@router.post("/helper/sessions/{sid}/done")
async def post_helper_done(sid: str, body: HelperDone, request: Request):
    """Run the checks, then keel's commit of KeelBot's files; the flow's timeline gets helper.commit."""
    if runner(request).busy(sid):
        raise EngineError(409, "KeelBot is still working in this chat.", "Wait for it, or stop it, then press Done.")
    bus_ = request.app.state.bus
    return await asyncio.to_thread(helper_call, helper.done, sid, body.flow,
                                   lambda t, tid, pid, data: bus_.emit(t, tid, pid, step="helper", data=data), body.message,
                                   body.commit)


@router.post("/helper/commands")
async def post_helper_commands(body: HelperCommands):
    """The slash commands of keel's plugins and the project's own (runtime/plugins.py)."""
    root = body.root if body.root and os.path.isdir(body.root) else None
    return await asyncio.to_thread(helper.commands, root, body.plugins)
