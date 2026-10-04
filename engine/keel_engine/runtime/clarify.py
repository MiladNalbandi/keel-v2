"""The explorer's questions before a spec (keel v1's clarify loop, as buttons).

The explorer cannot talk to the user during its run. It ends its answer with one fenced block:

    ```keel-questions
    [{"id": "identity", "question": "...", "why": "...",
      "options": [{"label": "A · Named entity", "description": "...", "recommended": true}, ...]}]
    ```

keel shows the questions on the spec gate, the user clicks, and the answers go back to the same explorer session.
"""
from __future__ import annotations

import json
import re

MAX_QUESTIONS = 4
MAX_ROUNDS = 2          # after this many rounds the explorer must decide (recommended options) and write the spec

BLOCK = re.compile(r"```[ \t]*keel-questions[ \t]*\n(.*?)\n[ \t]*```", re.S | re.I)
PREFIX = "Answers to your questions:"


def parse_questions(text: str) -> list[dict]:
    """The valid questions in the last keel-questions block of a text; [] when there is none or it is broken."""
    blocks = BLOCK.findall(text or "")
    if not blocks:
        return []
    try:
        raw = json.loads(blocks[-1])
    except ValueError:
        return []
    if isinstance(raw, dict):
        raw = raw.get("questions")
    out: list[dict] = []
    for i, q in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        opts = []
        for o in (q.get("options") or [])[:4]:
            if isinstance(o, str):
                o = {"label": o}
            if isinstance(o, dict) and str(o.get("label") or "").strip():
                opts.append({"label": str(o["label"]).strip()[:120], "description": str(o.get("description") or "").strip()[:300],
                             "recommended": bool(o.get("recommended"))})
        if opts and not any(o["recommended"] for o in opts):
            opts[0]["recommended"] = True
        qid = re.sub(r"[^a-z0-9_-]+", "-", str(q.get("id") or f"q{i + 1}").lower()).strip("-") or f"q{i + 1}"
        out.append({"id": qid, "question": str(q["question"]).strip()[:300], "why": str(q.get("why") or "").strip()[:300],
                    "options": opts})
        if len(out) == MAX_QUESTIONS:
            break
    ids = [q["id"] for q in out]
    for n, q in enumerate(out):                       # ids must be unique: they key the answers
        if ids.count(q["id"]) > 1:
            q["id"] = f"{q['id']}-{n + 1}"
    return out


def describe(questions: list[dict]) -> str:
    """Plain text of the questions (the gate's detail, and the fallback for a client without the form)."""
    lines = []
    for n, q in enumerate(questions, 1):
        lines.append(f"{n}. {q['question']}")
        if q.get("why"):
            lines.append(f"   why it matters: {q['why']}")
        for o in q.get("options") or []:
            lines.append(f"   - {o['label']}{' (recommended)' if o.get('recommended') else ''}"
                         + (f": {o['description']}" if o.get("description") else ""))
    return "\n".join(lines)


def answers_text(questions: list[dict], answers: dict | None, note: str, round_no: int) -> str:
    """What the explorer reads next. An unanswered question gets its recommended option, and says so."""
    answers = answers or {}
    lines = [PREFIX]
    for n, q in enumerate(questions, 1):
        given = str(answers.get(q["id"]) or "").strip()
        if given:
            lines.append(f"{n}. {q['question']}\n   → {given}")
        else:
            rec = next((o for o in q.get("options") or [] if o.get("recommended")), None)
            pick = rec["label"] if rec else "your best judgement"
            lines.append(f"{n}. {q['question']}\n   → not answered: use {pick} and write it under Assumptions")
    if note.strip():
        lines.append(f"Also, in the user's words: {note.strip()}")
    if round_no >= MAX_ROUNDS:
        lines.append("This was the last round of questions. Do not ask again: decide what is still open "
                     "(recommended options), write it under Assumptions, and write the spec now.")
    else:
        lines.append("Now write the spec. Put each answer into the spec as a sentence under Decisions. "
                     "Ask again only if an answer opens a new, bigger question.")
    return "\n".join(lines)
