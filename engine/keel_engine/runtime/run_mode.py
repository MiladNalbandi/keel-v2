"""Run modes (v0.4.1): how much of a flow keel decides by itself. One small policy, read at every pause.

    manual     stop at every gate (the default; keel before v0.4.1)
    important  approve a per-criterion AC gate by itself when its checks passed and its AC review found nothing
               blocking; stop at everything else (spec, contract, final review, PR gate, failures, budget, ...)
    auto       approve every gate keel can decide by itself: a plain approve, the gate's default choice, the
               recommended answers to the explorer's questions. Each one is logged "auto-approved (mode auto)" in the
               gate log, so the final review's exceptions and the PR body list it. open_pr opens no PR in this mode.
    readonly   stop at every gate like manual; agents may not edit, write or commit anything in any phase (the guard
               context's `readonly` flag, the ToolBox, the diff guard and the commit action all refuse)

Safety stops, in every mode (`SAFETY`): the token cap and plan windows, a new dependency, a secret staged for commit, a
step that failed or a check that keeps failing after its rounds, a loop past its rounds, an init rung that keeps failing,
a read-only run's refused commit, and answers only a person can give (a note that closes a hunt finding). Stop and
rewind are the user's and work in every mode; keel never pushes.

The mode lives in the thread's settings (`settings.run_mode`, StartThread); POST /threads/{id}/mode changes it during a
run, from the next pause on. The compiler asks `decide` before every interrupt() (Compiler._ask).

Stdlib + keel_engine.rules only: the guard hook imports this module for `readonly_bash`.
"""

from __future__ import annotations

import re

MODES = ("manual", "important", "auto", "readonly")
DEFAULT = "manual"

ABOUT = {
    "manual": "Stops at every gate.",
    "important": "Approves a criterion's AC gate by itself when its checks passed and its review found nothing blocking. "
                 "Stops at the spec, the contract, the final review, the PR gate and every problem.",
    "auto": "Approves every gate keel can decide by itself and logs each one. Still stops for money, new dependencies, "
            "secrets, a check that keeps failing, and anything only you can answer. Never opens a PR or pushes.",
    "readonly": "Stops at every gate. Agents may read and run checks, but cannot edit, write or commit anything.",
}

# Gate kinds: what a pause is about (classify), and the plain words for each.
KINDS = {
    "ac": "a criterion's AC gate",
    "spec": "the spec approval (or an amendment)",
    "clarify": "the explorer's questions before the spec",
    "contract": "the contract gate",
    "final-review": "the final review",
    "pr": "the PR gate",
    "choice": "a gate with named choices (scope, gate R/F, cover decisions, hunt gates)",
    "skip-menu": "which optional steps run",
    "findings": "a review's blocking findings, within their fix rounds",
    "already-met": "a criterion whose test passes before any code",
    "escalate": "a change flow that should become a feature flow",
    "gate": "any other gate (integration, e2e, smoke, reports, init questions and plan)",
    # safety: never decided by keel
    "failure": "a step that failed, or a check that keeps failing after its rounds",
    "rounds": "a loop past its rounds (still no, findings past their limit)",
    "rung": "an init rung that keeps failing",
    "budget": "the token cap or a plan window",
    "dependency": "a new dependency",
    "secrets": "a secret staged for commit",
    "readonly": "a commit in a read-only run",
    "note": "an answer only a person can give",
}
SAFETY = frozenset({"failure", "rounds", "rung", "budget", "dependency", "secrets", "readonly", "note"})
AUTO_KINDS = frozenset(k for k in KINDS if k not in SAFETY)
# How often keel may approve the very same question by itself before it asks (a loop it cannot see the end of).
MAX_REPEATS = 3

# mode -> kind -> "ask" | "auto" | "auto-if-clean" (the AC gate in important mode: checks passed, review clean)
TABLE: dict[str, dict[str, str]] = {
    "manual": {k: "ask" for k in KINDS},
    "readonly": {k: "ask" for k in KINDS},
    "important": {k: ("auto-if-clean" if k == "ac" else "ask") for k in KINDS},
    "auto": {k: ("ask" if k in SAFETY else "auto") for k in KINDS},
}


def normalize(mode) -> str:
    m = str(mode or "").strip().lower()
    return m if m in MODES else DEFAULT


def line(mode: str) -> str:
    """What an automatic approval says: the gate log, the gate.decided event's why, the step note."""
    return f"auto-approved (mode {mode})"


def is_auto_line(text: str) -> bool:
    return "auto-approved (mode " in str(text or "")


def classify(question: dict, step=None, *, flow: str = "", prev_actions: list[str] | tuple = (), per_ac: bool = False) -> str:
    """The kind of one pause (see KINDS). `step` is the gate step (None for pauses that are not a gate step's)."""
    kind = str(question.get("kind") or "gate")
    title = str(question.get("title") or "")
    sid = str(question.get("step") or "")
    if kind in ("budget", "usage"):
        return "budget"
    if title.startswith("Approve new dependency"):
        return "dependency"
    if kind == "fix":
        return "failure"
    if kind == "clarify":
        return "clarify"
    if sid.endswith("__fix"):
        return "findings"
    if step is None:
        return "gate"
    if flow == "init" and step.id == "rung_gate":
        return "rung"
    if flow == "hunt-next" and step.id == "close_gate":
        return "note"
    if step.skip_menu:
        return "skip-menu"
    if step.report == "verdicts":
        return "final-review"
    if "pr" in (prev_actions or ()):
        return "pr"
    if step.phase == "contract":
        return "contract"
    if step.phase in ("spec", "triage"):
        return "spec"
    if per_ac:
        return "ac"
    if step.choices:
        return "choice"
    return "gate"


_AC_REVIEW = re.compile(r"^\s*(?:\*\*|__|`)?\s*AC-REVIEW\s*:\s*(?:\*\*|__)?\s*(pass|findings)\b", re.I | re.M)


def review_clean(text: str | None) -> bool:
    """An AC review with nothing blocking: no `AC-REVIEW: findings`, no Blocking items (an empty review is clean)."""
    if not text:
        return True
    said = _AC_REVIEW.findall(text)
    if said and said[-1].lower() == "findings":
        return False
    from .findings import blocking       # lazy: the guard hook imports this module and must stay light

    return not blocking(text)


def decide(mode: str, kind: str, question: dict, state: dict | None = None, *, review: str | None = None,
           repeats: int = 0) -> dict | None:
    """The answer keel gives itself for this pause, or None to ask the user.

    `review` = the AC review's answer for an AC gate (None when no review step ran before it); `repeats` = how often keel
    already approved this very question by itself in this run.
    """
    mode = normalize(mode)
    rule = TABLE[mode].get(kind, "ask")
    if rule == "ask" or kind in SAFETY:
        return None
    if "approve" not in (question.get("options") or ["approve", "reject"]):
        return None        # nothing keel could approve (a spec gate without criteria only takes a send-back)
    if repeats >= MAX_REPEATS:
        return None
    if rule == "auto-if-clean":
        st = state or {}
        if st.get("last_failure") or not review_clean(review):
            return None
    payload: dict = {}
    if kind == "clarify":
        payload["answers"] = {q["id"]: rec for q in question.get("questions") or []
                              if (rec := next((o.get("label") for o in q.get("options") or [] if o.get("recommended")), None))}
    return {"decision": "approve", "why": line(mode), "payload": payload, "auto": True, "kind": kind}


# ------------------------------------------------------------------ readonly: the guard's shell rule

_MUTATING = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:sudo\s+)?(?:rm|rmdir|mv|cp|mkdir|touch|truncate|dd|ln|chmod|chown|install|patch|tee|unzip|tar\s+-?x"
    r"|git\s+(?:add|commit|push|reset|checkout|switch|restore|stash|merge|rebase|cherry-pick|revert|tag|apply|am|clean|rm|mv|"
    r"branch\s+-[dDmM]|worktree|init|pull|fetch\s+.*--prune)"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:install|i|ci|add|remove|uninstall|update|upgrade|link|run\s+(?:format|fix|lint:fix))"
    r"|(?:pip|pip3|uv|poetry)\s+(?:install|add|remove|uninstall|sync|lock)|cargo\s+(?:add|install|update)|go\s+(?:get|mod\s+tidy)"
    r"|sed\s+-i|perl\s+-[a-z]*i)\b")
_REDIRECT = re.compile(r"(?<![0-9&])>{1,2}\s*([^\s;&|>]+)")


def readonly_bash(command: str) -> str | None:
    """The reason a shell command is refused in a read-only run (it changes files or git), else None.

    Reading, searching and running tests stay allowed. The diff guard after each step puts back whatever slips through.
    """
    cmd = str(command or "")
    for target in _REDIRECT.findall(cmd):
        t = target.strip("\"'")
        if t and not t.startswith("/dev/"):
            return f"This flow runs read-only (run mode readonly): the shell may not write {t}."
    m = _MUTATING.search(cmd)
    if m:
        return (f"This flow runs read-only (run mode readonly): \"{m.group(0).strip()}\" changes files or git. "
                "Read, search and run the tests only.")
    return None


READONLY_EDIT = "This flow runs read-only (run mode readonly): agents may not edit or write any file."
READONLY_MCP = "This flow runs read-only (run mode readonly): MCP tools that change things are refused."
