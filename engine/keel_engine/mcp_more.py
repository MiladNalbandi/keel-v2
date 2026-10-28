"""keel v2 0.16.0 MCP tools beyond the first ones (mcp_server.py registers them by level): flows, inbox, live
agents, budget, specs, knowledge, search, files and engine actions (read); answering and sending back (write);
stopping and deleting flows, adding and removing projects, workflows (admin).

Each function takes the KeelApi and returns text for the client. Nothing here reads a secret: no route of keel's
logins, keys or connection secrets is called, and secret files are refused before the api is asked.
"""

from __future__ import annotations

import os
import posixpath
from typing import Any

from . import mcp_ask as ask_mod
from .mcp_api import q, waiting_thread
from .mcp_server import ApiError, KeelApi, _flow, _n, _project

READ_MAX_LINES = 400
ACTION_TIMEOUT = 900


def is_secret(path: str) -> bool:
    """The same names the guard and the api treat as secrets (rules.SECRET_FILES, keel_rules.json; the api's
    RepoService.isSecret reads that list too): never read through MCP."""
    from . import rules

    return rules.secret_name(posixpath.basename(str(path)))


_q = q


# ---------------------------------------------------------------- read

def flows(api: KeelApi, project: str | None = None, limit: int = 10) -> str:
    p = _project(api, project)
    rows = api.get(f"/projects/{p['id']}/runs", limit=max(1, min(int(limit or 10), 50))) or []
    try:
        from .mcp_flows import startable
        can = "\n\n" + startable(api, p["id"])
    except ApiError:
        can = ""
    if not rows:
        return f"no flow has run on {p['id']} yet." + can
    return "\n".join(
        f"{r.get('thread_id')}  {r.get('workflow_id') or '?'} \"{r.get('title')}\" · {r.get('status')}"
        + (f" at {r['current']}" if r.get("current") else "") + f" · criteria {r.get('acs_done', 0)}/{r.get('acs_total', 0)}"
        + f" · {_n(r.get('tokens'))} tokens · ${float(r.get('cost_usd') or 0):.2f} · {r.get('where') or 'folder'}"
        + (f" · branch {r['branch']}" if r.get("branch") else "") + (f" · error: {r['error']}" if r.get("error") else "")
        + (f" · replaced by {r['replaced_by']} (same {r.get('source') or 'change'}, started again)" if r.get("replaced_by") else "")
        for r in rows) + can


def inbox(api: KeelApi, project: str | None = None) -> str:
    pid = _project(api, project)["id"] if project else None
    view = api.get("/inbox", project=pid) or {}
    items = view.get("items") or []
    if not items:
        return "nothing waits for a person."
    out = []
    for i in items:
        out.append(f"{i.get('project_id')} · thread {i.get('thread_id')} · {i.get('kind')}: {i.get('title')}"
                   + (f" ({i['ac']})" if i.get("ac") else "") + f" · options {', '.join(i.get('options') or []) or '-'}")
        if i.get("choices"):
            out.append('    choices (choice="<name>"):')
            out += ask_mod.choice_lines(i, "      ")
        for k in ("approve", "reject"):
            e = (i.get("plain") or {}).get(k) if isinstance(i.get("plain"), dict) else None
            if isinstance(e, dict) and e.get("does"):
                out.append(f"    a plain {k} here {e['does']} (reason: {e.get('reason') or '-'})")
        out += ask_mod.question_lines(i.get("questions"), "    ")
        if i.get("questions") or i.get("choices"):
            out.append("    " + ask_mod.how_to_answer(i) + f" (thread_id={i.get('thread_id')!r}; ask_person=true asks in a form)")
        if i.get("detail"):
            d = str(i["detail"])
            out.append("    " + d[:600].replace("\n", "\n    ")
                       + (f" … (cut: keel_read_gate(project={i.get('project_id')!r}, flow={i.get('thread_id')!r}) reads it "
                          "whole)" if len(d) > 600 or i.get("more") else ""))
    return "\n".join(out)


def live(api: KeelApi, project: str | None = None) -> str:
    pid = _project(api, project)["id"] if project else None
    jobs = api.get("/jobs", project=pid, status="running", limit=50) or []
    if not jobs:
        return "no agent is running."
    out = []
    for j in jobs:
        out.append(f"{j.get('agent')} on {j.get('project_id')} · {' '.join(x for x in (j.get('step'), j.get('ac')) if x)} · "
                   f"{j.get('provider')}/{j.get('model')} · {_n(int(j.get('tokens_in') or 0) + int(j.get('tokens_out') or 0))} tokens · "
                   f"since {j.get('started_at')} · job {j.get('id')}")
        steps = (api.get(f"/jobs/{_q(j['id'])}/steps") or {}).get("steps") or []
        if steps:
            out.append(f"    last: {steps[-1].get('kind')}: {str(steps[-1].get('text') or '')[:160]}")
    return "\n".join(out)


def budget(api: KeelApi, project: str | None = None) -> str:
    p = _project(api, project)
    now = api.get(f"/projects/{p['id']}/budget/now") or {}
    t, m = now.get("today") or {}, now.get("month") or {}
    out = [f"today  {_n(t.get('tokens'))} tokens · ${float(t.get('cost_usd') or 0):.2f}",
           f"month  {_n(m.get('tokens'))} tokens · ${float(m.get('cost_usd') or 0):.2f}"]
    for f in now.get("flows") or []:
        cap = f" of cap {_n(f['cap_tokens'])}" if f.get("cap_tokens") else ""
        out.append(f"flow   \"{f.get('title')}\" ({f.get('status')}) · {_n(f.get('tokens'))} tokens{cap} · ${float(f.get('cost_usd') or 0):.2f}")
    for c in now.get("caps") or []:
        out.append(f"cap    {c.get('scope')} {c.get('window')}: {c.get('left')} of {c.get('limit')} {c.get('unit')} left"
                   + (f" (resets {c['resets_at']})" if c.get("resets_at") else "") + f" · then {c.get('action')}")
    return "\n".join(out)


def specs(api: KeelApi, project: str | None = None, path: str | None = None, history: bool = False) -> str:
    p = _project(api, project)
    pid = p["id"]
    if not path:
        docs = [d for d in api.get(f"/projects/{pid}/keel-docs") or [] if str(d.get("what", "")).startswith("Spec")]
        if not docs:
            lay = api.get(f"/projects/{pid}/layout") or {}
            return f"no spec yet on {pid} (new specs go to {lay.get('specs', 'the spec folder')}/)."
        return "\n".join(f"{d['path']}  {d.get('what')} · updated {d.get('updated') or '-'}" for d in docs)
    page = api.get(f"/projects/{pid}/wiki/page", id=f"spec:{path}") or {}
    out = [page.get("markdown") or ""]
    if history:
        vs = api.get(f"/projects/{pid}/docs/history/versions", path=path) or []
        out += ["", "history (newest first):"] + [
            f"  version {v.get('version')} · {v.get('by_kind')} {v.get('by_name') or ''} · {v.get('phase') or '-'} · {v.get('at')}"
            + (f" · commit {str(v['commit_sha'])[:7]}" if v.get("commit_sha") else "") + f" · id {v.get('id')}" for v in vs]
    return "\n".join(out)


def knowledge(api: KeelApi, project: str | None = None, section: str | None = None) -> str:
    p = _project(api, project)
    pid = p["id"]
    if section:
        return (api.get(f"/projects/{pid}/wiki/page", id=f"kb:{section}") or {}).get("markdown") or ""
    mem = api.get(f"/projects/{pid}/memory") or {}
    out = ["sections:"] + [f"  {k.get('id')}: {k.get('status')} · {k.get('words')} words · {k.get('cites')} citations"
                           for k in mem.get("knowledge") or []]
    facts = mem.get("facts") or []
    if facts:
        out += ["facts:"] + [f"  [{f.get('kind')}] {f.get('title')}: {f.get('text')}" for f in facts[:40]]
    return "\n".join(out)


def search(api: KeelApi, query: str, project: str | None = None, kind: str = "text") -> str:
    p = _project(api, project)
    pid = p["id"]
    if not (query or "").strip():
        raise ApiError("query is empty")
    if kind == "symbol":
        res = api.get(f"/projects/{pid}/graph/search", q=query) or {}
        hits = res.get("results") or res.get("nodes") or []
        if not hits:
            return res.get("reason") or "no symbol found."
        return "\n".join(f"{h.get('kind', '')} {h.get('name')} · {h.get('file') or h.get('path')}:{h.get('line') or h.get('start_line') or ''}"
                         for h in hits[:50])
    res = api.get(f"/projects/{pid}/repo/search", q=query, max=200) or {}
    out = [f"{f['path']}:{m['line']}: {str(m.get('text', '')).strip()[:200]}"
           for f in res.get("results") or [] if not is_secret(f["path"]) for m in f.get("matches") or []]
    tail = " (more: narrow the query)" if res.get("truncated") else ""
    return "\n".join(out[:200]) + (f"\n{res.get('matches')} matches in {res.get('files')} files{tail}" if out else "no match.")


def read_file(api: KeelApi, path: str, project: str | None = None, start: int = 1, lines: int = 200) -> str:
    if is_secret(path):
        raise ApiError(f"{path} holds secrets; keel never reads it through MCP.")
    p = _project(api, project)
    text = api.text(f"/projects/{p['id']}/repo/raw", path=path)
    rows = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    start = max(1, int(start or 1))
    n = max(1, min(int(lines or 200), READ_MAX_LINES))
    part = rows[start - 1:start - 1 + n]
    more = f"\n… {len(rows) - (start - 1 + len(part))} more line(s): read on with start={start + len(part)}" if start - 1 + len(part) < len(rows) else ""
    return "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(part)) + more


def spec_check(api: KeelApi, path: str, project: str | None = None) -> str:
    """0.18.64 keel's spec check on one file of the project, as a flow runs it (citations checked against the files)."""
    p = _project(api, project)
    r = api.post(f"/projects/{p['id']}/specs/check", {"path": path}) or {}
    crit = r.get("criteria") or []
    head = (f"{r.get('path') or path}: {len(crit)} acceptance criteria" + (f" ({', '.join(crit[:12])}{', …' if len(crit) > 12 else ''})" if crit else "")
            + f" · {r.get('fix') or 0} to fix (a flow sends the spec back once for these)")
    found = r.get("findings") or []
    if not found:
        return head + "\nnothing missing."
    return head + "\n" + "\n".join(f"{'fix ' if f.get('level') == 'fix' else 'note'}  {f.get('text')}" for f in found)


def citations_check(api: KeelApi, path: str | None = None, text: str | None = None, project: str | None = None) -> str:
    """0.18.64 every path:line in a file of the project (or a text) against the project's files: the ones that do not resolve."""
    if bool(path) == bool(text):
        raise ApiError("give a path (a file of the project) or a text, one of them.")
    if path and is_secret(path):
        raise ApiError(f"{path} holds secrets; keel never reads it through MCP.")
    p = _project(api, project)
    r = api.post(f"/projects/{p['id']}/citations/check", {"path": path} if path else {"text": text}) or {}
    bad = r.get("bad") or []
    where = r.get("path") or "the text"
    head = f"{where}: {r.get('checked') or 0} citations of project files checked, {len(bad)} do not resolve"
    return head + ("." if not bad else ":\n" + "\n".join(f"{'fix ' if b.get('level') == 'fix' else 'note'}  {b.get('problem')}" for b in bad))


def read_project_file(api: KeelApi, project: str, path: str, offset: int | None = None, limit: int | None = None) -> str:
    """keel 0.18.64 one file of a registered project, read only, under the rules a flow's agent reads another project by
    (runtime/other_projects.py): secrets and keel's keys are refused, build output too, and a big file is read in parts
    (offset, limit). Read from the folder keel sees (keel's MCP server runs next to the engine), not through a plugin's
    route. A project whose rule other-projects is off is not read here."""
    import os
    from pathlib import Path

    from . import rules
    from .runtime import other_projects
    from .tools.agent_tools import MAX_READ

    if not str(project or "").strip():
        raise ApiError("name the project (keel_projects lists them).")
    p = _project(api, project)
    root = os.path.realpath(str(p.get("root") or ""))
    if not p.get("root") or not os.path.isdir(root):
        raise ApiError(f"keel's MCP server cannot see the folder of {p['id']} ({p.get('root')}). Run it where keel runs "
                       "(keel2 mcp does), or use keel_read_file.")
    full = os.path.realpath(os.path.join(root, os.path.expanduser(str(path or ""))))
    if not other_projects.under(full, root):
        raise ApiError(f"{path} is outside {p['id']}: name a path inside the project.")
    rel = os.path.relpath(full, root).replace(os.sep, "/")
    o = {"id": p["id"], "name": p.get("name") or p["id"], "root": root}
    if other_projects.is_off(rules.load_config(root)):
        raise ApiError(f"{o['name']} is not read from outside: its rule other-projects is off (.keel/config.yml rules.off).")
    if is_secret(rel):
        raise ApiError(f"{rel} holds secrets; keel never reads it through MCP.")
    if rules.keel_engine_file(full, root):
        raise ApiError(rules.ENGINE_REFUSAL)
    why = other_projects.check_read(o, rel, full, ranged=offset is not None or limit is not None)
    if why:
        raise ApiError(why)
    if not Path(full).is_file():
        raise ApiError(f"no file {rel} in {o['name']}.")
    lines = Path(full).read_text(errors="replace").splitlines(keepends=True)
    start = max(int(offset or 1), 1) - 1
    part = lines[start:start + (int(limit) if limit else len(lines))]
    text = "".join(part)
    if len(text) > MAX_READ:
        shown = text[:MAX_READ]
        return shown + f"\n… cut at {MAX_READ} characters: read on with offset={start + shown.count(chr(10)) + 1}"
    left = len(lines) - (start + len(part))
    return text + (f"\n… {left} more line(s): read on with offset={start + len(part) + 1}" if left > 0 else "")


def _vs(name: str | None, ahead: int | None, behind: int | None) -> str:
    if not name or behind is None:
        return ""
    if not ahead and not behind:
        return f"same as {name}"
    return f"{behind} behind {name}" + (f", {ahead} ahead" if ahead else "")


def fetch_remotes(api: KeelApi, project: str | None = None) -> str:
    """0.18.64 (field report bug 4) every remote of the project fetched (POST /projects/{pid}/remotes/fetch): one line
    per remote, ok or failed with why, and how far the base branch is behind the remote's copy of it."""
    p = _project(api, project)
    res = api.post(f"/projects/{p['id']}/remotes/fetch", {}) or {}
    rows = res.get("remotes") or []
    if not rows:
        return f"{p['id']} has no git remote: nothing to fetch."
    base = res.get("base") or "the base branch"
    out = [f"remotes of {p['id']} (base {base}):"]
    for r in rows:
        said = "fetched" if r.get("ok") else f"fetch failed: {r.get('error')} (the base may be stale)"
        vs = [x for x in (_vs(r.get("branch"), r.get("ahead"), r.get("behind")),
                          _vs(f"{r.get('default')} (its default branch)" if r.get("default") else None,
                              r.get("default_ahead"), r.get("default_behind"))) if x]
        out.append(f"- {r.get('remote')}: {said}" + (f"; {base} is " + "; ".join(vs) if vs else ""))
    if res.get("note"):
        out.append(str(res["note"]))
    behind = [r.get("branch") for r in rows if r.get("behind")] + [r.get("default") for r in rows if r.get("default_behind")]
    if behind:
        out.append(f"A new flow can start from the newer code: keel_start_flow(..., base={behind[0]!r}). A flow that runs "
                   f"already: keel_branch_sync(onto={behind[0]!r}).")
    return "\n".join(out)


def branch_sync(api: KeelApi, project: str | None = None, thread_id: str | None = None, onto: str | None = None,
                mode: str = "merge", action: str = "start") -> str:
    """0.18.64 (field report bug 6) keel merges or rebases a flow's branch onto a base (POST /threads/{id}/sync). The
    flow: thread_id, else the project folder's flow. A conflict comes back with its files and the way on."""
    if not thread_id:
        thread, _ = _flow(api, _project(api, project)["id"])
        if not thread:
            return "no flow on this project: pass thread_id (keel_flows lists them)."
        thread_id = thread["thread_id"]
    body = {"mode": mode, "action": action, **({"onto": onto} if onto else {})}
    res = api.post(f"/threads/{_q(thread_id)}/sync", body) or {}
    if res.get("ok"):
        out = [f"flow {thread_id}: {res.get('note')}."]
        if res.get("synced") and res.get("base_head"):
            out.append(f"Its next step compares its own work with {str(res['base_head'])[:7]} (where the branch's work starts now).")
        return "\n".join(out)
    files = res.get("conflicts") or []
    return "\n".join([f"flow {thread_id}: {res.get('note')}.", *(f"- {f}" for f in files[:50]),
                      str(res.get("next") or "Resolve the files, then sync again with action=continue."),
                      f"Then: keel_branch_sync(thread_id={thread_id!r}, action='continue'); or action='abort' to go back."])


def actions(level: str) -> str:
    from .runtime import action_docs

    out = []
    for name in action_docs.dispatch_names():
        lv = action_docs.action_level(name)
        d = action_docs.describe(name)
        mark = " (only inside a flow)" if lv == "flow-only" else " (not at this level)" if _rank(level) < _rank(lv) else ""
        out.append(f"{name} [{lv}]{mark}: {d.get('summary') or ''}")
    return "\n".join(out)


def _rank(level: str) -> int:
    return {"read": 0, "write": 1, "admin": 2}.get(level, 9)


def run_action(api: KeelApi, action: str, level: str, args: dict | None = None, project: str | None = None) -> str:
    from .runtime import action_docs

    if action not in action_docs.dispatch_names():
        raise ApiError(f'no action "{action}". keel_actions lists them.')
    lv = action_docs.action_level(action)
    if lv == "flow-only":
        raise ApiError(f"{action} only runs inside a flow (it needs the flow's criteria and state). Start a flow instead.")
    if _rank(level) < _rank(lv):
        raise ApiError(f"{action} is a {lv} action; this MCP server runs at level {level}.")
    p = _project(api, project)
    # a check like verify_release runs the whole test suite: wait as long as the api waits for the engine
    r = api.post(f"/projects/{p['id']}/actions/{_q(action)}", {"args": args or {}}, timeout=ACTION_TIMEOUT) or {}
    return f"{action}: {'ok' if r.get('ok') else 'failed'} · {r.get('note') or ''}" + (f"\n{r['detail']}" if r.get("detail") else "")


def state(api: KeelApi, project: str | None = None, write: bool = False) -> str:
    """0.18.64 keel's flow state for the project (GET /projects/{pid}/keel-state): the keel v2 flows that run or wait
    there, and keel v1's leftover .keel/state.json with what it blocks."""
    p = _project(api, project)
    s = api.get(f"/projects/{p['id']}/keel-state") or {}
    out = [f"keel's flow state on {p['id']}:"]
    flows = s.get("flows") or []
    out.append("keel v2 flows that run or wait here:" if flows else "keel v2: no flow runs or waits here.")
    for f in flows:
        where = f", worktree {f['worktree']}" if f.get("worktree") else ""
        out.append(f"- \"{f.get('title') or '?'}\" ({f.get('workflow') or 'flow'}, thread {f.get('id')}): {f.get('status')} "
                   f"at {f.get('step') or '-'}, phase {f.get('phase') or 'none'}{where}")
    v1 = s.get("v1")
    if not v1:
        out.append("keel v1: no state file (.keel/state.json is not there).")
        return "\n".join(out)
    when = f", last changed {v1['updated']}" if v1.get("updated") else ""
    if v1.get("error"):
        out.append(f"keel v1: {v1.get('path')} is there but {v1['error']}{when}.")
    elif v1.get("active"):
        out.append(f"keel v1: {v1.get('path')} names the flow {v1.get('name')}{when}. keel v2 does not run it, but "
                   "keel v1's hooks (when they are still installed) refuse every commit while it is there.")
    else:
        out.append(f"keel v1: {v1.get('path')} names no active flow ({v1.get('name')}{when}); it blocks nothing.")
    how = 'keel_state(action="close")' + ("" if write else " with keel2 mcp --write")
    out.append(f"Archive it (moved to .keel/archive/state-<date>.json, never deleted): {how}, or `keel2 state close` in "
               "the project folder.")
    return "\n".join(out)


def state_close(api: KeelApi, project: str | None = None) -> str:
    """0.18.64 archive keel v1's leftover state file (POST /projects/{pid}/keel-state/close)."""
    p = _project(api, project)
    r = api.post(f"/projects/{p['id']}/keel-state/close", {}) or {}
    blocked = " It no longer blocks commits." if r.get("was_active") else ""
    return f"keel v1's state ({r.get('flow')}) is archived as {r.get('archived')} on {p['id']}.{blocked}"


# ---------------------------------------------------------------- write

def waiting_of(api: KeelApi, project: str | None = None, thread_id: str | None = None) -> tuple[dict | None, str]:
    """(what the thread waits for, its thread id), or (None, why there is nothing to answer)."""
    # 0.18.57 by thread id through the project's flows (the api has no GET /threads/{id}), and a worktree flow or one
    # started after another is found through the Inbox (mcp_api.waiting_thread)
    thread, why = waiting_thread(api, project, thread_id)
    if not thread:
        return None, why or "nothing waits on this project."
    return thread["waiting"], thread["thread_id"] if thread.get("thread_id") else str(thread_id)


def answer(api: KeelApi, decision: str, project: str | None = None, thread_id: str | None = None, why: str | None = None,
           answers: dict[str, str | list[str]] | None = None, choice: str | None = None, item_id: str | None = None,
           question_id: str | None = None) -> str:
    """Answer what waits in keel's Inbox: a gate, questions (a label, a list of labels, or own words), a named choice
    (with why when it needs a reason), or a task item (item_id)."""
    if item_id:
        # the api reads action + note (0.18.52: answers for a proposal's questions); decision/why kept for older apis
        # choice names the item's button (confirm, send_back, done; 0.18.52 start, other, later, reply, address, handle)
        body: dict[str, Any] = {"action": choice or decision, "decision": decision}
        if why:
            body.update(note=why, why=why)
        if answers:
            body["answers"] = answers
        api.post(f"/inbox/tasks/{_q(item_id)}/act", body)
        return f"task item {item_id}: {choice or decision}."
    w, tid = waiting_of(api, project, thread_id)
    if not w:
        return tid
    thread_id = tid
    if choice and w.get("choices") and choice not in w["choices"]:
        return f"{w.get('title')}: \"{choice}\" is not a choice here. Choose one of: {', '.join(w['choices'])}."
    missing = ask_mod.missing_reason(w, decision, choice, why)
    if missing:
        return f"{w.get('title')}: {missing}"
    payload: dict[str, Any] = {}
    if answers:
        payload["answers"] = answers
    if choice:
        payload["choice"] = choice
    # for the question that was shown: a flow that moved on refuses the answer (409); the api passes the payload through
    if question_id or w.get("id"):
        payload["question_id"] = question_id or w.get("id")
    st = api.post(f"/inbox/{_q(thread_id)}/act", {"decision": decision, "why": why, "payload": payload or None}) or {}
    again = st.get("waiting") or {}
    if st.get("status") == "waiting" and again.get("step") == w.get("step") and again.get("title") != w.get("title"):
        # 0.18.57 the gate refused the whole answer and asks again (a skip without its reason): say so, nothing ran
        return f"refused, nothing was done: {again.get('title')}. Answer again (keel_answer)."
    # 0.18.64 what ran, from the engine's answer: a reject with the choice edit ran edit (it said "(reject)")
    did = ask_mod.did_text(st.get("answered"), decision, choice, w)
    return f"answered thread {thread_id}: {did}. The flow is {st.get('status')} at {st.get('current') or '-'}."


EDIT_CHOICES = ("edit", "change", "rewrite", "context", "reject")   # 0.18.28 a gate's edit choices, the closest first


def edit_gate(api: KeelApi, notes: str, project: str | None = None) -> str:
    """Send a waiting gate back with notes: the agent changes its work and the gate asks again. 0.18.28 a gate with
    named choices takes its edit choice with the notes (the spec gate's edit, the contract's change, an amendment's
    context); a plain send-back only where the gate takes one."""
    if not (notes or "").strip():
        raise ApiError("notes are empty: say what to change.")
    from .mcp_server import approve_gate

    w, _tid = waiting_of(api, project)
    choices = [str(c) for c in (w or {}).get("choices") or []]
    pick = next((c for c in EDIT_CHOICES if c in choices), None)
    if pick:
        return approve_gate(api, "approve", project, notes, None, pick)
    if w and choices and "reject" not in (w.get("options") or ["approve", "reject"]):
        return (f"{w.get('title')}: it has no edit choice ({', '.join(choices)}). Pick one with keel_approve_gate "
                "(choice=..., why=your notes).")
    return approve_gate(api, "reject", project, notes)


def restore_doc(api: KeelApi, path: str, version: int | None = None, project: str | None = None) -> str:
    p = _project(api, project)
    vs = [v for v in api.get(f"/projects/{p['id']}/docs/history/versions", path=path) or [] if not v.get("deleted")]
    pick = next((v for v in vs if version is None or int(v.get("version")) == int(version)), None)
    if not pick:
        raise ApiError(f"no stored version {version or ''} of {path}. keel_specs with history=true lists them.")
    r = api.post(f"/projects/{p['id']}/docs/history/restore", {"version_id": pick["id"]}) or {}
    return f"{path} is back as it was in version {pick['version']} (now version {r.get('version')})."


# ---------------------------------------------------------------- admin

def stop_flow(api: KeelApi, project: str | None = None, thread_id: str | None = None) -> str:
    if not thread_id:
        thread, _ = _flow(api, _project(api, project)["id"])
        if not thread or thread.get("status") not in ("running", "waiting"):
            return "no running or waiting flow on this project."
        thread_id = thread["thread_id"]
    st = api.post(f"/threads/{_q(thread_id)}/stop", {}) or {}
    return f"flow {thread_id} stopped ({st.get('status')})."


def delete_run(api: KeelApi, thread_id: str, project: str | None = None) -> str:
    p = _project(api, project)
    api.delete(f"/projects/{p['id']}/flows/{_q(thread_id)}")
    return f"flow {thread_id} is gone from {p['id']}'s run history (its records stay for budgets)."


def seen_as(path: str) -> str:
    """0.18.64 a folder on the person's computer as keel's container sees it: the main project is mounted at /workspace
    (KEEL_HOST_WORKSPACE → KEEL_WORKSPACE), so a repo inside it was "not visible" by its own path although keel has it.
    Added folders are mounted at their own path."""
    p = os.path.normpath(str(path).strip())
    host = os.environ.get("KEEL_HOST_WORKSPACE", "").rstrip("/")
    ws = os.environ.get("KEEL_WORKSPACE", "").rstrip("/")
    if host and ws and host != ws and (p == host or p.startswith(host + "/")):
        return ws + p[len(host):]
    return p


def add_project(api: KeelApi, path: str, name: str | None = None) -> str:
    """A git repo keel can see becomes a project, with no terminal and no restart: one in the main project folder or in
    a folder `keel2 add` mounted (0.18.64 also by its path on the person's computer). Only a folder keel cannot see
    needs `keel2 add`, which restarts keel once."""
    want = seen_as(path)
    seen = api.get("/folders") or {}
    roots = [r.get("path") for r in seen.get("roots") or [] if r.get("exists", True)]
    if not any(want == r or want.startswith(str(r).rstrip("/") + "/") for r in roots):
        raise ApiError(f"keel can't see {path}: it is in none of keel's folders ({', '.join(roots) or 'none'}). Run "
                       f"`keel2 add {path}` in a terminal: keel restarts once (about half a minute) to mount it, and "
                       "keel2 mcp reconnects by itself; then call keel_add_project again.")
    p = api.post("/projects", {"root": want, **({"name": name} if name else {})}) or {}
    return f"project {p.get('id')} added ({p.get('root')})."


def connect_github(api: KeelApi, host: str | None = None) -> str:
    """Start "Sign in with GitHub" (the device flow): the code and the link are for the person to open. The token is
    saved by keel when they finish, and is never in the answer."""
    v = api.post("/github/login", {"host": (host or "github.com").strip()}) or {}
    if not v.get("code") or not v.get("url"):
        return f"GitHub sign-in started on {v.get('host', host or 'github.com')}: {v.get('message') or 'no code yet'}. Look in Connections › GitHub."
    return (f"Open {v['url']} and type the code {v['code']} (host {v.get('host', host or 'github.com')}). keel notices by itself when "
            "you allow access, saves the token encrypted and shows who is connected in Connections › GitHub. The token is never shown here.")


def remove_project(api: KeelApi, project: str) -> str:
    p = _project(api, project)
    api.delete(f"/projects/{p['id']}")
    return f"project {p['id']} is off the list; keel keeps its history (add the folder again to see it)."


def workflows(api: KeelApi, project: str | None = None, workflow: str | None = None) -> str:
    p = _project(api, project)
    if workflow:
        y = api.text(f"/workflows/{_q(workflow)}/export")
        return y
    rows = api.get(f"/projects/{p['id']}/workflows") or []
    return "\n".join(f"{w.get('id')}  {w.get('name')} · {len(w.get('steps') or [])} steps · {w.get('source') or ''}" for w in rows)


def edit_workflow(api: KeelApi, workflow: str, yaml_text: str, project: str | None = None) -> str:
    p = _project(api, project)
    check = api.post(f"/projects/{p['id']}/workflows/check", {"yaml": yaml_text}) or {}
    if check.get("errors"):
        return "keel did not save it:\n" + "\n".join(f"  {e}" for e in check["errors"])
    api.put(f"/workflows/{_q(workflow)}", {"yaml": yaml_text})
    return f"workflow {workflow} saved."



# ---------------------------------------------------------------- v0.17.2 rules

WHEN = {"before": "before", "wrapped": "before (some commands)", "after": "after the step", "none": "not checked"}


def _engines(e: dict | None) -> str:
    return ", ".join(f"{k} {WHEN.get(v, v)}" for k, v in (e or {}).items())


def rules(api: KeelApi, project: str | None = None, thread: str | None = None) -> str:
    """keel's rules for the project, in words; with a running flow, what agents may change right now."""
    p = _project(api, project)
    v = api.get(f"/projects/{_q(p['id'])}/rules") or {}
    out = [f"keel's rules for {p.get('name') or p['id']} (saved in {v.get('config_file')} and {v.get('rules_folder')}/):", "",
           "Engines:"]
    out += [f"  {e['engine']}: {e['words']}" for e in v.get("engines") or []]
    out += ["", "Always on:"]
    out += [f"  {b['id']}: {b['words']} ({b['phases']})" for b in v.get("builtins") or [] if not b.get("can_off")]
    out += ["", "keel's rules you can switch off (rules.off):"]
    out += [f"  {b['id']}{' [OFF]' if b.get('off') else ''}: {b['words']} ({b['phases']})" for b in v.get("builtins") or [] if b.get("can_off")]
    out += ["", "The project's lists:"]
    out += [f"  {x['key']} = {x['value']}  ({x['saved_in']})" for x in v.get("lists") or []]
    packs = v.get("packs") or []
    out += ["", "Rule packs:" if packs else "Rule packs: none (keel_rule_install adds one from the library)."]
    for pk in packs:
        out.append(f"  {pk['id']} {pk.get('version')}{' [OFF]' if pk.get('off') else ''} — {pk['title']}: {pk.get('summary') or ''} "
                   f"({pk.get('source')}, {pk.get('file')})")
        out += [f"    {r['kind']} {r['match']} ({r['phases']}): {r['reason']}  [{_engines(r.get('enforced'))}]" for r in pk.get("rules") or []]
    for err in v.get("pack_errors") or []:
        out.append(f"  NOT USED {err['file']}: {'; '.join(err['errors'])}")
    tid = thread
    if tid is None:
        try:
            tid = (_flow(api, p["id"])[0] or {}).get("thread_id")
        except ApiError:
            tid = None
    if tid:
        try:
            n = api.get(f"/threads/{_q(tid)}/rules-now") or {}
            out += ["", f"Now (phase {n.get('phase')}): {n.get('meaning')}",
                    "  may change: " + (", ".join(b["what"] for b in n.get("may") or []) or "nothing"),
                    "  may not change: " + (", ".join(b["what"] for b in n.get("may_not") or []) or "—")]
            out += [f"  stopped {f['count']}x: {f['reason']}" for f in n.get("fired") or []]
        except ApiError:
            pass
    return "\n".join(out)


def rule_library(api: KeelApi) -> list[dict]:
    return api.get("/rules/library") or []


def rule_install(api: KeelApi, pack: str, project: str | None = None) -> str:
    p = _project(api, project)
    if not any(x.get("id") == pack for x in rule_library(api)):
        return "keel: no rule pack " + pack + " in the library. There are: " + ", ".join(x["id"] for x in rule_library(api))
    r = api.post(f"/projects/{_q(p['id'])}/rule-packs/install", {"source": "library", "name": pack}) or {}
    got = r.get("pack") or {}
    return (f"Installed {got.get('title', pack)} {got.get('version', '')} in .keel/rules/{pack}.yaml. Agents meet it at their "
            "next tool call; commit the file to share it.")


def rule_edit(api: KeelApi, op: str, pack: str | None = None, yaml_text: str | None = None, rule: str | None = None,
              project: str | None = None) -> str:
    """add | change (a pack's whole YAML) | remove (a pack) | off | on (a built-in rule id or pack/<id>)."""
    p = _project(api, project)
    pid = _q(p["id"])
    if op in ("add", "change"):
        if not yaml_text:
            return "keel: give the pack's YAML (id, name, rules)."
        check = api.post(f"/projects/{pid}/rules/check", {"yaml": yaml_text}) or {}
        if not check.get("ok"):
            return "keel did not save it:\n" + "\n".join(f"  {e}" for e in check.get("errors") or [])
        if op == "add":
            r = api.post(f"/projects/{pid}/rules/packs", {"yaml": yaml_text}) or {}
        else:
            if not pack:
                return "keel: name the pack to change (pack)."
            r = api.put(f"/projects/{pid}/rules/packs/{_q(pack)}", {"yaml": yaml_text}) or {}
        blocked = len(((check.get("dry_run") or {}).get("blocked")) or [])
        return f"Saved {r.get('pack', {}).get('id')}. On the recent tool calls it would have refused {blocked}."
    if op == "remove":
        if not pack:
            return "keel: name the pack to remove (pack)."
        api.delete(f"/projects/{pid}/rules/packs/{_q(pack)}")
        return f"Removed the rule pack {pack}."
    if op in ("off", "on"):
        if not rule:
            return "keel: name the rule (a built-in id like read-twice, or pack/<id>)."
        v = api.get(f"/projects/{pid}/rules") or {}
        builtins = v.get("builtins") or []
        # 0.18.64 an opt-in rule (other-projects) is off until rules.on names it: it never goes into rules.off
        opt_in = {b["id"] for b in builtins if b.get("opt_in")}
        off = {b["id"] for b in builtins if b.get("off") and b["id"] not in opt_in} | \
            {f"pack/{x['id']}" for x in v.get("packs") or [] if x.get("off")}
        if rule in opt_in:
            on = {b["id"] for b in builtins if b["id"] in opt_in and not b.get("off")}
            on = on | {rule} if op == "on" else on - {rule}
            api.put(f"/projects/{pid}/rules/config", {"rules": {"off": sorted(off), "on": sorted(on)}})
            return f"{rule} is {op}."
        off = off | {rule} if op == "off" else off - {rule}
        api.put(f"/projects/{pid}/rules/config", {"rules": {"off": sorted(off)}})
        return f"{rule} is {op}."
    return "keel: op is add, change, remove, off or on."
