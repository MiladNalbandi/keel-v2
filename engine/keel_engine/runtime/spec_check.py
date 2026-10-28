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


# ------------------------------------------------------------------ 0.18.64 what a handed-over spec named, kept

# A flow handed a spec (a clarify hand-over, a spec pasted into the request, the spec file it started from) wrote its
# spec again and dropped a risk the person had named. These sections' items and every criterion line must still be in
# the spec the flow wrote: in its words, or close (most of an item's words in one line of the spec).
KEPT = (("risk", ("risk",)), ("out-of-scope item", ("out of scope", "not in scope", "non-goal")))
HEAD = re.compile(r"^\s*(?:#{1,6}\s+(.+?)\s*#*|\*\*([^*\n]{2,60}?):?\*\*:?|([A-Za-z][\w ,/&()-]{2,40}):)\s*$")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?")
STOP = frozenset("the and for with that this from into when then than are was were has have had not but its it's will "
                 "can may must should would could any all each per our your their they them there here what which who "
                 "how why also only just been being given".split())
KEEP_MIN = 0.6              # the share of an item's words one line of the spec must have
LOST_SHOWN = 8              # lost items named one by one; the rest are counted


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if (len(w) > 2 or w.isdigit()) and w not in STOP]


def _units(text: str) -> list[str]:
    """The text's items: a list item with its follow-up lines, a table row, a paragraph line (fenced code and HTML
    comments left out)."""
    text = re.sub(r"<!--.*?-->", "", re.sub(r"```.*?(```|\Z)", "", text or "", flags=re.S), flags=re.S)
    out: list[str] = []
    open_item = False
    for line in text.splitlines():
        s = line.strip()
        if not s or re.match(r"^\|?[\s:|-]+\|?$", s) or HEAD.match(line):
            open_item = False
            continue
        if s.startswith("|"):
            out.append(" ".join(c.strip() for c in s.strip("|").split("|")))
            open_item = False
        elif BULLET.match(line) or not open_item:
            out.append(BULLET.sub("", line).strip())
            open_item = True
        else:
            out[-1] += " " + s
    return [u for u in out if u]


def _parts(text: str) -> list[tuple[str, str]]:
    """(section name in lower case, its body) for every heading, bold label or "Name:" line of a document."""
    marks = []
    for m in re.finditer(r"^.*$", text or "", re.M):
        h = HEAD.match(m.group(0))
        if h:
            marks.append((m.start(), m.end(), (h.group(1) or h.group(2) or h.group(3) or "").strip().lower()))
    return [(name, text[e:marks[n + 1][0] if n + 1 < len(marks) else len(text)]) for n, (_s, e, name) in enumerate(marks)]


def _is_spec(text: str) -> bool:
    """A handed-over document is a spec (not a spike's findings or a hunt's evidence): front matter, or an Acceptance
    criteria section."""
    return bool(re.match(r"\A---\n.*?\n---\n", text or "", re.S)) or any(n.startswith("acceptance criteria") for n, _b in _parts(text))


def kept_items(text: str) -> list[tuple[str, str]]:
    """(kind, text) of what a handed-over spec names that the flow's spec must keep: each item under a Risks or Out of
    scope heading (or a "Risks:" label), and every criterion line."""
    from .prompts import parse_acs

    out: list[tuple[str, str]] = []
    for name, body in _parts(text):
        kind = next((k for k, pre in KEPT if name.startswith(pre)), None)
        if kind:
            out += [(kind, u) for u in _units(body) if len(_words(u)) >= 2 and not BARE_NONE.match(u)]
    out += [("criterion", f"{a['id']} {a['title']}") for a in parse_acs(text)]
    return out


def seeded(request: str, data: dict | None) -> list[tuple[str, str]]:
    """(where, the document) of every spec this flow was handed: the spec file it started from (its text at the
    start, data.spec_at_start), a parent flow's document that is a spec (data.seed.evidence: the clarify hand-over),
    and the request when it names risks, out-of-scope items or criteria itself."""
    data = data or {}
    seed = data.get("seed") if isinstance(data.get("seed"), dict) else {}
    ev = seed.get("evidence")
    ev = "\n".join(str(x) for x in ev) if isinstance(ev, list) else str(ev or "")
    out = []
    if str(data.get("spec_at_start") or "").strip():
        out.append(("the spec this flow started from", str(data["spec_at_start"])))
    if _is_spec(ev):
        out.append(("the handed-over document", ev))
    if kept_items(request or ""):
        out.append(("the request", request))
    return out


def lost(text: str, request: str, data: dict | None) -> list[dict]:
    """"fix" findings for what a handed-over spec (seeded) named and the spec no longer has: a risk, an out-of-scope
    item or a criterion whose words are not in any line of the spec. [] when the flow was handed no spec, or there is
    no spec text yet."""
    if not (text or "").strip():
        return []
    units = [set(_words(u)) for u in _units(text)]
    flat = " ".join(_words(text))
    gone: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for where, doc in seeded(request, data):
        for kind, item in kept_items(doc):
            words = _words(re.sub(r"^\s*AC-\d+\s*", "", item))
            key = " ".join(words)
            if not words or key in seen:
                continue
            seen.add(key)
            if key in flat or max((len(set(words) & u) / len(set(words)) for u in units), default=0) >= KEEP_MIN:
                continue
            gone.append((where, kind, item))
    out = [{"level": "fix", "text": f"Lost from {where}: the {kind} “{_short(item)}” is not in the spec. Put it back "
                                    "(its words, or close), in its section."} for where, kind, item in gone[:LOST_SHOWN]]
    if len(gone) > LOST_SHOWN:
        out.append({"level": "fix", "text": f"{len(gone) - LOST_SHOWN} more items of what the flow was handed are not in "
                                            "the spec: keep every risk, criterion and out-of-scope item it named."})
    return out


def _short(text: str, n: int = 140) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[:n].rstrip() + " …"


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
