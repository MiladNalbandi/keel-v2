"""Stack packs: which of keel's stacks a project uses (the engine's twin of the api's StackService).

    defs(root)       every stack keel knows: content/stacks/*.yml, content/packs/<name>/stack.yml, then the project's
                     own <root>/.keel/stacks (a project stack with the same name wins)
    matched(root)    the ones this project uses: a project stack always, a shipped one when its `detect` block matches
                     (exclude_files first, then files / package_deps / extensions, in the root and one folder down)

Detection walks the tree for `extensions`, so the answer is kept for a minute per root.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from .. import config

SKIP = {"node_modules", "build", ".git", ".venv", "venv", "dist", "target", ".gradle", "vendor", "__pycache__"}
TTL = 60.0
_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _read(f: Path, source: str) -> dict | None:
    try:
        doc = yaml.safe_load(f.read_text())
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(doc, dict) or not doc.get("name"):
        return None
    return {**doc, "_source": source, "_file": str(f)}


def shipped() -> list[dict]:
    """keel's own stacks (source "keel") and packs (source "keel pack")."""
    base = config.content_dir()
    out = [d for f in sorted((base / "stacks").glob("*.yml")) if (d := _read(f, "keel"))]
    packs = base / "packs"
    if packs.is_dir():
        out += [d for p in sorted(packs.iterdir()) if (p / "stack.yml").is_file() and (d := _read(p / "stack.yml", "keel pack"))]
    return out


def project_defs(root: str) -> list[dict]:
    """<root>/.keel/stacks: a <name>.yml, a <dir>/stack.yml or yml files in <dir> (what installing a pack leaves)."""
    d = Path(root) / ".keel" / "stacks"
    if not d.is_dir():
        return []
    files = sorted(f for f in d.rglob("*.yml") if f.is_file() and len(f.relative_to(d).parts) <= 3)
    seen: set[str] = set()
    out = []
    for f in files:
        doc = _read(f, "project")
        if doc and doc["name"] not in seen:
            seen.add(doc["name"])
            out.append(doc)
    return out


def defs(root: str) -> list[dict]:
    project = project_defs(root)
    names = {d["name"] for d in project}
    return [d for d in shipped() if d["name"] not in names] + project


def _strings(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def _dirs(root: Path) -> list[Path]:
    try:
        kids = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP)
    except OSError:
        kids = []
    return [root, *kids]


def _package_deps(d: Path) -> set[str]:
    f = d / "package.json"
    if not f.is_file():
        return set()
    try:
        doc = json.loads(f.read_text())
    except (OSError, ValueError):
        return set()
    return {k for key in ("dependencies", "devDependencies") for k in (doc.get(key) or {})}


def _has_extension(d: Path, exts: list[str], budget: list[int]) -> bool:
    stack = [d]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:
            continue
        for e in entries:
            budget[0] -= 1
            if budget[0] <= 0:
                return False
            if e.is_dir():
                if e.name not in SKIP and not e.name.startswith(".") and len(e.relative_to(d).parts) < 6:
                    stack.append(e)
            elif any(e.name.endswith(x) for x in exts):
                return True
    return False


def detects(detect: dict, root: str) -> bool:
    """The api's StackService.detects: `exclude_files` first; then any of files / package_deps / extensions."""
    detect = detect if isinstance(detect, dict) else {}
    exclude, wanted = _strings(detect.get("exclude_files")), _strings(detect.get("files"))
    deps, exts = _strings(detect.get("package_deps")), _strings(detect.get("extensions"))
    if not (wanted or deps or exts):
        return False
    budget = [20_000]
    for d in _dirs(Path(root)):
        if any((d / x).exists() for x in exclude):
            continue
        if any((d / x).exists() for x in wanted):
            return True
        if deps and _package_deps(d) & set(deps):
            return True
        if exts and _has_extension(d, exts, budget):
            return True
    return False


def matched(root: str) -> list[dict]:
    """The stacks this project uses, keel's first, then the project's own."""
    key = str(Path(root).resolve())
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    out = [d for d in defs(root) if d["_source"] == "project" or detects(d.get("detect") or {}, root)]
    _CACHE[key] = (time.monotonic(), out)
    return out


def forget(root: str | None = None):
    """Drop the cached detection (tests, or after the project's files changed shape)."""
    if root is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(Path(root).resolve()), None)
