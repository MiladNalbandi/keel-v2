"""Eval sets for keel's quality runs (content/evals/<name>/eval.yml, content/evals/README.md).

An eval set is a tiny project and the cases keel runs on it. The api runs each case as a flow in run mode auto with a
chosen model and scores the run (api QualityService); the engine only lists the sets and prepares a case's project:

    load_all   every eval set with its cases, and the problems of a broken one (never used)
    prepare    a fresh git repository with the set's project in it, committed once on main
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import yaml

from .. import config
from ..demo import create_demo

CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class EvalError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def folder() -> Path:
    return config.content_dir() / "evals"


def _case(raw: dict, problems: list[str]) -> dict | None:
    cid = str(raw.get("id") or "")
    if not CASE_ID.match(cid):
        problems.append(f"case id {cid!r}: use lower-case letters, digits and dashes")
        return None
    request = str(raw.get("request") or "").strip()
    if not request:
        problems.append(f"case {cid}: no request")
        return None
    acs = [{"id": str(a.get("id")), "layer": str(a.get("layer") or "API"), "title": str(a.get("title") or "")}
           for a in raw.get("acs") or [] if isinstance(a, dict) and a.get("id")]
    # no cap of its own: the api gives the case twice the flow's estimate
    cap = int(raw["cap_tokens"]) if raw.get("cap_tokens") else None
    return {"id": cid, "title": str(raw.get("title") or cid)[:120], "workflow": str(raw.get("workflow") or "change"),
            "request": request, "acs": acs, "cap_tokens": cap}


def load_all() -> list[dict]:
    """[{name, description, project, cases, problems}] for every content/evals/<name>/eval.yml, by name."""
    out = []
    base = folder()
    for f in sorted(base.glob("*/eval.yml")) if base.is_dir() else []:
        problems: list[str] = []
        try:
            data = yaml.safe_load(f.read_text()) or {}
        except yaml.YAMLError as exc:
            out.append({"name": f.parent.name, "description": "", "project": "", "cases": [], "problems": [f"eval.yml: {exc}"]})
            continue
        project = str(data.get("project") or "project")
        if project != "demo" and not (f.parent / project).is_dir():
            problems.append(f"no project folder {project}/")
        cases = [c for c in (_case(x, problems) for x in data.get("cases") or [] if isinstance(x, dict)) if c]
        if not cases:
            problems.append("no case")
        out.append({"name": f.parent.name, "description": str(data.get("description") or ""), "project": project,
                    "cases": cases, "problems": problems})
    return out


def find(name: str) -> dict:
    for s in load_all():
        if s["name"] == name:
            if s["problems"]:
                raise EvalError(400, f"The eval set {name} is broken: {'; '.join(s['problems'])}.")
            return s
    raise EvalError(404, f"No eval set {name}.")


def prepare(name: str, dest: str) -> dict:
    """The set's project in a new git repository at dest (which must not exist yet): {path, set, cases}."""
    s = find(name)
    target = Path(dest)
    if target.exists():
        raise EvalError(409, f"{dest} exists already.")
    if s["project"] == "demo":
        create_demo(target)
    else:
        shutil.copytree(folder() / name / s["project"], target, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        ident = ["-c", "user.name=keelbot", "-c", "user.email=keel.dev.bot@gmail.com"]
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, check=True)
        subprocess.run(["git", "add", "-A"], cwd=target, check=True)
        subprocess.run(["git", *ident, "commit", "-q", "-m", f"chore: eval project {name}"], cwd=target, check=True)
    return {"path": str(target), "set": name, "cases": s["cases"]}
