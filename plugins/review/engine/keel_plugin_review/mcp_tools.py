"""The Code Review plugin's tools in keel's own MCP server (keel2 mcp, keel_engine/mcp_server.py): what keel TUI's review
mode and the dashboard's Code › Review do, through keel's api (the plugin's routes, /api/projects/<id>/review/...).

    read   keel_review_prs, keel_review_files, keel_review_diff, keel_review_notes, keel_review_threads,
           keel_review_thread (every comment and what keel added on it), keel_review_conversation, keel_review_file,
           keel_review_symbol, keel_review_diagrams
    write  keel_review_comment (a draft, kept in keel), keel_review_draft, keel_review_move_drafts, keel_review_viewed,
           keel_review_base, keel_review_explain, keel_review_findings, keel_review_check, keel_review_button (keel's
           AI, it edits nothing), keel_review_finding (your decision on a finding); asking the person first:
           keel_review_reply, keel_review_resolve, keel_review_submit, keel_review_merge (they post to GitHub or GitLab),
           keel_review_address, keel_review_fix_finding (a flow changes the branch, never pushes), keel_review_checkout
           (the project folder moves to the review's branch)

A review is a pull request (`pr`), a branch (`branch`), or without either the branch the project folder is on."""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import Context
from keel_engine.mcp_server import ApiError, KeelApi, _plugin_on, _project

MAX_DIFF_LINES = 600
MAX_FILE_LINES = 400
VERDICT = {"reviewer_right": "the reviewer is right", "author_right": "the author is right", "partly": "partly right",
           "unclear": "unclear"}


def _open(api: KeelApi, project: str | None, pr: int | None, branch: str | None) -> tuple[str, dict]:
    """The project and the review (GET review/open: it fetches a pull request first)."""
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "review")
    ref = str(int(pr)) if pr else (branch or "")
    return pid, api.get(f"/projects/{pid}/review/open", ref=ref)


def _name(v: dict) -> str:
    return f"pull request #{v['number']}" if v.get("kind") == "pr" else f"branch {v.get('branch')}"


def _lines(c: dict) -> str:
    """12, or 10-12 for a comment on several lines."""
    if not c.get("line"):
        return "-"
    return f"{c['start_line']}-{c['line']}" if c.get("start_line") and c["start_line"] != c["line"] else str(c["line"])


def _who(t: dict) -> str:
    c = (t.get("comments") or [{}])[0]
    return f"{c.get('author', '?')}: {' '.join(str(c.get('body', '')).split())[:200]}"


def review_prs(api: KeelApi, project: str | None = None, filter: str = "all") -> str:
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "review")
    r = api.get(f"/projects/{pid}/review/prs", filter=filter)
    prs = r.get("prs") or []
    if not prs:
        return r.get("note") or f"No open pull request ({filter})."
    head = f"{(r.get('host') or {}).get('path', '')} · {len(prs)} pull request(s) ({filter})".lstrip(" ·")
    lines = [f"#{p['number']}  {p['title']} · {p['author']} · {p['branch']} → {p['base']}"
             + (" · asks you" if p.get("review_requested") else "") + (" · yours" if p.get("mine") else "") + (" · draft" if p.get("draft") else "")
             for p in prs]
    return "\n".join([head, *lines])


def review_files(api: KeelApi, project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
    _, v = _open(api, project, pr, branch)
    viewed = set(v.get("viewed") or [])
    out = [f"{_name(v).capitalize()}: {v.get('title')} · {v.get('branch')} → {v.get('base')} · +{v.get('added', 0)} −{v.get('removed', 0)}"
           f" · review key {v.get('key')}"]
    if v.get("approved") or v.get("changes_requested"):
        out.append(f"approved by {', '.join(v.get('approved') or []) or 'nobody'} · changes asked by {', '.join(v.get('changes_requested') or []) or 'nobody'}")
    out.append(f"{len(v.get('files') or [])} file(s):")
    out += [f"  {f['status']}  {f['path']}  +{f['added']} −{f['removed']}" + ("  (viewed)" if f["path"] in viewed else "")
            for f in v.get("files") or []]
    threads = v.get("threads") or []
    if threads:
        out.append(f"{len(threads)} thread(s) (thread_id for keel_review_reply / keel_review_resolve):")
        out += [f"  {t['id']}  {t.get('path') or '(the pull request)'}:{t.get('line') or '-'}  {'resolved' if t.get('resolved') else 'open'}"
                f"  {_who(t)} ({len(t.get('comments') or [])} comment(s))" for t in threads]
    drafts = v.get("drafts") or []
    if drafts:
        out.append(f"{len(drafts)} draft comment(s), sent with your review (keel_review_submit):")
        out += [f"  {d.get('id')}  {d.get('path') or '(the review)'}:{_lines(d)} {d.get('side', 'RIGHT')}  {' '.join(str(d.get('body', '')).split())[:200]}"
                for d in drafts]
    out += [f"note: {n}" for n in v.get("notes") or []]
    return "\n".join(out)


def review_diff(api: KeelApi, path: str | None = None, project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    paths = [path] if path else [f["path"] for f in v.get("files") or [] if not f.get("binary")]
    out: list[str] = []
    for p in paths:
        d = api.get(f"/projects/{pid}/review/diff", key=v["key"], path=p)
        out += (d.get("diff") or f"(no text diff for {p})").rstrip("\n").split("\n")
        if len(out) > MAX_DIFF_LINES:
            more = len(paths) - paths.index(p) - 1
            return "\n".join(out[:MAX_DIFF_LINES] + [f"… cut at {MAX_DIFF_LINES} lines" + (f"; {more} more file(s): ask for one with path" if more else "")])
    return "\n".join(out) or "No change."


def _coverage(r: dict) -> list[str]:
    """keel 0.18.57: a run that did not read every changed file says so, with the files (the first 20)."""
    c = r.get("coverage") or {}
    if not c.get("known"):
        return []
    if not r.get("partial"):
        return [f"  read all {c.get('files')} changed file(s)"]
    unread = c.get("unread") or []
    return [f"  PARTIAL: read {c.get('read')} of {c.get('files')} changed file(s); not read: " + ", ".join(unread[:20])
            + (f" … {len(unread) - 20} more" if len(unread) > 20 else "")]


def review_notes(api: KeelApi, project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    ai = api.get(f"/projects/{pid}/review/ai", key=v["key"]) or {}
    out = [f"keel on {_name(v)}:"]
    o = ai.get("overview")
    if not o:
        out.append("overview: none yet (keel_review_explain asks for one)")
    else:
        r = o.get("result") or {}
        out.append(f"overview: {o.get('status')}" + (" (the code moved on since)" if o.get("stale") else "") + (f" · {o['error']}" if o.get("error") else ""))
        if r.get("summary"):
            out += [f"  {r['summary']}", f"  risk {r.get('risk') or '-'}: {r.get('risk_why') or ''} · effort {r.get('effort', '-')}/5"]
            out += _coverage(r)
            out += [f"  {f['path']}: {f['what']}" for f in r.get("files") or []]
    f = ai.get("findings")
    if not f:
        out.append("findings: none yet (keel_review_findings asks for them)")
    else:
        r = f.get("result") or {}
        found = r.get("findings") or []
        out.append(f"findings: {f.get('status')} · {len(found)} found" + (f" · {f['error']}" if f.get("error") else ""))
        out += _coverage(r)
        decided = ai.get("decisions") or {}
        fixes = ai.get("fixes") or {}

        def mark(x: dict) -> str:
            d = decided.get(x.get("id")) or {}
            fx = fixes.get(x.get("id")) or {}
            return ((f" · you {d['decision']}" + (f": {d['why']}" if d.get("why") else "") if d.get("decision") not in (None, "open") else "")
                    + (f" · Fix it flow {fx['flow_id']}" if fx.get("flow_id") else ""))

        out += [f"  {x.get('id') or '-'}  {x.get('severity')}  {x.get('path') or '-'}:{x.get('line') or '-'}  {x.get('title')} ({x.get('check', '')})"
                f"{mark(x)}\n    {x.get('why', '')}" for x in found]
        unproven = r.get("not_checked") or []
        if unproven:   # keel 0.18.57: nobody could prove or disprove these; they are not findings
            out.append(f"not checked ({len(unproven)}): no proof either way, not counted as findings")
            out += [f"  {x.get('path') or '-'}:{x.get('line') or '-'}  {x.get('title')} - {x.get('check_why') or ''}" for x in unproven[:20]]
    return "\n".join(out)


def review_comment(api: KeelApi, path: str, line: int, body: str, side: str = "RIGHT", project: str | None = None,
                   pr: int | None = None, branch: str | None = None, start_line: int | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    draft = {"key": v["key"], "path": path, "line": int(line), "side": side, "body": body}
    if start_line is not None and int(start_line) != int(line):
        draft |= {"start_line": int(start_line), "start_side": side}
    d = api.post(f"/projects/{pid}/review/drafts", draft)
    at = f"{d['start_line']}-{line}" if d.get("start_line") else f"{line}"
    return f"Draft {d['id']} on {path}:{at} saved in keel; it is sent with your review (keel_review_submit)."


def review_reply(api: KeelApi, thread_id: str, body: str, project: str | None = None, pr: int | None = None) -> str:
    pid, v = _open(api, project, pr, None)
    api.post(f"/projects/{pid}/review/threads/{thread_id}/reply", {"key": v["key"], "body": body})
    return f"Replied on thread {thread_id} of {_name(v)}."


def review_resolve(api: KeelApi, thread_id: str, resolved: bool = True, project: str | None = None, pr: int | None = None) -> str:
    pid, v = _open(api, project, pr, None)
    api.post(f"/projects/{pid}/review/threads/{thread_id}/resolve", {"key": v["key"], "resolved": bool(resolved)})
    return f"Thread {thread_id} of {_name(v)} is {'resolved' if resolved else 'open again'}."


def review_ai(api: KeelApi, kind: str, project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    api.post(f"/projects/{pid}/review/ai/{kind}", {"key": v["key"]})
    what = "reads the change for an overview" if kind == "overview" else "looks for problems (two reviewers, then each claim is checked)"
    return f"keel {what} on {_name(v)} in KeelBot's sessions (it edits nothing). keel_review_notes shows it when it is done."


def review_submit(api: KeelApi, event: str, body: str = "", project: str | None = None, pr: int | None = None) -> str:
    pid, v = _open(api, project, pr, None)
    r = api.post(f"/projects/{pid}/review/submit", {"key": v["key"], "event": event, "body": body})
    return f"Sent your review ({event}) on {_name(v)} with {r.get('posted', 0)} line comment(s)" + (f": {r['url']}" if r.get("url") else ".")


# ---------------------------------------------------------------- threads, and what keel added on them

def _pr(api: KeelApi, project: str | None, pr: int | None) -> tuple[str, dict]:
    pid, v = _open(api, project, pr, None)
    if v.get("kind") != "pr":
        raise ApiError("A branch has no threads: name its pull request (pr).", 400)
    return pid, v


def _find(v: dict, thread_id: str) -> dict:
    t = next((t for t in v.get("threads") or [] if str(t.get("id")) == str(thread_id)), None)
    if t is None:
        raise ApiError(f"No thread {thread_id} on {_name(v)} (keel_review_threads lists them).", 400)
    return t


def _state(t: dict) -> str:
    return " ".join(x for x in ("resolved" if t.get("resolved") else "open", "outdated" if t.get("outdated") else "",
                                "pending (your own, not sent)" if t.get("pending") else "") if x)


def _check_line(run: dict | None) -> str:
    """keel's "Is the reviewer right?" on a thread, in one line."""
    if not run:
        return "not checked (keel_review_check)"
    r = run.get("result") or {}
    if run.get("status") == "running":
        return "keel is checking it"
    if r.get("stage") == "flow":
        return f"checked in flow {r.get('flow_id')} ({r.get('workflow') or 'a flow'})"
    if run.get("status") != "done":
        return f"the check failed: {run.get('error') or run.get('status')}"
    items = r.get("items") or []
    tally: dict[str, int] = {}
    for i in items:
        tally[i.get("verdict", "unclear")] = tally.get(i.get("verdict", "unclear"), 0) + 1
    parts = [f"{n}× {VERDICT.get(k, k)}" for k, n in tally.items()]
    return ", ".join(parts or ["no point found"]) + (" · a reply suggested" if r.get("reply") else "") + (" (the code moved on since)" if run.get("stale") else "")


def _fixed_line(run: dict | None) -> str | None:
    if not run:
        return None
    r = run.get("result") or {}
    if run.get("status") != "done":
        return f"keel checks the author's \"fixed\": {run.get('status')}" + (f" ({run['error']})" if run.get("error") else "")
    return (f"keel checked the author's \"fixed\": {'fixed' if r.get('fixed') else 'NOT fixed'}"
            + (f" · {r['why']}" if r.get("why") else "") + (f" · still open: {r['still_open']}" if r.get("still_open") else ""))


def review_threads(api: KeelApi, project: str | None = None, pr: int | None = None, open_only: bool = False) -> str:
    pid, v = _pr(api, project, pr)
    ai = api.get(f"/projects/{pid}/review/ai", key=v["key"]) or {}
    judges, fixed, addressed = ai.get("judges") or {}, ai.get("fixed") or {}, ai.get("addressed") or {}
    threads = [t for t in v.get("threads") or [] if not (open_only and t.get("resolved"))]
    out = [f"{_name(v).capitalize()}: {len(threads)} thread(s)" + (" open" if open_only else "") + " (keel_review_thread reads one in full)"]
    for t in threads:
        cs = t.get("comments") or []
        last = cs[-1] if len(cs) > 1 else None
        out.append(f"{t['id']}  {t.get('path') or '(the pull request)'}:{_lines(t)}  {_state(t)}  {_who(t)} ({len(cs)} comment(s))")
        if last:
            out.append(f"    last: {last.get('author', '?')}: {' '.join(str(last.get('body', '')).split())[:200]}")
        out.append(f"    keel: {_check_line(judges.get(t['id']))}")
        if (f := _fixed_line(fixed.get(t["id"]))):
            out.append(f"    {f}")
        if (a := addressed.get(t["id"])) and a.get("flow_id"):
            out.append(f"    Address it: flow {a['flow_id']} ({a.get('workflow') or 'a flow'}" + (f" on {a['branch']}" if a.get("branch") else "") + ")")
    groups = [g for g in v.get("thread_groups") or [] if len(g) > 1]
    if groups:
        out.append("threads that make the same point (keel checks each group once): " + "; ".join(", ".join(g) for g in groups))
    return "\n".join(out)


def review_thread(api: KeelApi, thread_id: str, project: str | None = None, pr: int | None = None) -> str:
    pid, v = _pr(api, project, pr)
    t = _find(v, thread_id)
    ai = api.get(f"/projects/{pid}/review/ai", key=v["key"]) or {}
    out = [f"Thread {t['id']} on {_name(v)} · {t.get('path') or '(the pull request)'}:{_lines(t)} {t.get('side') or ''} · {_state(t)}".rstrip()]
    for c in t.get("comments") or []:
        out += [f"--- {c.get('author', '?')} · {c.get('at', '')}" + (f" · {c['url']}" if c.get("url") else ""), str(c.get("body", ""))[:6000]]
    out.append("--- keel on this thread")
    run = (ai.get("judges") or {}).get(t["id"])
    out.append(f"Is the reviewer right? {_check_line(run)}")
    r = (run or {}).get("result") or {}
    for i in r.get("items") or []:
        out.append(f"  - {VERDICT.get(i.get('verdict'), i.get('verdict'))}: {i.get('claim', '')}\n    why: {i.get('why', '')}"
                   + (f"\n    fix: {i['fix']}" if i.get("fix") else ""))
    if r.get("reply"):
        out += ["  suggested reply (keel_review_reply posts it):", "    " + str(r["reply"]).replace("\n", "\n    ")]
    if r.get("model"):
        out.append(f"  checked with {r['model']}" + (f" · {r.get('tokens')} tokens" if r.get("tokens") else ""))
    if r.get("same_as"):
        out.append(f"  the same point as: {', '.join(map(str, r['same_as']))}")
    if (f := _fixed_line((ai.get("fixed") or {}).get(t["id"]))):
        out.append(f)
    if (a := (ai.get("addressed") or {}).get(t["id"])) and a.get("flow_id"):
        out.append(f"Address it: flow {a['flow_id']} ({a.get('workflow') or 'a flow'}" + (f" on {a['branch']}" if a.get("branch") else "") + ")")
    cfg = ai.get("config") or {}
    for b in cfg.get("buttons") or []:
        br = (ai.get("buttons") or {}).get(f"{b['id']}|{t['id']}")
        if not br:
            out.append(f"button {b['id']} ({b.get('label')}, {b.get('mode', 'ask')}): not used (keel_review_button)")
            continue
        res = br.get("result") or {}
        text = res.get("text") or (f"flow {res['flow_id']}" if res.get("flow_id") else (br.get("error") or br.get("status")))
        out.append(f"button {b['id']} ({b.get('label')}): {br.get('status')}\n    " + str(text).replace("\n", "\n    "))
    return "\n".join(out)


def review_conversation(api: KeelApi, project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
    _, v = _open(api, project, pr, branch)
    out = [f"{_name(v).capitalize()}: {v.get('title')} · {v.get('author') or '?'} · {v.get('branch')} → {v.get('base')}"
           + (f" · {v['state']}" if v.get("state") else "") + (" · draft" if v.get("draft") else "") + (f" · {v['url']}" if v.get("url") else "")]
    if v.get("kind") == "pr":
        out.append(f"mergeable: {v.get('mergeable')}" + (f" ({v['merge_state']})" if v.get("merge_state") else "")
                   + (" · yours: keel_review_merge can merge it" if v.get("mine") else "") + ("" if v.get("can_post") else " · keel has no token to post here"))
    if v.get("base_warning"):
        out.append(f"base: {v['base_warning']}")
    if v.get("base_options"):
        out.append("bases (keel_review_base): " + ", ".join(f"{b['id']} = {b.get('label') or b.get('ref')}" for b in v["base_options"])
                   + (f" · now {v['base_choice']}" if v.get("base_choice") else ""))
    if v.get("body"):
        out += ["--- description", str(v["body"])[:8000]]
    checks = v.get("checks") or []
    if checks:
        out.append(f"--- checks ({len(checks)})")
        out += [f"  {c.get('state')}  {c.get('name')}" + (f"  {c['url']}" if c.get("url") else "") for c in checks]
    commits = v.get("commits") or []
    if commits:
        out.append(f"--- commits ({len(commits)})")
        out += [f"  {str(c.get('sha', ''))[:8]}  {str(c.get('message', '')).splitlines()[0] if c.get('message') else ''} · {c.get('author', '')}"
                + (" · keel" if c.get("keel") else "") for c in commits]
    conv = v.get("conversation") or []
    if conv:
        out.append(f"--- conversation ({len(conv)})")
        for c in conv:
            out += [f"  {c.get('author', '?')} · {c.get('at', '')}", "    " + str(c.get("body", ""))[:3000].replace("\n", "\n    ")]
    if v.get("setup_left_out"):
        out.append("left out of the review (keel init's setup commits): " + ", ".join(v["setup_left_out"]))
    return "\n".join(out)


def review_file(api: KeelApi, path: str, side: str = "head", start_line: int = 1, end_line: int | None = None, project: str | None = None,
                pr: int | None = None, branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    f = api.get(f"/projects/{pid}/review/file", key=v["key"], path=path, side=side)
    lines = str(f.get("text") or "").split("\n")
    start = max(1, int(start_line or 1))
    end = min(len(lines), int(end_line) if end_line else start + MAX_FILE_LINES - 1, start + MAX_FILE_LINES - 1)
    width = len(str(end))
    out = [f"{f.get('path', path)} at {side} ({f.get('ref', '')} {str(f.get('sha', ''))[:8]}) · lines {start}-{end} of {len(lines)}"
           + (" · the file is cut (too big)" if f.get("truncated") else "")]
    out += [f"{str(n).rjust(width)}  {lines[n - 1]}" for n in range(start, end + 1)]
    if end < len(lines) and (not end_line or end < int(end_line)):       # cut by the window, not by the lines asked for
        out.append(f"… {len(lines) - end} more line(s): ask with start_line={end + 1}")
    return "\n".join(out)


def review_symbol(api: KeelApi, symbol: str, kind: str = "definition", project: str | None = None, pr: int | None = None,
                  branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    r = api.get(f"/projects/{pid}/review/{'usages' if kind == 'usages' else 'definition'}", key=v["key"], symbol=symbol)
    places = r.get("places") or []
    if not places:
        return f"No {kind} of {symbol} at {r.get('ref') or 'the head'}."
    out = [f"{kind.capitalize()} of {symbol} at {r.get('ref') or 'the head'}: {len(places)}" + (" (cut)" if r.get("truncated") else "")]
    out += [f"  {p['path']}:{p['line']}  {str(p.get('text', '')).strip()[:200]}"
            + "".join(f" · {w}" for w, on in (("declaration", p.get("declaration")), ("test", p.get("test")), ("changed here", p.get("changed"))) if on)
            for p in places]
    return "\n".join(out)


def review_diagrams(api: KeelApi, which: str = "both", project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    d = api.get(f"/projects/{pid}/review/diagrams", key=v["key"]) or {}
    out = [f"Diagrams of {_name(v)}"]
    if d.get("text"):
        out.append(str(d["text"]))
    for name in ("database", "system"):
        if which not in ("both", name):
            continue
        g = d.get(name) or {}
        out.append(f"--- {name}")
        out.append(str(g.get("ascii") or g.get("mermaid") or "(none: nothing in this change)").rstrip())
    return "\n".join(out)


# ---------------------------------------------------------------- acting: keel only (nothing posted)

def review_draft(api: KeelApi, draft_id: str, body: str | None = None, delete: bool = False, project: str | None = None) -> str:
    pid = _project(api, project)["id"]
    _plugin_on(api, pid, "review")
    if delete:
        api.delete(f"/projects/{pid}/review/drafts/{draft_id}")
        return f"Draft {draft_id} deleted."
    if not (body or "").strip():
        raise ApiError("Give the new text (body), or delete=true.", 400)
    d = api.put(f"/projects/{pid}/review/drafts/{draft_id}", {"body": body})
    return f"Draft {d.get('id', draft_id)} on {d.get('path') or '(the review)'}:{_lines(d)} now reads: {' '.join(str(d.get('body', '')).split())[:200]}"


def review_move_drafts(api: KeelApi, project: str | None = None, branch: str | None = None, pr: int | None = None) -> str:
    pid, v = _open(api, project, None, branch)
    to = pr or v.get("branch_pr")
    if v.get("kind") != "branch" or not to:
        raise ApiError("Name a branch that has an open pull request (or give pr).", 400)
    r = api.post(f"/projects/{pid}/review/drafts/move", {"from": v["key"], "to": f"pr:{int(to)}"})
    return f"The drafts of {_name(v)} are now on pull request #{int(to)} ({len(r.get('drafts') or [])} draft(s) there)."


def review_viewed(api: KeelApi, path: str, viewed: bool = True, project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    r = api.post(f"/projects/{pid}/review/viewed", {"key": v["key"], "path": path, "viewed": bool(viewed)})
    return f"{path} is {'viewed' if viewed else 'not viewed'} · {len(r.get('viewed') or [])} file(s) viewed in {_name(v)}."


def review_base(api: KeelApi, base: str = "", project: str | None = None, branch: str | None = None) -> str:
    pid, v = _open(api, project, None, branch)
    if v.get("kind") != "branch":
        raise ApiError("Only a branch review picks its base (a pull request has its own).", 400)
    r = api.put(f"/projects/{pid}/review/base", {"key": v["key"], "base": base})
    return f"{_name(r).capitalize()} compares against {r.get('base')}" + (f" ({r['base_choice']})" if r.get("base_choice") else "") + \
        f" · {len(r.get('files') or [])} file(s), +{r.get('added', 0)} −{r.get('removed', 0)}."


def review_check(api: KeelApi, thread_id: str | None = None, again: bool = False, harder: bool = False, project: str | None = None,
                 pr: int | None = None) -> str:
    pid, v = _pr(api, project, pr)
    body = {"key": v["key"], "again": bool(again), "harder": bool(harder)}
    if thread_id:
        _find(v, thread_id)
        st = api.post(f"/projects/{pid}/review/threads/{thread_id}/judge", body)
        return f"Thread {thread_id}: {_check_line((st.get('judges') or {}).get(thread_id))}. keel_review_thread shows the answer when it is done."
    st = api.post(f"/projects/{pid}/review/threads/judge", body)
    judges = st.get("judges") or {}
    open_ = [t["id"] for t in v.get("threads") or [] if not t.get("resolved")]
    return f"keel checks the open threads of {_name(v)} in one call ({len(open_)} open). keel_review_threads shows the answers:\n" + \
        "\n".join(f"  {i}: {_check_line(judges.get(i))}" for i in open_)


def review_button(api: KeelApi, thread_id: str, button: str, again: bool = False, harder: bool = False, project: str | None = None,
                  pr: int | None = None) -> str:
    pid, v = _pr(api, project, pr)
    _find(v, thread_id)
    cfg = (api.get(f"/projects/{pid}/review/ai", key=v["key"]) or {}).get("config") or {}
    b = next((b for b in cfg.get("buttons") or [] if button in (b.get("id"), b.get("label"))), None)
    if b is None:
        names = ", ".join(f"{x['id']} ({x.get('label')})" for x in cfg.get("buttons") or []) or "none (Settings › Code Review threads adds them)"
        raise ApiError(f"No thread button {button}. The buttons: {names}.", 400)
    st = api.post(f"/projects/{pid}/review/threads/{thread_id}/buttons/{b['id']}", {"key": v["key"], "again": bool(again), "harder": bool(harder)})
    run = (st.get("buttons") or {}).get(f"{b['id']}|{thread_id}") or {}
    res = run.get("result") or {}
    if res.get("flow_id"):
        return f"{b.get('label')} on thread {thread_id}: flow {res['flow_id']} started."
    return f"{b.get('label')} on thread {thread_id}: {run.get('status', 'started')}. keel_review_thread shows its answer."


def review_finding(api: KeelApi, finding_id: str, decision: str, why: str | None = None, project: str | None = None, pr: int | None = None,
                   branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    api.post(f"/projects/{pid}/review/ai/findings/{finding_id}", {"key": v["key"], "decision": decision, "why": why})
    return f"Finding {finding_id} of {_name(v)}: " + {"dismissed": f"dismissed ({why})", "commented": "marked as commented",
                                                       "open": "open again"}.get(decision, decision) + "."


# ---------------------------------------------------------------- acting: a flow, the folder or the host (the person says yes)

def _flow(r: dict, what: str) -> str:
    return (f"{what}: flow {r.get('flow_id')} ({r.get('workflow')})" + (f" on {r['branch']}" if r.get("branch") else "")
            + f" · {str(r.get('note') or 'it never pushes').rstrip('.')}. keel_flow reads it.")


def review_address(api: KeelApi, thread_id: str, note: str | None = None, workflow: str | None = None, fix: str | None = None,
                   project: str | None = None, pr: int | None = None) -> str:
    pid, v = _pr(api, project, pr)
    _find(v, thread_id)
    r = api.post(f"/projects/{pid}/review/threads/{thread_id}/address", {"key": v["key"], "note": note, "workflow": workflow, "fix": fix})
    return _flow(r, f"Address it on thread {thread_id}")


def review_fix_finding(api: KeelApi, finding_id: str, workflow: str | None = None, project: str | None = None, pr: int | None = None,
                       branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    r = api.post(f"/projects/{pid}/review/ai/findings/{finding_id}/fix", {"key": v["key"], "workflow": workflow})
    return _flow(r, f"Fix it on finding {finding_id}")


def review_checkout(api: KeelApi, project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
    pid, v = _open(api, project, pr, branch)
    r = api.post(f"/projects/{pid}/review/checkout", {"key": v["key"]})
    return f"The project folder is on {r.get('branch')}. {r.get('note') or ''}".rstrip()


def review_merge(api: KeelApi, method: str = "merge", delete_branch: bool = False, project: str | None = None, pr: int | None = None) -> str:
    pid, v = _pr(api, project, pr)
    r = api.post(f"/projects/{pid}/review/merge", {"key": v["key"], "method": method, "delete_branch": bool(delete_branch)})
    return (f"Merged {_name(v)} ({method})" if r.get("merged") else f"Not merged {_name(v)}") + (f": {r['message']}" if r.get("message") else ".")


def _acting(srv, guard):
    """keel's way to run an acting tool (keel 0.16.1: who acts, and with `confirm` the person's OK through the client
    first; PartServer.act in keel_engine/mcp_server.py). A keel before 0.16.1 has none: then the tool runs as it did."""
    act = getattr(srv, "act", None)

    async def run(ctx, name: str, what: str, fn, *a, confirm: bool = False, **kw) -> str:
        if act is None:
            return guard(fn, *a, **kw)
        return await act(ctx, name, what, fn, *a, confirm=confirm, **kw)

    return run


def register(srv, api: KeelApi, guard, write: bool) -> None:
    """The read tools, or (write) the acting ones."""
    from mcp.types import ToolAnnotations

    if not write:
        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_prs(project: str | None = None, filter: Literal["all", "review", "assigned", "mine"] = "all") -> str:
            """Code Review plugin: the open pull requests (GitHub) or merge requests (GitLab): number, title, author, branch.
            filter: all, review (you are asked), assigned or mine."""
            return guard(review_prs, api, project, filter)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_files(project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
            """Code Review plugin: a review's changed files (status, +/-, viewed), its threads (with their ids), your draft
            comments and who approved. pr: a pull request's number; branch: a branch; neither: the branch you are on."""
            return guard(review_files, api, project, pr, branch)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_diff(path: str | None = None, project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
            """Code Review plugin: the diff of one changed file (path), or of every file (cut at 600 lines), against the
            review's base."""
            return guard(review_diff, api, path, project, pr, branch)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_notes(project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
            """Code Review plugin: keel's overview of a review (what changes, file by file, risk) and its checked findings."""
            return guard(review_notes, api, project, pr, branch)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_threads(project: str | None = None, pr: int | None = None, open_only: bool = False) -> str:
            """Code Review plugin: a pull request's threads (ids), each with its first and last comment and what keel added
            on it: "Is the reviewer right?" (its verdicts), the check of the author's "fixed", the Address it flow; and the
            threads that make the same point."""
            return guard(review_threads, api, project, pr, open_only)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_thread(thread_id: str, project: str | None = None, pr: int | None = None) -> str:
            """Code Review plugin: one thread in full: every comment (author, time, text), keel's check point by point
            (verdict, why, fix) with its suggested reply, the "fixed?" check, the extra buttons and their answers, and the
            Address it flow."""
            return guard(review_thread, api, thread_id, project, pr)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_conversation(project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
            """Code Review plugin: the pull request's description, state, mergeable, checks (CI), commits and its
            conversation (the comments not on a line); for a branch, its bases."""
            return guard(review_conversation, api, project, pr, branch)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_file(path: str, side: Literal["head", "base"] = "head", start_line: int = 1, end_line: int | None = None,
                             project: str | None = None, pr: int | None = None, branch: str | None = None) -> str:
            """Code Review plugin: a file of the review as it is at head (the change) or base, with line numbers, up to 400
            lines from start_line."""
            return guard(review_file, api, path, side, start_line, end_line, project, pr, branch)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_symbol(symbol: str, kind: Literal["definition", "usages"] = "definition", project: str | None = None,
                               pr: int | None = None, branch: str | None = None) -> str:
            """Code Review plugin: where a symbol (a function, a class, a field) is defined, or used, at the review's head;
            each place says if it is a test or changed in this review."""
            return guard(review_symbol, api, symbol, kind, project, pr, branch)

        @srv.tool(annotations=ro, structured_output=False)
        def keel_review_diagrams(which: Literal["both", "database", "system"] = "both", project: str | None = None,
                                 pr: int | None = None, branch: str | None = None) -> str:
            """Code Review plugin: the change's Database and System diagrams (text), read from git."""
            return guard(review_diagrams, api, which, project, pr, branch)

        return
    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    out = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
    act = _acting(srv, guard)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_comment(path: str, line: int, body: str, side: Literal["RIGHT", "LEFT"] = "RIGHT", project: str | None = None,
                                  pr: int | None = None, branch: str | None = None, start_line: int | None = None,
                                  ctx: Context | None = None) -> str:
        """Code Review plugin: a line comment as a draft, kept in keel until you submit the review (keel_review_submit).
        line: the new file's line (side RIGHT), or the old file's for a removed line (side LEFT). start_line: the first
        line of a comment on several lines (line is the last)."""
        return await act(ctx, "keel_review_comment", "add a draft comment", review_comment, api, path, line, body, side, project, pr, branch,
                         start_line)

    @srv.tool(annotations=out, structured_output=False)
    async def keel_review_reply(thread_id: str, body: str, project: str | None = None, pr: int | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: reply on a pull request's thread (ids from keel_review_files). It is posted on GitHub or
        GitLab at once, so the person says yes first."""
        return await act(ctx, "keel_review_reply", f"post a reply on thread {thread_id}", review_reply, api, thread_id, body, project, pr, confirm=True)

    @srv.tool(annotations=out, structured_output=False)
    async def keel_review_resolve(thread_id: str, resolved: bool = True, project: str | None = None, pr: int | None = None,
                                  ctx: Context | None = None) -> str:
        """Code Review plugin: resolve a thread (or open it again with resolved=false), on GitHub or GitLab, after the
        person's yes."""
        return await act(ctx, "keel_review_resolve", f"{'resolve' if resolved else 'reopen'} thread {thread_id}", review_resolve, api, thread_id,
                         resolved, project, pr, confirm=True)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_explain(project: str | None = None, pr: int | None = None, branch: str | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: ask keel for an overview of the change (KeelBot reads the code and edits nothing; it uses
        tokens). keel_review_notes reads it when it is done."""
        return await act(ctx, "keel_review_explain", "ask keel to explain the change", review_ai, api, "overview", project, pr, branch)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_findings(project: str | None = None, pr: int | None = None, branch: str | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: ask keel for findings (two reviewers, then each claim is checked against the code; it
        edits nothing and uses tokens). keel_review_notes reads them when they are done."""
        return await act(ctx, "keel_review_findings", "ask keel for findings", review_ai, api, "findings", project, pr, branch)

    @srv.tool(annotations=out, structured_output=False)
    async def keel_review_submit(event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"], body: str = "", project: str | None = None,
                                 pr: int | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: send your review of a pull request with every draft comment: APPROVE, REQUEST_CHANGES or
        COMMENT, with an optional text. The person says yes first."""
        return await act(ctx, "keel_review_submit", f"send your review ({event})" + (f" on #{pr}" if pr else ""), review_submit, api, event, body,
                         project, pr, confirm=True)

    gone = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
    merge = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

    @srv.tool(annotations=gone, structured_output=False)
    async def keel_review_draft(draft_id: str, body: str | None = None, delete: bool = False, project: str | None = None,
                                ctx: Context | None = None) -> str:
        """Code Review plugin: change a draft comment's text (body), or delete it (delete=true). Draft ids are in
        keel_review_files."""
        return await act(ctx, "keel_review_draft", f"{'delete' if delete else 'edit'} draft {draft_id}", review_draft, api, draft_id, body,
                         delete, project)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_move_drafts(project: str | None = None, branch: str | None = None, pr: int | None = None,
                                      ctx: Context | None = None) -> str:
        """Code Review plugin: a branch review's drafts move to the branch's pull request (pr: another one)."""
        return await act(ctx, "keel_review_move_drafts", "move the drafts to the pull request", review_move_drafts, api, project, branch, pr)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_viewed(path: str, viewed: bool = True, project: str | None = None, pr: int | None = None, branch: str | None = None,
                                 ctx: Context | None = None) -> str:
        """Code Review plugin: mark a changed file as viewed (or not, viewed=false)."""
        return await act(ctx, "keel_review_viewed", f"mark {path} {'viewed' if viewed else 'not viewed'}", review_viewed, api, path, viewed,
                         project, pr, branch)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_base(base: Literal["remote", "local", ""] = "", project: str | None = None, branch: str | None = None,
                               ctx: Context | None = None) -> str:
        """Code Review plugin: which base a branch review compares against: remote (the canonical remote's copy), local,
        or "" for keel's choice. keel_review_conversation lists them."""
        return await act(ctx, "keel_review_base", "compare against " + (base or "keel's choice"), review_base, api, base, project, branch)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_check(thread_id: str | None = None, again: bool = False, harder: bool = False, project: str | None = None,
                                pr: int | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: "Is the reviewer right?": keel checks a thread against its code (one cheap model call,
        posts nothing), or every open thread without an answer in one call (no thread_id). again: ask anew; harder:
        a stronger model (if Settings allow). keel_review_thread reads the answer."""
        return await act(ctx, "keel_review_check", f"check {'thread ' + thread_id if thread_id else 'the open threads'}", review_check, api,
                         thread_id, again, harder, project, pr)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_button(thread_id: str, button: str, again: bool = False, harder: bool = False, project: str | None = None,
                                 pr: int | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: a thread's extra button from Settings › Code Review threads (its id or label, listed by
        keel_review_thread): a question to a model, or a flow."""
        return await act(ctx, "keel_review_button", f"run {button} on thread {thread_id}", review_button, api, thread_id, button, again,
                         harder, project, pr)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_finding(finding_id: str, decision: Literal["dismissed", "commented", "open"], why: str | None = None,
                                  project: str | None = None, pr: int | None = None, branch: str | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: your decision on one of keel's findings (ids in keel_review_notes): dismissed (why is
        needed, keel keeps it), commented, or open again."""
        return await act(ctx, "keel_review_finding", f"mark finding {finding_id} {decision}", review_finding, api, finding_id, decision, why,
                         project, pr, branch)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_address(thread_id: str, note: str | None = None, workflow: str | None = None, fix: str | None = None,
                                  project: str | None = None, pr: int | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: Address it: a flow makes the change the thread asks for, on the pull request's branch; it
        never pushes. note: words for the flow; fix: what the fix should be; workflow: another flow. The person says
        yes first."""
        return await act(ctx, "keel_review_address", f"start a flow on thread {thread_id}", review_address, api, thread_id, note, workflow, fix,
                         project, pr, confirm=True)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_fix_finding(finding_id: str, workflow: str | None = None, project: str | None = None, pr: int | None = None,
                                      branch: str | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: Fix it: the flow Settings › Review names (or workflow) fixes a confirmed finding on the
        reviewed branch; it never pushes. The person says yes first."""
        return await act(ctx, "keel_review_fix_finding", f"start a flow to fix finding {finding_id}", review_fix_finding, api, finding_id,
                         workflow, project, pr, branch, confirm=True)

    @srv.tool(annotations=rw, structured_output=False)
    async def keel_review_checkout(project: str | None = None, pr: int | None = None, branch: str | None = None,
                                   ctx: Context | None = None) -> str:
        """Code Review plugin: the project folder moves to the review's branch (git checkout). The person says yes first."""
        return await act(ctx, "keel_review_checkout", "check out the review's branch in the project folder", review_checkout, api, project, pr,
                         branch, confirm=True)

    @srv.tool(annotations=merge, structured_output=False)
    async def keel_review_merge(method: Literal["merge", "squash", "rebase"] = "merge", delete_branch: bool = False, project: str | None = None,
                                pr: int | None = None, ctx: Context | None = None) -> str:
        """Code Review plugin: merge your own pull request on GitHub or GitLab (method merge, squash or rebase;
        delete_branch after). The person says yes first."""
        return await act(ctx, "keel_review_merge", f"merge #{pr} ({method})" if pr else f"merge the pull request ({method})", review_merge, api,
                         method, delete_branch, project, pr, confirm=True)
