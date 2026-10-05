"""Architecture detection (keel v1 lib/arch.js `keel arch detect`, ported): a score over weighted evidence per module,
deterministic so it can show its working. Low confidence (two styles close, or no signal) is what the arch-surveyor
agent settles at init.

    weight 3  declared: an architecture test library, spring-modulith, a submodule with no framework dependency
    weight 2  structure: domain/ beside adapter/, ports/, controller/ service/ repository/, features/ beside entities/
    weight 2  statistics: framework-free domain files, interfaces in domain implemented under adapter/, where @Entity sits
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import rules

STYLES = ["hexagonal", "ddd", "layered", "mvc", "feature-sliced"]
SKIP = {"node_modules", "build", "target", ".git", "dist", "generated", ".venv", "venv", "__pycache__", ".keel"}


def _walk(root: Path, limit: int = 1500) -> list[str]:
    out: list[str] = []
    stack = [root]
    while stack and len(out) < limit:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name in SKIP:
                continue
            if e.is_dir():
                stack.append(e)
            else:
                out.append(str(e.relative_to(root)).replace("\\", "/"))
    return out


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(errors="replace")
    except OSError:
        return ""


def _evidence(root: Path, files: list[str]) -> list[dict]:
    hits: list[dict] = []
    for b in [f for f in files if re.search(r"build\.gradle(\.kts)?$", f)]:
        text = _read(root, b)
        if re.search(r"archunit|konsist", text, re.I):
            hits.append({"w": 3, "styles": ["hexagonal", "ddd"], "why": f"{b} depends on an architecture test library"})
        if re.search(r"spring-modulith", text, re.I):
            hits.append({"w": 3, "styles": ["ddd"], "why": f"{b} uses spring-modulith"})
        if "/" in b and not re.search(r"org\.springframework|jakarta\.persistence|javax\.persistence", text):
            hits.append({"w": 3, "styles": ["hexagonal", "ddd"], "why": f"{b} declares no framework dependency"})
    segs = {s.lower() for f in files for s in f.split("/")}

    def has(*names):
        return all(n in segs for n in names)

    def any_(*names):
        return any(n in segs for n in names)
    if any_("adapter", "adapters") and "domain" in segs:
        hits.append({"w": 2, "styles": ["hexagonal"], "why": "domain/ beside adapter/"})
    if "domain" in segs and any_("application", "usecase", "usecases"):
        hits.append({"w": 2, "styles": ["hexagonal", "ddd"], "why": "domain/ beside application/"})
    if any_("port", "ports"):
        hits.append({"w": 2, "styles": ["hexagonal"], "why": "an explicit ports/ package"})
    if has("controller", "service", "repository") or has("controllers", "services", "repositories"):
        hits.append({"w": 2, "styles": ["layered"], "why": "role-first roots: controller/ service/ repository/"})
    if any_("controller", "controllers") and not any_("service", "services"):
        hits.append({"w": 2, "styles": ["mvc"], "why": "controllers with no service layer"})
    if any(re.search(r"Aggregate|AggregateRoot", f) for f in files):
        hits.append({"w": 2, "styles": ["ddd"], "why": "aggregate types by name"})
    if "features" in segs and any_("entities", "shared", "widgets"):
        hits.append({"w": 2, "styles": ["feature-sliced"], "why": "features/ beside entities/ or shared/"})
    if "components" in segs and "hooks" in segs and "features" not in segs:
        hits.append({"w": 2, "styles": ["layered"], "why": "components/ and hooks/ with no features/"})

    source = [f for f in files if re.search(r"\.(kt|java)$", f)]
    domain = [f for f in source if re.search(r"(^|/)(domain|model)/", f, re.I)]
    if len(domain) >= 3:
        pure = annotated = 0
        for f in domain:
            text = _read(root, f)
            imports = [ln for ln in text.splitlines() if re.match(r"^\s*import\s", ln)]
            if not any(re.search(r"org\.springframework|jakarta\.|javax\.persistence", ln) for ln in imports):
                pure += 1
            if re.search(r"@Entity|@Table|@Service|@Component", text):
                annotated += 1
        ratio = pure / len(domain)
        if ratio > 0.9:
            hits.append({"w": 2, "styles": ["hexagonal", "ddd"], "why": f"{round(ratio * 100)}% of domain files are framework-free"})
        elif ratio < 0.5 and annotated:
            hits.append({"w": 2, "styles": ["layered"], "why": f"only {round(ratio * 100)}% of domain files are framework-free"})
    ports = [f for f in source if re.search(r"(^|/)(domain|application)/", f, re.I) and re.search(r"\binterface\s+\w+", _read(root, f))]
    impls = [f for f in source if re.search(r"(^|/)(adapter|adapters|infrastructure)/", f, re.I)]
    if len(ports) >= 2 and len(impls) >= 2:
        hits.append({"w": 2, "styles": ["hexagonal"], "why": f"{len(ports)} interfaces in domain/application, implemented under adapter/"})
    entities = [f for f in source if re.search(r"@Entity\b", _read(root, f))]
    if entities:
        inside = sum(1 for f in entities if re.search(r"(adapter|adapters|infrastructure|persistence)/", f, re.I))
        if inside / len(entities) > 0.7:
            hits.append({"w": 2, "styles": ["hexagonal"], "why": "@Entity sits under adapter/persistence, not on domain types"})
        elif any(re.search(r"(^|/)(domain|model)/", f, re.I) for f in entities):
            hits.append({"w": 2, "styles": ["layered"], "why": "@Entity sits directly on domain types"})
    return hits


def score(root: str, folder: str = "") -> dict | None:
    base = Path(root) / folder if folder else Path(root)
    files = _walk(base)
    if not files:
        return None
    hits = _evidence(base, files)
    scores = {s: 0 for s in STYLES}
    for h in hits:
        for s in h["styles"]:
            scores[s] += h["w"]
    ranked = sorted(STYLES, key=lambda s: -scores[s])
    top, second = ranked[0], ranked[1]
    if not scores[top]:
        return {"style": "unknown", "confidence": "low", "scores": scores, "evidence": [], "hybrid_with": None,
                "why": "no architectural signal found"}
    close = scores[second] > 0 and (scores[top] - scores[second]) / scores[top] < 0.2
    declared = any(h["w"] == 3 and top in h["styles"] for h in hits)
    return {"style": top, "confidence": "low" if close else "high" if declared else "medium",
            "hybrid_with": second if close else None, "scores": scores, "evidence": [f"{h['why']} (w{h['w']})" for h in hits]}


def detect(root: str) -> dict:
    """Per module (backend.dir, frontend.dir in .keel/config.yml; else the whole project). The top level reports the
    backend's style: it is where the architecture question bites."""
    cfg = rules.load_config(root) or {}
    dirs = [d for d in (str((cfg.get("backend") or {}).get("dir") or "").strip("/"),
                        str((cfg.get("frontend") or {}).get("dir") or "").strip("/")) if d and (Path(root) / d).is_dir()]
    modules = {d: m for d in dirs if (m := score(root, d))}
    if not modules:
        whole = score(root)
        if whole:
            modules["."] = whole
    if not modules:
        return {"style": "unknown", "confidence": "low", "source": "detected", "modules": {}, "evidence": ["no source files found"]}
    lead = modules.get(dirs[0]) if dirs and dirs[0] in modules else next(iter(modules.values()))
    return {"style": lead["style"], "confidence": lead["confidence"], "source": "detected", "hybrid_with": lead.get("hybrid_with"),
            "modules": modules, "evidence": [f"{d}: {e}" for d, m in modules.items() for e in m["evidence"]][:12]}
