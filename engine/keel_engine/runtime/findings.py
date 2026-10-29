"""Blocking findings in a reviewer's answer (branch-reviewer, security-auditor, the ship review lenses).

keel's reviewer agents answer with a "Blocking" section (a heading, a bold label, or "Blocking: none") and a
"Non-blocking" one. Only the blocking items stop the flow; "none" means there are none. An agent that ends with its own
verdict line instead (CODE-REVIEW, SECURITY or DEPS: findings) stops it with the items it lists.
"""
from __future__ import annotations

import re

REVIEWERS = {"branch-reviewer", "security-auditor", "lens-reviewer"}

# "## Blocking", "**Blocking**", "**Blocking:** none.", "Blocking findings:", "### Blocking (2)" — not "Non-blocking".
_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?<![-\w])blocking\b(?:\s+(?:findings?|issues?))?\s*(?:\(\d+\))?\s*"
                   r"(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*(.*)$", re.I)
_OTHER_HEAD = re.compile(r"^\s*(?:#{1,6}\s+\S|(?:\*\*|__)[^*_]+(?:\*\*|__)\s*:?\s*$|non-?blocking\b|(?:\*\*|__)\s*non-?blocking)", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
_NONE = re.compile(r"^\s*(?:\*\*|__)?\s*(?:none|nothing|no (?:blocking )?(?:findings|issues))\b", re.I)
_VERDICT = re.compile(r"^\s*(?:\*\*|__|`)?\s*BLOCKING\s*:\s*(?:\*\*|__)?\s*(yes|no)\b", re.I | re.M)
_REF = re.compile(r"`?([\w./-]+\.\w+:\d+(?:-\d+)?)`?")
_E2E_FAIL = re.compile(r"^\s*E2E-RESULT:\s*fail\b(.*)$", re.I | re.M)
# The other reviewers' verdict lines (branch-reviewer, security-auditor, dependency-triager): "findings" without a Blocking
# section still stops the flow; their findings are the answer's list items.
_OWN_VERDICT = re.compile(r"^\s*(?:\*\*|__|`)?\s*(?:CODE-REVIEW|SECURITY|DEPS)\s*:\s*(?:\*\*|__)?\s*(pass|clean|findings)\b",
                          re.I | re.M)


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
    own = _OWN_VERDICT.findall(text or "")
    if not verdicts and not found and own and own[-1].lower() == "findings":
        # keel 0.18.68 a list that holds only checklist answers and "did not check" items is a pass with notes; only an
        # answer with no list at all falls back to its first line
        found = _listed(text) or ([] if _has_items(text) else ["The reviewer reported findings: " + next(
            (l.strip() for l in (text or "").splitlines() if l.strip() and not _OWN_VERDICT.match(l)), "")[:300]])
    return found


# keel 0.18.68 (field report: a branch review sent back twice with 27, then 26 "blocking findings" that were its own
# checklist answers): the headings of an answer's parts that hold no findings, and an item that says there is nothing
_ANY_HEAD = re.compile(r"^\s*(?:#{1,6}\s+(.+?)|(?:\*\*|__)([^*_]+?)(?:\*\*|__)\s*:?)\s*$")
_FINDINGS_HEAD = re.compile(r"(?i)^\W*(?:\d+[.)]\s*)?(?:findings?|problems?|issues?|must fix|should fix)\b")
_NOT_FINDINGS_HEAD = re.compile(r"(?i)\b(?:did not check|didn't check|not checked|unchecked|could not check|checklist|"
                                r"what i (?:checked|read)|checked|scope|summary|notes?|context|method|verdict)\b")
_NOTHING = re.compile(r"(?i)^\W*(?:none|nothing|no (?:issues?|problems?|findings?|change|concerns?)|n/?a|not applicable|"
                      r"ok(?:ay)?|fine|clean|pass(?:es|ed)?|looks (?:good|fine|right|correct)|unchanged|nothing to report|"
                      r"nothing found|no new)\b")
_LABEL = re.compile(r"^\s*(?:\*\*|__)?([^:*_]{1,80}?)(?:\*\*|__)?\s*[:—–-]\s*(?:\*\*|__)?\s*(.*)$")


def _says_nothing(item: str) -> bool:
    """A checklist answer, not a finding: "nothing", "Shared config keys: nothing to report", a bare "**Correctness**"."""
    t = item.strip()
    if not re.sub(r"[*_`:\s]", "", t):
        return True
    if _NOTHING.match(t):
        return True
    m = _LABEL.match(t)
    if m and not _REF.search(m.group(1)):
        rest = m.group(2).strip().strip("*_").strip()
        return not rest or bool(_NOTHING.match(rest))
    return bool(re.fullmatch(r"(?:\*\*|__)[^*_]+(?:\*\*|__)\.?", t))


def _listed(text: str) -> list[str]:
    """The list items of an answer that names its findings without a Blocking heading. keel 0.18.68: the items under a
    Findings heading when it has one; else every item but those under a checklist or "What I did not check" heading,
    and never an item that says there is nothing ("Shared config keys: nothing to report") or is only a label."""
    parts: list[tuple[str, list[str]]] = [("", [])]
    for line in (text or "").splitlines():
        h = _ANY_HEAD.match(line)
        if h and not _BULLET.match(line):
            parts.append(((h.group(1) or h.group(2) or "").strip(), []))
            continue
        b = _BULLET.match(line)
        if b and b.group(1).strip():
            parts[-1][1].append(re.sub(r"\s+", " ", b.group(1)).strip())
    named = [items for head, items in parts if head and _FINDINGS_HEAD.match(head)]
    chosen = named or [items for head, items in parts if not (head and _NOT_FINDINGS_HEAD.search(head))]
    return [x for items in chosen for x in items if not _NONE.match(x) and not _says_nothing(x)]


def _has_items(text: str) -> bool:
    return any(_BULLET.match(line) and _BULLET.match(line).group(1).strip() for line in (text or "").splitlines())


_NB_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*non-?blocking\b[^:\n]*?(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*(.*)$", re.I)


def nonblocking(text: str) -> list[str]:
    """The non-blocking findings, one string per list item under a "Non-blocking" heading or label ([] when there is
    none or it says none). They do not stop the flow; keel keeps them for the final review (data.nonblocking_findings)."""
    items: list[str] = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _NB_HEAD.match(lines[i])
        i += 1
        if not m:
            continue
        rest = m.group(1).strip().strip("*_").strip()
        if rest and _NONE.match(rest):
            continue
        cur: list[str] | None = None
        while i < len(lines):
            line = lines[i]
            if (_OTHER_HEAD.match(line) or _HEAD.match(line) or _VERDICT.match(line) or _OWN_VERDICT.match(line)) \
                    and not _BULLET.match(line):
                break
            b = _BULLET.match(line)
            if b:
                if cur:
                    items.append(" ".join(cur))
                cur = [b.group(1).strip()]
            elif line.strip() and cur is not None and line[:1].isspace():
                cur.append(line.strip())
            elif line.strip() and cur is not None:
                break
            i += 1
        if cur:
            items.append(" ".join(cur))
    return [x for x in (re.sub(r"\s+", " ", x).strip() for x in items) if x and not _NONE.match(x)]


def key(text: str) -> str:
    """What makes two findings the same one: its file:line, else its words (unique() and the dismissed list)."""
    m = _REF.search(text or "")
    return m.group(1) if m else re.sub(r"\s+", " ", text or "").strip().lower()


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
