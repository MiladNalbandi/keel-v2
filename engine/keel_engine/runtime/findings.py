"""Blocking findings in a reviewer's answer (code-reviewer, security-auditor, the ship review lenses).

keel's reviewer agents answer with a "Blocking" section (a heading, a bold label, or "Blocking: none") and a
"Non-blocking" one. Only the blocking items stop the flow; "none" means there are none.
"""
from __future__ import annotations

import re

REVIEWERS = {"code-reviewer", "security-auditor", "reviewer"}

# "## Blocking", "**Blocking**", "**Blocking:** none.", "Blocking findings:", "### Blocking (2)" — not "Non-blocking".
_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?<![-\w])blocking\b(?:\s+(?:findings?|issues?))?\s*(?:\(\d+\))?\s*"
                   r"(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*(.*)$", re.I)
_OTHER_HEAD = re.compile(r"^\s*(?:#{1,6}\s+\S|(?:\*\*|__)[^*_]+(?:\*\*|__)\s*:?\s*$|non-?blocking\b|(?:\*\*|__)\s*non-?blocking)", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
_NONE = re.compile(r"^\s*(?:\*\*|__)?\s*(?:none|nothing|no (?:blocking )?(?:findings|issues))\b", re.I)
_VERDICT = re.compile(r"^\s*(?:\*\*|__|`)?\s*BLOCKING\s*:\s*(?:\*\*|__)?\s*(yes|no)\b", re.I | re.M)
_REF = re.compile(r"`?([\w./-]+\.\w+:\d+(?:-\d+)?)`?")
_E2E_FAIL = re.compile(r"^\s*E2E-RESULT:\s*fail\b(.*)$", re.I | re.M)


def blocking(text: str) -> list[str]:
    """The blocking findings, one string per item; [] when the section is missing or says none.

    keel's reviewer ends with `BLOCKING: yes` or `BLOCKING: no`; that last verdict line decides when it is there.
    """
    verdicts = _VERDICT.findall(text or "")
    if verdicts and verdicts[-1].lower() == "no":
        return [f for f in _sections(text) if f.startswith("E2E failed")]
    found = _sections(text)
    if verdicts and not found:
        lead = next((l.strip() for l in (text or "").splitlines() if l.strip() and not _VERDICT.match(l)), "")
        found = [f"The reviewer marked this blocking: {lead[:300]}"]
    return found


def unique(findings: list[dict]) -> list[dict]:
    """One finding per file:line (two lenses often report the same one)."""
    seen: set[str] = set()
    out = []
    for f in findings:
        m = _REF.search(f["text"])
        key = m.group(1) if m else f["text"]
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _sections(text: str) -> list[str]:
    items: list[str] = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _HEAD.match(lines[i])
        if not m or re.search(r"non-?blocking", lines[i], re.I) or _VERDICT.match(lines[i]):
            i += 1
            continue
        rest = m.group(1).strip().strip("*_").strip()
        i += 1
        if rest and _NONE.match(rest):
            continue
        if rest and not rest.startswith(("(", "—", "-")):
            items.append(rest)
        cur: list[str] | None = None
        listed = False
        while i < len(lines):
            line = lines[i]
            if _OTHER_HEAD.match(line) and not _BULLET.match(line):
                break
            b = _BULLET.match(line)
            if b:
                if cur:
                    items.append(" ".join(cur))
                cur = [b.group(1).strip()]
                listed = True
            elif line.strip() and listed and cur is None and not line[:1].isspace():
                break   # a plain paragraph after the list explains it; it is not another finding
            elif line.strip():
                if cur is None:
                    if _NONE.match(line):
                        break
                    cur = [line.strip()]
                else:
                    cur.append(line.strip())
            elif cur:
                items.append(" ".join(cur))
                cur = None
            i += 1
        if cur:
            items.append(" ".join(cur))
    for m in _E2E_FAIL.finditer(text or ""):
        items.append(("E2E failed" + (": " + m.group(1).strip(" :—-") if m.group(1).strip(" :—-") else "")))
    return [x for x in (re.sub(r"\s+", " ", x).strip() for x in items)
            if x and not _NONE.match(x) and not re.fullmatch(r"(?i)(yes|no)\.?", x)]
