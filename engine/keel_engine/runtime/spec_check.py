"""keel v1's `keel spec check`, in the engine: is this spec ready for a human to approve?

It never blocks the person: they decide at the spec gate. The engine sends a spec back to the explorer once when
something clear is missing (a section of keel's spec template, a criterion with no test layer, a screen with no states
drawn, an API with no request path, outside facts with no Unverified and open questions, 0.18.64 a `path:line` citation
whose file or line does not exist); after that the findings are shown on the spec gate as missing, the rest as warnings.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import citations

SECTION = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.M)
STATES = {"loading": r"load", "empty": r"empt|no results|nothing", "error": r"error|fail", "filled": r"fill|result|row|list|data|show"}
VAGUE = re.compile(r"\b(either|any of|whichever|or something|etc\.?|as needed|appropriate(ly)?|fast|quickly|user[- ]friendly|nice)\b", re.I)


# 0.18.21 keel v1's spec template (content/templates/spec.md), with the evidence a spike brings: the sections every spec
# file must have, as (shown name, heading prefixes, what goes in it). A spec without one is sent back once, then the
# gate names it.
REQUIRED = (
    # 0.18.25 the six sections a reader scans first (every spec, every flow that writes one)
    ("In plain words", ("in plain words",), "5 to 10 short sentences for a colleague who does not know the code: what is "
                                            "wrong or missing, what we do, what a user notices, what stays the same, what we "
                                            "still must find out; simple words, one everyday example"),
    ("For engineers", ("for engineers",), "8 to 15 precise lines: the change per layer with its classes and tables, the "
                                          "design choices and why, compatibility, how it fails and how we see it"),
    ("What will change", ("what will change",), "one line per file or area: new, changed or removed, and what happens"),
    ("Payload changes", ("payload change",), "JSON before and after with the fields marked + ~ -, and the compatibility "
                                             "note; or \"none: <the reason>\""),
    ("Database changes", ("database change",), "the table before and after, the migration (up, down, online or locking), "
                                               "backfill, deploy order, rollback and its data loss, the repository methods; "
                                               "or \"none: <the reason>\""),
    ("Architecture", ("architecture",), "an ASCII request path (inbound → app → domain → persistence → outbound), each box "
                                        "new, changed or unchanged, and why it fits the layers"),
    ("Goal", ("goal",), "why this exists and what changes for whom, in two or three sentences"),
    ("User stories", ("user stor",), "\"As a <role>, I want <capability>, so that <benefit>.\", one per actor"),
    ("Findings (evidence)", ("findings",), "what is true today, each claim with its file:line or URL: the inbound, "
                                           "internal-message, persistence and outbound paths, and the tests that exist"),
    ("Acceptance criteria", ("acceptance criteria",), "numbered, layer-tagged, testable from outside"),
    ("Test layers", ("test layer",), "per criterion the layers that prove it (unit, functional, integration, smoke, "
                                     "e2e) or why a layer is not used"),
    ("Validation and security rules", ("validation",), "what is refused and how, who may read and change what"),
    ("Edge cases", ("edge case",), "absent, malformed, duplicate, limit, a later update, an old message still queued"),
    ("Risks and cost", ("risk",), "files touched, migration or not, the rolling deploy order, what is ambiguous"),
    ("Definition of done", ("definition of done",), "what must hold to call it done: criteria green on their layers, "
                                                    "contract and docs updated, migration and rollout done, review passed"),
)
UNVERIFIED = ("unverified", "open question")
# a section that may say only "none", but must say why (Payload changes, Database changes)
NONE_NEEDS_REASON = (("Payload changes", ("payload change",)), ("Database changes", ("database change",)))
BARE_NONE = re.compile(r"^[\s*_`>-]*(none|no changes?|n/?a|nothing)[\s.*_`]*$", re.I)
SECTION_ORDER = ("In plain words", "For engineers", "What will change", "Payload changes", "Database changes",
                 "Architecture", "Request", "Goal", "User stories", "Decisions", "Findings (evidence)", "Acceptance criteria", "Test layers",
                 "UI mockup", "Request path", "Data and migrations", "Validation and security rules", "Edge cases",
                 "Risks and cost", "Assumptions", "Unverified and open questions", "Out of scope", "Definition of done", "Plan")
STORY = re.compile(r"\bas an?\b.+?\bi want\b.+?\bso that\b", re.I | re.S)
CITE = re.compile(r"[\w./-]+\.\w+:\d+|https?://\S+")
URL = re.compile(r"https?://[^\s)>\]]+")


def sections(text: str) -> dict[str, str]:
    marks = [(m.start(), m.end(), m.group(1).strip().lower()) for m in SECTION.finditer(text)]
    out = {}
    for n, (_s, e, name) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(text)
        out[name] = text[e:end].strip()
    return out


def _shape(title: str) -> str:
    """A criterion with its numbers and quoted values blanked out: two criteria with the same shape test one rule."""
    t = re.sub(r"`[^`]{1,60}`|'[^'\s]{1,40}'|\"[^\"]{1,60}\"|-?\d+(?:\.\d+)?", "#", title.lower())
    return re.sub(r"\s+", " ", t).strip() if "#" in t else ""


def check(text: str, acs: list[dict], outside: bool = False, root: str | None = None) -> list[dict]:
    """Findings as {level: "fix"|"note", text}. "fix" ones are worth one automatic revision. `outside`: the work relies on
    facts from outside the repository (web research, a spike's sources), so the spec needs Unverified and open
    questions; a URL in the spec says the same. `root`: the project the spec is about; 0.18.64 with it, every citation
    that names a project file is checked (runtime/citations.py): a missing file or a line past its end is a "fix"."""
    found: list[dict] = []
    sec = sections(text)
    layers = {str(a.get("layer", "API")).upper() for a in acs}

    def has(*names: str) -> str | None:
        return next((v for k, v in sec.items() if any(k.startswith(n) for n in names)), None)

    if not acs:
        return [{"level": "fix", "text": "No acceptance criteria."}]
    if text.strip():
        found += _template(text, sec, acs, outside)
        if root:
            found += citations.findings(root, text)
    if "WEB" in layers:
        mock = has("mockup", "ui mockup", "wireframe", "screen")
        if mock is None or not mock.strip("`\n "):
            found.append({"level": "fix", "text": "[WEB] criteria but no Mockup section: draw the screen (fenced ASCII)."})
        else:
            missing = [s for s, pat in STATES.items() if not re.search(pat, mock, re.I)]
            if len(missing) > 1:
                found.append({"level": "fix", "text": "The mockup should show four states (loading, empty, filled, error); "
                                                       f"not drawn: {', '.join(missing)}."})
    if "API" in layers:
        path = has("request path", "request-path")
        if path is None or not path.strip("`\n "):
            found.append({"level": "fix", "text": "[API] criteria but no Request path section: draw the hops, each marked NEW or CHANGED."})
    for name in ("request", "decisions", "assumptions", "out of scope"):
        v = has(name)
        if v is not None and not v.strip():
            found.append({"level": "note", "text": f"The section “{name}” is empty: fill it or remove it."})
    if not (has("out of scope") or "out of scope" in text.lower()):
        found.append({"level": "note", "text": "No Out of scope: say what is not built, and what that costs."})
    for a in acs:
        title = str(a.get("title") or "")
        if not re.search(r"\bthen\b", title, re.I):
            found.append({"level": "note", "text": f"{a['id']} has no “then”: say what an outside observer sees."})
        elif (m := VAGUE.search(title)):
            found.append({"level": "note", "text": f"{a['id']} is vague (“{m.group(0)}”): name the number or the exact outcome."})
    ids = [a["id"] for a in acs]
    if len(set(ids)) != len(ids):
        found.append({"level": "fix", "text": "Two criteria have the same id."})
    # Cases of one rule split into criteria (only a number or a quoted value differs) cost a full round each; a real
    # feature run had applyCoupon(cart, -1), (cart, 101) and (cart, 50.5) as three criteria the first code already met.
    shapes: dict[str, list[str]] = {}
    for a in acs:
        shapes.setdefault(_shape(str(a.get("title") or "")), []).append(a["id"])
    for same in (v for k, v in shapes.items() if k and len(v) > 1):
        found.append({"level": "fix", "text": f"{', '.join(same)} differ only in their values: they are cases of one rule. "
                                               "Merge them into one criterion and list the values as examples."})
    if len(acs) > 8:
        found.append({"level": "note", "text": f"{len(acs)} criteria: more than 8 usually means two features. Split it?"})
    for block in re.findall(r"```.*?```", text, re.S):
        if re.search(r"\bAC-\d+", block):
            found.append({"level": "note", "text": "A drawing names an AC id: take it out (ids belong on the criteria lines)."})
            break
    return found


def _template(text: str, sec: dict[str, str], acs: list[dict], outside: bool) -> list[dict]:
    """The template's required sections (a spec file only: a triage answer has none), a test layer for every criterion,
    and Unverified and open questions when outside facts are involved."""
    found: list[dict] = []

    def body(prefixes: tuple[str, ...]) -> str | None:
        hit = [v for k, v in sec.items() if k.startswith(prefixes)]
        return "\n".join(hit) if hit else None

    def bare(v: str) -> str:
        return re.sub(r"<!--.*?-->", "", v, flags=re.S).strip()

    missing = [(name, what) for name, pre, what in REQUIRED if not bare(body(pre) or "")]
    for name, what in missing:
        found.append({"level": "fix", "text": f"No “{name}” section (or it is empty): {what}."})
    for name, pre in NONE_NEEDS_REASON:
        v = bare(body(pre) or "")
        if v and BARE_NONE.match(v):
            found.append({"level": "fix", "text": f"“{name}” says only none: add the reason in one line (\"none: <why>\")."})
    stories = body(("user stor",))
    if stories and bare(stories) and not STORY.search(stories):
        found.append({"level": "fix", "text": "User stories: write each as “As a <role>, I want <capability>, so that <benefit>.”"})
    layers = body(("test layer",))
    if layers is not None and bare(layers):
        named = {int(n) for n in re.findall(r"\bAC-0*(\d+)\b", layers)}
        lacking = [a["id"] for a in acs if (m := re.search(r"(\d+)$", str(a["id"]))) and int(m.group(1)) not in named]
        if lacking:
            found.append({"level": "fix", "text": f"Test layers names no layer for {', '.join(lacking)}: give each criterion the "
                                                   "layers that prove it (unit, functional, integration, smoke, e2e) or say why none."})
    findings = body(("findings",))
    if findings and bare(findings) and not CITE.search(findings):
        found.append({"level": "note", "text": "Findings (evidence) cites nothing: give each claim its file:line or URL."})
    if (outside or URL.search(text)) and not bare(body(UNVERIFIED) or ""):
        found.append({"level": "fix", "text": "The spec relies on facts from outside the repository but has no “Unverified and "
                                               "open questions” section: list each fact no source confirmed and what must be "
                                               "confirmed, by whom (or write “none: every outside fact is cited”)."})
    done = body(("definition of done",))
    if done and bare(done) and not re.search(r"^\s*[-*]\s", done, re.M):
        found.append({"level": "note", "text": "Definition of done: list what must hold, one checkbox line each."})
    return found


def outside_facts(request: str, data: dict | None) -> bool:
    """Does this flow rely on facts from outside the repository: web research ran (data.web_docs_text), or the request or
    the evidence a parent flow handed over (a spike's report) cites a URL?"""
    data = data or {}
    seed = data.get("seed") if isinstance(data.get("seed"), dict) else {}
    return bool(str(data.get("web_docs_text") or "").strip()) or bool(URL.search(f"{request or ''}\n{seed.get('evidence') or ''}"))


def template_path() -> str:
    from .. import config

    return str(config.content_dir() / "templates" / "spec.md")


def read_spec(root: str, rel: str | None) -> str:
    if not rel:
        return ""
    p = Path(root) / rel
    try:
        return p.read_text(errors="replace") if p.is_file() else ""
    except OSError:
        return ""


def start_spec(root: str, rel) -> str | None:
    """A flow started from a spec (StartThread.data.spec, the Wiki's "Start a flow from this spec"): that path when it is
    a .md file inside the project (no symlink, nothing outside it), else None."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    rel = rel.strip().removeprefix("./")
    if rel.startswith("/") or ".." in rel.split("/") or not rel.endswith(".md"):
        return None
    base = Path(root).resolve()
    p = base / rel
    try:
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(base):
            return None
    except OSError:
        return None
    return rel


def check_file(root: str, rel: str) -> dict:
    """0.18.64 keel's spec check on one file of the project (keel_spec_check; the engine's POST /specs/check): its
    criteria, read as a flow reads them, and the findings with the citations checked against the project."""
    from .prompts import parse_acs

    text = read_spec(root, rel)
    acs = parse_acs(text)
    found = check(text, acs, root=root) if text.strip() else [{"level": "fix", "text": f"{rel} is empty."}]
    return {"path": rel, "criteria": [a["id"] for a in acs], "findings": found,
            "fix": sum(f["level"] == "fix" for f in found), "summary": describe(found)}


def revision_note(findings: list[dict]) -> str:
    fixes = [f["text"] for f in findings if f["level"] == "fix"]
    return ("keel's spec check found problems in your spec. Change the existing file to fix them (do not explore again, "
            "do not change criteria that are fine, keep the ids):\n" + "\n".join(f"- {t}" for t in fixes))


def describe(findings: list[dict]) -> str:
    """Markdown: a label line, a blank line, then the list (so the dashboard renders a real list)."""
    from .md import safe_words
    if not findings:
        return "Spec check: nothing missing."
    fixes = [f["text"] for f in findings if f["level"] == "fix"]
    notes = [f["text"] for f in findings if f["level"] != "fix"]
    out = []
    if fixes:
        out.append("Spec check, missing (keel sends the spec back once for these; choose edit or rewrite to fix the rest "
                   "before you approve):\n\n" + "\n".join(f"- {safe_words(t)}" for t in fixes))
    if notes:
        out.append("Spec check (warnings, you decide):\n\n" + "\n".join(f"- {safe_words(t)}" for t in notes))
    return "\n\n".join(out)
