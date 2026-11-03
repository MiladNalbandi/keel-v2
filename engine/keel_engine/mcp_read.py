"""The read-only MCP tools' text: status, projects, timeline, next action, explain, dashboard and the gate reader."""
from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import quote

from . import mcp_ask as ask_mod
from . import rules
from .mcp_api import ApiError, KeelApi, _clock, _flow, _n, _project, _when, cut
from .runtime import md

BUCKETS = ["api-main", "api-test", "web-src", "web-test", "contract", "specs", "migration", "e2e", "smoke", "other"]
BUCKET_TEXT = {
    "api-main": "backend production code", "api-test": "backend tests", "web-src": "frontend production code",
    "web-test": "frontend tests", "contract": "the API contract, openapi", "specs": "spec files", "migration": "database migrations",
    "e2e": "end-to-end tests", "smoke": "smoke tests", "other": "any other file: docs, build files",
}
TIMELINE_FILTERS = ("all", "steps", "agents", "failed")
# keel_dashboard: the web app's page for each view (web/src/routes.ts; #/projects/<id>/… picks the project)
DASHBOARD_VIEWS = {"flow": "flow", "map": "map/system", "er": "map/er"}


def _flow_name(thread: dict, workflow: dict | None) -> str:
    wid = thread.get("workflow_id") or "?"
    name = (workflow or {}).get("name")
    return f"{name} ({wid})" if name and name != wid else wid


ORDER_FROM = {"spec": "the spec's ORDER line", "plan": "the plan's ORDER", "keel": "keel moved it (an order question)",
              "person": "you moved it", "id": "criteria ids (no ORDER given)"}


def _choice_lines(w: dict) -> list[str]:
    """A gate with named choices: each name with its label, what it does, the plain approve's pick and whether it needs a
    reason (choice_info; an older engine's prose otherwise)."""
    return ask_mod.choice_lines(w)


def _choice_lines_at(w: dict, indent: str) -> list[str]:
    return ask_mod.choice_lines(w, indent)


GATE_PAGE = 12_000       # 0.18.28 keel_read_gate's page: a gate's text is paged, never cut without a word
STATUS_DETAIL = 4_000    # keel_status shows this much of a gate's text, then says how to read the rest


def plain_lines(w: dict, indent: str = "          ") -> list[str]:
    """0.18.28 what a plain approve and a plain reject do at this gate, and whether the reason is used."""
    out = []
    for k in ("approve", "reject"):
        e = (w.get("plain") or {}).get(k) if isinstance(w.get("plain"), dict) else None
        if isinstance(e, dict) and e.get("does"):
            out.append(f"{indent}a plain {k} here {e['does']} (reason: {e.get('reason') or '-'})")
    return out


def read_gate_hint(thread: dict, project: str | None, page: int = 2) -> str:
    args = ", ".join(x for x in (f"project={project!r}" if project else "", f"flow={thread.get('thread_id')!r}",
                                 f"page={page}" if page != 1 else "") if x)
    return f"keel_read_gate({args})"


def _waiting_lines(w: dict, thread: dict | None = None, project: str | None = None) -> list[str]:
    labels = w.get("labels") or {}
    if w.get("choices"):
        out = [f"waiting   {w.get('kind', 'gate')}: {w.get('title', '')}",
               '          choices (approve with payload {"choice": "<name>"}):', *_choice_lines(w)]
    else:
        opts = " | ".join(f"{o}" + (f' ("{labels[o]}")' if labels.get(o) else "") for o in w.get("options") or [])
        out = [f"waiting   {w.get('kind', 'gate')}: {w.get('title', '')}", f"          options: {opts or '-'}"]
    out += plain_lines(w)
    if w.get("questions"):
        out.append("          questions (answers: {<id>: <label> | [<labels>] | <your words>}):")
        out += ask_mod.question_lines(w["questions"], "            ")
    # a gate's detail is Markdown: the summary first, the fences dropped so a diff or JSON reads as plain text
    detail = md.unfence(w.get("detail") or "").strip()
    if detail:
        shown = detail if len(detail) <= STATUS_DETAIL else cut(detail, STATUS_DETAIL)
        out.append("          " + shown.replace("\n", "\n          "))
        if len(detail) > STATUS_DETAIL:
            # 0.18.28 never cut without a word: how much is left and the call that reads it whole
            pages = -(-len(detail) // GATE_PAGE)
            out.append(f"          (the gate's text goes on: {len(detail):,} characters, {pages} page(s); "
                       f"{read_gate_hint(thread or {}, project, 1)} reads it whole)")
    return out


def read_gate(api: KeelApi, project: str | None = None, flow: str | None = None, page: int = 1) -> str:
    """0.18.28 the whole text of the gate a flow waits at: its title, choices with what each does, questions, the
    plain approve's and reject's effect, its full detail (findings, failure output) and, at a spec gate, the spec,
    paged (GATE_PAGE characters a page)."""
    p = _project(api, project)
    if flow:
        thread = (api.get(f"/projects/{p['id']}/flows/{quote(flow, safe='')}") or {}).get("thread") or {}
    else:
        thread, _ = _flow(api, p["id"])
        thread = thread or {}
    w = thread.get("waiting")
    if thread.get("status") != "waiting" or not w:
        return f"\"{thread.get('title') or flow or p['id']}\" waits for nothing now ({thread.get('status') or 'no flow'})."
    head = [f"gate      {w.get('name') or w.get('step')}: {w.get('title')} · {w.get('kind', 'gate')} · thread {thread.get('thread_id')}"]
    if w.get("choices"):
        head += ['choices (approve with choice="<name>"):', *_choice_lines(w)]
    else:
        head.append("options   " + " | ".join(w.get("options") or ["approve", "reject"]))
    head += plain_lines(w, "")
    if w.get("questions"):
        head += ["questions (answers: {<id>: <label> | [<labels>] | <your words>}):", *ask_mod.question_lines(w["questions"], "  ")]
    body = md.unfence(w.get("detail") or "").strip()
    if thread.get("spec") and (thread.get("phase") in ("spec", "triage") or str(w.get("step") or "").startswith(("spec", "amend"))):
        try:
            from . import mcp_more as more
            body += "\n\n--- the spec ---\n" + more.specs(api, p["id"], thread["spec"])
        except ApiError:
            pass
    pages = max(1, -(-len(body) // GATE_PAGE))
    page = max(1, min(int(page or 1), pages))
    chunk = body[(page - 1) * GATE_PAGE: page * GATE_PAGE]
    out = (head if page == 1 else head[:1]) + ["", f"text      page {page} of {pages} ({len(body):,} characters)", chunk]
    if page < pages:
        out += ["", f"page {page} of {pages}: the next is {read_gate_hint(thread, project, page + 1)}"]
    return "\n".join(out)


# ---------------------------------------------------------------- read tools

def status(api: KeelApi, project: str | None = None) -> str:
    p = _project(api, project)
    thread, workflow = _flow(api, p["id"])
    # 0.22.0 the folder keel works in, on the person's computer when keel knows it (keel's own clone may not be theirs)
    host, root = p.get("host"), p.get("root")
    folder = f"{host} (keel sees it as {root})" if host and root and host != root else (host or root)
    head = f"project   {p['id']} · branch {p.get('branch') or '-'}" + (f" · folder {folder}" if folder else "")
    if not thread:
        out = f"{head}\nflow      none. Start one in the dashboard (Flow › Start a flow)."
        other = None if project else _waiting_elsewhere(api, p["id"])
        return f"{out}\nelsewhere {other}" if other else out
    out = [head, f"flow      {_flow_name(thread, workflow)} · \"{thread.get('title', '')}\" · {thread.get('status')} · thread {thread.get('thread_id')}",
           f"step      {thread.get('current') or '-'} · phase {thread.get('phase') or 'none'}" + (f" · {thread['ac']}" if thread.get("ac") else "")
           + (f" · run mode {thread['run_mode']}" if thread.get("run_mode") not in (None, "", "manual") else "")]
    if thread.get("base_behind"):
        out.append(f"WARNING   the flow started from an old base: {thread['base_behind']}")     # 0.22.0
    acs = thread.get("acs") or []
    if acs:
        done = sum(1 for a in acs if a.get("status") in ("done", "already-met"))
        out.append(f"criteria  {done}/{len(acs)} done")
        order = thread.get("ac_order") or {}
        out.append(f"order     {' → '.join(a.get('id', '') for a in acs)}"
                   + (f" (from {ORDER_FROM.get(order.get('from'), order.get('from'))})" if order.get("from") else ""))
        out += [f"  {('[' + str(a.get('status')) + ']'):<14}{a.get('id')} {a.get('layer', '')}  {a.get('title', '')}" for a in acs]
    if thread.get("waiting"):
        out += _waiting_lines(thread["waiting"], thread, project)
    u = thread.get("usage") or {}
    total = int(u.get("tokens_in") or 0) + int(u.get("tokens_out") or 0)
    cap = f" of cap {_n(u['cap_tokens'])}" if u.get("cap_tokens") else " (no cap)"
    cap_usd = f" of cap ${float(u['cap_usd']):.2f}" if u.get("cap_usd") else ""
    out.append(f"usage     {_n(total)} tokens{cap} · in {_n(u.get('tokens_in'))} · out {_n(u.get('tokens_out'))} · cached "
               f"{_n(u.get('tokens_cached'))} · ${float(u.get('cost_usd') or 0):.2f}{cap_usd} · {_n(u.get('premium_requests'))} premium requests")
    if thread.get("error"):
        out.append(f"error     {thread['error']}")
    for b in thread.get("blockers") or []:
        out.append(f"blocker   {b.get('gate')}: {b.get('why')} -> {b.get('fix')}")
    return "\n".join(out)


def _waiting_elsewhere(api: KeelApi, default: str) -> str | None:
    """When the default project (none named) has no flow: the first other project whose flow waits for a person, as
    "no flow in <default>; <other> has a flow waiting at <gate>", so a caller in the wrong folder is not told "none"."""
    try:
        rows = api.get("/projects") or []
    except ApiError:
        return None
    for row in rows:
        if row.get("id") == default or not row.get("waiting"):
            continue
        try:
            thread, _ = _flow(api, row["id"])
        except ApiError:
            thread = None
        gate = ((thread or {}).get("waiting") or {}).get("title") or (thread or {}).get("current") or "a gate"
        return f"no flow in {default}; {row['id']} has a flow waiting at {gate} (keel_status project={row['id']})"
    return None


def projects(api: KeelApi) -> str:
    rows = api.get("/projects") or []
    if not rows:
        return "keel has no project yet. Add one in the dashboard (Projects › Add)."
    out = []
    for p in rows:
        acs = p.get("acs") or [0, 0]
        out.append(f"{p['id']}  branch {p.get('branch') or '-'} · flow {p.get('flow') or 'none'} · phase {p.get('phase') or 'none'} · "
                   f"criteria {acs[0]}/{acs[1] if len(acs) > 1 else 0} · waiting {'yes' if p.get('waiting') else 'no'}"
                   + (f" · {p['running']} running" if p.get("running") else ""))
    return "\n".join(out)


def timeline(api: KeelApi, project: str | None = None, limit: int = 20, filter: str = "all") -> str:
    if filter not in TIMELINE_FILTERS:
        raise ApiError(f'filter must be one of: {", ".join(TIMELINE_FILTERS)}')
    limit = max(1, min(int(limit or 20), 200))
    p = _project(api, project)
    thread, _ = _flow(api, p["id"])
    events: list[tuple[datetime, str]] = []
    if filter in ("all", "steps") and thread:
        for c in api.get(f"/threads/{thread['thread_id']}/history") or []:
            events.append((_when(c.get("at")), f"{_clock(c.get('at'))}  step   {c.get('step')}" + (f" · {c['note']}" if c.get("note") else "")))
    if filter in ("all", "agents", "failed"):
        for j in api.get("/jobs", project=p["id"], limit=limit) or []:
            if filter == "failed" and j.get("status") not in ("failed", "guard"):
                continue
            tokens = int(j.get("tokens_in") or 0) + int(j.get("tokens_out") or 0)
            where = " ".join(x for x in (j.get("step"), j.get("ac")) if x)
            events.append((_when(j.get("started_at")), f"{_clock(j.get('started_at'))}  agent  {j.get('agent')} {j.get('status')} · {where} · "
                                                         f"{_n(tokens)} tokens · {j.get('provider')}/{j.get('model')} · job {j.get('id')}"))
    if filter == "failed" and thread and thread.get("error"):
        events.append((_when(thread.get("updated_at")), f"{_clock(thread.get('updated_at'))}  flow   failed · {thread['error']}"))
    if not events:
        return "nothing yet: no flow has run on this project." if not thread else f"no {filter} events."
    events.sort(key=lambda e: e[0], reverse=True)
    return "\n".join(line for _, line in events[:limit])


def next_action(api: KeelApi, project: str | None = None) -> str:
    p = _project(api, project)
    thread, workflow = _flow(api, p["id"])
    if not thread:
        return "no flow: start one in the dashboard (Flow › Start a flow: feature, change, fix, diagnose, review, cover, ship, hunt, lint, init …)."
    st, w = thread.get("status"), thread.get("waiting")
    ac = f" for {thread['ac']}" if thread.get("ac") else ""
    if st == "waiting" and w:
        kind = w.get("kind")
        if kind == "clarify":
            n = len(w.get("questions") or [])
            line = "\n".join([f"waiting at {w.get('title')}: answer the {n} question(s), then send them (approve with answers).",
                              *ask_mod.question_lines(w.get("questions"), "  "),
                              *(['  choices (choice="<name>"):', *_choice_lines_at(w, "    ")] if w.get("choices") else []),
                              "  " + ask_mod.how_to_answer(w) + "; keel_answer(ask_person=true) asks the person in a form."])
        elif kind == "budget":
            line = f"waiting at {w.get('title')}: approve to go past the token cap, or reject to stop."
        elif w.get("choices"):
            # before kind fix: a stuck criterion is a fix with real choices, not "approve to try again"
            line = "\n".join([f"waiting at {w.get('step')} ({w.get('title')}){ac}: choose one "
                               '(approve with payload {"choice": "<name>"} and a note):', *_choice_lines(w), *plain_lines(w, "  "),
                              *(["  questions (answers: {<id>: <label> | [<labels>]}):",
                                 *ask_mod.question_lines(w.get("questions"), "    ")] if w.get("questions") else [])])
        elif kind == "fix":
            line = "\n".join([f"waiting at {w.get('title')}: approve to try again, or reject to stop the flow.", *plain_lines(w, "  ")])
        else:
            labels = w.get("labels") or {}
            line = (f"waiting at {w.get('step')} ({w.get('title')}){ac}: approve"
                    + (f' ("{labels["approve"]}")' if labels.get("approve") else "")
                    + (" or send back with a reason" + (f' ("{labels["reject"]}")' if labels.get("reject") else "")
                       if "reject" in (w.get("options") or []) else "") + ".")
        out = [line, "A human decides in the dashboard (or a client with keel2 mcp --write: keel_approve_gate)."]
    elif st == "running":
        out = [f"running {thread.get('current') or 'the next step'} (phase {thread.get('phase') or 'none'}){ac}: nothing to do, keel is working."]
    elif st == "done":
        out = [f"flow {_flow_name(thread, workflow)} is done. Review the branch {p.get('branch') or ''} and open a PR, or start a new flow."]
    elif st == "failed":
        out = [f"flow failed at {thread.get('current')}: {thread.get('error') or 'see keel_timeline filter=failed'}. Rewind or start it again from the dashboard."]
    elif st == "stopped":
        out = [f"flow stopped at {thread.get('current')}. Rewind to a checkpoint in the dashboard, or start a new flow."]
    else:
        out = [f"flow is {st} at {thread.get('current')}."]
    blockers = thread.get("blockers") or []
    if blockers:
        out += ["", "blocking a push:"] + [f"  {b.get('gate')}: {b.get('why')} -> {b.get('fix')}" for b in blockers]
    return "\n".join(out)


def explain(api: KeelApi | None, phase: str | None = None, project: str | None = None) -> str:
    if not phase:
        thread = _flow(api, _project(api, project)["id"])[0] if api else None
        phase = (thread or {}).get("phase") or "none"
    if phase not in rules.PHASES:
        return f'no phase "{phase}". keel knows: {", ".join(rules.PHASES)}'
    out = [f"phase  {phase}"]
    for flow, names in rules.RAILS.items():
        if phase in names:
            i = names.index(phase)
            out += ["", f"on the {flow} rail, step {i + 1} of {len(names)}:", "  " + " - ".join(f"[{n}]" if j == i else n for j, n in enumerate(names))]
    row = rules.MATRIX.get(phase) or rules.CLOSED
    fallback = row.get("*", "deny")
    allowed, refused, other = [], [], []
    for b in BUCKETS:
        rule = row.get(b, fallback)
        item = f"{b} ({BUCKET_TEXT[b]})"
        (allowed if rule == "allow" else refused if rule == "deny" else other).append(item if rule in ("allow", "deny") else f"{item}: {rule}")
    out += ["", "may edit:", *[f"  {x}" for x in allowed or ["nothing"]], "refused:", *[f"  {x}" for x in refused or ["nothing"]]]
    if other:
        out += ["conditional:", *[f"  {x}" for x in other]]
    out += ["always refused:", *[f"  {b}: {why}" for b, why in rules.ALWAYS_DENY.items()]]
    if phase in rules.LANE_SCOPED_PHASES:
        out.append(f"in {phase}, an agent edits only its criterion's lane (api or web).")
    hint = rules._hint(phase, {"red": "api-main", "green": "api-test"}.get(phase, "other")).strip()
    if hint:
        out += ["", hint]
    ctype = rules.commit_type_for(phase)
    cr = rules.COMMIT_RULES.get(ctype)
    if cr:
        out += ["", f"commits here are '{ctype}': may hold {', '.join(cr.get('allow') or []) or '-'}; never {', '.join(cr.get('deny') or []) or '-'}."]
    if phase != "none":
        out += ["shell: commits, git reset --hard and interactive rebase are refused during a flow (the engine commits); "
                "adding a dependency needs the spec gate; shell writes follow the edit rules above."]
    out += ["an unlock (dashboard › Repo › Unlock) lets one path through in one phase."]
    nxt = rules.TRANSITIONS.get(phase) or []
    if nxt:
        out.append(f"next phases: {', '.join(nxt)}")
    return "\n".join(out)


def dashboard_url(api: KeelApi, view: str = "flow", project: str | None = None) -> str:
    """The web app's URL for a view of the project: KEEL_PUBLIC_URL (keel2 mcp sets it from the running port), else
    the api's own address (inside the container that is keel's port 8080, not the one on your machine)."""
    if view not in DASHBOARD_VIEWS:
        raise ApiError(f'view must be one of: {", ".join(DASHBOARD_VIEWS)}')
    p = _project(api, project)
    base = (os.environ.get("KEEL_PUBLIC_URL") or api.url).rstrip("/")
    return f"{base}/#/projects/{quote(p['id'], safe='')}/{DASHBOARD_VIEWS[view]}"


def dashboard(api: KeelApi, view: str = "flow", project: str | None = None) -> str:
    url = dashboard_url(api, view, project)
    what = {"flow": "the flow page", "map": "the map", "er": "the database diagram (ER)"}[view]
    return f"{url}\nOpen it in a browser: {what} of this project in keel's dashboard."
