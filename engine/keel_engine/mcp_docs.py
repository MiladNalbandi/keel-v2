"""keel 0.18.64 MCP tools for a flow's document (mcp_server.py registers them): its clarify document or spec.

    keel_flow_document          read: the document's path, whether the person edited it, and its text (from the file);
                                with `text` (keel2 mcp --write only): replace it while the flow waits at a gate
    keel_reject_with_document   (write) the clarify result's send-back with a replacement draft: keel writes it to the
                                file and runs the spec check; it passes, and the gate shows it again with no assemble
                                pass; it fails, and the assemble step patches it
    keel_handover               (write) the clarify result's hand-over to a named flow, with a replacement document
                                first if given; returns the new flow's thread id and the link both ways

The document on disk is the source of truth: the clarify result gate shows the file as it is now and a hand-over uses
it, so an edit here is what the next flow gets. Engine routes GET / PUT /threads/{id}/document (api the same).
"""

from __future__ import annotations

import time

from .mcp_api import _flow_of, q, waiting_thread
from .mcp_server import ApiError, KeelApi, _flow, _project

DOC_WAIT = 20           # keel_reject_with_document waits this long for the spec check's answer
DOC_POLL = 1
# what a reject with a draft says when the person gave no note: the assemble step reads it with the draft
DRAFT_NOTE = "Here is my own draft of the document: keep it, and fix only what keel's spec check finds."


def _thread(api: KeelApi, project: str | None, thread_id: str | None) -> dict:
    """The flow whose document is meant: thread_id (a worktree flow too), else the project's flow."""
    if thread_id:
        thread, _why = waiting_thread(api, project, thread_id)
        if thread:
            return thread
        pid = _project(api, project)["id"]
        thread = _flow_of(api, pid, thread_id)
        if thread is None:
            raise ApiError(f"no flow {thread_id} on {pid}.")
        return thread
    pid = _project(api, project)["id"]
    thread, _workflow = _flow(api, pid)
    if not thread:
        raise ApiError(f"no flow on {pid}: a clarify flow writes its document before its result gate.")
    return thread


def _lines(d: dict, thread: dict) -> list[str]:
    tid = d.get("thread_id") or thread.get("thread_id")
    at = f"waits at {d['waiting']}" if d.get("waiting") else "does not wait at a gate (the document cannot be replaced now)"
    if not d.get("exists"):
        state = "the file is gone: this is keel's copy, and a hand-over uses it"
    elif d.get("edited"):
        state = f"you edited it at {d.get('edited_at')}; the gate shows this file and a hand-over uses it"
    elif d.get("kind") == "clarify":
        state = "as keel wrote it; the hand-over uses the file as it is when the person chooses"
    else:
        state = "the file as it is now"
    return [f"document  {d.get('path')} · the {d.get('kind')} document of \"{thread.get('title')}\" · thread {tid} · {at}",
            f"state     {state}"]


def flow_document(api: KeelApi, project: str | None = None, thread_id: str | None = None) -> str:
    thread = _thread(api, project, thread_id)
    d = api.get(f"/threads/{q(thread['thread_id'])}/document") or {}
    return "\n".join(_lines(d, thread) + ["", str(d.get("text") or "")])


def write_flow_document(api: KeelApi, text: str, project: str | None = None, thread_id: str | None = None) -> str:
    if not (text or "").strip():
        raise ApiError("text is empty: send the document's whole new text.")
    thread = _thread(api, project, thread_id)
    d = api.put(f"/threads/{q(thread['thread_id'])}/document", {"text": text}) or {}
    then = ("the result gate shows it, and a hand-over uses it" if d.get("kind") == "clarify"
            else "the gate reads it from the file")
    return (f"{d.get('path')}: replaced ({len(text):,} characters) while the flow waits at {d.get('waiting')}; keel's "
            f"file history records it as your edit, and {then}.")


def reject_with_document(api: KeelApi, text: str, why: str | None = None, project: str | None = None,
                         thread_id: str | None = None, sleep=time.sleep, wait: float = DOC_WAIT) -> str:
    if not (text or "").strip():
        raise ApiError("text is empty: send the whole replacement draft.")
    thread, none = waiting_thread(api, project, thread_id)
    if not thread:
        return none or "nothing waits on this project."
    w = thread["waiting"]
    if w.get("step") != "result" or "reject" not in (w.get("choices") or []):
        return (f"{w.get('title')}: keel_reject_with_document is for a clarify flow's result gate (this flow waits at "
                f"{w.get('step')}). keel_flow_document replaces a document; keel_edit_gate sends a gate back with notes.")
    tid = thread["thread_id"]
    payload = {"choice": "reject", "document": text}
    if w.get("id"):
        payload["question_id"] = w["id"]         # for the question that was shown: a flow that moved on refuses it (409)
    st = api.post(f"/threads/{q(tid)}/resume", {"decision": "approve", "why": (why or "").strip() or DRAFT_NOTE,
                                                "payload": payload}) or {}
    pid = st.get("project_id") or thread.get("project_id") or _project(api, project)["id"]
    deadline = time.monotonic() + wait
    while st.get("status") == "running" and time.monotonic() < deadline:
        sleep(DOC_POLL)
        st = _flow_of(api, pid, tid) or st
    now = st.get("waiting") or {}
    if st.get("status") == "waiting" and now.get("step") == "result":
        if "This is your own draft" in str(now.get("detail") or ""):
            return (f"sent back with your draft on thread {tid}: keel wrote it to the file, the spec check passed, and "
                    "the result gate shows it again (no assemble pass). Choose a hand-over there.")
        return (f"sent back with your draft on thread {tid}: the spec check found something missing, so the assemble step "
                "patched your draft; the result gate shows the patched document (keel_read_gate).")
    if st.get("status") == "running":
        return (f"sent back with your draft on thread {tid}: keel wrote it to the file and checks it now (the assemble "
                "step patches it when the spec check finds something missing); keel_status shows where the flow is.")
    return f"sent back with your draft on thread {tid}. The flow is {st.get('status')} at {st.get('current') or '-'}."


HANDS = ("feature", "fix", "change", "small", "spike")


def handover(api: KeelApi, flow: str, text: str | None = None, why: str | None = None, project: str | None = None,
             thread_id: str | None = None, sleep=time.sleep, wait: float = DOC_WAIT) -> str:
    """The clarify result's hand-over to a named flow: with `text`, keel writes that document first (the person's edit,
    as keel_flow_document); the hand-over reads the file. Returns the new flow's thread id and the link both ways."""
    if flow not in HANDS:
        raise ApiError(f'"{flow}" is not a flow a clarify document hands over to: one of {", ".join(HANDS)}.')
    thread, none = waiting_thread(api, project, thread_id)
    if not thread:
        return none or "nothing waits on this project."
    w = thread["waiting"]
    if w.get("step") != "result" or "reject" not in (w.get("choices") or []):
        return (f"{w.get('title')}: keel_handover is for a clarify flow's result gate (this flow waits at {w.get('step')}).")
    if flow not in (w.get("choices") or []):
        offered = [c for c in w.get("choices") or [] if c in HANDS]
        return (f"{w.get('title')}: this clarify result does not offer {flow} (its task type fits {', '.join(offered)}). "
                "Send it back with a note to change the task type, or choose one of those.")
    tid = thread["thread_id"]
    wrote = ""
    if text is not None:
        if not text.strip():
            raise ApiError("text is empty: send the whole document, or leave text out to hand over the file as it is.")
        d = api.put(f"/threads/{q(tid)}/document", {"text": text}) or {}
        wrote = f"keel wrote your document to {d.get('path')} first (recorded as your edit); "
    payload = {"choice": flow}
    if w.get("id"):
        payload["question_id"] = w["id"]
    body: dict = {"decision": "approve", "payload": payload}
    if (why or "").strip():
        body["why"] = why.strip()
    st = api.post(f"/threads/{q(tid)}/resume", body) or {}
    pid = st.get("project_id") or thread.get("project_id") or _project(api, project)["id"]
    deadline = time.monotonic() + wait
    while st.get("status") == "running" and time.monotonic() < deadline:
        sleep(DOC_POLL)
        st = _flow_of(api, pid, tid) or st
    kids = [c for c in st.get("children") or [] if c.get("workflow") == flow]
    if not kids:
        return (f"{wrote}handed over to {flow} on thread {tid}; the {flow} flow has not started yet (the clarify flow is "
                f"{st.get('status')} at {st.get('current') or '-'}). keel_flows lists it once it runs.")
    kid = kids[-1]["thread_id"]
    return (f"{wrote}handed over to {flow}: the {flow} flow is thread {kid}, started from the clarify document as its file "
            f"was at the choice. Link: clarify {tid} → children [{kid}] ({flow}); {flow} {kid} → parent {tid} "
            f"(step {kids[-1].get('step')}). Follow it with keel_status or keel_read_gate(flow={kid!r}).")
