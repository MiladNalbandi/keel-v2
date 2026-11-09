#!/usr/bin/env python3
"""How far the move from the Kotlin api to the Python api (api-py/) is: every Kotlin route, and whether the Python
api answers it yet (docs/plans/python-api.md).

    python3 scripts/rewrite-routes.py            # totals per area (core, each plugin, product)
    python3 scripts/rewrite-routes.py --left     # also every route still in Kotlin
    python3 scripts/rewrite-routes.py --done     # also every route already in Python

The Kotlin routes are read from the source (@GetMapping & co., with the class's @RequestMapping prefix); the Python
ones from the app itself (cd api-py && uv run python ../scripts/rewrite-routes.py, or any Python with api-py's
packages). A path's variables are compared by place only: /api/projects/{pid} = /api/projects/{id}.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAPPING = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*(\((?P<args>[^)]*)\))?")
STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
METHOD = re.compile(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH)")
CLASS = re.compile(r"^\s*(?:[a-z]+\s+)*class\s", re.MULTILINE)


def norm(path: str) -> str:
    path = re.sub(r"\{[^}]*\}", "{}", path.rstrip("/") or "/")
    return path.replace("$", "")


def area_of(f: Path) -> str:
    rel = f.relative_to(ROOT).parts
    if rel[0] == "plugins":
        return f"plugin {rel[1]}"
    return "product" if rel[0] == "product" else "core"


def kotlin_routes() -> list[tuple[str, str, str, str]]:
    out = []
    files = list((ROOT / "api/src/main/kotlin").rglob("*.kt")) + list(ROOT.glob("plugins/*/api/src/main/**/*.kt")) \
        + list(ROOT.glob("plugins/*/api/**/*.kt")) + list((ROOT / "product/api").rglob("*.kt"))
    seen_files = set()
    for f in files:
        if f in seen_files or "/test/" in str(f):
            continue
        seen_files.add(f)
        text = f.read_text(encoding="utf-8", errors="replace")
        first_class = CLASS.search(text)
        prefix = ""
        for m in MAPPING.finditer(text):
            kind, args = m.group(1), m.group("args") or ""
            paths = STRING.findall(args) or [""]
            if kind == "Request" and first_class and m.start() < first_class.start():
                prefix = paths[0]
                continue
            methods = [kind.upper()] if kind != "Request" else (METHOD.findall(args) or ["ANY"])
            for p in paths:
                for meth in methods:
                    out.append((area_of(f), meth, norm(prefix + p), str(f.relative_to(ROOT))))
    return out


def python_routes() -> set[tuple[str, str]]:
    sys.path.insert(0, str(ROOT / "api-py"))
    import tempfile

    from keel_api.app import create_app
    from keel_api.settings import Settings

    app = create_app(Settings(data=Path(tempfile.mkdtemp()), web_dir=""))
    done = set()

    def walk(routes):
        for r in routes:
            inner = getattr(r, "original_router", None)  # FastAPI keeps an included router as it is
            if inner is not None:
                walk(inner.routes)
                continue
            path = getattr(r, "path", None)
            if not path or path == "/{rest:path}":
                continue
            for meth in getattr(r, "methods", None) or ():
                if meth != "HEAD":
                    done.add((meth, norm(path)))

    walk(app.routes)
    return done


def main() -> None:
    k = kotlin_routes()
    try:
        py = python_routes()
    except ImportError as e:
        sys.exit(f"cannot load the Python api ({e}): run it with api-py's packages, e.g. cd api-py && uv run python ../scripts/rewrite-routes.py")
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    left, done = [], []
    for area, meth, path, f in sorted(set(k)):
        ported = (meth, path) in py
        totals[area][0] += 1
        totals[area][1] += ported
        (done if ported else left).append(f"{meth:6} {path}  ({f})")
    all_k, all_p = sum(t[0] for t in totals.values()), sum(t[1] for t in totals.values())
    print(f"Python answers {all_p} of {all_k} Kotlin routes ({100 * all_p // max(all_k, 1)}%)\n")
    for area in sorted(totals, key=lambda a: (a != "core", a != "product", a)):
        n, p = totals[area]
        print(f"  {area:22} {p:4} / {n:<4}")
    if "--done" in sys.argv:
        print("\nin Python:\n  " + "\n  ".join(done))
    if "--left" in sys.argv:
        print("\nstill in Kotlin:\n  " + "\n  ".join(left))


if __name__ == "__main__":
    main()
