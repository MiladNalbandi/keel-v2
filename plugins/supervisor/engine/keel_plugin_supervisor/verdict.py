"""The Supervisor's answer: one JSON object (the "verdict"), and the gate answer keel makes from it.

    {"action": "answer" | "send_back" | "escalate",
     "decision": "approve" | "reject", "choice": "retry", "answers": {"q1": "Postgres"},
     "commands": ["composer update -W"]   (a choice that takes commands: init's own_fix)
     "why": "...", "confidence": "high" | "medium" | "low",
     "evidence": ["spec:AC-3", "src/orders.py:88", "knowledge:conventions"], "risks": ["..."],
     "summary": "two or three short lines for the person"}

The model proposes; keel checks (policy.py) and answers. The model never calls a tool that answers a gate.
"""

from __future__ import annotations

import json
import os
import re

ACTIONS = ("answer", "send_back", "escalate")
LEVELS = ("high", "medium", "low")
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse(text: str) -> dict | None:
    """The last JSON object in the model's text (a ```json block first), normalized; None when there is none."""
    text = text or ""
    found = [m.group(1) for m in _FENCE.finditer(text)]
    if not found:
        start = text.rfind("{")
        while start != -1:
            try:
                json.loads(text[start:text.rindex("}") + 1])
                found = [text[start:text.rindex("}") + 1]]
                break
            except ValueError:
                start = text.rfind("{", 0, start)
    for raw in reversed(found):
        try:
            v = json.loads(raw)
        except ValueError:
            continue
        if isinstance(v, dict):
            return normalize(v)
    return None


def normalize(v: dict) -> dict:
    action = str(v.get("action") or "escalate").strip().lower().replace("-", "_")
    level = str(v.get("confidence") or "low").strip().lower()
    ev = v.get("evidence") or []
    return {
        "action": action if action in ACTIONS else "escalate",
        "decision": str(v.get("decision") or ("reject" if action == "send_back" else "approve")).lower(),
        "choice": v.get("choice") if v.get("choice") not in ("", None) else None,
        "answers": v.get("answers") if isinstance(v.get("answers"), dict) else {},
        "commands": [str(c).strip() for c in (v.get("commands") if isinstance(v.get("commands"), list) else
                                              str(v.get("commands") or "").splitlines()) if str(c).strip()][:20],
        "why": str(v.get("why") or "").strip()[:1200],
        "confidence": level if level in LEVELS else "low",
        "evidence": [str(e) for e in (ev if isinstance(ev, list) else [ev])][:20],
        "risks": [str(r) for r in (v.get("risks") or [])][:10] if isinstance(v.get("risks"), list) else [],
        "summary": str(v.get("summary") or v.get("summary_for_person") or "").strip()[:800],
    }


def to_answer(v: dict) -> tuple[str, str, dict]:
    """(decision, why, payload) for Engine.resume. The why starts with "supervisor:" so the gate log, the final review
    and the PR body show who decided."""
    payload: dict = {}
    if v.get("choice") is not None:
        payload["choice"] = v["choice"]
    if v.get("answers"):
        payload["answers"] = v["answers"]
    if v.get("commands"):
        payload["commands"] = v["commands"]
    decision = "reject" if v.get("action") == "send_back" else v.get("decision") or "approve"
    return decision, f"supervisor: {v.get('why') or 'no reason given'}", payload


# ------------------------------------------------------------------ the fake model (tests)

_SEEN: dict[str, int] = {}
NO_SCRIPT = {"action": "escalate", "confidence": "low", "why": "the fake model has no script (KEEL_FAKE_SUPERVISOR)",
             "summary": "Fake model: I do not know, please decide."}


def fake_verdict(req) -> str:
    """KEEL_FAKE_SUPERVISOR: a JSON file with one verdict, a list (one per turn, the last repeats) or {kind: verdict}
    (the brief's "KIND:" line picks one, "*" is the rest). Without it: escalate."""
    path = os.environ.get("KEEL_FAKE_SUPERVISOR", "")
    v = NO_SCRIPT
    if path and os.path.isfile(path):
        with open(path) as f:
            script = json.load(f)
        if isinstance(script, list) and script:
            n = _SEEN.get(path, 0)
            _SEEN[path] = n + 1
            v = script[min(n, len(script) - 1)]
        elif isinstance(script, dict) and "action" not in script:
            m = re.search(r"^KIND: (\S+)", req.prompt or "", re.M)
            v = script.get(m.group(1) if m else "", script.get("*", NO_SCRIPT))
        elif isinstance(script, dict):
            v = script
    return "Looked at it.\n```json\n" + json.dumps(v) + "\n```"


def reset_fake() -> None:
    _SEEN.clear()
