"""The knowledge check (keel v1 lib/memory.js `check`, ported): is docs/knowledge/ still checkable evidence?

Nothing here can tell whether a claim is true. It refuses the claims that cannot even be checked:
  - a `path:line` citation (backticked, with a line number) whose file is gone or shorter than the line. A shorthand
    path (`.../owner/OwnerRepository.java:36`) or a bare file name (`MySqlIntegrationTests.java:50`) counts when exactly
    one tracked file ends with it (its line is checked in that file); more than one match is a problem,
  - a template placeholder ({{LIKE_THIS}}) nobody filled in,
  - a section with no citation at all (written from memory),
  - a chosen section that does not exist,
  - a line naming one of `memory.proof_required_terms` with neither a citation into a test nor an `unverified:` marker.

Which sections count: the ones chosen at init (.keel/config.yml init.knowledge_sections), plus every section that
exists (it is read back as project authority either way). Never chosen and nothing on disk is its own state.

0.18.57 the project notes are opt-in (`knowledge.build: on | off` in .keel/config.yml, see enabled): a real-model
evaluation (0.18.55) found that agents given only the knowledge file paths opened them twice in 46 runs and answered
no better, while building them cost about $4.60 per project. Off, keel's flows run no librarian, agents get no
knowledge paths and ship skips its knowledge step; the knowledge-refresh flow (the Wiki's Build them) still builds
them on request and turns them on.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from .. import layout, rules
from ..tools import git
from . import citations as cite_mod
from .citations import resolve

SECTIONS = ["architecture", "domain", "conventions", "data", "integrations", "journeys"]

PLACEHOLDER = re.compile(r"\{\{[A-Z_]+\}\}")
PROOF = [re.compile(p) for p in (r"(^|/)(test|tests)/", r"(^|/)src/(test|integrationTest)/", r"Tests?\.(kt|java)$",
                                 r"\.(test|spec)\.(ts|tsx|js|jsx)$", r"^\.keel/hunt/repro/")]


def section_file(root: str, name: str) -> Path:
    """The section's file where the project's layout keeps it (docs/knowledge, .keel/knowledge; keel_engine/layout.py)."""
    return Path(root) / layout.resolve(root).knowledge_file(root, name)


def knowledge_dir(root: str) -> str:
    """Where the knowledge sections go in this project's layout, e.g. docs/knowledge or .keel/knowledge."""
    return layout.resolve(root).knowledge


def is_proof(rel: str) -> bool:
    """A citation into a test (or a hunt's repro recipe) is a proof: somebody ran it."""
    return any(p.search(rel) for p in PROOF)


def citations(text: str) -> list[dict]:
    """A citation is a backticked path with a line number; prose that merely names a file is not evidence. 0.18.64 the
    parser is shared with the spec check (runtime/citations.py)."""
    return cite_mod.find(text, quoted=True)


def built(root: str) -> list[str]:
    return [n for n in SECTIONS if section_file(root, n).is_file()]


# ---------------------------------------------------------------- 0.18.57 project notes: on or off

def build_setting(cfg: dict | None) -> str | None:
    """`knowledge.build` as "on" / "off", or None when the config does not say. YAML 1.1 reads a bare on/off as a
    boolean, so both spellings count."""
    v = ((cfg or {}).get("knowledge") or {}).get("build") if isinstance((cfg or {}).get("knowledge"), dict) else None
    if v is True or str(v).strip().lower() in ("on", "true", "yes"):
        return "on"
    if v is False or str(v).strip().lower() in ("off", "false", "no"):
        return "off"
    return None


def enabled(root: str, cfg: dict | None = None) -> bool:
    """Do keel's agents use the project notes, and do its flows keep them up to date? The config's `knowledge.build`
    when it says; else on only for a project that already has them (a section on disk, or sections an earlier init
    chose): a new project starts with them off."""
    cfg = cfg if cfg is not None else rules.load_config(root)
    said = build_setting(cfg)
    if said:
        return said == "on"
    chosen = (cfg.get("init") or {}).get("knowledge_sections")
    return bool(built(root)) or (isinstance(chosen, list) and bool(chosen))


def set_build(root: str, on: bool) -> bool:
    """Writes `knowledge.build` into .keel/config.yml (only that block; the other lines stay). False when the project
    has no config yet (nothing is created) or it already says so."""
    f = Path(root) / ".keel" / "config.yml"
    if not f.is_file():
        return False
    from . import rules_view

    try:
        raw = yaml.safe_load(f.read_text()) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(raw, dict) or build_setting(raw) == ("on" if on else "off"):
        return False
    rules_view.set_config(root, {"knowledge": {"build": "on" if on else "off"}})
    return True


# What a librarian reads besides its default (catalog.AGENT_DEFAULTS): calibrated on the 0.18.55 evaluation (five
# librarians, about $4.60 on Sonnet for a small Spring and a small PHP project), growing with the code it has to read.
READ_BASE, READ_MAX = 100_000, 600_000
TEXT_SKIP = re.compile(r"\.(png|jpe?g|gif|webp|ico|svg|pdf|zip|gz|tgz|jar|war|class|so|dylib|dll|exe|bin|woff2?|ttf|otf|"
                       r"eot|mp[34]|mov|lock)$|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|composer\.lock|"
                       r"poetry\.lock|uv\.lock|Cargo\.lock|go\.sum)$", re.I)
FILE_MAX = 512 * 1024


def repo_tokens(root: str) -> int:
    """The size of the project's tracked text files in tokens (bytes / 4): what a librarian may have to read. Lock
    files, binaries and files over 512 KB are left out."""
    if not git.is_repo(root):
        return 0
    total = 0
    for rel in git.tracked(root):
        if TEXT_SKIP.search(rel):
            continue
        try:
            n = (Path(root) / rel).stat().st_size
        except OSError:
            continue
        if n <= FILE_MAX:
            total += n
    return total // 4


def librarian_read_tokens(root: str) -> int:
    """Extra input tokens per librarian call for this project: a base plus its code, capped (a librarian reads what
    its section needs, and its turns send what it read again)."""
    return max(READ_BASE, min(READ_BASE + repo_tokens(root), READ_MAX))


def cost_label(est: dict) -> str:
    """"≈$3–7" from an estimate's cost (low 0.7x, high 1.6x, as its tokens), or "≈900k–2.1M tokens" when the models
    are paid by a plan."""
    cost = float(est.get("cost_usd") or 0)
    if cost > 0:
        lo, hi = cost * 0.7, cost * 1.6
        fmt = (lambda x: f"{x:.2f}".rstrip("0").rstrip(".")) if hi < 1 else (lambda x: f"{round(x):d}")
        a, b = fmt(lo), fmt(hi)
        return f"≈${a}" if a == b else f"≈${a}–{b}"

    def k(n: int) -> str:
        return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{round(n / 1000)}k"
    return f"≈{k(int(est.get('low') or 0))}–{k(int(est.get('high') or 0))} tokens"


def build_estimate(root: str, models: dict | None = None, history: list[dict] | None = None) -> dict:
    """What building the five notes costs (the knowledge-refresh flow's librarians), with the same estimate as Start a
    flow plus what the librarians read of this project's code: {tokens, low, high, cost_usd, label}."""
    from ..workflows.estimate import estimate
    from ..workflows.templates import get_template

    e = estimate(get_template("knowledge-refresh"), 0, history, models or {}, {"librarian": librarian_read_tokens(root)})
    return {"tokens": e["tokens"], "low": e["low"], "high": e["high"], "cost_usd": e["cost_usd"], "label": cost_label(e)}


def selection(root: str, cfg: dict | None = None) -> list[str] | None:
    """The sections this project maintains; None = never asked and nothing written."""
    cfg = cfg if cfg is not None else rules.load_config(root)
    chosen = ((cfg.get("init") or {}).get("knowledge_sections"))
    if isinstance(chosen, list):
        return list(dict.fromkeys([n for n in chosen if n in SECTIONS] + built(root)))
    return built(root) or None


def check_section(root: str, name: str, cfg: dict, files: list[str] | None = None) -> dict:
    rel = layout.resolve(root).knowledge_file(root, name)
    f = Path(root) / rel
    if not f.is_file():
        return {"name": name, "missing": True, "problems": [f"{rel} is missing"], "citations": 0, "proofs": 0, "unverified": 0}
    text = f.read_text(errors="replace")
    problems = []
    left = PLACEHOLDER.findall(text)
    if left:
        problems.append(f"{rel}: {len(left)} template placeholder(s) never filled in: {', '.join(list(dict.fromkeys(left))[:4])}")
    cites = citations(text)
    for c in cites:
        path, why = resolve(root, c["rel"], files)
        n = cite_mod.lines(Path(root) / path) if path else None
        if n is None:
            problems.append(f"{rel}: cites `{c['rel']}:{c['line']}`, which {why or 'does not exist'}")
            continue
        last = c["end"] or c["line"]
        if last > n:
            problems.append(f"{rel}: cites `{c['rel']}:{last}` but that file has {n} line(s)"
                            + (f" ({path})" if path != c["rel"] else ""))
    if not cites:
        problems.append(f"{rel}: no citations at all; every claim here is unsourced")
    terms = (cfg.get("memory") or {}).get("proof_required_terms") or []
    unverified = 0
    for i, line in enumerate(text.split("\n"), 1):
        hit = next((t for t in terms if t in line), None)
        if not hit:
            continue
        if re.search(r"\bunverified:", line, re.I):
            unverified += 1
        elif not any(is_proof(c["rel"]) for c in citations(line)):
            problems.append(f"{rel}:{i}: mentions {hit} with no proof and no `unverified:` marker; cite a test, "
                            "or say it is unverified")
    return {"name": name, "missing": False, "problems": problems, "citations": len(cites),
            "proofs": sum(is_proof(c["rel"]) for c in cites), "unverified": unverified}


def check(root: str, only: list[str] | None = None) -> dict:
    """{pass, problems, sections: [...], selected, unanswered}. `only` overrides the chosen sections."""
    cfg = rules.load_config(root)
    sel = selection(root, cfg) if only is None else only
    unanswered = sel is None
    chosen = sel or []
    sections = []
    files = git.tracked(root) if git.is_repo(root) else None   # for shorthand citations (resolve)
    for n in SECTIONS:
        if n in chosen or section_file(root, n).is_file():
            sections.append({**check_section(root, n, cfg, files), "selected": n in chosen})
        else:
            sections.append({"name": n, "selected": False, "not_selected": True, "missing": True, "problems": [],
                             "citations": 0, "proofs": 0, "unverified": 0})
    problems = [p for s in sections for p in s["problems"]]
    # keel v2 before 0.4 failed an empty knowledge base; a project that chose no sections (init) passes.
    if unanswered:
        problems.append(f"{knowledge_dir(root)}/ has no sections")
    return {"pass": not problems, "problems": problems, "sections": sections, "selected": chosen, "unanswered": unanswered}


def cited_files(root: str, name: str, files: list[str] | None = None) -> list[str]:
    """The files a section cites (shorthand citations resolved), each once."""
    f = section_file(root, name)
    if not f.is_file():
        return []
    files = files if files is not None else (git.tracked(root) if git.is_repo(root) else None)
    out = [resolve(root, c["rel"], files)[0] for c in citations(f.read_text(errors="replace"))]
    return list(dict.fromkeys(x for x in out if x))


def stale(root: str, since: str | None) -> list[dict]:
    """keel v1 `memory show`: the sections whose cited files changed since `since` (a commit; the knowledge check's,
    else the branch's start), committed or not: [{name, files}]. A commit that touches no cited file leaves a section
    current; the knowledge pages themselves do not count."""
    if not git.is_repo(root):
        return []
    changed = set(git.dirty(root))
    if since:
        r = git.git(root, "diff", "--name-only", f"{since}..HEAD")
        changed |= {x.strip() for x in r.stdout.splitlines() if x.strip()} if r.returncode == 0 else set()
    kdirs = layout.resolve(root).knowledge_dirs
    changed = {f for f in changed if not any(f == d or f.startswith(d.rstrip("/") + "/") for d in kdirs)}
    if not changed:
        return []
    files = git.tracked(root)
    out = []
    for n in built(root):
        hit = sorted(set(cited_files(root, n, files)) & changed)
        if hit:
            out.append({"name": n, "files": hit})
    return out


def content_hash(root: str) -> str:
    """What the knowledge base says now: a verdict for the same text is current at any later commit."""
    h = hashlib.sha1()
    for n in SECTIONS:
        h.update(n.encode())
        f = section_file(root, n)
        h.update(f.read_bytes() if f.is_file() else b"<missing>")
    return h.hexdigest()[:12]
