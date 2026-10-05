"""Result markers: one-line verdicts an agent ends its answer with, read into the flow state.

    REPRO: confirmed | not-reproducible        (reproducer)
    ROOT-CAUSE: confirmed | unconfirmed        (investigator)
    BLOCKING: yes | no                         (reviewers; findings.py reads the findings themselves)
    E2E-RESULT: pass | fail                    (e2e-author)
    NAME: any value                            any other name a step declares in `markers: [NAME]`

A registered marker keeps only its first word, lower case (`REPRO: Confirmed — the test fails` -> "confirmed");
any other marker keeps the rest of its line. The last line that names a marker wins. A step can also collect a JSON
list from its answer (`collect: <key>`): the last ```json (or ```keel-items) block that holds a list.
"""

from __future__ import annotations

import json
import re

REGISTRY: dict[str, tuple[str, ...]] = {
    "REPRO": ("confirmed", "not-reproducible"),
    "ROOT-CAUSE": ("confirmed", "unconfirmed"),
    "BLOCKING": ("yes", "no"),
    "E2E-RESULT": ("pass", "fail"),
}

_FENCE = re.compile(r"```(?:json|keel-items)\s*\n(.*?)```", re.S)


def _line(name: str) -> re.Pattern:
    return re.compile(rf"^\s*(?:[-*>]\s*)?(?:\*\*|__|`)?\s*{re.escape(name)}\s*(?:\*\*|__|`)?\s*:\s*(?:\*\*|__|`)?\s*(.*?)\s*(?:\*\*|__|`)?\s*$",
                      re.I | re.M)


def parse(text: str, names: list[str] | None = None) -> dict[str, str]:
    """{NAME: value} for each declared (default: registered) marker found in the text."""
    out: dict[str, str] = {}
    for name in [n.upper() for n in (names or REGISTRY)]:
        found = _line(name).findall(text or "")
        if not found:
            continue
        value = found[-1].strip()
        if name in REGISTRY:
            word = re.match(r"[\w-]+", value)
            value = word.group(0).lower() if word else ""
        if value:
            out[name] = value
    return out


def matches(value: str | None, when: dict) -> bool:
    """A branch's `when`: equals (one value) or in (a list); case does not matter."""
    if value is None:
        return False
    v = str(value).strip().lower()
    if "equals" in when:
        return v == str(when["equals"]).strip().lower()
    if "in" in when:
        return v in {str(x).strip().lower() for x in when.get("in") or []}
    return bool(v)


def collect(text: str, key: str) -> list[dict]:
    """The last JSON list in a fenced block of the answer, as items with an id (`<key>-<n>` when missing)."""
    found = None
    for block in _FENCE.findall(text or ""):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get(key), list):
            data = data[key]
        if isinstance(data, list):
            found = data
    out = []
    for n, x in enumerate(found or []):
        item = dict(x) if isinstance(x, dict) else {"title": str(x)}
        item["id"] = str(item.get("id") or f"{key}-{n + 1}")
        out.append(item)
    return out
