"""keel 0.20.0 the design-review flow (content/workflows/design-review.yaml): Google's Elephant-Goldfish Model (EGM).

A design document (a spec, a plan) is read by goldfish: reviewers in a fresh session each, with no history, that see
only the document and the files it names. EGM's five review stages run in order, each a parallel step of keel agents
(content/agents/egm-*.md):

    1 zoom out           egm-zoom-out                                     the whole document
    2 comprehension      egm-comprehension                                the whole document
    3 critique           egm-design, egm-structural, egm-style            the feature part
    4 zoom out again     egm-zoom-out (with stage 1's findings)           the whole document
    5 readiness          egm-implementation-readiness, egm-codebase-details, egm-style   the implementation plan

The feature part and the implementation plan are split at the plan's heading (`## Detailed Implementation Plan`,
`## Implementation Plan`, `## Work Breakdown`, `## Implementation Steps`). Presets: full (1-5), feature (1-4),
implementation (5). Every goldfish answers in `F | severity | where | finding | change` lines; design_collect merges
the same place and text, the egm-synthesizer agent groups the findings that mean the same thing in other words (a real
review of 15 lines gave 61 findings, most of them repeats), and design_report applies its groups. The model only groups:
the severity (the highest), the reviewers and the places come from the findings themselves, and a finding it leaves out
stays in the report as it was.

The document comes from data.doc (a path in the project), the start_flow seed (doc, or text: the harness plan, which
has no file) or the first .md path in the request.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..tools import git
from . import md
from .flow_actions import _fail, _ok

SPLIT = re.compile(r"^##\s+(detailed implementation plan|implementation plan|work breakdown|implementation steps)\b",
                   re.I)
DOC_IN_TEXT = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.md)\b")
INLINE_MAX = 11_000          # a document this long is in the reviewers' prompt with line numbers; a longer one is read
STAGES = [  # (step id, stage name, part, agents)
    ("zoom_out", "Stage 1: zoom out", "all", ["egm-zoom-out"]),
    ("comprehension", "Stage 2: comprehension", "all", ["egm-comprehension"]),
    ("critic", "Stage 3: design critique", "feature", ["egm-design", "egm-structural", "egm-style"]),
    ("zoom_again", "Stage 4: zoom out again", "all", ["egm-zoom-out"]),
    ("readiness", "Stage 5: implementation readiness", "detailed",
     ["egm-implementation-readiness", "egm-codebase-details", "egm-style"]),
]
PRESETS = {"full": {"zoom_out", "comprehension", "critic", "zoom_again", "readiness"},
           "feature": {"zoom_out", "comprehension", "critic", "zoom_again"},
           "implementation": {"readiness"}}
SEVERITY = {"blocker": 0, "major": 1, "nit": 2}
ICON = {"blocker": "🔴 Blocker", "major": "🟡 Major", "nit": "🟢 Nit"}
FINDING = re.compile(r"^\s*F\s*\|\s*(blocker|major|nit)\s*\|([^|]*)\|([^|]*)\|?(.*)$", re.I)
SUMMARY = re.compile(r"^\s*S\s*\|\s*(.+)$")
GROUP = re.compile(r"^\s*M\s*\|([^|]*)\|([^|]*)\|?(.*)$")
SYNTH_FROM = 4               # with fewer findings there is nothing to group: no model call
FOCUS = {
    "zoom_again": "Intermediate zoom out (EGM stage 8): the whole document again, after the comprehension and critique "
                  "stages. Stage 1 found what is under `stage1` (it may be fixed now). Look at what was added or changed "
                  "to answer the other stages: new complexity, new parts nobody needs yet, a mitigation heavier than "
                  "the risk.",
    "readiness": "Implementation readiness (EGM stage 9): can an engineer build the plan without making a design "
                 "decision of their own?",
}


def _seed(a) -> dict:
    return a.data.get("seed") if isinstance(a.data.get("seed"), dict) else {}


def _find_doc(a) -> tuple[str | None, str | None, str]:
    """(path, text, label): a file in the project (path), or a document that is only text (the harness plan)."""
    seed = _seed(a)
    root = Path(a.root)
    for cand in (a.data.get("doc"), seed.get("doc")):
        if cand:
            rel = str(cand).strip().removeprefix("./")
            if not (root / rel).is_file():
                return None, None, f"There is no file {rel} in the project."
            return rel, (root / rel).read_text(errors="replace"), rel
    text = str(seed.get("text") or a.data.get("text") or "").strip()
    if text:
        return None, text, str(seed.get("source") or "the document")
    for m in DOC_IN_TEXT.finditer(f"{a.title or ''}\n{a.request or ''}"):
        rel = m.group(1).removeprefix("./")
        if (root / rel).is_file():
            return rel, (root / rel).read_text(errors="replace"), rel
    return None, None, "No document to review."


def split_at(text: str) -> int | None:
    """The 1-based line of the implementation plan's heading, or None (the document is only a feature spec)."""
    inside = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            inside = not inside
        elif not inside and SPLIT.match(line.strip()):
            return n
    return None


def numbered(text: str) -> str:
    return "\n".join(f"{n:>5}  {line}" for n, line in enumerate(text.splitlines(), 1))


def doc(a):
    """The document, its two parts and one list of goldfish per stage (data.design_<stage>, the parallel steps' `from`)."""
    path, text, label = _find_doc(a)
    if text is None:
        return _fail(label, "Start the design review with the document's path in the project (docs/designs/x.md), or "
                            "name it in the request.")
    if not text.strip():
        return _fail(f"{label} is empty.", "Write the document first, then review it.")
    preset = str(a.data.get("preset") or _seed(a).get("preset") or "full").lower()
    preset = preset if preset in PRESETS else "full"
    end = len(text.splitlines())
    at = split_at(text)
    notes = []
    if preset == "feature":
        feature, detailed = (1, end), None
    elif at and at > 1:
        feature, detailed = (1, at - 1), (at, end)
    else:
        feature, detailed = (1, end), None
        notes.append("No implementation plan heading (## Implementation Plan, ## Detailed Implementation Plan, "
                     "## Work Breakdown, ## Implementation Steps): the whole document is the feature part.")
    if preset == "implementation" and not detailed:
        detailed = (1, end)
        notes.append("Implementation preset with no plan heading: stage 5 reads the whole document.")
    run = set(PRESETS[preset])
    if not detailed:
        run.discard("readiness")
    parts = {"all": (1, end), "feature": feature, "detailed": detailed}
    data = {**a.data, "design": {"path": path, "label": label, "preset": preset, "lines": end,
                                 "feature": list(feature), "detailed": list(detailed) if detailed else None,
                                 "stages": [s for s, *_ in STAGES if s in run], "file": "yes" if path else "no"}}
    for sid, name, part, agents in STAGES:
        rng = parts[part]
        data[f"design_{sid}"] = [] if sid not in run or not rng else [
            {"id": f"{sid}-{who}", "title": f"{name}: {who}", "agent": who, "stage": name,
             "lines": f"{rng[0]}-{rng[1]}" + (" (the whole document)" if rng == (1, end) else ""),
             **({"focus": FOCUS[sid]} if sid in FOCUS else {})} for who in agents]
    where = (f"The document is `{path}` in the project folder ({end} lines)." if path
             else f"The document is {label} ({end} lines); it has no file, its text is below.")
    if len(text) <= INLINE_MAX or not path:
        body = numbered(text)[: INLINE_MAX + end * 7]
        shown = f"{where} Its text, with line numbers:\n\n```\n{body}\n```"
    else:
        shown = f"{where} Read it with line numbers: `cat -n {path}` (the lines your item names, then the rest)."
    data["design_brief"] = (
        "EGM goldfish review of a design document. You have no history: judge only what the document and the files "
        f"it links or names say. {shown}\n\nYour item names your stage and the lines you review.")
    data["design_file_set"] = "yes" if path else "no"
    for sid in ("zoom_out", "comprehension", "critic", "zoom_again", "readiness"):
        data.pop(f"{sid}_results", None)
    split = (f"feature part 1-{feature[1]}, implementation plan {detailed[0]}-{detailed[1]}" if detailed and preset != "implementation"
             else f"lines 1-{end}")
    return _ok(f"{label}: {preset} review ({split}); {len(data['design']['stages'])} stage(s)",
               "\n".join(notes), update={"data": data})


def stage1_findings(a):
    """Stage 4 sees what stage 1 found (EGM: the intermediate zoom out reads the zoom out report)."""
    lines = [line for r in a.data.get("zoom_out_results") or [] for line in str(r.get("text") or "").splitlines()
             if FINDING.match(line)]
    items = []
    for it in a.data.get("design_zoom_again") or []:
        items.append({**it, "stage1": "\n".join(lines)[:3000] or "nothing"})
    return _ok(f"stage 1 found {len(lines)} thing(s)", update={"data": {**a.data, "design_zoom_again": items}})


def parse(text: str) -> tuple[list[dict], str]:
    """A goldfish's F lines and its S line."""
    found, said = [], ""
    for line in (text or "").splitlines():
        m = FINDING.match(line)
        if m:
            where, what, rest = m.group(2).strip(), m.group(3).strip(), m.group(4)
            change = rest.split("|")[0].strip()
            if what:
                found.append({"severity": m.group(1).lower(), "where": where, "finding": what, "change": change})
            continue
        s = SUMMARY.match(line)
        if s:
            said = s.group(1).strip()
    return found, said


def _key(f: dict) -> tuple[str, str]:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return norm(f["where"]), norm(f["finding"])[:80]


def merge(results: list[tuple[str, str, str]]) -> tuple[list[dict], list[dict]]:
    """[(stage, agent, answer)] -> (findings, reviewers). EGM's synthesizer: the same place and text once, at the
    highest severity any reviewer gave it, with every reviewer that saw it; blockers first."""
    merged: dict[tuple[str, str], dict] = {}
    reviewers = []
    for stage, agent, text in results:
        found, said = parse(text)
        answered = bool(found or said or "GOLDFISH:" in (text or ""))
        reviewers.append({"stage": stage, "agent": agent, "status": "completed" if answered else "no answer",
                          "findings": len(found), "said": said})
        for f in found:
            k = _key(f)
            if k in merged:
                old = merged[k]
                if SEVERITY[f["severity"]] < SEVERITY[old["severity"]]:
                    old["severity"] = f["severity"]
                old["by"] = sorted(set(old["by"]) | {agent})
                old["stages"] = list(dict.fromkeys(old["stages"] + [stage]))
                old["change"] = old["change"] or f["change"]
            else:
                merged[k] = {**f, "by": [agent], "stages": [stage]}
    out = sorted(merged.values(), key=lambda f: SEVERITY[f["severity"]])
    for n, f in enumerate(out, 1):
        f["id"] = f"G{n}"
    return out, reviewers


def _paraphrase(text: str) -> str:
    """The comprehension goldfish's paraphrase: its answer before the first F/S line (EGM keeps it for the author)."""
    out = []
    for line in (text or "").splitlines():
        if FINDING.match(line) or SUMMARY.match(line) or line.strip().startswith("GOLDFISH:") or line.strip() == "```":
            break
        out.append(line)
    return "\n".join(out).strip()


def group(findings: list[dict], answer: str) -> list[dict]:
    """The synthesizer's `M | G1,G5 | finding | change` lines applied: each group is one finding, at its highest severity,
    with all its places, reviewers and stages. An id it names twice stays in the first group, an unknown id is ignored and
    a finding it left out stays as it was: the model cannot lose a finding or lower a severity."""
    by_id = {f["id"]: f for f in findings}
    used: set[str] = set()
    out = []
    for line in (answer or "").splitlines():
        m = GROUP.match(line)
        if not m:
            continue
        ids = [i for i in re.findall(r"G\d+", m.group(1)) if i in by_id and i not in used]
        if not ids:
            continue
        used.update(ids)
        src = [by_id[i] for i in ids]
        out.append({"severity": min((f["severity"] for f in src), key=SEVERITY.get),
                    "where": "; ".join(dict.fromkeys(f["where"] for f in src if f["where"])),
                    "finding": m.group(2).strip() or src[0]["finding"],
                    "change": m.group(3).split("|")[0].strip() or next((f["change"] for f in src if f["change"]), ""),
                    "by": sorted({b for f in src for b in f["by"]}),
                    "stages": list(dict.fromkeys(st for f in src for st in f["stages"])), "from": ids})
    out += [dict(f) for f in findings if f["id"] not in used]
    out.sort(key=lambda f: SEVERITY[f["severity"]])
    for n, f in enumerate(out, 1):
        f["id"] = f"G{n}"
    return out


def _results(a) -> tuple[list[tuple[str, str, str]], str]:
    results, paraphrase = [], ""
    for sid, name, _part, _agents in STAGES:
        for r in a.data.get(f"{sid}_results") or []:
            agent = str(r.get("item") or "").removeprefix(f"{sid}-")
            results.append((name, agent, str(r.get("text") or "")))
            if sid == "comprehension":
                paraphrase = _paraphrase(str(r.get("text") or ""))
    return results, paraphrase


def collect(a):
    """Every goldfish's findings, the same place and text merged, as lines for the synthesizer (data.design_raw)."""
    results, _ = _results(a)
    findings, _ = merge(results)
    raw = "\n".join(f"{f['id']} | {f['severity']} | {f['where']} | {f['finding']} | {f['change']}" for f in findings)
    return _ok(f"{len(findings)} finding(s) from {len(results)} reviewer(s)",
               update={"data": {**a.data, "design_raw": raw, "synthesize_text": "",
                                "design_group": "yes" if len(findings) >= SYNTH_FROM else "no"}})


def report(a):
    """EGM's consolidated review report: reviewer status, the findings by severity, the comprehension paraphrase."""
    d = a.data.get("design") or {}
    results, paraphrase = _results(a)
    if not results:
        return _fail("No goldfish ran.", "Start the review again with the document's path.")
    raw, reviewers = merge(results)
    findings = group(raw, str(a.data.get("synthesize_text") or ""))
    count = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITY}
    head = (f"**Document:** {('`' + d['path'] + '`') if d.get('path') else d.get('label', 'the document')} "
            f"({d.get('lines')} lines) · **preset:** {d.get('preset')} · "
            + " · ".join(f"{ICON[s]}: {count[s]}" for s in SEVERITY))
    status = md.table(["Stage", "Reviewer", "Status", "Findings"],
                      [[r["stage"], f"`{r['agent']}`", r["status"], str(r["findings"])] for r in reviewers])
    parts = [md.section("Goldfish review", head), md.section("Reviewers", status)]
    if paraphrase:
        parts.append(md.section("How a fresh reader understood it (comprehension)", md.demote(paraphrase[:4000])))
    if findings:
        for s in SEVERITY:
            rows = [f for f in findings if f["severity"] == s]
            if rows:
                parts.append(md.section(ICON[s], "\n".join(
                    f"- **{f['id']}** · {f['where'] or '(no place given)'} · {f['finding']}"
                    + (f"\n  - change: {f['change']}" if f["change"] else "")
                    + f"\n  - seen by: {', '.join('`' + b + '`' for b in f['by'])} ({'; '.join(f['stages'])})"
                    for f in rows)))
    else:
        parts.append(md.section("Findings", "No goldfish found a problem."))
    missing = [r for r in reviewers if r["status"] != "completed"]
    if missing:
        parts.append(md.section("Not reviewed", md.bullets(
            [f"`{r['agent']}` ({r['stage']}) gave no answer in keel's format: its part is not reviewed" for r in missing])))
    choices = ["**resolve**: an agent (EGM's elephant) answers each finding: it fixes the ones you name in your note "
               "(or all blockers and majors when you name none) in the document, and argues against the ones it thinks "
               "are wrong" if d.get("file") == "yes" else
               "**resolve**: this document has no file here (the harness plan): go back to its flow and send the "
               "findings you want fixed as your note there",
               "**again**: a fresh review of the document as it is now",
               "**done**: end here; the report stays on this flow"]
    parts.append(md.section("Your choice", md.bullets(choices)))
    text = md.doc(*parts)
    data = {**a.data, "design_findings": findings, "design_reviewers": reviewers, "design_report_text": text[:12000]}
    grouped = f" ({len(raw)} before grouping)" if len(raw) != len(findings) else ""
    return _ok(f"{len(findings)} finding(s){grouped}: " + ", ".join(f"{count[s]} {s}" for s in SEVERITY),
               update={"data": data, "show": text})


def resolve_show(a):
    """What the resolver did: its answer and the document's diff."""
    d = a.data.get("design") or {}
    path = d.get("path")
    said = str(a.data.get("resolve_text") or a.state.get("last_answer") or "").strip()
    parts = []
    if d.get("file") != "yes":
        parts.append(md.section("Nothing to resolve here", "This document has no file in the project. Go back to "
                                "the flow it came from and send the findings you want fixed as your note."))
    else:
        if said:
            parts.append(md.section("What the resolver says", md.demote(said[:6000])))
        diff = git.git(a.root, "diff", "--", path).stdout if git.is_repo(a.root) and path else ""
        parts.append(md.section(f"Change to {path}", md.fence(diff[:12000], "diff") if diff.strip()
                                else "The document did not change."))
    parts.append(md.section("Your choice", md.bullets([
        "**again**: a fresh goldfish review of the changed document",
        "**more**: your note goes back to the resolver (the same session)",
        "**done**: end here; the change stays in the folder, not committed"])))
    return _ok("resolved" if said else "nothing resolved", update={"show": md.doc(*parts)})


ACTIONS = {"design_doc": doc, "design_stage1": stage1_findings, "design_collect": collect, "design_report": report,
           "design_resolve_show": resolve_show}
