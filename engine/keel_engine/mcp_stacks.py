"""keel 0.18.9: stacks through keel's MCP tools (and keel TUI's /stacks, /stack and /create stack): how each project
builds and tests. The project's stacks and which ones it uses (keel_stacks), one stack with its YAML (keel_stack), what
keel reads in the project's files for a new one (keel_stack_suggest), a check that saves nothing (keel_stack_check), a
new one saved (keel_stack_create), one the project added removed (keel_stack_remove), a stack turned off or on for the
project (keel_stack_off), and one from the marketplace (keel_stack_market_install). Create, remove and a marketplace
install ask the person first. keel's own stacks are never removed: a project turns one off instead.
"""

from __future__ import annotations

from typing import Any

from .formats import FORMATS
from .marketplace.kinds import PACK_NAME
from .mcp_api import q
from .mcp_server import ApiError, KeelApi, _project


_q = q


def _where(s: dict) -> str:
    m = s.get("market") or {}
    o = s.get("origin") or s.get("source") or "?"
    return f"{o} {m.get('version')}{' (edited)' if m.get('edited') else ''}" if m else o


def _state(s: dict) -> str:
    return "turned off" if s.get("off") else "used" if s.get("detected") else "not used"


def _line(s: dict) -> str:
    return (f"- {s.get('name')}  {s.get('lane')} · {_where(s)} · {_state(s)}"
            + ("" if s.get("removable") else " · keel's own" if s.get("origin") == "keel" else "")
            + (" · installable pack" if s.get("installable") else ""))


def stacks(api: KeelApi, project: str | None = None) -> str:
    p = _project(api, project)
    rows = api.get(f"/projects/{_q(p['id'])}/stacks") or []
    if not rows:
        return f"keel knows no stack for {p['id']}."
    used = [s for s in rows if s.get("detected") and not s.get("off")]
    return (f"{len(rows)} stacks for {p['id']}, {len(used)} used (name · lane · from · in this project):\n"
            + "\n".join(_line(s) for s in rows)
            + "\nkeel_stack <name> shows one; keel_stack_create saves a new one; keel's own are turned off, not removed.")


def stack(api: KeelApi, name: str, project: str | None = None) -> str:
    p = _project(api, project)
    s = api.get(f"/projects/{_q(p['id'])}/stacks/{_q(name)}") or {}
    head = [f"{s.get('name')}  {s.get('lane')} · {_where(s)} · {_state(s)}" + (f" · {s['file']}" if s.get("file") else ""),
            "removable" if s.get("removable") else f"not removable: {s.get('why') or 'it ships with keel'}"]
    layers = [str(x.get("id")) for x in s.get("layers") or [] if isinstance(x, dict)]
    if layers:
        head.append("test layers (lowest first): " + ", ".join(layers))
    for c in s.get("commands") or []:
        head.append(f"  {c.get('name')}: {c.get('cmd')}")
    for t in s.get("tools") or []:
        head.append(f"  tool {t.get('name')}: " + ("turned off" if t.get("off") else f"on {t.get('on')}, fail {t.get('fail')}"))
    return "\n".join(head) + ("\n\n" + s["yaml"].strip() if s.get("yaml") else "")


def suggest(api: KeelApi, lane: str | None = None, project: str | None = None) -> str:
    p = _project(api, project)
    r = api.get(f"/projects/{_q(p['id'])}/stacks/suggest", lane=lane or None) or {}
    out = [f"what keel reads in {p['id']} for a new {r.get('lane')} stack (closest of keel's: {r.get('closest') or 'none'}):",
           "read: " + (", ".join(r.get("read") or []) or "nothing keel knows")]
    det = r.get("detect") or {}
    out.append("detect: " + "; ".join(f"{k} {', '.join(v)}" for k, v in det.items() if v))
    out.append("layers: " + ", ".join(str(x.get("id")) for x in r.get("layers") or []))
    for k in r.get("keys") or []:
        rows = (r.get("commands") or {}).get(k["key"]) or []
        out.append(f"  {k['key']} ({k['what']}): " + (" | ".join(f"{x['cmd']}  [{x['from']}]" for x in rows[:3]) or "nothing found"))
    for t in r.get("tools") or []:
        out.append(f"  tool {t['name']}: {t['run']} (on {t['on']}, fail {t['fail']}) [{t.get('from')}]")
    out.append("Write the YAML (keel_format kind=stack), check it with keel_stack_check, then keel_stack_create.")
    return "\n".join(out)


def _check_text(r: dict) -> str:
    rv = r.get("review") or {}
    what = (f"{r.get('name') or '?'} ({r.get('lane') or 'no lane'}): {len(rv.get('layers') or [])} layers, "
            f"{len(rv.get('commands') or {})} commands, {len(rv.get('tools') or [])} tools")
    lines = [what]
    if r.get("detects_here") is not None:
        lines.append("its detect block " + ("matches" if r["detects_here"] else "does not match") + " this project's files")
    lines += [f"error: {e}" for e in r.get("errors") or []]
    lines += [f"warning: {w}" for w in r.get("warnings") or []]
    if r.get("skills_missing"):
        lines.append("skills this project does not have: " + ", ".join(r["skills_missing"]))
    return "\n".join(lines)


def check(api: KeelApi, yaml: str, project: str | None = None, replace: bool = False) -> str:
    p = _project(api, project)
    r = api.post(f"/projects/{_q(p['id'])}/stacks/check", {"yaml": yaml, "replace": replace}) or {}
    return ("keel would save it: " if r.get("ok") else "keel would not save it: ") + _check_text(r)


def create(api: KeelApi, yaml: str, replace: bool = False, project: str | None = None) -> str:
    p = _project(api, project)
    r = api.post(f"/projects/{_q(p['id'])}/stacks/check", {"yaml": yaml, "replace": replace}) or {}
    if not r.get("ok"):
        return ("keel did not save it: " + _check_text(r) + "\n\n" + FORMATS["stack"].split("Example:")[0].strip())
    s = api.post(f"/projects/{_q(p['id'])}/stacks/save", {"yaml": yaml, "replace": replace}) or {}
    st = s.get("stack") or {}
    return (f"{'replaced' if s.get('replaced') else 'saved'} the stack {st.get('name') or r.get('name')} as {s.get('path')} on "
            f"{p['id']}: keel uses it from the next step on." + (f"\n{len(r.get('warnings') or [])} warnings:\n"
                                                                  + "\n".join(f"  {w}" for w in r["warnings"]) if r.get("warnings") else ""))


def remove(api: KeelApi, name: str, project: str | None = None) -> str:
    p = _project(api, project)
    r = api.delete(f"/projects/{_q(p['id'])}/stacks/{_q(name)}") or {}
    return (f"removed the stack {name} from {p['id']} ({r.get('removed')})."
            + (f" keel's own {name} is used again." if r.get("back") == "keel" else ""))


def switch(api: KeelApi, name: str, off: bool = True, project: str | None = None) -> str:
    p = _project(api, project)
    r = api.put(f"/projects/{_q(p['id'])}/stacks/{_q(name)}/off", {"off": off}) or {}
    return (f"{name} is turned {'off' if off else 'on again'} for {p['id']}"
            + (f"; turned off there now: {', '.join(r['all'])}." if r.get("all") else "; no stack is turned off there now.")
            # 0.22.0 a committed .keel/config.yml stays unchanged (the tree stays clean): this computer keeps the switch
            + (" This computer keeps it (.keel/config.yml is committed, so it stays unchanged and flows can start); "
               "to share it, add the stack to stacks.ignore in .keel/config.yml and commit it." if r.get("where") else ""))


def market_install(api: KeelApi, name: str, version: str | None = None, replace: bool = False,
                   project: str | None = None) -> str:
    if not PACK_NAME.match(name or ""):
        raise ApiError(f"'{name}' is not a stack name (a-z, 0-9 and '-').")
    p = _project(api, project)
    body: dict[str, Any] = {"name": name, **({"version": version} if version else {}), **({"replace": True} if replace else {})}
    r = api.post(f"/projects/{_q(p['id'])}/stacks/market/install", body) or {}
    m = r.get("market") or {}
    return (f"installed the stack {name} {m.get('version') or ''} from the catalog {m.get('catalog') or '?'} on {p['id']} "
            f"(.keel/stacks/{name}.yml)." + ("\nwarnings:\n" + "\n".join(f"  {w}" for w in r["warnings"]) if r.get("warnings") else ""))


def market_lines(api: KeelApi, q: str = "") -> list[str]:
    """The marketplace's stacks that match q, for keel_marketplace_search (nothing when the api has no stack catalog)."""
    try:
        body = api.get("/marketplace/stacks", q=q or None) or {}
    except ApiError:
        return []
    out: list[str] = []
    for h in body.get("stacks") or []:
        have = ("installed " + str((h.get("installed") or {}).get("version"))
                + (f", update {h['update']}" if h.get("update") else "")) if h.get("installed") else "not installed"
        fits = f"version {h['version']}" if h.get("version") else f"does not fit: {h.get('why_not') or '?'}"
        out.append(f"stack {h.get('name')}  {h.get('title')} · {(h.get('review') or {}).get('lane') or '?'} · "
                   f"{h.get('publisher_title') or h.get('publisher')} · {have} · {fits}")
        if h.get("summary"):
            out.append(f"  {h['summary']}")
    words = [w for w in (q or "").lower().split() if w]
    for b in body.get("builtin") or []:
        text = f"{b.get('name')} {b.get('lane') or ''} {b.get('source') or ''}".lower()
        if all(w in text for w in words):
            out.append(f"stack {b.get('name')}  keel's own ({b.get('source')}) · {b.get('lane')}"
                       + (" · an installable pack" if b.get("installable") else ""))
    if out:
        out.append("A stack from the catalogs: keel_stack_market_install; keel's own are used when detected.")
    return out
