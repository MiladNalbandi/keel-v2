"""init's extra code steps (content/workflows/init.yaml).

    arch_detect   scores the architecture style (runtime/arch.py); low confidence -> one arch-surveyor item
    arch_set      writes architecture: {style, confidence, source} into .keel/config.yml (the surveyor's ARCH wins)
    ladder_soft   the run ladder as a lane that never fails the step: failing rungs become state.data.failed_rungs,
                  one setup-doctor each
    ladder_retry  runs the ladder again after the doctors; a rung that failed fix_attempts_per_rung times asks the user
                  (fix it / exclude it / accept it as not checked) through the rung gate
    rung_apply    applies that answer (config setup.ladder_exclude / setup.not_checked) and runs the ladder again

The ladder steps set the marker LADDER: green | retry | stuck (the latest value, "*"), which the init branches read.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from .. import rules
from . import arch
from .actions import ActionInput, ActionResult, ladder


def _marker(a: ActionInput, name: str, value: str, step: str) -> dict:
    mk = dict(a.state.get("markers") or {})
    mk[step] = {**(mk.get(step) or {}), name: value}
    mk["*"] = {**(mk.get("*") or {}), name: value}
    return mk


def _config_file(root: str) -> Path:
    return Path(root) / ".keel" / "config.yml"


def _edit_config(root: str, change) -> dict:
    """Read .keel/config.yml, apply change(dict), write it back (the header comment is kept)."""
    f = _config_file(root)
    text = f.read_text() if f.is_file() else ""
    head = "".join(line + "\n" for line in text.splitlines() if line.startswith("#")) if text else "# Written by keel v2 init. Edit freely.\n"
    cfg = (yaml.safe_load(text) or {}) if text else {"version": 4}
    change(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(head + yaml.safe_dump(cfg, sort_keys=False))
    return cfg


# ------------------------------------------------------------------ architecture

async def arch_detect(a: ActionInput) -> ActionResult:
    r = await asyncio.to_thread(arch.detect, a.root)
    survey = r["confidence"] == "low" and not a.settings.get("fast")
    items = [{"id": "architecture", "detection": {k: r.get(k) for k in ("style", "confidence", "hybrid_with", "evidence")},
              "modules": {d: {"style": m["style"], "confidence": m["confidence"], "scores": m["scores"]}
                          for d, m in (r.get("modules") or {}).items()},
              "question": "Detection is not sure. Which style does this codebase follow in practice? End with ARCH: <style> <confidence>."}] \
        if survey else []
    note = f"architecture: {r['style']} ({r['confidence']})" + (" · asking the arch-surveyor" if survey else "")
    return ActionResult(True, note, "\n".join(r.get("evidence") or []),
                        {"data": {**a.data, "arch": r, "arch_items": items}})


async def arch_set(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_arch_set, a)


def _arch_set(a: ActionInput) -> ActionResult:
    found = a.data.get("arch") or {}
    said = ((a.state.get("markers") or {}).get("arch_survey") or {}).get("ARCH") or ""
    words = said.lower().split()
    style, conf, source = found.get("style") or "unknown", found.get("confidence") or "low", "detected"
    if words and words[0] in [*arch.STYLES, "unknown"]:
        style, source = words[0], "surveyed"
        conf = words[1] if len(words) > 1 and words[1] in ("high", "medium", "low") else conf
    have = (rules.load_config(a.root) or {}).get("architecture") or {}
    if _config_file(a.root).is_file() and have.get("style") and have.get("source") not in ("detected", "surveyed"):
        return ActionResult(True, f"architecture: {have['style']} (set in .keel/config.yml; left as it is)")
    block = {"style": style, "confidence": conf, "source": source}
    if found.get("hybrid_with") and source == "detected":
        block["hybrid_with"] = found["hybrid_with"]

    def change(cfg):
        cfg["architecture"] = {**(cfg.get("architecture") or {}), **block}
    _edit_config(a.root, change)
    return ActionResult(True, f"architecture: {style} ({conf}, {source}) written to .keel/config.yml")


# ------------------------------------------------------------------ ladder repair

def _limit(a: ActionInput) -> int:
    n = a.settings.get("fix_attempts_per_rung") or ((rules.load_config(a.root) or {}).get("setup") or {}).get("fix_attempts_per_rung")
    return max(1, int(n or 3))


def _audit_detail(rungs: list[dict]) -> str:
    ran = [r for r in rungs if r.get("status") == "pass"]
    return (f"The ladder checked liveness only: {len(ran)} of {len(rungs)} steps passed, and none of them asked whether "
            "an endpoint behaves correctly.\n\nskip: hand over now; the backlog stays empty and nothing checked behaviour.\n"
            "hunt: start a bug hunt now (semi mode, whole project). It changes no code: hunters propose, provers "
            "reproduce against the running stack, and only what reproduces is reported.")


async def _ladder_state(a: ActionInput, attempts: dict, step: str) -> ActionResult:
    """Run the ladder; failing rungs -> failed_rungs, LADDER green | retry | stuck."""
    r = await ladder(a)
    rungs = r.update.get("ladder") or []
    failed = [x for x in rungs if x.get("status") == "fail"]
    limit = _limit(a)
    data = {**a.data, "rung_attempts": attempts, "rung_question": None, "audit_now_detail": _audit_detail(rungs)}
    data["failed_rungs"] = [{"id": f"rung-{x['n']}", "n": x["n"], "name": x["name"], "cmd": x["cmd"], "detail": x.get("detail"),
                             "attempts": attempts.get(str(x["n"]), 0),
                             "ask": "Find why this rung fails and fix what you safely can (a retry that changes nothing may "
                                    "run; never edit application code). End with DIAGNOSIS: fixable | needs-you | unknown."}
                            for x in failed]
    stuck = [x for x in failed if attempts.get(str(x["n"]), 0) >= limit]
    value = "green" if not failed else "stuck" if stuck else "retry"
    if stuck:
        x = stuck[0]
        data["rung_question"] = {"n": x["n"], "name": x["name"], "cmd": x["cmd"], "detail": x.get("detail")}
        data["rung_gate_detail"] = (f"Rung {x['n']} ({x['name']}) failed {attempts.get(str(x['n']))} time(s) after the setup "
                                    f"doctor's fixes.\n$ {x['cmd']}\n{x.get('detail') or ''}\n\n"
                                    "fix: you fixed it (or want more doctor rounds); the ladder runs again.\n"
                                    "exclude: leave this rung out of the ladder (setup.ladder_exclude).\n"
                                    "accept: keep it, marked as not checked (setup.not_checked).")
    note = r.note + {"green": "", "retry": " · back to the setup doctor", "stuck": f" · rung {stuck[0]['n'] if stuck else ''} asks you"}[value]
    return ActionResult(True, note, r.detail, {"ladder": rungs, "data": data, "markers": _marker(a, "LADDER", value, step)})


async def ladder_soft(a: ActionInput) -> ActionResult:
    return await _ladder_state(a, {}, "ladder")


async def ladder_retry(a: ActionInput) -> ActionResult:
    failed = a.data.get("failed_rungs") or []
    attempts = dict(a.data.get("rung_attempts") or {})
    if not failed:
        return ActionResult(True, "the ladder is green", update={"markers": _marker(a, "LADDER", "green", "ladder_retry")})
    for x in failed:
        attempts[str(x["n"])] = attempts.get(str(x["n"]), 0) + 1
    return await _ladder_state(a, attempts, "ladder_retry")


async def rung_apply(a: ActionInput) -> ActionResult:
    ans = a.data.get("rung_gate_answer") or {}
    q = a.data.get("rung_question") or {}
    if not ans or not q:
        return ActionResult(True, "nothing to apply")
    n, choice = int(q["n"]), ans.get("choice")
    attempts = dict(a.data.get("rung_attempts") or {})
    if choice == "fix":
        attempts[str(n)] = 0
    else:
        key = "ladder_exclude" if choice == "exclude" else "not_checked"

        def change(cfg):
            setup = cfg.setdefault("setup", {})
            setup[key] = sorted({*[int(x) for x in setup.get(key) or [] if str(x).isdigit()], n})
        await asyncio.to_thread(_edit_config, a.root, change)
    a.data = {**a.data, "rung_gate_answer": None}
    r = await _ladder_state(a, attempts, "rung_apply")
    r.note = f"rung {n}: {choice}" + (f" ({ans.get('why')})" if ans.get("why") else "") + " · " + r.note
    return r


ACTIONS = {"arch_detect": arch_detect, "arch_set": arch_set, "ladder_soft": ladder_soft, "ladder_retry": ladder_retry,
           "rung_apply": rung_apply}
