"""What the Cluster tools answer an agent or KeelBot. The rules come with the agent call (key cluster:access, the api's ClusterKeys:
{context: read|ask|free} of the contexts that are on). A command or a change on an "ask" context shows the person the exact
command in keel's Inbox and runs only after their yes; on a "read" or an off context it is refused. Every one is recorded."""

from __future__ import annotations

import json

from . import core, kube
from .kube import ClusterError

AGE_UNITS = ((86400, "d"), (3600, "h"), (60, "m"))
LIST_MAX = 60


def rules_of(call: dict) -> dict:
    raw = (call.get("keys") or {}).get("cluster:access") or "{}"
    try:
        rules = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return {}
    return rules if isinstance(rules, dict) else {}


def _readable(call: dict, ctx: str) -> str:
    ctx = kube.check_context(ctx)
    if core.access_of(rules_of(call), ctx) is None:
        raise ClusterError(403, f"{ctx} is not open to agents: it is off in Cluster › Connect, or the Cluster plugin is off here.")
    return ctx


def age(s: int | None) -> str:
    if s is None:
        return "?"
    for size, unit in AGE_UNITS:
        if s >= size:
            return f"{s // size}{unit}"
    return f"{s}s"


def _ask(call: dict, title: str, words: str) -> tuple[bool, str]:
    """Asks the person in keel's Inbox and waits: (yes, why). Without a way to ask, the answer is no."""
    bus = call.get("bus")
    if bus is None:
        return False, "keel cannot ask the person from here."
    from keel_engine import approvals

    broker = approvals.of(bus)
    q = broker.open("plugin", call.get("project") or "", title, words, source="plugin:cluster", thread_id=call.get("thread"),
                    session=call.get("session"))
    ans = broker.wait_blocking(q["id"])
    return ans.get("decision") == "allow", ans.get("why") or ""


def _who(call: dict) -> str:
    return f"agent:{call.get('who') or 'agent'}"


def find(call: dict, query: str, context: str, kind: str) -> str:
    rules = rules_of(call)
    ctxs = [kube.check_context(context)] if context else list(rules)
    if not ctxs:
        return "No cluster is open to agents: the person turns one on in Cluster › Connect."
    for c in ctxs:
        _readable(call, c)
    q, kind = (query or "").strip().lower(), (kind or "").strip().lower()
    out: list[str] = []
    for ctx in ctxs[:8]:
        try:
            snap = kube.snapshot(ctx)
        except ClusterError as exc:
            out.append(f"[{ctx}] not reached: {exc}")
            continue
        out.append(f"[{ctx}] access {rules.get(ctx, 'read')}, {len(snap['nodes'])} nodes, {len(snap['pods'])} pods")
        if not q and kind in ("", "problems"):
            out += [f"  problem: {p['title']} ({p['object']}): {p['line']}" for p in snap["problems"]] or ["  nothing needs attention"]
            continue
        hit = lambda *xs: not q or q in " ".join(str(x) for x in xs).lower()  # noqa: E731
        rows: list[str] = []
        if kind in ("", "pods", "pod"):
            rows += [f"  pod {p['ns']}/{p['name']} {p['status']} ready {p['ready']} restarts {p['restarts']} ip {p['ip'] or '-'} node "
                     f"{p['node'] or '-'} age {age(p['age'])}" + (f" commit {p['commit']}" if p["commit"] else "")
                     for p in snap["pods"] if hit(p["name"], p["ip"], p["node"], p["status"], p["ns"], p["app"])]
        if kind in ("", "deployments", "deployment"):
            rows += [f"  deployment {d['ns']}/{d['name']} ready {d['ready']} images {', '.join(d['images'])}" for d in snap["deployments"]
                     if hit(d["name"], d["ns"], *d["images"])]
        if kind in ("", "services", "service"):
            rows += [f"  service {s['ns']}/{s['name']} {s['type']} {s['cluster_ip']} {' '.join(s['external'])} ports {', '.join(s['ports'])}"
                     for s in snap["services"] if hit(s["name"], s["ns"], s["cluster_ip"], *s["external"])]
        if kind in ("", "nodes", "node"):
            rows += [f"  node {n['name']} {'Ready' if n['ready'] else 'NotReady'} {n['internal_ip']} {','.join(n['roles'])} pods {n['pods']}"
                     for n in snap["nodes"] if hit(n["name"], n["internal_ip"], n["external_ip"])]
        out += rows[:LIST_MAX] + ([f"  … {len(rows) - LIST_MAX} more; ask with a narrower query"] if len(rows) > LIST_MAX else [])
        if not rows:
            out.append(f'  nothing matches "{query}"')
    return "\n".join(out)


def logs(call: dict, a: dict) -> str:
    ctx = _readable(call, str(a.get("context") or ""))
    r = kube.logs(ctx, str(a.get("ns") or ""), str(a.get("name") or ""), str(a.get("container") or ""), bool(a.get("previous")),
                  int(a.get("tail") or 200))
    head = f"{a.get('name')} container {r['container']}" + (" (before its last restart)" if a.get("previous") else "")
    return "\n".join([head, r.get("note") or ""] + r["lines"]).strip() if r["lines"] or r.get("note") else head + ": the log is empty."


def describe(call: dict, a: dict) -> str:
    ctx = _readable(call, str(a.get("context") or ""))
    return kube.describe(ctx, str(a.get("kind") or ""), str(a.get("ns") or ""), str(a.get("name") or ""))["text"]


def _gate(call: dict, ctx: str, title: str, words: str, action: str, target: str) -> str | None:
    """None = go on; else why not (and it is recorded)."""
    access = core.access_of(rules_of(call), ctx)
    try:
        core.allowed(access, ctx)
    except ClusterError as exc:
        core.record(_who(call), ctx, action, target, f"refused: {exc}")
        return f"Nothing was done: {exc} {exc.hint}".strip()
    if access == "ask":
        yes, why = _ask(call, title, words)
        if not yes:
            core.record(_who(call), ctx, action, target, "the person did not approve it")
            return f"Nothing was done: the person did not approve it. {why}".strip()
    return None


def exec_(call: dict, a: dict) -> str:
    ctx = kube.check_context(str(a.get("context") or ""))
    ns, name, cmd = str(a.get("ns") or ""), str(a.get("name") or ""), str(a.get("command") or "").strip()
    kube.check_name(ns, "namespace"), kube.check_name(name, "pod")
    if not cmd:
        return "Say which command to run."
    target = core.target_of("exec", "pod", ns, name)
    stop = _gate(call, ctx, f"May an agent run a command in {name} ({ctx})?", f"In pod {ns}/{name} on {ctx}:\n\n{cmd}", f"exec: {cmd[:200]}",
                 target)
    if stop:
        return stop
    r = kube.exec_(ctx, ns, name, cmd, str(a.get("container") or ""))
    core.record(_who(call), ctx, f"exec: {cmd[:200]}", target, f"exit {r['code']}")
    return f"exit {r['code']}\n{r['output']}".rstrip()


def change(call: dict, a: dict) -> str:
    ctx = kube.check_context(str(a.get("context") or ""))
    action = str(a.get("action") or "").strip().lower()
    ns, name = str(a.get("ns") or ""), str(a.get("name") or "")
    kind = "pod" if action == "delete" else "deployment"
    replicas = a.get("replicas")
    kube.check_name(ns, "namespace"), kube.check_name(name, kind)
    if action not in ("delete", "restart", "scale"):
        return "action is delete (a pod), restart or scale (a deployment)."
    target = core.target_of(action, kind, ns, name, replicas)
    words = {"delete": f"Delete pod {ns}/{name} on {ctx}. Its controller, if it has one, starts a new one.",
             "restart": f"Restart deployment {ns}/{name} on {ctx}: its pods are replaced one by one.",
             "scale": f"Scale deployment {ns}/{name} on {ctx} to {replicas} pods."}[action]
    stop = _gate(call, ctx, f"May an agent {action} {name} ({ctx})?", words, action, target)
    if stop:
        return stop
    r = kube.change(ctx, action, kind, ns, name, replicas)
    core.record(_who(call), ctx, action, target, r["message"])
    return r["message"]


def call_tool(call: dict, tool: str, args: dict) -> str:
    try:
        if tool == "cluster_find":
            return find(call, str(args.get("query") or ""), str(args.get("context") or ""), str(args.get("kind") or ""))
        if tool == "cluster_logs":
            return logs(call, args)
        if tool == "cluster_describe":
            return describe(call, args)
        if tool == "cluster_exec":
            return exec_(call, args)
        if tool == "cluster_change":
            return change(call, args)
    except ClusterError as exc:
        return f"{exc} {exc.hint}".strip()
    return f"Cluster has no tool called {tool}."
