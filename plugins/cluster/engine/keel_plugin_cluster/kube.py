"""kubectl, with the person's own kubeconfig: keel copies no token and keeps no cluster address of its own (a GKE context can sign
in with a Google service account instead of gcloud, gke.py). Each call is one
`kubectl --context <name> ...` with a timeout; its JSON becomes the small rows the page and the tools show (the contract in
plugins/cluster/README.md). Names are checked before they reach kubectl, and kubectl gets them as separate arguments, never
through a shell."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from urllib.parse import urlparse

from keel_engine.extensions import PartError

NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")         # a namespace, pod, deployment or node name
CONTEXT = re.compile(r"^[A-Za-z0-9@._:/+=-]{1,253}$")
CONTAINER = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
HEX = re.compile(r"^[0-9a-f]{7,40}$")
DESCRIBE_KINDS = ("pod", "deployment", "service", "node", "ingress", "statefulset", "daemonset", "job", "cronjob", "replicaset")
LOOPBACK = ("127.0.0.1", "localhost", "::1", "0.0.0.0", "host.docker.internal", "kubernetes.docker.internal")
TIMEOUT = 20
OUT_MAX = 64 * 1024


class ClusterError(PartError):
    """A refusal or a failure the app answers as {error, hint} with its status (400 bad input, 403 not allowed, 404 not found,
    409 kubectl failed or the cluster was not reached)."""


def kubectl_path() -> str | None:
    return os.environ.get("KEEL_KUBECTL") or shutil.which("kubectl")


def kube_env() -> dict:
    """kubectl's environment: its discovery cache in keel's data when ~/.kube is not writable (keel2 mounts it read-only)."""
    env = dict(os.environ)
    if "KUBECACHEDIR" not in env and env.get("KEEL_DATA") and not os.access(os.path.expanduser("~/.kube"), os.W_OK):
        env["KUBECACHEDIR"] = os.path.join(env["KEEL_DATA"], "cluster", "cache")
    return env


def kubeconfig() -> str | None:
    """The kubeconfig kubectl reads: KUBECONFIG (its first file), else ~/.kube/config when it exists."""
    env = os.environ.get("KUBECONFIG")
    if env:
        return env.split(os.pathsep)[0]
    p = os.path.expanduser("~/.kube/config")
    return p if os.path.exists(p) else None


def in_docker() -> bool:
    return os.path.exists("/.dockerenv") or os.environ.get("KEEL_EDITION") is not None and os.path.isdir("/opt/keel-v2")


def check_context(ctx: str) -> str:
    ctx = (ctx or "").strip()
    if not ctx:
        raise ClusterError(400, "Say which cluster (its kubeconfig context).")
    if not CONTEXT.match(ctx):
        raise ClusterError(400, f"{ctx!r} is not a kubeconfig context name.")
    return ctx


def check_name(value: str, what: str) -> str:
    value = (value or "").strip()
    if not value or not NAME.match(value):
        raise ClusterError(400, f"{value!r} is not a {what} name.", "Kubernetes names are lower case letters, digits, '-' and '.'.")
    return value


def check_container(value: str) -> str:
    value = (value or "").strip()
    if value and not CONTAINER.match(value):
        raise ClusterError(400, f"{value!r} is not a container name.")
    return value


def run(args: list[str], context: str | None = None, timeout: int = TIMEOUT) -> str:
    """kubectl's output; a failure or a timeout is a ClusterError(409) with kubectl's own words."""
    exe = kubectl_path()
    if not exe:
        raise ClusterError(409, "kubectl is not installed where keel runs.",
                           "Install kubectl (https://kubernetes.io/docs/tasks/tools/), or use keel's Docker image, which has it.")
    from . import gke

    cmd = [exe] + (["--context", context] if context else []) + args
    env = gke.env_for(auth_of(context), kube_env()) if context else kube_env()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        raise ClusterError(409, f"kubectl took longer than {timeout} s.", "Is the cluster reachable from where keel runs?") from None
    if p.returncode != 0:
        raise ClusterError(409, error_line(p.stderr or p.stdout or "", p.returncode), hint_for(context, p.stderr or ""))
    return p.stdout


ERROR_START = re.compile(r"^(error:|Error from server|Unable to connect|The connection to the server)")


def error_line(text: str, code: int) -> str:
    """kubectl's own error line: its last one that starts like an error (after it may come an explanation and a link), else its last line."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    pick = next((ln for ln in reversed(lines) if ERROR_START.match(ln)), lines[-1] if lines else f"kubectl stopped with code {code}.")
    return pick[:600]


def run_json(args: list[str], context: str | None = None, timeout: int = TIMEOUT) -> dict:
    out = run(args + ["-o", "json"], context, timeout)
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError:
        raise ClusterError(409, "kubectl did not answer JSON.") from None


def auth_of(context: str | None) -> str:
    """How the context's user signs in (gke.auth_of); kubeconfig when keel does not know the context."""
    return next((c.get("auth", "kubeconfig") for c in _contexts_cached() if c["name"] == context), "kubeconfig") if context else "kubeconfig"


def hint_for(context: str | None, err: str) -> str:
    from . import gke

    low = err.lower()
    google = gke.hint(auth_of(context), err)
    if google:
        return google
    if "forbidden" in low:
        return "Your kubeconfig user may not do this. keel can only do what kubectl can do with it."
    if "connection refused" in low or "no such host" in low or "i/o timeout" in low or "unable to connect" in low:
        server = next((c["server"] for c in _contexts_cached() if c["name"] == context), "")
        if in_docker() and is_local(server):
            return docker_loopback_hint()
        return "keel could not reach the cluster's API server. Is it running, and is a VPN needed?"
    if "executable" in low and "not found" in low:
        return "Your kubeconfig signs in with a helper program (exec) that is not installed where keel runs."
    return ""


def docker_loopback_hint() -> str:
    return ("This cluster listens on 127.0.0.1 of your computer, and keel runs in Docker, where 127.0.0.1 is keel's own container. "
            "For kind: `docker network connect kind <keel's container>`, then use the kubeconfig from `kind get kubeconfig --internal`.")


def is_local(server: str) -> bool:
    try:
        host = (urlparse(server).hostname or "").lower()
    except ValueError:
        return False
    return host in LOOPBACK or host.endswith(".local") or host.startswith("127.") or host.endswith("-control-plane")


_CTX_CACHE: tuple[float, list[dict]] = (0.0, [])


def _contexts_cached() -> list[dict]:
    at, rows = _CTX_CACHE
    if time.time() - at < 30:
        return rows
    try:
        return contexts()["contexts"]
    except ClusterError:
        return []


def contexts() -> dict:
    """The kubeconfig's contexts: names, cluster and server only (kubectl's `config view` leaves out the secrets)."""
    global _CTX_CACHE
    exe = kubectl_path()
    out = {"kubectl": bool(exe), "kubectl_version": None, "kubeconfig": kubeconfig(), "in_docker": in_docker(), "contexts": []}
    if not exe:
        return out
    try:
        v = run_json(["version", "--client"], timeout=8)
        out["kubectl_version"] = (v.get("clientVersion") or {}).get("gitVersion")
    except ClusterError:
        pass
    from . import gke

    cfg = run_json(["config", "view"], timeout=8)
    servers = {c.get("name"): (c.get("cluster") or {}).get("server") or "" for c in cfg.get("clusters") or []}
    users = {u.get("name"): u.get("user") or {} for u in cfg.get("users") or []}
    current = cfg.get("current-context") or ""
    rows = []
    for c in cfg.get("contexts") or []:
        name, ctx = c.get("name") or "", c.get("context") or {}
        server = servers.get(ctx.get("cluster"), "")
        rows.append({"name": name, "cluster": ctx.get("cluster") or "", "server": server, "namespace": ctx.get("namespace") or "",
                     "current": name == current, "local": is_local(server), "auth": gke.auth_of(users.get(ctx.get("user")) or {})})
    out["contexts"] = rows
    _CTX_CACHE = (time.time(), rows)
    return out


def known(ctx: str) -> dict:
    row = next((c for c in contexts()["contexts"] if c["name"] == ctx), None)
    if row is None:
        raise ClusterError(404, f"Your kubeconfig has no context {ctx}.", "Cluster › Connect lists the contexts it has.")
    return row


def test(ctx: str) -> dict:
    """Can keel reach it? Never raises for an unreachable cluster: the page shows the reason."""
    t = time.monotonic()
    try:
        ctx = check_context(ctx)
        known(ctx)
        v = run_json(["version", "--request-timeout=6s"], ctx, timeout=10)
        note = None
        try:
            count = len(run_json(["get", "nodes", "--request-timeout=6s"], ctx, timeout=10).get("items") or [])
        except ClusterError as exc:
            if "forbidden" not in str(exc).lower():
                raise
            count, note = None, "Reached, but this user may not list the nodes."
    except ClusterError as exc:
        return {"ok": False, "ms": int((time.monotonic() - t) * 1000), "error": str(exc), "hint": exc.hint}
    from . import gke

    return {"ok": True, "ms": int((time.monotonic() - t) * 1000), "version": (v.get("serverVersion") or {}).get("gitVersion"),
            "nodes": count, "note": note, "as": gke.email() if auth_of(ctx) == "gke" else None}


# ---------- rows ----------

def _age(stamp: str | None, now: float) -> int | None:
    if not stamp:
        return None
    try:
        return max(0, int(now - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()))
    except ValueError:
        return None


def _meta(o: dict) -> dict:
    return o.get("metadata") or {}


def commit_of(meta: dict, images: list[str]) -> str | None:
    """The commit an object runs: keel.dev/commit, org.opencontainers.image.revision, or an image tag that is a commit."""
    for src in (meta.get("annotations") or {}, meta.get("labels") or {}):
        for key in ("keel.dev/commit", "org.opencontainers.image.revision"):
            v = str(src.get(key) or "").strip().lower()
            if HEX.match(v):
                return v
    for img in images:
        tag = img.rsplit("@", 1)[0].rsplit("/", 1)[-1].partition(":")[2].lower()
        tag = tag.removeprefix("sha-").removeprefix("git-")
        if HEX.match(tag):
            return tag
    return None


def _app(meta: dict) -> str:
    labels = meta.get("labels") or {}
    return labels.get("app.kubernetes.io/name") or labels.get("app") or labels.get("k8s-app") or ""


def pod_row(o: dict, now: float) -> dict:
    meta, spec, st = _meta(o), o.get("spec") or {}, o.get("status") or {}
    cs = st.get("containerStatuses") or []
    ready = sum(1 for c in cs if c.get("ready"))
    total = len(spec.get("containers") or []) or len(cs)
    status = st.get("reason") or st.get("phase") or "Unknown"
    for c in cs:
        state = c.get("state") or {}
        if "waiting" in state and state["waiting"].get("reason"):
            status = state["waiting"]["reason"]
            break
        if "terminated" in state and state["terminated"].get("reason") and not c.get("ready"):
            status = state["terminated"]["reason"]
    if status == "Succeeded":
        status = "Completed"
    if meta.get("deletionTimestamp"):
        status = "Terminating"
    images = [c.get("image") or "" for c in spec.get("containers") or []]
    owner = next((f"{r.get('kind', '').lower()}/{r.get('name')}" for r in meta.get("ownerReferences") or []), "")
    return {"name": meta.get("name"), "ns": meta.get("namespace"), "ready": f"{ready}/{total}", "status": status,
            "restarts": sum(int(c.get("restartCount") or 0) for c in cs), "ip": st.get("podIP") or "",
            "node": spec.get("nodeName") or "", "age": _age(meta.get("creationTimestamp"), now), "app": _app(meta),
            "containers": [c.get("name") for c in spec.get("containers") or []], "images": images,
            "commit": commit_of(meta, images), "owner": owner}


def deploy_row(o: dict, now: float) -> dict:
    meta, spec, st = _meta(o), o.get("spec") or {}, o.get("status") or {}
    tmeta = (spec.get("template") or {}).get("metadata") or {}
    images = [c.get("image") or "" for c in ((spec.get("template") or {}).get("spec") or {}).get("containers") or []]
    desired = int(spec.get("replicas") if spec.get("replicas") is not None else 1)
    merged = {"annotations": {**(meta.get("annotations") or {}), **(tmeta.get("annotations") or {})},
              "labels": {**(meta.get("labels") or {}), **(tmeta.get("labels") or {})}}
    return {"name": meta.get("name"), "ns": meta.get("namespace"), "ready": f"{int(st.get('readyReplicas') or 0)}/{desired}",
            "desired": desired, "available": int(st.get("availableReplicas") or 0), "updated": int(st.get("updatedReplicas") or 0),
            "images": images, "commit": commit_of(merged, images), "age": _age(meta.get("creationTimestamp"), now)}


def service_row(o: dict) -> dict:
    meta, spec, st = _meta(o), o.get("spec") or {}, o.get("status") or {}
    ext = [i.get("ip") or i.get("hostname") for i in ((st.get("loadBalancer") or {}).get("ingress") or [])]
    ext += spec.get("externalIPs") or []
    ports = []
    for p in spec.get("ports") or []:
        proto = p.get("protocol") or "TCP"
        if p.get("nodePort"):
            ports.append(f"{p.get('port')}:{p.get('nodePort')}/{proto}")
        elif p.get("targetPort") not in (None, p.get("port")):
            ports.append(f"{p.get('port')}→{p.get('targetPort')}/{proto}")
        else:
            ports.append(f"{p.get('port')}/{proto}")
    return {"name": meta.get("name"), "ns": meta.get("namespace"), "type": spec.get("type") or "ClusterIP",
            "cluster_ip": spec.get("clusterIP") or "", "external": [e for e in ext if e], "ports": ports,
            "selector": spec.get("selector") or {}}


def _qty_cpu(v: str) -> float:
    v = str(v or "0")
    return float(v[:-1]) / 1000 if v.endswith("m") else float(v or 0)


def _qty_mem(v: str) -> float:
    v = str(v or "0")
    units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 1e3, "M": 1e6, "G": 1e9}
    for u, f in units.items():
        if v.endswith(u):
            return float(v[: -len(u)]) * f
    try:
        return float(v)
    except ValueError:
        return 0.0


def node_row(o: dict) -> dict:
    meta, st = _meta(o), o.get("status") or {}
    addr = {a.get("type"): a.get("address") for a in st.get("addresses") or []}
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in st.get("conditions") or [])
    roles = sorted(k.split("/", 1)[1] for k in (meta.get("labels") or {}) if k.startswith("node-role.kubernetes.io/")) or ["worker"]
    alloc = st.get("allocatable") or st.get("capacity") or {}
    return {"name": meta.get("name"), "ready": ready, "roles": roles, "internal_ip": addr.get("InternalIP") or "",
            "external_ip": addr.get("ExternalIP") or "", "version": (st.get("nodeInfo") or {}).get("kubeletVersion") or "",
            "cpu": alloc.get("cpu") or "", "memory": alloc.get("memory") or "", "cpu_pct": None, "mem_pct": None, "pods": 0}


def event_row(o: dict, now: float) -> dict:
    meta, inv = _meta(o), o.get("involvedObject") or o.get("regarding") or {}
    stamp = o.get("lastTimestamp") or o.get("eventTime") or (o.get("series") or {}).get("lastObservedTime") or meta.get("creationTimestamp")
    return {"type": o.get("type") or "Normal", "reason": o.get("reason") or "", "object": f"{(inv.get('kind') or '').lower()}/{inv.get('name') or ''}",
            "ns": meta.get("namespace") or inv.get("namespace") or "", "message": (o.get("message") or o.get("note") or "").strip(),
            "age": _age(stamp, now), "count": int(o.get("count") or (o.get("series") or {}).get("count") or 1)}


def ingress_row(o: dict) -> dict:
    meta, spec, st = _meta(o), o.get("spec") or {}, o.get("status") or {}
    hosts = []
    for rule in spec.get("rules") or []:
        for path in ((rule.get("http") or {}).get("paths") or []):
            svc = ((path.get("backend") or {}).get("service") or {})
            port = (svc.get("port") or {}).get("number") or (svc.get("port") or {}).get("name") or ""
            hosts.append({"host": rule.get("host") or "*", "path": path.get("path") or "/", "service": f"{svc.get('name')}:{port}"})
    address = [i.get("ip") or i.get("hostname") for i in ((st.get("loadBalancer") or {}).get("ingress") or [])]
    return {"name": meta.get("name"), "ns": meta.get("namespace"), "hosts": hosts, "address": [a for a in address if a]}


BAD_WAITING = {"CrashLoopBackOff": "crash", "ImagePullBackOff": "image", "ErrImagePull": "image", "CreateContainerConfigError": "failed",
               "InvalidImageName": "image", "RunContainerError": "failed"}


def problems(pods: list[dict], deploys: list[dict], nodes: list[dict], events: list[dict]) -> list[dict]:
    """What needs a person, most urgent first: crashing or failing pods, pods that cannot start, nodes that are not ready."""
    out = []
    for n in nodes:
        if not n["ready"]:
            out.append({"kind": "node", "object": f"node//{n['name']}", "ns": "", "name": n["name"], "title": f"node {n['name']} is not ready",
                        "line": "Pods on it may stop or move."})
    for p in pods:
        kind = BAD_WAITING.get(p["status"]) or ("failed" if p["status"] in ("Error", "Failed", "OOMKilled") else None)
        if kind is None and p["status"] == "Pending" and (p["age"] or 0) > 120:
            kind = "waiting"
        if kind is None:
            continue
        why = next((e["message"] for e in events if e["type"] == "Warning" and e["object"] == f"pod/{p['name']}"), "")
        title = {"crash": f"{p['name']} keeps restarting ({p['restarts']} times)", "image": f"{p['name']} cannot pull its image",
                 "waiting": f"{p['name']} cannot start", "failed": f"{p['name']} failed ({p['status']})"}[kind]
        out.append({"kind": kind, "object": f"pod/{p['ns']}/{p['name']}", "ns": p["ns"], "name": p["name"], "title": title,
                    "line": why[:300] or f"Status {p['status']}, ready {p['ready']}."})
    for d in deploys:
        if d["desired"] and d["available"] == 0:
            out.append({"kind": "deploy", "object": f"deployment/{d['ns']}/{d['name']}", "ns": d["ns"], "name": d["name"],
                        "title": f"deployment {d['name']} has no pod ready", "line": f"Ready {d['ready']}."})
    order = {"node": 0, "crash": 1, "failed": 2, "image": 3, "deploy": 4, "waiting": 5}
    return sorted(out, key=lambda x: order.get(x["kind"], 9))


def _top_nodes(ctx: str) -> dict[str, tuple[int, int]]:
    """CPU and memory use per node in %, from metrics-server; empty when the cluster has none."""
    try:
        out = run(["top", "nodes", "--no-headers", "--request-timeout=4s"], ctx, timeout=6)
    except ClusterError:
        return {}
    use = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[2].endswith("%") and parts[4].endswith("%"):
            try:
                use[parts[0]] = (int(parts[2][:-1]), int(parts[4][:-1]))
            except ValueError:
                pass
    return use


def snapshot(ctx: str, ns: str = "") -> dict:
    """Everything the Workloads and Overview tabs show, from one kubectl call (plus metrics when the cluster has them)."""
    ctx = check_context(ctx)
    ns = check_name(ns, "namespace") if ns else ""
    now = time.time()
    warnings: list[str] = []
    scope = ["-n", ns] if ns else ["--all-namespaces"]
    kinds = "pods,deployments,services,events,ingresses"
    try:
        data = run_json(["get", kinds, "--request-timeout=10s"] + scope, ctx)
    except ClusterError as exc:
        if "forbidden" not in str(exc).lower() or ns:
            raise
        mine = known(ctx).get("namespace") or "default"
        warnings.append(f"Your kubeconfig user may not list every namespace, so keel shows only {mine}.")
        data = run_json(["get", kinds, "--request-timeout=10s", "-n", mine], ctx)
    try:
        nodes_raw = run_json(["get", "nodes", "--request-timeout=8s"], ctx).get("items") or []
    except ClusterError as exc:
        nodes_raw = []
        warnings.append(f"keel cannot list the nodes: {exc}")
    pods, deploys, svcs, events, ings = [], [], [], [], []
    for o in data.get("items") or []:
        kind = o.get("kind")
        if kind == "Pod":
            pods.append(pod_row(o, now))
        elif kind == "Deployment":
            deploys.append(deploy_row(o, now))
        elif kind == "Service":
            svcs.append(service_row(o))
        elif kind == "Event":
            events.append(event_row(o, now))
        elif kind == "Ingress":
            ings.append(ingress_row(o))
    events.sort(key=lambda e: e["age"] if e["age"] is not None else 10**9)
    events = events[:200]
    nodes = [node_row(o) for o in nodes_raw]
    per_node: dict[str, int] = {}
    for p in pods:
        if p["node"]:
            per_node[p["node"]] = per_node.get(p["node"], 0) + 1
    use = _top_nodes(ctx) if nodes else {}
    for n in nodes:
        n["pods"] = per_node.get(n["name"], 0)
        if n["name"] in use:
            n["cpu_pct"], n["mem_pct"] = use[n["name"]]
    names = sorted({p["ns"] for p in pods} | {d["ns"] for d in deploys} | {s["ns"] for s in svcs})
    pods.sort(key=lambda p: (p["ns"], p["name"]))
    deploys.sort(key=lambda d: (d["ns"], d["name"]))
    svcs.sort(key=lambda s: (s["ns"], s["name"]))
    return {"context": ctx, "at": int(now), "namespaces": names, "pods": pods, "deployments": deploys, "services": svcs,
            "nodes": nodes, "events": events, "ingresses": ings, "problems": problems(pods, deploys, nodes, events),
            "metrics": bool(use), "warnings": warnings}


def deployments(ctx: str) -> list[dict]:
    """Only the deployments, for "what runs where" (lighter than a whole snapshot)."""
    now = time.time()
    data = run_json(["get", "deployments", "--all-namespaces", "--request-timeout=10s"], check_context(ctx))
    return [deploy_row(o, now) for o in data.get("items") or []]


def logs(ctx: str, ns: str, name: str, container: str = "", previous: bool = False, tail: int = 300) -> dict:
    ctx, ns, name, container = check_context(ctx), check_name(ns, "namespace"), check_name(name, "pod"), check_container(container)
    tail = min(max(int(tail or 300), 1), 5000)
    pod = run_json(["get", "pod", name, "-n", ns, "--request-timeout=8s"], ctx)
    containers = [c.get("name") for c in (pod.get("spec") or {}).get("containers") or []]
    container = container or (containers[0] if containers else "")
    if container and container not in containers:
        raise ClusterError(404, f"{name} has no container {container}.", f"It has: {', '.join(containers)}.")
    args = ["logs", name, "-n", ns, f"--tail={tail}", "--timestamps", "--request-timeout=10s"] + (["-c", container] if container else [])
    if previous:
        args.append("--previous")
    try:
        out = run(args, ctx)
    except ClusterError as exc:
        if previous and "not found" in str(exc).lower():
            return {"lines": [], "container": container, "containers": containers, "note": "This container has not restarted, so there is no earlier log."}
        if "waiting to start" in str(exc).lower() or "ContainerCreating" in str(exc):
            return {"lines": [], "container": container, "containers": containers, "note": "The container has not started yet, so there is no log."}
        raise
    return {"lines": out.splitlines()[-tail:], "container": container, "containers": containers}


def describe(ctx: str, kind: str, ns: str, name: str) -> dict:
    ctx, kind = check_context(ctx), (kind or "").strip().lower()
    if kind not in DESCRIBE_KINDS:
        raise ClusterError(400, f"keel describes only {', '.join(DESCRIBE_KINDS)}.", "Secrets and ConfigMaps are left out: they hold values.")
    name = check_name(name, kind)
    args = ["describe", kind, name, "--request-timeout=10s"] + ([] if kind == "node" else ["-n", check_name(ns, "namespace")])
    return {"text": run(args, ctx)[:OUT_MAX]}


def exec_(ctx: str, ns: str, name: str, command: str, container: str = "") -> dict:
    ctx, ns, name, container = check_context(ctx), check_name(ns, "namespace"), check_name(name, "pod"), check_container(container)
    command = (command or "").strip()
    if not command:
        raise ClusterError(400, "Say which command to run.")
    if len(command) > 2000:
        raise ClusterError(400, "The command is too long (2000 characters at most).")
    exe = kubectl_path()
    if not exe:
        raise ClusterError(409, "kubectl is not installed where keel runs.")
    args = [exe, "--context", ctx, "exec", name, "-n", ns] + (["-c", container] if container else []) + ["--", "sh", "-c", command]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, env=kube_env())
    except subprocess.TimeoutExpired:
        return {"output": "(stopped after 30 s)", "code": 124}
    out = (p.stdout or "") + (p.stderr or "")
    if len(out) > OUT_MAX:
        out = out[:OUT_MAX] + "\n(cut at 64 KB)"
    return {"output": out, "code": p.returncode}


def change(ctx: str, action: str, kind: str, ns: str, name: str, replicas: int | None = None) -> dict:
    ctx, ns = check_context(ctx), check_name(ns, "namespace")
    action, kind = (action or "").strip().lower(), (kind or "").strip().lower()
    if action == "delete" and kind in ("", "pod"):
        name = check_name(name, "pod")
        run(["delete", "pod", name, "-n", ns, "--wait=false", "--request-timeout=10s"], ctx)
        return {"done": True, "message": f"pod {name} is being deleted; its controller, if it has one, starts a new one."}
    if action == "restart" and kind in ("", "deployment"):
        name = check_name(name, "deployment")
        run(["rollout", "restart", f"deployment/{name}", "-n", ns, "--request-timeout=10s"], ctx)
        return {"done": True, "message": f"deployment {name} restarts its pods one by one."}
    if action == "scale" and kind in ("", "deployment"):
        name = check_name(name, "deployment")
        try:
            n = int(replicas if replicas is not None else -1)
        except (TypeError, ValueError):
            n = -1
        if not 0 <= n <= 100:
            raise ClusterError(400, "Say how many pods, from 0 to 100.")
        run(["scale", f"deployment/{name}", f"--replicas={n}", "-n", ns, "--request-timeout=10s"], ctx)
        return {"done": True, "message": f"deployment {name} now asks for {n} pod{'s' if n != 1 else ''}."}
    raise ClusterError(400, "keel can delete a pod, or restart or scale a deployment.", "action: delete (pod), restart or scale (deployment).")
