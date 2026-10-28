"""What waits, for keel's MCP tools: questions and named choices as text, and as one elicitation form (keel 0.19.0).

A waiting question carries `questions` (runtime/clarify.py's shape) and, with named choices, `choice_info` (runtime/
choices.py: label, description, why, default). The read tools show both from that data, not from the gate's prose. A
client that supports elicitation can show the person one form for them (keel_answer with ask_person); its answer goes
back as payload.answers, payload.choice and why.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import Field, create_model

WHY_WORDS = {"required": "needs a reason", "optional": "a note helps", "none": ""}


def info_of(w: dict) -> list[dict]:
    """The waiting question's choice_info; for an older engine (names only) each name with what its prose says."""
    if w.get("choice_info"):
        return [dict(c) for c in w["choice_info"]]
    names = [str(c) for c in w.get("choices") or []]
    said = {}
    for line in str(w.get("detail") or "").splitlines():
        m = re.match(r"^\s*-\s*([a-z][\w-]*)\s*:\s*(.+)$", line)
        if m and m.group(1) in names:
            said[m.group(1)] = m.group(2).strip()
    if said:
        names = [c for c in names if c in said]      # the gate's text names the choices that apply now
    return [{"name": c, "label": c, "description": said.get(c, ""), "why": "optional", "default": i == 0}
            for i, c in enumerate(names)]


def choice_lines(w: dict, indent: str = "            ") -> list[str]:
    """One line per choice: name, its button text, what it does, the plain approve's pick, whether it needs a reason."""
    out = []
    for c in info_of(w):
        tags = [t for t in ("plain approve" if c.get("default") else "", WHY_WORDS.get(str(c.get("why")), "")) if t]
        label = f' "{c["label"]}"' if c.get("label") and c.get("label") != c.get("name") else ""
        out.append(f"{indent}{c['name']}{label}: {c.get('description') or ''}".rstrip()
                   + (f"  [{', '.join(tags)}]" if tags else ""))
    return out


def question_lines(questions: list[dict] | None, indent: str = "          ") -> list[str]:
    """Each question with its id, why, and options (description, recommended); a multiple one says "pick any"."""
    out = []
    for q in questions or []:
        how = "  (pick any)" if q.get("multiple") else "  (pick one)" if q.get("options") else "  (your words)"
        out.append(f"{indent}{q.get('id')}: {q.get('question')}{how}")
        if q.get("why"):
            out.append(f"{indent}  why: {q['why']}")
        for o in q.get("options") or []:
            out.append(f"{indent}  - {o.get('label', '')}" + (" (recommended)" if o.get("recommended") else "")
                       + (f": {o['description']}" if o.get("description") else "")
                       + (f'  [needs a reason: "{o.get("label")}: <reason>"]' if o.get("why") == "required" else ""))
    return out


def how_to_answer(w: dict) -> str:
    """The one line that says how to send an answer to this question with keel_answer."""
    parts = []
    if w.get("questions"):
        parts.append("answers={<question id>: <label> | [<labels>] | <your words>}")
    if w.get("choices"):
        parts.append('choice="<name>"')
    if any(c.get("why") == "required" for c in info_of(w)) or "reject" in (w.get("options") or []):
        parts.append('why="<reason>"')
    return "answer with keel_answer(" + ", ".join(parts or ["decision=\"approve\""]) + ")"


def needs_answers(w: dict, decision: str, answers: dict | None, choice: str | None) -> bool:
    """An approve of a question gate (kind clarify: the interview, per-finding decisions, a doctor's rung...) with no
    answers yet: the tool asks the person (a form) or shows the questions, instead of sending an empty answer."""
    return bool(decision == "approve" and not answers and not choice and w.get("questions") and w.get("kind") == "clarify")


def text_of(w: dict) -> str:
    """The waiting question as text: its questions, its choices and how to answer (a client without forms)."""
    return "\n".join([f"{w.get('kind', 'gate')}: {w.get('title', '')}", *question_lines(w.get("questions"), "  "),
                      *(["  choices:", *choice_lines(w, "    ")] if w.get("choices") else []), how_to_answer(w)])


def picked(w: dict, decision: str, choice: str | None) -> dict | None:
    """The choice an answer takes (its choice, else the default on approve), as choice_info has it."""
    info = info_of(w) if w.get("choices") else []
    name = choice or ("reject" if decision == "reject" else None)
    if name is None and decision == "approve":
        return next((c for c in info if c.get("default")), None)
    return next((c for c in info if c.get("name") == name), None)


def did_text(answered: dict | None, decision: str, choice: str | None, w: dict | None = None) -> str:
    """0.18.64 what an answer really did, from the engine's `answered` (the choice that ran): a reject with the choice
    edit runs edit, and keel_answer said "(reject)". An older engine sends none: the choice given, else the default."""
    a = dict(answered or {})
    if not a and w:
        c = picked(w, decision, choice) if w.get("choices") else None
        a = {"choice": c.get("name"), "does": f'the choice "{c.get("name")}" ({c.get("label") or c.get("name")})'} if c else {}
    took = a.get("choice") or choice
    does = str(a.get("does") or "").strip()
    if took:
        out = does if does.startswith("the choice") else f'the choice "{took}"' + (f": {does}" if does else "")
        out = "ran " + out
        if decision == "reject" and took != "reject":
            out += f' (the choice decides: "{took}" ran, not a plain reject)'
        return out
    return ("approved" if decision == "approve" else "sent back") + (f": {does}" if does else "")


def missing_reason(w: dict, decision: str, choice: str | None, why: str | None) -> str | None:
    """The refusal keel would give for a choice that needs a reason and has none, said before it is sent."""
    c = picked(w, decision, choice)
    if c and c.get("why") == "required" and not (why or "").strip():
        return f'"{c.get("label") or c.get("name")}" needs a reason: pass why.'
    return None


# ---------------------------------------------------------------- elicitation

def form(w: dict):
    """(the pydantic model of one elicitation form for this question, a function from its answer to (decision, why,
    payload)), or None when there is nothing to ask in a form. Single questions are an enum, "multiple" ones a list of
    an enum, each with an own-words field; named choices an enum of their labels with a why field."""
    fields: dict[str, Any] = {}
    qs = list(w.get("questions") or [])
    for n, q in enumerate(qs, 1):
        labels = [str(o.get("label")) for o in q.get("options") or [] if o.get("label")]
        recs = [str(o.get("label")) for o in q.get("options") or [] if o.get("recommended") and o.get("label")]
        title, about = str(q.get("question") or q.get("id")), str(q.get("why") or "")
        if q.get("multiple"):
            fields[f"q{n}"] = (list[str], Field(default=recs, title=title, description=about,
                                                json_schema_extra={"items": {"type": "string", "enum": labels}}))
        elif labels:
            fields[f"q{n}"] = (str, Field(default=(recs or labels)[0], title=title, description=about,
                                          json_schema_extra={"enum": labels}))
        needs = [str(o.get("label")) for o in q.get("options") or [] if o.get("why") == "required" and o.get("label")]
        own_title = (f"{title} (your reason for {' / '.join(needs)}, or your own words)" if needs
                     else f"{title} (or in your own words)" if labels else title)
        fields[f"q{n}_own_words"] = (str, Field(default="", title=own_title, description="" if labels else about))
    info = info_of(w) if w.get("choices") else []
    by_label = {str(c.get("label") or c["name"]): c["name"] for c in info}
    if info:
        default = next((c for c in info if c.get("default")), info[0])
        fields["choice"] = (str, Field(default=str(default.get("label") or default["name"]), title="Choose one",
                                       description="; ".join(f"{c.get('label') or c['name']}: {c.get('description') or ''}"
                                                             for c in info)[:900],
                                       json_schema_extra={"enum": list(by_label)}))
    elif not qs and len(w.get("options") or []) > 1:
        fields["decision"] = (str, Field(default="approve", title="Decision", json_schema_extra={"enum": list(w["options"])}))
    if not fields:
        return None
    needs = [c.get("label") or c["name"] for c in info if c.get("why") == "required"]
    fields["why"] = (str, Field(default="", title="Why" + (f" (needed for: {', '.join(needs)})" if needs else " (optional)")))
    model = create_model("KeelAnswer", **fields)

    def to_answer(data) -> tuple[str, str | None, dict]:
        got = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        answers: dict[str, Any] = {}
        for n, q in enumerate(qs, 1):
            own = str(got.get(f"q{n}_own_words") or "").strip()
            val = got.get(f"q{n}")
            if q.get("multiple"):
                val = [str(x) for x in val or []] + ([own] if own else [])
                if val:
                    answers[q["id"]] = val
            elif own or val:
                # an option that needs a reason (a findings gate's "accept"): the own words are its reason
                opt = next((o for o in q.get("options") or [] if o.get("label") == val), None)
                answers[q["id"]] = f"{val}: {own}" if own and opt and opt.get("why") in ("required", "optional") else (own or str(val))
        payload: dict[str, Any] = {}
        if answers:
            payload["answers"] = answers
        if info and got.get("choice"):
            payload["choice"] = by_label.get(str(got["choice"]), str(got["choice"]))
        decision = str(got.get("decision") or "approve")
        return decision, (str(got.get("why") or "").strip() or None), payload

    return model, to_answer


def can_elicit(ctx) -> bool:
    try:
        return bool(ctx and ctx.session.client_params and ctx.session.client_params.capabilities.elicitation)
    except Exception:  # noqa: BLE001
        return False


async def ask(ctx, w: dict) -> tuple[str, str | None, dict] | str:
    """Asks the person the waiting question through the client: (decision, why, payload), or the text to show instead
    (the client cannot ask, the person declined, nothing to ask in a form)."""
    built = form(w)
    text = text_of(w)
    if not built:
        return text
    if not can_elicit(ctx):
        return "keel: this client cannot show a form, so here is the question as text: pass answers (and choice) to the tool.\n" + text
    model, to_answer = built
    detail = str(w.get("detail") or "").strip()
    res = await ctx.elicit(message=f"keel: {w.get('title', 'a question')}" + (f"\n\n{detail[:600]}" if detail else ""),
                           schema=model)
    if res.action != "accept":
        return "keel: not answered — you closed the form. The question still waits.\n" + text
    return to_answer(res.data)
