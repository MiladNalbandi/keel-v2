"""The Cluster plugin's rules and its record: who may change what (a context's access), the history of every command and change,
and "what runs where" (each deployment's commit against the project's git base)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import kube
from .kube import ClusterError

ACCESS = ("read", "ask", "free")
HISTORY_MAX = 200
_LOCK = threading.Lock()
PR = re.compile(r"\(#(\d+)\)\s*$|Merge pull request #(\d+)|Merge branch .* into .*\(!(\d+)\)|See merge request .*!(\d+)")


def history_file() -> Path:
    base = os.environ.get("KEEL_DATA")
    d = Path(base) / "cluster" if base else Path.home() / ".keel" / "cluster"
    return d / "history.jsonl"


def record(who: str, ctx: str, action: str, target: str, result: str) -> None:
    """One line per command or change, also when it was refused or failed."""
    p = history_file()
    at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    row = {"at": at, "who": who or "person", "context": ctx, "action": action, "target": target, "result": result[:500]}
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            if len(lines) > HISTORY_MAX * 5:
                p.write_text("\n".join(lines[-HISTORY_MAX:]) + "\n", encoding="utf-8")
        except OSError:
            pass


def history() -> dict:
    p = history_file()
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"items": []}
    items = []
    for line in reversed(lines[-HISTORY_MAX:]):
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"items": items}


def access_of(rules: dict | None, ctx: str) -> str | None:
    """A context's access from the rules the api sends ({context: read|ask|free}); None = the context is off."""
    if not isinstance(rules, dict) or ctx not in rules:
        return None
    a = rules.get(ctx)
    return a if a in ACCESS else "read"


def allowed(access: str | None, ctx: str) -> None:
    if access is None:
        raise ClusterError(403, f"{ctx} is turned off in Cluster › Connect.")
    if access == "read":
        raise ClusterError(403, f"{ctx} is read only in keel.", "Change it in Cluster › Connect.")


def target_of(action: str, kind: str, ns: str, name: str, replicas=None) -> str:
    t = f"{kind or ('pod' if action in ('delete', 'exec') else 'deployment')}/{ns}/{name}"
    return t + (f" → {replicas}" if action == "scale" else "")


def person_exec(ctx: str, ns: str, name: str, command: str, container: str, access: str | None, who: str = "person") -> dict:
    target = target_of("exec", "pod", ns, name)
    try:
        allowed(access, ctx)
        out = kube.exec_(ctx, ns, name, command, container)
    except ClusterError as exc:
        record(who, ctx, f"exec: {command[:200]}", target, f"refused or failed: {exc}")
        raise
    record(who, ctx, f"exec: {command[:200]}", target, f"exit {out['code']}")
    return out


def person_change(ctx: str, action: str, kind: str, ns: str, name: str, replicas, access: str | None, who: str = "person") -> dict:
    target = target_of(action, kind, ns, name, replicas)
    try:
        allowed(access, ctx)
        out = kube.change(ctx, action, kind, ns, name, replicas)
    except ClusterError as exc:
        record(who, ctx, action, target, f"refused or failed: {exc}")
        raise
    record(who, ctx, action, target, out["message"])
    return out


# ---------- what runs where ----------

def _git(root: str, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def web_url(remote: str) -> str | None:
    """https://host/owner/repo for a GitHub or GitLab remote (ssh or https), else None."""
    r = (remote or "").strip()
    m = re.match(r"^(?:git@|ssh://git@)([^:/]+)[:/](.+?)(?:\.git)?/?$", r) or re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", r)
    if not m:
        return None
    return f"https://{m.group(1)}/{m.group(2)}"


def repo_of(root: str) -> dict | None:
    if not root or not os.path.isdir(root) or _git(root, "rev-parse", "--git-dir") is None:
        return None
    remote = _git(root, "remote", "get-url", "origin") or ""
    base = None
    head_ref = _git(root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    for cand in ([head_ref] if head_ref else []) + ["origin/main", "origin/master", "main", "master"]:
        if _git(root, "rev-parse", "--verify", "-q", f"{cand}^{{commit}}"):
            base = cand
            break
    head = _git(root, "rev-parse", f"{base}^{{commit}}") if base else None
    return {"remote": remote, "web": web_url(remote), "base": base, "head": head}


def pr_of(subject: str) -> str | None:
    m = PR.search(subject or "")
    if not m:
        return None
    n = next(g for g in m.groups() if g)
    return ("!" if "!" in m.group(0) else "#") + n


def commit_state(root: str, repo: dict, commit: str | None) -> dict:
    if not commit or not repo or not repo.get("head"):
        return {"commit": commit, "state": "unknown", "behind": 0, "subject": None, "pr": None}
    full = _git(root, "rev-parse", "--verify", "-q", f"{commit}^{{commit}}")
    if not full:
        return {"commit": commit, "state": "unknown", "behind": 0, "subject": None, "pr": None}
    subject = _git(root, "log", "-1", "--format=%s", full) or ""
    head = repo["head"]
    if full == head:
        state, behind = "same", 0
    elif _git(root, "merge-base", "--is-ancestor", full, head) is not None:
        state, behind = "behind", int(_git(root, "rev-list", "--count", f"{full}..{head}") or 0)
    elif _git(root, "merge-base", "--is-ancestor", head, full) is not None:
        state, behind = "ahead", 0
    else:
        state, behind = "diverged", 0
    return {"commit": commit, "state": state, "behind": behind, "subject": subject[:200], "pr": pr_of(subject)}


def runs(root: str, contexts: list[str]) -> dict:
    """Each deployment's commit on each cluster, against the project's base on its remote (origin/HEAD)."""
    repo = repo_of(root)
    apps: dict[tuple[str, str], dict] = {}
    errors: dict[str, str] = {}
    for ctx in contexts[:12]:
        try:
            deploys = kube.deployments(ctx)
        except ClusterError as exc:
            errors[ctx] = str(exc)
            continue
        for d in deploys:
            if d["ns"] in ("kube-system", "kube-public", "kube-node-lease", "local-path-storage"):
                continue
            row = apps.setdefault((d["name"], d["ns"]), {"name": d["name"], "ns": d["ns"], "by": {}})
            row["by"][ctx] = commit_state(root, repo, d["commit"]) if repo else {"commit": d["commit"], "state": "unknown", "behind": 0,
                                                                                    "subject": None, "pr": None}
    return {"repo": repo, "contexts": contexts, "apps": sorted(apps.values(), key=lambda a: (a["ns"], a["name"])), "errors": errors}
