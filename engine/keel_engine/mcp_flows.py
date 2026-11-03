"""keel 0.19.x MCP: what the dashboard's "Start a flow" and keel TUI's project switcher do, as tools.

- the flows a project can start, each with one line on what it is for and the inputs it reads (keel_start_flow with no
  type, keel_flows), read from the workflows the api lists (a new flow, like review-pr, shows up without code here);
- pause a flow (stop it, every finished step saved) — keel_resume (or keel_resume_flow) goes on from the last step;
- the focused project: what the dashboard and keel TUI look at (GET / PUT /api/ui/focus, keel 0.18.6 UiFocus).

Plain functions of the api, like mcp_more.py; mcp_server.py registers them at their level (TOOLS).
"""

from __future__ import annotations

import re
from urllib.parse import quote

from .mcp_server import ApiError, KeelApi, _flow, _project, cut

# what keel's own flows are for, one line each (the dashboard's size picker says the same for the small ones,
# web/src/flowSize.ts); any other flow says it in its YAML (`about:`, else its first comment line)
ABOUT = {
    "quick": "one agent, the tests, one look, one commit: a typo, a small bug, a config value",
    "address": "a review comment or a change to test code only: one agent, code and tests, the tests run, one look",
    "implement": "the code, then its tests; done when they pass",
    "small": "a short plan with 1-3 criteria, test-first, one review",
    "change": "work without a spec: 1-3 criteria, test-first, then ship",
    "feature": "the spec flow: interview, spec, contract, criteria test-first, reviews, ship",
    "fix": "a bug: reproduce it as a failing test, investigate, fix, ship",
    "diagnose": "a bug nobody can make fail yet: find its cause, read-only",
    "review": "review the branch's diff through a lens, read-only",
    "review-pr": "review a pull request (or ticket) and share the findings",
    "design-review": "fresh reviewers read a design document in five stages (Elephant-Goldfish Model); one merged report",
    "cover": "tests for code that already exists, where coverage is missing",
    "ship": "the release checks, reviews and the PR for a finished branch",
    "hunt": "a read-only bug hunt through lenses: proven findings and a backlog",
    "hunt-next": "fix the next group of the hunt's backlog",
    "lint": "the project's static checks, and fixes for what they find",
    "init": "set the project up for keel: toolchain rungs, stack, knowledge",
    "knowledge-refresh": "rewrite keel's knowledge sections from the code",
    "plugin": "build a keel plugin: spec, scaffold, test-first, lint",
    "spike": "research a question from evidence and the web, then decide: build or drop",
    "clarify": "ask before you code: task type, sections, rounds of questions; a spec-format document with criteria, "
               "a definition of done and a prompt; hands over to the flow that fits",
}
# B13a flows that build nothing read no request (init, a review, a ship ...): they start without one, like the
# dashboard's Start a flow, which hides "What to build" for them (web/src/components/StartFlow.tsx NO_BUILD)
NO_REQUEST = frozenset({"review", "cover", "ship", "lint", "hunt", "hunt-next", "init", "knowledge-refresh"})


def reads_request(w: dict | None, type: str) -> bool:
    """Whether a flow needs the person's request: not one of NO_REQUEST, nor a flow based on one of them."""
    based = str((w or {}).get("based_on") or "").removeprefix("keel/")
    return type not in NO_REQUEST and based not in NO_REQUEST


# the inputs keel_start_flow takes by name (they go to the flow as its options, the same as the dashboard's form)
NAMED_INPUTS = ("ticket", "pr")
INPUT_LINE = re.compile(r"^#\s{2,4}([a-z][\w-]*(?:,\s*[a-z][\w-]*)*)(?::\s*|\s{2,})(.*)$")


def _comments(yaml_text: str) -> list[str]:
    """The comment lines before the YAML's first key, and the ones right after its header keys (spike's style)."""
    out, seen_steps = [], False
    for line in (yaml_text or "").splitlines():
        if line.startswith("steps:"):
            seen_steps = True
        if seen_steps:
            break
        if line.startswith("#"):
            out.append(line)
    return out


def about_of(w: dict) -> str:
    """One line on what a flow is for."""
    wid = str(w.get("id") or "")
    y = str(w.get("yaml") or "")
    m = re.search(r"^about:\s*[\"']?(.+?)[\"']?\s*$", y, re.M)
    if m:
        return cut(m.group(1), 140)
    if wid in ABOUT:
        return ABOUT[wid]
    text = " ".join(c.lstrip("#").strip() for c in _comments(y)).strip()
    if text:
        first = re.split(r"(?<=\.)\s", text, maxsplit=1)[0]
        return cut(first if len(first) >= 25 and not first.endswith(":") else text, 140)
    return str(w.get("name") or wid)


def inputs_of(w: dict) -> list[tuple[str, str]]:
    """The inputs a flow's YAML says it reads ("Inputs" / "Options" in its comments: one `#   key  what` line each)."""
    lines = _comments(str(w.get("yaml") or ""))
    at = next((i for i, c in enumerate(lines) if re.search(r"\b(Inputs|Options)\b", c)), None)
    if at is None:
        return []
    out = []
    for c in lines[at + 1:]:
        m = INPUT_LINE.match(c)
        if not m:
            if out:
                break
            continue
        out.append((re.sub(r",\s*", ", ", m.group(1)), cut(m.group(2).strip(), 100)))
    return out


def catalog(api: KeelApi, project: str | None = None) -> list[dict]:
    p = _project(api, project)
    return [w for w in api.get(f"/projects/{p['id']}/workflows") or [] if w.get("id")]


def startable(api: KeelApi, project: str | None = None, header: bool = True) -> str:
    """Every flow the project can start: id, what it is for, the inputs it reads."""
    rows = catalog(api, project)
    if not rows:
        return "this project has no workflow yet."
    out = ["flows you can start (keel_start_flow type=<id>, title, request; ticket / pr / inputs when the flow reads them):"] if header else []
    for w in rows:
        ins = inputs_of(w)
        # 0.22.0 a flow that cuts no branch says so: it takes no base and works on the folder's branch
        own = w.get("own_branch") is False or bool(re.search(r"(?m)^own_branch:\s*false\b", str(w.get("yaml") or "")))
        out.append(f"  {w['id']:<18} {about_of(w)}" + (f"  [inputs: {', '.join(k for k, _ in ins)}]" if ins else "")
                   + ("  [works on the folder's branch; takes no base]" if own else ""))
    return "\n".join(out)


def start_body(type: str, title: str | None, request: str, ticket: str | None = None, pr: str | None = None,
               inputs: dict | None = None) -> dict:
    """The dashboard's start body: the title (given, else the request's first line), the request, and the flow's
    inputs as options (ticket, pr and any other a flow reads)."""
    first = request.splitlines()[0].strip() if request else ""
    title = (title or "").strip() or first or (f"PR {pr}" if pr else f"ticket {ticket}" if ticket else type)
    title = title if len(title) <= 72 else title[:71].rstrip() + "…"
    options: dict = {"started_via": "mcp", **{k: v for k, v in (inputs or {}).items() if v not in (None, "")}}
    if ticket:
        options["ticket"] = ticket
    if pr:
        options["pr"] = pr
    body = {"workflow_id": type, "title": title, "request": request or title, "options": options}
    if not request and not (ticket or pr):
        del body["request"]          # B13a a flow that reads no request (init) gets none, not its title as one
    return body


# ---------------------------------------------------------------- pause

def pause(api: KeelApi, project: str | None = None, thread_id: str | None = None) -> str:
    """Pause a running or waiting flow: keel stops it, every finished step stays saved, and keel_resume goes on."""
    p = _project(api, project)
    if thread_id:
        thread = (api.get(f"/projects/{p['id']}/flows/{quote(thread_id, safe='')}") or {}).get("thread") or {}
    else:
        thread = _flow(api, p["id"])[0] or {}
    if thread.get("status") not in ("running", "waiting"):
        return f"nothing to pause: the flow is {thread.get('status') or 'not there'}."
    tid = thread["thread_id"]
    api.post(f"/threads/{quote(tid, safe='')}/stop", {})
    return (f'paused "{thread.get("title")}" at {thread.get("current") or "-"} (thread {tid}): every finished step stays '
            "saved. keel_resume (no decision) goes on from the last saved step.")


# ---------------------------------------------------------------- focus

def focus(api: KeelApi, project: str | None = None, chat: str | None = None) -> str:
    """The project (and KeelBot chat) the dashboard and keel TUI look at; with `project`, switch both to it."""
    if not project and not chat:
        f = api.get("/ui/focus") or {}
        if not f.get("project"):
            return "no project in focus yet (the dashboard and keel TUI each pick their own)."
        c = (f.get("chats") or {}).get(f["project"])
        return (f"in focus: {f['project']}" + (f" · KeelBot chat {c}" if c else "")
                + (f" · set by {f['by']}" if f.get("by") else "") + (f" at {f['at']}" if f.get("at") else ""))
    body: dict = {"by": "mcp", "client": api.client[:64]}
    if project:
        body["project"] = _project(api, project)["id"]
    if chat:
        body["chat"] = chat
        if not project:
            body["chat_project"] = _project(api, None)["id"]
    f = api.put("/ui/focus", body) or {}
    if project and f.get("project") != body["project"]:
        raise ApiError(f"keel kept {f.get('project')} in focus.")
    return (f"the dashboard and keel TUI now look at {f.get('project')}"
            + (f" with KeelBot chat {chat} open" if chat else "") + ".")
