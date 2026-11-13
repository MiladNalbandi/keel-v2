"""The flow doctor's repairs (#101 part 3): a step that fails is named, logged, repaired when keel can, and otherwise
waits for the person with what happened and what to do, instead of ending the flow.

    step raises ─► classify ─► a repair keel can make (wait / fresh session / stale git lock), at most twice ─► run again
                       │                                       └── the step went through: "repaired" in the crash log
                       └── nothing keel can do, or the repairs did not help
                             ─► the flow waits: "<step> failed: <what happened>" with what to do: try again │ stop

Agent steps already waited for the person after a failed call (compiler_agent: kind "fix", approve = try again): they
call `before_asking` first and put `question_text` in that pause. Any other step that raises (a code step, a bug in
keel) goes through `treat`: the same repairs, then a pause of kind "crash" with Retry and Stop. A stop or a cancel is
never caught. After MAX_ASKS Retry answers for the same step the exception goes through (the flow fails, as before).
Every crash, repair and answer goes to the crash log (crashlog.py) and the thread's events (doctor.crash,
doctor.repair, doctor.repaired).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END

from . import classify as classify_mod, crashlog

MAX_REPAIRS = 2          # the doctor's own tries per step (a Retry from the person starts again)
MAX_ASKS = 5             # Retry answers per step before the flow fails as it did before the doctor
WAITS = (20.0, 90.0)     # seconds before the 1st and 2nd try after a busy, limited or unreachable provider
STALE_LOCK = 30.0        # seconds: an older .git/index.lock with no git running is left over
log = logging.getLogger(__name__)


def waits() -> tuple[float, ...]:
    """KEEL_DOCTOR_WAITS="0,0" (tests) or the defaults."""
    raw = os.environ.get("KEEL_DOCTOR_WAITS")
    if raw:
        try:
            return tuple(float(x) for x in raw.split(","))
        except ValueError:
            pass
    return WAITS


def _id(x) -> str:
    """A criterion's or an item's id (an item may be a plain value, not a dict)."""
    if isinstance(x, dict):
        return str(x.get("id") or "")
    return "" if x is None else str(x)[:80]


def step_key(step, state: dict) -> str:
    ac = state.get("ac") if getattr(step, "per_ac", False) else None
    item = state.get("item") if getattr(step, "per_item", False) else None
    return "|".join([step.id, _id(ac), _id(item)])


def _git_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "git"], capture_output=True, text=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def clear_stale_lock(root: str) -> bool:
    """Remove `<root>/.git/index.lock` when it is older than STALE_LOCK seconds and no git program runs."""
    lock = Path(root) / ".git" / "index.lock"
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    if age < STALE_LOCK or _git_running():
        return False
    try:
        lock.unlink()
        return True
    except OSError:
        return False


def _line(comp, step, state: dict, crash: classify_mod.Crash, exc: BaseException) -> dict:
    """The crash log's fields for this step and crash."""
    ctx = comp.ctx
    model: dict = {}
    if step.kind == "agent" and hasattr(comp, "_model"):
        try:
            model = comp._model(state, step, step.agent or "") or {}
        except Exception:  # noqa: BLE001 - the line is written without the model then
            model = {}
    return {"thread": ctx.thread_id, "project": Path(ctx.root).name, "workflow": comp.wf.id, "step": step.id,
            "agent": step.agent or None, "provider": model.get("provider"), "model": model.get("model"),
            "kind": crash.kind, "title": crash.title, "fix": crash.fix, "error": classify_mod.text_of(exc),
            "where": classify_mod.where(exc) if crash.kind in ("keel-bug", "unknown") else None}


async def before_asking(comp, step, state: dict, exc: BaseException) -> str | None:
    """A step failed: log it, and make the repair keel can make by itself (waiting first when that is the repair). Returns
    the repair made ("wait", "fresh", "git-lock"), after which the caller runs the step again, or None: ask the person.
    A problem of the doctor's own is logged and means None (the flow asks, as it did without the doctor)."""
    try:
        return await _before_asking(comp, step, state, exc)
    except (asyncio.CancelledError, GraphBubbleUp):
        raise
    except Exception:  # noqa: BLE001
        log.warning("the flow doctor failed on step %s", step.id, exc_info=True)
        return None


async def _before_asking(comp, step, state: dict, exc: BaseException) -> str | None:
    ctx = comp.ctx
    doc = ctx.doctor
    key = step_key(step, state)
    crash = classify_mod.classify(exc)
    line = _line(comp, step, state, crash, exc)
    ctx.emit("doctor.crash", step=step.id, data={k: line[k] for k in ("kind", "title", "fix", "error") if line.get(k)})
    tries = doc.setdefault("repairs", {}).get(key, 0)
    action = crash.repair if tries < MAX_REPAIRS else None
    if action == "git-lock" and not clear_stale_lock(ctx.root):
        action = None
    if not action:
        doc.setdefault("pending", {}).pop(key, None)
        return None
    doc["repairs"][key] = tries + 1
    wait = 0.0
    if action == "wait":
        ws = waits()
        wait = ws[min(tries, len(ws) - 1)] if ws else 0.0
    if action == "fresh":
        doc.setdefault("fresh", set()).add(step.id)
    crashlog.record(**line, outcome="retrying", action=action, attempt=tries + 1)
    ctx.emit("doctor.repair", step=step.id, data={"kind": crash.kind, "title": crash.title, "action": action,
                                                  "attempt": tries + 1, "wait": wait})
    doc["pending"] = {**doc.get("pending", {}), key: (line, action)}
    if wait:
        await asyncio.sleep(wait)
    return action


def went_through(comp, step, state: dict) -> None:
    """The step ran through: a repair made for it worked. Never raises (the doctor's books never fail a step)."""
    try:
        _went_through(comp, step, state)
    except Exception:  # noqa: BLE001
        log.warning("the flow doctor could not note that step %s went through", step.id, exc_info=True)


def _went_through(comp, step, state: dict) -> None:
    doc = comp.ctx.doctor
    key = step_key(step, state)
    doc.get("fresh", set()).discard(step.id)
    if doc.get("repairs", {}).get(key):
        doc["repairs"][key] = 0
    done = doc.get("pending", {}).pop(key, None)
    waited = doc.get("waited", {}).pop(key, None)
    if done:
        line, action = done
        crashlog.record(**line, outcome="repaired", action=action)
        comp.ctx.emit("doctor.repaired", step=step.id, data={"kind": line["kind"], "title": line["title"], "action": action})
    elif waited:
        crashlog.record(**waited, outcome="retried")     # the person's try again went through
    doc.get("asked", {}).pop(key, None)


def asking(comp, step, state: dict, exc: BaseException) -> tuple[classify_mod.Crash, int]:
    """The flow is about to wait for the person after this crash: log it. (crash, how many times this step asked)."""
    try:
        return _asking(comp, step, state, exc)
    except Exception:  # noqa: BLE001
        log.warning("the flow doctor could not log the pause of step %s", step.id, exc_info=True)
        return classify_mod.classify(exc), 1


def _asking(comp, step, state: dict, exc: BaseException) -> tuple[classify_mod.Crash, int]:
    doc = comp.ctx.doctor
    key = step_key(step, state)
    doc.get("fresh", set()).discard(step.id)
    crash = classify_mod.classify(exc)
    asked = doc.setdefault("asked", {}).get(key, 0) + 1
    doc["asked"][key] = asked
    doc.setdefault("repairs", {})[key] = 0          # an answer of try again gets the doctor's repairs again
    line = _line(comp, step, state, crash, exc)
    doc.setdefault("waited", {})[key] = line
    crashlog.record(**line, outcome="asked", attempt=asked)
    return crash, asked


def answered(comp, step, state: dict, exc: BaseException, again: bool) -> None:
    """The person answered the pause. Stop is logged here; a try again that goes through is logged by went_through."""
    if not again:
        try:
            comp.ctx.doctor.get("waited", {}).pop(step_key(step, state), None)
            crashlog.record(**_line(comp, step, state, classify_mod.classify(exc), exc), outcome="stopped")
        except Exception:  # noqa: BLE001
            log.warning("the flow doctor could not log the stop of step %s", step.id, exc_info=True)


def question_text(crash: classify_mod.Crash, exc: BaseException, tries: int = 0) -> str:
    """What happened and what to do, for the pause."""
    err = classify_mod.text_of(exc).strip()
    return (f"{crash.title}. {crash.fix}\n\nWhat happened ({crash.kind}): {err[:600]}"
            + (f"\n\nkeel tried {tries} repair(s) by itself first." if tries else ""))


async def treat(comp, i: int, step, state: dict, run, exc: BaseException):
    """A step other than an agent step raised. `run()` runs it once more and returns its (update, goto). Returns
    (update, goto), or raises `exc` after MAX_ASKS answers of Retry."""
    ctx = comp.ctx
    while True:
        action = await before_asking(comp, step, state, exc)
        if action:
            try:
                result = await run()
            except (asyncio.CancelledError, GraphBubbleUp):
                raise
            except Exception as again:  # noqa: BLE001 - treated on the next round
                exc = again
                continue
            went_through(comp, step, state)
            return result
        tries = ctx.doctor.get("repairs", {}).get(step_key(step, state), 0)
        crash, asked = asking(comp, step, state, exc)
        if asked > MAX_ASKS:
            crashlog.record(**_line(comp, step, state, crash, exc), outcome="failed")
            exc.keel_doctor_logged = True       # the engine's own "thread failed" line leaves it out
            raise exc
        from ..runtime import choices as choices_mod

        question = choices_mod.for_question({
            "step": step.id, "kind": "crash", "title": f"{step.name} failed: {crash.title}" + (f" (time {asked})" if asked > 1 else ""),
            "detail": question_text(crash, exc, tries) + "\n\nretry: run this step again (after you fixed it)\n"
                                                         "stop: end the flow here; its commits so far stay",
            "options": ["approve", "reject"], "choices": ["retry", "stop"], "crash": crash.as_dict()}, context="crash")
        answer, extra = comp._ask(state, question, gate="crash")
        choice = ((answer or {}).get("payload") or {}).get("choice")
        again = (answer or {}).get("decision") == "approve" and choice != "stop"
        answered(comp, step, state, exc, again)
        if not again:
            note = f"stopped after a crash: {crash.title}"
            return {**extra, "status": "stopped", "note": note, "error": f"{note}: {classify_mod.text_of(exc)[:300]}"}, END
        try:
            update, goto = await run()
        except (asyncio.CancelledError, GraphBubbleUp):
            raise
        except Exception as again_exc:  # noqa: BLE001
            exc = again_exc
            continue
        went_through(comp, step, state)
        return {**extra, **update}, goto
