"""What the Supervisor may answer by itself, and the checks every verdict must pass before it is used.

Each kind of pause (keel_engine.runtime.run_mode.KINDS) has a rule:

    never      it does not look: only a person answers (a cap, a new dependency, a secret, the final review, the PR...)
    escalate   it looks and writes its suggestion for the person, but never answers
    may        it may answer when every check below passes

NEVER is a constant: no setting, prompt or file can change it. The other kinds take settings.supervisor.policy
({kind: "escalate" | "may"}) over DEFAULTS. A title that names a cap is never answered whatever its kind says.
"""

from __future__ import annotations

import re
from pathlib import Path

from keel_engine.runtime import finding_decisions, run_mode
from keel_engine.runtime.compiler import answer_problem

# 0.21.3 "publish": a gate whose own step or the step right after it starts, merges, pushes or publishes something (another
# flow, a PR, a ticket comment, a release): repo and PR text the Supervisor reads before it answers is not trusted, and
# an answer here acts outside the flow's own branch
NEVER = frozenset({"budget", "dependency", "secrets", "readonly", "note", "final-review", "pr", "publish"})
# the actions (workflow `action:`, the plugins' too) that act outside the flow: by name
OUTSIDE_ACTION = re.compile(r"^(?:start_flow|open_pr|pr|ship_ready|ticket_record|amend_start)$"
                            r"|(?:^|_)(?:push|merge|publish|release|share|comment|post|deploy|ship|tag|upload|send)(?:_|$)")
DEFAULTS = {
    "spec": "escalate", "contract": "escalate", "escalate": "escalate", "rounds": "escalate", "rung": "escalate",
    "clarify": "may", "ac": "may", "findings": "may", "choice": "may", "skip-menu": "may", "already-met": "may",
    "gate": "may", "failure": "may",
}
RULES = ("escalate", "may")
CAP_TITLE = re.compile(r"cap reached|token cap|cost cap|plan window|budget", re.I)

# why it did not answer: a fixed code each (the panel, the tests)
CODES = {
    "never-kind": "only you can answer this",
    "policy-escalate": "the settings say you decide this kind of gate",
    "model-escalated": "it was not sure and asks you",
    "low-confidence": "it was not sure enough",
    "no-evidence": "it named nothing that backs its answer",
    "bad-evidence": "what it named as evidence does not exist",
    "not-offered": "its answer is not one the question offers",
    "no-verdict": "its answer could not be read",
    "repeat": "it answered this before and the question came back",
    "too-many": "it answered too many gates in this flow",
    "budget-flow": "its next turn would pass the flow's token cap",
    "budget-own": "its own token cap is used up",
    "budget-day": "its token cap for this project today is used up",
    "turned-off": "it was turned off while it was looking",
    "shadow": "shadow mode: it only suggests",
    "model-error": "its model failed",
    "stale": "the flow moved on before it answered",
    "stalled": "the flow has been quiet for a long time",
    "fix-off": "it wants to make a small fix, but fix mode is off",
    "fix-no-check": "this gate cannot check a fix again",
    "fix-not-own-branch": "a fix is only made on the flow's own branch",
    "fix-readonly": "this flow runs read-only",
    "fix-no-phase": "the flow has no phase whose rules would hold for a fix",
    "fix-no-change": "its fix changed nothing",
    "fix-too-big": "its fix is over the size limit, so it put it back",
    "fix-never-file": "its fix touched a file only you change, so it put it back",
    "fix-git-moved": "its fix changed git (a commit, a branch or a push)",
}


def rule(kind: str, title: str = "", settings: dict | None = None) -> str:
    if kind in NEVER or CAP_TITLE.search(title or ""):
        return "never"
    want = ((settings or {}).get("policy") or {}).get(kind)
    return want if want in RULES else DEFAULTS.get(kind, "escalate")


def offered_problem(question: dict, decision: str, why: str, payload: dict) -> str | None:
    """Why this answer is not one the question offers, else None: keel's own checks (the same the resume makes), and
    each answer to the explorer's questions must be one of that question's labels."""
    said = answer_problem(question, decision, why, payload) or finding_decisions.problem(question, decision, why, payload)
    if said:
        return said[0]
    if payload.get("choice") is not None and not question.get("choices"):
        return "the question has no named choices"
    qs = {q.get("id"): q for q in question.get("questions") or []}
    for qid, ans in (payload.get("answers") or {}).items():
        q = qs.get(qid)
        if q is None:
            return f'there is no question "{qid}"'
        labels = {str(o.get("label")) for o in q.get("options") or []}
        picked = ans if isinstance(ans, list) else [ans]
        if not picked or any(str(p) not in labels for p in picked) or (len(picked) > 1 and not q.get("multiple")):
            return f'"{ans}" is not an option of "{qid}"'
    if decision == "approve" and "approve" not in (question.get("options") or ["approve", "reject"]):
        return "approve is not offered"
    return None


def evidence_problem(evidence: list, root: str, acs: list[dict]) -> str | None:
    """None when each item points at something that exists: "path/to/file:line" or "path" under the project folder,
    "spec:AC-3" (a criterion of the flow), "knowledge:...", "gate:...", "log:..." (the brief's own parts)."""
    if not evidence:
        return "no-evidence"
    ids = {a.get("id") for a in acs or []}
    base = Path(root).resolve() if root else None
    for item in evidence:
        text = str(item).strip()
        kind, _, rest = text.partition(":")
        if kind in ("knowledge", "gate", "log", "brief", "rule", "question"):
            continue
        if kind == "spec":
            ac = rest.split("#")[0].strip()
            if ac and ac.upper().startswith("AC-") and ac not in ids:
                return "bad-evidence"
            continue
        path = re.sub(r":\d+(?:-\d+)?$", "", text)
        if base is None:
            return "bad-evidence"
        target = (base / path).resolve()
        if base not in target.parents and target != base or not target.exists():
            return "bad-evidence"
    return None


def outside_actions(*steps) -> list[str]:
    """The actions of these workflow steps that act outside the flow (OUTSIDE_ACTION)."""
    return [a for s in steps if s is not None for a in (s.actions() if hasattr(s, "actions") else [])
            if OUTSIDE_ACTION.search(a)]


def kind_of(question: dict, step, flow: str, prev_actions, per_ac: bool, next_step=None) -> str:
    """run_mode's kind, except "publish" (never) when the gate step or the step its answer leads to starts, merges, pushes
    or publishes something (0.21.3)."""
    kind = run_mode.classify(question, step, flow=flow, prev_actions=prev_actions, per_ac=per_ac)
    if kind not in NEVER and step is not None and outside_actions(step, next_step):
        return "publish"
    return kind
