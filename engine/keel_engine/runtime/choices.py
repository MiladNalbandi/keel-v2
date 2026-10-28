"""choice_info: a waiting question's named choices as data a button can show (keel 0.19.0).

A gate with named choices kept only the names (`choices`) and wrote what each one does into its text ("- retry: ...").
Every client then read the meaning back out of the prose. Now each question that has `choices` also has `choice_info`,
in the same order:

    {"name": "retry", "label": "Try again", "description": "one more round, with your note for the agent",
     "why": "optional", "default": true}

`name` is what the resume sends as payload.choice; `label` is a short button text; `description` is the one line of
meaning (the gate's own prose when it has some, else this catalogue's); `why` says whether a note is "required" (the
answer is refused without one: compiler.answer_problem reads this), "optional" (a handler reads it: the next agent's
prompt, a record, the stop reason) or "none" (nothing reads it); `default` marks the one choice a plain approve takes.

`choices` and the prose stay as they were, for old clients and the MCP text.
"""
from __future__ import annotations

import re

REQUIRED, OPTIONAL, NONE = "required", "optional", "none"

# name -> (label, why, description): what a choice means when nothing more specific is known
GENERIC: dict[str, tuple[str, str, str]] = {
    "approve": ("Approve", NONE, "go on"),
    "retry": ("Try again", OPTIONAL, "one more round, with your note for the agent"),
    "recheck": ("Check again", NONE, "run the check again"),
    "skip": ("Skip this criterion", NONE, "move it to the end of the list and go on with the next criterion"),
    "edit": ("Amend the spec first", OPTIONAL, "amend the spec first (your note says what is wrong)"),
    "stop": ("Stop the flow", OPTIONAL, "stop the flow here (the commits so far stay)"),
    "reject": ("Send it back", REQUIRED, "send it back with what to change"),
    "rebuild": ("Rebuild from here", OPTIONAL, "start again in a new flow with what was learned"),
    "change": ("Change it", OPTIONAL, "it is close but wrong; your note says how"),
}

# (context, name) -> (label, why, description). The context is the gate's step id, or the kind of a check's question
# (stuck, blocked, cannot-run, usage, item). The why values follow what each handler reads.
CATALOGUE: dict[tuple[str, str], tuple[str, str, str]] = {
    # a provider's plan window nearly used (Compiler._provider_window)
    ("usage", "continue"): ("Continue anyway", NONE, "use the plan anyway (the provider may refuse when it is full)"),
    ("usage", "wait"): ("Wait for the reset", NONE, "sleep until the window resets, then go on"),
    ("usage", "cheaper"): ("Use the cheaper model", NONE, "switch to the cheaper model in settings"),
    ("usage", "stop"): ("Stop the flow", OPTIONAL, "end the flow here"),
    # a check that keeps failing, a blocked agent, a command that cannot start (the criterion's skip reads no note)
    ("stuck", "skip"): ("Skip this criterion", NONE, GENERIC["skip"][2]),
    ("blocked", "skip"): ("Skip this criterion", NONE, GENERIC["skip"][2]),
    ("cannot-run", "skip"): ("Skip this criterion", NONE, GENERIC["skip"][2]),
    ("cannot-run", "recheck"): ("Check again", NONE, "run the check again after you fixed .keel/config.yml"),
    # keel 0.18.22 the flow's first step could not set up the stack (Compiler.setup_gate, runtime/stack_setup.py)
    ("setup", "retry"): ("I fixed it, try again", NONE, "run the stack setup again (the packages, then each_worktree)"),
    ("setup", "skip"): ("Skip the setup", OPTIONAL, "go on without it; the flow's tests may fail until it is done"),
    ("setup", "stop"): ("Stop the flow", OPTIONAL, "end the flow here; nothing was changed"),
    # 0.18.57 the toolchain does not answer before the first step (Compiler.preflight_gate, runtime/container.py)
    ("preflight", "retry"): ("I started the container, check again", NONE, "run the preflight again"),
    ("preflight", "folder"): ("Run in the project folder", NONE,
                              "stop this worktree flow; start it again in the project folder, where the container sees it"),
    ("preflight", "skip"): ("Skip the preflight", OPTIONAL, "go on without the check; the tests fail until it answers"),
    ("preflight", "stop"): ("Stop the flow", OPTIONAL, "end the flow here; nothing was changed"),
    # a for_each item's gate (A5): skipping an item is recorded with its reason
    ("item", "approve"): ("Approve", NONE, "approve this item and go on"),
    ("item", "skip"): ("Skip this item", REQUIRED, "skip this item; the reason is recorded"),
    # feature
    ("feature:spec_gate", "approve"): ("Approve and freeze", NONE, "freeze the spec, commit it, go to the contract"),
    ("feature:spec_gate", "edit"): ("Edit the criteria", OPTIONAL, "change the criteria your note names, then come back here"),
    ("feature:spec_gate", "rewrite"): ("Rewrite a section", OPTIONAL, "rewrite the section your note names"),
    ("feature:spec_gate", "review"): ("Review it first", NONE, "an adversarial read first, shown here"),
    ("feature:spec_gate", "order"): ("Plan again", OPTIONAL, "the criteria are right but the plan under them is not"),
    ("feature:spec_gate", "reject"): ("Start the interview again", REQUIRED, "the spec misses the intent: the interview starts again"),
    ("feature:contract_gate", "approve"): ("Approve the contract", NONE, "commit the contract delta and go on to the criteria"),
    ("feature:contract_gate", "change"): ("Change the contract", OPTIONAL, "the contract agent writes it again with your note"),
    ("feature:contract_gate", "amend"): ("Amend the spec first", OPTIONAL,
                                 "the spec is wrong, not the contract: the draft goes back and the spec is amended"),
    ("feature:ready_gate", "ship"): ("Ship it", NONE, "ship runs its whole sequence now and stops next at the final review"),
    ("feature:ready_gate", "diff"): ("Show me the diff first", NONE, "the branch diff is shown, then this question again"),
    ("feature:ready_gate", "wrong"): ("Something is not right", REQUIRED,
                                      "say what; the spec gets a dated amendment and the criteria it reopens are built again"),
    ("feature:ready_gate", "stop"): ("Stop here", OPTIONAL, "stop before ship; the branch and its commits stay"),
    ("feature:e2e_tool", "write"): ("Write without running", NONE, "the specs are written and committed but not run"),
    ("feature:e2e_tool", "check"): ("Check again", NONE, "you set commands.e2e now; keel looks again"),
    ("feature:amend_loop", "first"): ("Build the other one first", NONE, "build the other criterion first, then this one"),
    ("feature:amend_loop", "skip"): ("Skip this criterion", NONE, GENERIC["skip"][2]),
    ("feature:amend_loop", "edit"): ("Amend again", OPTIONAL, "write the amendment again with your note"),
    ("feature:amend_gate", "approve"): ("Commit the amendment", NONE, "commit the amendment, then back to the criteria"),
    ("feature:amend_gate", "context"): ("Add what was missing", OPTIONAL, "tell keel what it was missing; the block is written again"),
    ("feature:amend_gate", "change"): ("Change the amendment", OPTIONAL, "the block is close but wrong; say how"),
    ("feature:amend_gate", "rebuild"): ("Rebuild from here", OPTIONAL, "the spec itself is wrong; a new feature flow starts"),
    # ship
    ("ship:memory_gate", "update"): ("Update it", NONE, "a librarian updates the stale sections (it costs tokens)"),
    ("ship:memory_gate", "skip"): ("Skip it", REQUIRED, "keep the knowledge base as it is; the reason goes in the PR body"),
    # fix and diagnose
    ("fix:gate_r", "investigate"): ("Yes, investigate", NONE, "this is the bug: find its cause"),
    ("fix:gate_r", "reject"): ("Reproduce differently", REQUIRED,
                               "not this bug: the test and its commit are dropped, the reproducer writes a new one with your note"),
    ("fix:gate_r", "stop"): ("Stop here", NONE, "stop; the reproducing test stays on the branch"),
    ("fix:gate_f", "fix"): ("Fix it this way", NONE, "the plan is right: fix the cause narrowly"),
    ("fix:gate_f", "investigate"): ("Investigate more", OPTIONAL,
                                    "the cause is not proven yet: new hypotheses and investigators, with your note"),
    ("fix:gate_f", "feature"): ("A missing requirement", NONE, "not a bug: start a feature flow with the evidence"),
    ("fix:gate_f", "reject"): ("Change the approach", REQUIRED, "the cause is right, the plan is not: it is written again with your note"),
    ("diagnose:decide", "fix"): ("Hand to the fix flow", NONE, "start the fix flow with the recipe"),
    ("diagnose:decide", "feature"): ("Hand to the feature flow", NONE, "start the feature flow with the evidence"),
    ("diagnose:decide", "unresolved"): ("Leave it unresolved", OPTIONAL, "record the investigation as unresolved (your note is kept)"),
    # cover
    ("cover:measure_failed", "try_again"): ("Measure again", NONE, "run the coverage command again"),
    ("cover:measure_failed", "go_on"): ("Go on without it", NONE, "go on to the coverage verdict without a measurement"),
    ("cover:no_reports", "go_on"): ("Go on", NONE, "go on to the coverage verdict; it stays failing"),
    ("cover:no_reports", "stop"): ("Stop the flow", OPTIONAL, "end here and configure coverage.reports first"),
    ("cover:decide", "test"): ("Write a test", OPTIONAL, "a test covers the group"),
    ("cover:decide", "delete"): ("Delete the lines", OPTIONAL, "the lines cannot be reached: delete them"),
    ("cover:decide", "accept"): ("Accept as uncovered", REQUIRED,
                                 "leave it uncovered; the reason is shown at the final review and in the PR body"),
    # change
    ("change:scope_gate", "small"): ("Small change", OPTIONAL,
                              "the test-first loop per criterion, one gate at the end (against a feature recommendation, "
                              "say why: it is recorded as an escalation override)"),
    ("change:scope_gate", "trivial"): ("Trivial change", NONE, "one refactor commit; existing tests stay untouched"),
    ("change:scope_gate", "feature"): ("Make it a feature", NONE, "hand the criteria to a feature flow"),
    # init
    ("init:git_repo", "init"): ("Make the first commit", NONE, "git init (if needed) and commit the project as it is now"),
    ("init:git_repo", "retry"): ("I did it, check again", NONE, "you made the repository and its first commit; keel looks again"),
    ("init:git_repo", "go_on"): ("Go on without git", OPTIONAL, "nothing is committed and verdicts carry no commit"),
    ("init:git_repo", "stop"): ("Stop init", OPTIONAL, "end init here (open the right folder first)"),
    ("init:rung_gate", "apply_fix"): ("Apply the fix", NONE, "keel runs the doctor's proposed commands, then the ladder again"),
    ("init:rung_gate", "retry"): ("I fixed it, run again", OPTIONAL, "you fixed it (or it was a passing problem); the ladder runs again"),
    ("init:rung_gate", "own_fix"): ("Run my own fix", REQUIRED, "write the fix commands, one per line; keel runs them where "
                                    "the rungs run, then the ladder again"),
    ("init:rung_gate", "skip"): ("Skip, not checked", OPTIONAL, "keep the rung, marked as not checked (setup.not_checked); the ladder goes on"),
    ("init:rung_gate", "stop"): ("Stop init", OPTIONAL, "end init here; no knowledge check and no commit"),
    ("init:audit_now", "skip"): ("Hand over now", NONE, "hand over now; nothing checked behaviour"),
    ("init:audit_now", "hunt"): ("Start a bug hunt", NONE, "start a bug hunt now (semi mode, whole project); it changes no code"),
    ("init:audit_now", "notes"): ("Build project notes", NONE, "start the knowledge-refresh flow: five librarians write "
                                  "the project notes and turn them on (it costs tokens; recommended for large repos)"),
    # review-pr: nothing reaches the author unless the person shares and then posts
    ("review-pr:share", "done"): ("Keep it here", NONE, "the review stays here; nothing is shared with the author"),
    ("review-pr:share", "share"): ("Make a text for the author", NONE,
                                   "a text from the sections you tick, shown to you first; nothing is posted yet"),
    ("review-pr:post", "keep"): ("Do not post", NONE, "nothing is posted; copy the text yourself if you want"),
    ("review-pr:post", "post"): ("Post it on the PR", NONE, "post this text as one comment on the PR (gh pr comment)"),
    # hunt
    ("hunt:confirm_lenses", "confirm"): ("Run these lenses", OPTIONAL, "sweep with the lenses ticked (or named in your note)"),
    ("hunt:confirm_lenses", "stop"): ("Stop the hunt", NONE, "end here; nothing is swept"),
    ("hunt:sweep_gate", "prove"): ("Prove them", NONE, "one prover per candidate, with the symptom only"),
    ("hunt:sweep_gate", "stop"): ("Stop here", NONE, "end here; the backlog keeps the candidates"),
    ("hunt:verdicts_gate", "report"): ("Write the report", NONE, "group shared causes and render the report"),
    ("hunt:verdicts_gate", "stop"): ("Stop here", NONE, "end here"),
    ("hunt:triage", "take"): ("Take the top group", NONE, "start hunt-next for the top group (or the one picked in the question)"),
    ("hunt:triage", "close"): ("Close a finding", REQUIRED, "close one finding as fixed, accepted or wontfix; say why"),
    ("hunt:triage", "stop"): ("Stop here", NONE, "end here; the backlog stays for hunt-next"),
    ("hunt:stack_gate", "recheck"): ("I started it: check again", NONE, "start the stack yourself, then keel checks its health again"),
    ("hunt:stack_gate", "prove"): ("Prove anyway", NONE, "send the provers with the stack down (most will come back unproven)"),
    ("hunt:stack_gate", "stop"): ("Stop here", NONE, "end here; the backlog keeps the candidates"),
    ("hunt-next:next_gate", "take"): ("Take this one", NONE, "hand the finding shown to a fix or feature flow"),
    ("hunt-next:next_gate", "pick"): ("Take the one I picked", NONE, "hand the finding or group picked in the question over instead"),
    ("hunt-next:next_gate", "stop"): ("Stop here", NONE, "end here; nothing is handed over"),
    # clarify: the gate's own text names the flow that fits the task type first (clarify_flow.gate_detail); a hand-over
    # reads the document again from its file (0.18.64)
    ("clarify:result", "feature"): ("Hand over to feature", NONE, "start the feature flow with the criteria and the answers"),
    ("clarify:result", "fix"): ("Hand over to fix", NONE, "start the fix flow with the bug report"),
    ("clarify:result", "change"): ("Hand over to change", NONE, "start the change flow with the prompt and the criteria"),
    ("clarify:result", "small"): ("Hand over to small", NONE, "start the small flow with the criteria"),
    ("clarify:result", "spike"): ("Hand over to spike", NONE, "start the spike flow: research the options first"),
    ("clarify:result", "prompt"): ("Copy the prompt only", NONE, "the prompt is shown to copy; no flow starts"),
    ("clarify:result", "reject"): ("Send it back", REQUIRED, "the outputs are patched with your note: the document as its "
                                                          "file is then is the draft (0.18.64)"),
    ("clarify:result", "stop"): ("Stop here", NONE, "end here; the document stays"),
}


def _spoken(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _known(context: str | None, name: str) -> tuple[str, str, str]:
    """The catalogue's entry for a name: the exact context, else a workflow gate under an include's prefix (ship's cover
    in feature is "feature:ship_cover_decide" for "cover:decide"), else GENERIC, else the name in words."""
    ctx = str(context or "")
    hit = CATALOGUE.get((ctx, name))
    if not hit and ":" in ctx:
        step = ctx.split(":", 1)[1]
        hit = next((v for (k, n), v in CATALOGUE.items() if n == name and ":" in k
                    and (step == k.replace(":", "_") or step.endswith("_" + k.replace(":", "_")))), None)
    return hit or GENERIC.get(name) or (_spoken(name), OPTIONAL, "")


def texts_from(detail: str | None, names: list[str]) -> dict[str, str]:
    """What a gate's own prose says each choice does: "- retry: ..." lines (also "- **retry**: ..."), "retry: ..." lines,
    or several on one line ("prove: ... stop: ..."). A choice the prose does not name is left out."""
    text = str(detail or "")
    if not names or not text:
        return {}
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # a name starts a line (after an optional "- ") or follows a sentence's end
    # (Markdown gate details write it bold or as code: "- **retry**: ...", "- `retry`: ...")
    marker = re.compile(rf"(?:(?<=\n)|^|(?<=[.!?] ))[ \t]*(?:[-*][ \t]+)?(?:\*\*|`)?({alt})(?:\*\*|`)?:[ \t]+", re.M)
    found = list(marker.finditer(text))
    out: dict[str, str] = {}
    for n, m in enumerate(found):
        end = found[n + 1].start() if n + 1 < len(found) else len(text)
        said = text[m.end():end].split("\n", 1)[0].strip()
        if said and m.group(1) not in out:
            out[m.group(1)] = said[:300]
    return out


def choice_info(names: list[str], texts: dict[str, str] | None = None, default: str | None = None,
                needs_why: dict[str, str] | None = None, context: str | None = None) -> list[dict]:
    """One entry per name, in order. `texts` (the gate's prose) wins over the catalogue's description; `needs_why`
    overrides the why of a name; `default` is the plain approve's choice (else the first)."""
    texts, needs_why = texts or {}, needs_why or {}
    names = [str(n) for n in names]
    pick = default if default in names else (names[0] if names else None)
    out = []
    for name in names:
        label, why, about = _known(context, name)
        out.append({"name": name, "label": label, "description": texts.get(name) or about,
                    "why": needs_why.get(name, why), "default": name == pick})
    return out


def for_question(question: dict, context: str | None = None, default: str | None = None,
                 needs_why: dict[str, str] | None = None) -> dict:
    """The question with its choice_info, from its `choices` and its own text (a question without choices: unchanged)."""
    names = [str(c) for c in question.get("choices") or []]
    if not names:
        return question
    return {**question, "choice_info": choice_info(names, texts_from(question.get("detail"), names), default, needs_why,
                                                   context)}


def keep(info: list[dict] | None, names: list[str]) -> list[dict]:
    """The entries of the names still offered, in their order, the first of them the default (a check's plain approve
    takes its first choice; past a limit some choices are left out)."""
    by = {c.get("name"): c for c in info or []}
    return [{**by[n], "default": k == 0} for k, n in enumerate(names) if n in by]


def picked(question: dict, decision: str, payload: dict | None) -> dict | None:
    """The choice_info entry an answer takes: payload.choice, else the default on approve (reject on a plain send-back
    when the gate has an exit named reject)."""
    info = question.get("choice_info") or []
    name = (payload or {}).get("choice")
    if name is None and (payload or {}).get("skip") is True:
        name = "skip"           # the older `skip: true` is the choice "skip" (and needs its reason too)
    if name is None and decision == "reject":
        name = "reject"
    if name is None and decision == "approve":
        return next((c for c in info if c.get("default")), None)
    return next((c for c in info if c.get("name") == str(name)), None)


# 0.18.28 what a plain approve and a plain reject (no choice) do at a waiting question, and whether the reason is used.
# KeelBot advised "send the gate back (reject) with this reason" at a gate whose reject only stopped the flow and handed
# the reason to nobody; now every client and KeelBot read the real effect. A question's producer may set its own
# `plain` ({"reject": {"does": ..., "reason": ...}}); else it follows from the question's kind and choices.
REASON = {REQUIRED: "required", OPTIONAL: "used", NONE: "not used"}


def _effect(c: dict) -> dict:
    return {"does": f'the choice "{c.get("name")}" ({c.get("label") or c.get("name")}): {c.get("description") or ""}'.rstrip(": "),
            "choice": c.get("name"), "reason": REASON.get(str(c.get("why")), "used")}


def plain_effects(question: dict) -> dict:
    """{"approve": {does, reason, choice?}, "reject": {...}} for this question; reason is required, used or not used."""
    q = question or {}
    given = q.get("plain") if isinstance(q.get("plain"), dict) else {}
    options = list(q.get("options") or ["approve", "reject"])
    names = [str(c) for c in q.get("choices") or []]
    info = list(q.get("choice_info") or (for_question(q, q.get("step")).get("choice_info") if names else []) or [])
    by = {c.get("name"): c for c in info}
    labels = q.get("labels") or {}
    kind = str(q.get("kind") or "gate")
    out: dict = {}
    if "approve" in options:
        pick = next((c for c in info if c.get("default")), None) or (info[0] if info else None)
        if pick:
            out["approve"] = _effect(pick)
        elif kind == "clarify":
            out["approve"] = {"does": labels.get("approve") or "sends the answers (an unanswered question takes its recommended option)",
                              "reason": "used"}
        else:
            out["approve"] = {"does": labels.get("approve") or "approves and goes on", "reason": "used" if kind == "fix" else "not used"}
    rc = q.get("reject_choice")
    if "reject" in by:
        out["reject"] = _effect(by["reject"])
    elif rc and rc in by:
        out["reject"] = _effect(by[rc])
    elif "reject" not in options:
        out["reject"] = {"does": "not taken here" + (": choose one of the choices" if names else ""), "reason": "not used"}
    elif names:
        out["reject"] = {"does": "stops the flow (the work so far stays)", "reason": "not used: it is only kept as the stop reason"}
    elif kind in ("fix", "budget", "usage"):
        out["reject"] = {"does": labels.get("reject") or "stops the flow", "reason": "not used: it is only kept as the stop reason"}
    else:
        out["reject"] = {"does": labels.get("reject") or "sends it back to the step before; the agent gets your reason",
                         "reason": "required"}
    for k, v in given.items():
        if k in ("approve", "reject") and isinstance(v, dict):
            out[k] = {**out.get(k, {}), **v}
    return out


def how_to(*, note: bool = True, plain: str | None = None) -> str:
    """The line that tells how to answer a gate with named choices:
    'Choose one (approve with payload {"choice": "<name>"} and a note; a plain approve is retry):'"""
    out = 'Choose one (approve with payload {"choice": "<name>"}'
    if note:
        out += " and a note"
    if plain:
        out += f"; a plain approve {plain}"
    return out + "):"
