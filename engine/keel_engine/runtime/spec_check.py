"""keel v1's `keel spec check`, in the engine: is this spec ready for a human to approve?

It warns, it never blocks: a one-line change does not need a wireframe, and the user decides. The engine sends a
spec back to the explorer once when something clear is missing (a screen with no states drawn, an API with no
request path); after that the findings are shown on the spec gate.
"""
from __future__ import annotations

import re
from pathlib import Path

SECTION = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.M)
STATES = {"loading": r"load", "empty": r"empt|no results|nothing", "error": r"error|fail", "filled": r"fill|result|row|list|data|show"}
VAGUE = re.compile(r"\b(either|any of|whichever|or something|etc\.?|as needed|appropriate(ly)?|fast|quickly|user[- ]friendly|nice)\b", re.I)


def sections(text: str) -> dict[str, str]:
    marks = [(m.start(), m.end(), m.group(1).strip().lower()) for m in SECTION.finditer(text)]
    out = {}
    for n, (_s, e, name) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(text)
        out[name] = text[e:end].strip()
    return out


def check(text: str, acs: list[dict]) -> list[dict]:
    """Findings as {level: "fix"|"note", text}. "fix" ones are worth one automatic revision."""
    found: list[dict] = []
    sec = sections(text)
    layers = {str(a.get("layer", "API")).upper() for a in acs}

    def has(*names: str) -> str | None:
        return next((v for k, v in sec.items() if any(k.startswith(n) for n in names)), None)

    if not acs:
        return [{"level": "fix", "text": "No acceptance criteria."}]
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
    if len(acs) > 8:
        found.append({"level": "note", "text": f"{len(acs)} criteria: more than 8 usually means two features. Split it?"})
    for block in re.findall(r"```.*?```", text, re.S):
        if re.search(r"\bAC-\d+", block):
            found.append({"level": "note", "text": "A drawing names an AC id: take it out (ids belong on the criteria lines)."})
            break
    return found


def read_spec(root: str, rel: str | None) -> str:
    if not rel:
        return ""
    p = Path(root) / rel
    try:
        return p.read_text(errors="replace") if p.is_file() else ""
    except OSError:
        return ""


def revision_note(findings: list[dict]) -> str:
    fixes = [f["text"] for f in findings if f["level"] == "fix"]
    return ("keel's spec check found problems in your spec. Change the existing file to fix them (do not explore again, "
            "do not change criteria that are fine, keep the ids):\n" + "\n".join(f"- {t}" for t in fixes))


def describe(findings: list[dict]) -> str:
    if not findings:
        return "Spec check: nothing missing."
    return "Spec check (warnings, you decide):\n" + "\n".join(f"- {f['text']}" for f in findings)
