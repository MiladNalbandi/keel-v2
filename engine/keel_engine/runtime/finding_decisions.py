"""Per-finding decisions on a findings gate (keel v1's security phase: the person decides each finding).

The findings gate (compiler.findings_step) asks one question per blocking finding in keel's question shape
(runtime/clarify.py), the recommended option first:

    fix            the implementer fixes it, then the review runs again (recommended)
    accept         keep it as it is; needs a reason        → data.dismissed_findings (decision "accept")
    not a finding  the reviewer is wrong; needs a reason   → data.dismissed_findings (decision "not a finding")
    amend spec     the spec is wrong here: a spec amendment starts (only where the flow has one)

An answer is the label, "<label>: <reason>", or own words (they count as "accept" with the words as the reason).
A finding without an answer follows the gate's plain decision, so an old client still works: approve ("Fix them")
fixes every unmarked finding, reject ("Go on anyway") accepts them with the answer's why.
"""
from __future__ import annotations

from . import clarify

FIX, ACCEPT, NOT_A_FINDING, AMEND = "fix", "accept", "not a finding", "amend spec"
DISMISSED = (ACCEPT, NOT_A_FINDING)
MAX_ASKED = 10          # findings past this many follow the plain decision (the form stays readable)


def questions(found: list[dict], can_amend: bool, spec_files: list[str] | None = None) -> list[dict]:
    """One question per finding (at most MAX_ASKED): id f1, f2, ...; the lens as its why. keel 0.18.71 [spec_files]: spec
    files keel put back after a fix round; a finding that names one (or "spec") recommends amend spec instead of fix."""
    opts = [
        {"label": FIX, "description": "the implementer fixes it, then the review runs again", "recommended": True},
        {"label": ACCEPT, "description": "keep it as it is; say why (accept: <reason>), it shows at the final review",
         "why": "required"},
        {"label": NOT_A_FINDING, "description": "the reviewer is wrong; say why (not a finding: <reason>)", "why": "required"},
    ]
    if can_amend:
        opts.append({"label": AMEND, "description": "the spec is wrong here: start a spec amendment with this finding"})
    def about_spec(text: str) -> bool:
        low = text.lower()
        return bool(spec_files) and (any(p.lower() in low or p.rsplit("/", 1)[-1].lower() in low for p in spec_files)
                                     or "spec" in low)

    def options(text: str) -> list[dict]:
        out = [dict(o) for o in opts]
        if can_amend and about_spec(text):
            for o in out:
                o["recommended"] = o["label"] == AMEND
            out.sort(key=lambda o: not o.get("recommended"))
        return out
    return [{"id": f"f{n}", "question": str(f.get("text") or "")[:300], "why": f"from {f.get('lens') or 'the review'}",
             "options": options(str(f.get("text") or ""))} for n, f in enumerate(found[:MAX_ASKED], 1)]


def read(asked: list[dict], found: list[dict], answer: dict) -> list[dict]:
    """Each finding with its decision and reason: [{lens, text, decision, reason}]. An automatic answer (run mode)
    and an unanswered finding take the plain decision: approve = fix, else accept with the answer's why."""
    why = str(answer.get("why") or "").strip()
    plain = FIX if answer.get("decision") == "approve" else ACCEPT
    given = {} if answer.get("auto") else dict((answer.get("payload") or {}).get("answers") or {})
    out = []
    for n, f in enumerate(found):
        q = asked[n] if n < len(asked) else None
        raw = given.get(q["id"]) if q else None
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        label, said = clarify.split_answer(q, raw) if q and raw not in (None, "") else (None, "")
        if label is None and said:
            # own words naming no option are a reason to keep it; "amend spec" where the flow has no amendment stays open
            label = FIX if said.lower().startswith(AMEND) else ACCEPT
        decision = label or plain
        reason = said or (why if decision != FIX or label is None else "")
        out.append({"lens": f.get("lens"), "text": f.get("text"), "decision": decision, "reason": reason})
    return out


def problem(question: dict, decision: str, why: str | None, payload: dict | None) -> tuple[str, str] | None:
    """(error, hint) when a findings gate's answer accepts or dismisses a finding without a reason, else None."""
    qs = [q for q in question.get("questions") or [] if any(o.get("why") == "required" for o in q.get("options") or [])]
    said = clarify.reason_problem(qs, (payload or {}).get("answers"), why)
    return (said, "Write the reason after the label, or in the answer's why.") if said else None
