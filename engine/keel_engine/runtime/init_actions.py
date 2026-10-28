"""init's extra code steps (content/workflows/init.yaml).

    git_check     not a git repository (or no commit yet): data.git_question, the git_repo gate asks init / retry /
                  go on without git / stop
    git_init      that gate's init: git init and the first commit (refused for files that look like secrets or
                  dependency folders)
    arch_detect   scores the architecture style (runtime/arch.py); low confidence -> one arch-surveyor item
    arch_set      writes architecture: {style, confidence, source} into .keel/config.yml (the surveyor's ARCH wins)
    ladder_soft   the run ladder as a lane that never fails the step: failing rungs become state.data.failed_rungs and
                  one setup-doctor item for all of them (the doctor is read-only: it diagnoses and proposes a fix)
    rung_report   the rung gate's text after the doctor: the failing rungs, the DIAGNOSIS, the proposed fix and the
                  commands apply_fix would run (data.rung_fix)
    rung_apply    applies the rung gate's answer: apply_fix (run the shown commands, then write the corrected rung
                  commands to .keel/config.yml), retry, skip (setup.not_checked); then the ladder runs again (stop ends
                  the flow at the gate)
    init_summary  the hand-over's text (data.hand_over_detail): rungs, sections, what was not checked, what next

The ladder steps set the marker LADDER: green | failing (the latest value, "*"), which the init branches read.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path

import yaml

from .. import rules
from . import arch, init_gates, md
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


_KNOWLEDGE_STYLE = re.compile(r"^\s*Style:\s*\**\s*`?([a-z][a-z-]*)`?\s*\**\s*\(\s*(high|medium|low)\b", re.I | re.M)


def arch_from_knowledge(root: str) -> str | None:
    """The architecture the knowledge base names (its architecture section's "Style: **layered** (high, ...)") when
    .keel/config.yml says unknown: an init's config said "unknown" while its knowledge base said a style with high
    confidence. A style a person set is never changed. Returns what it wrote, or None."""
    from . import knowledge
    have = (rules.load_config(root) or {}).get("architecture") or {}
    if not _config_file(root).is_file() or (have.get("style") or "unknown") != "unknown" \
            or have.get("source") not in (None, "detected", "surveyed"):
        return None
    f = Path(root) / knowledge.knowledge_dir(root) / "architecture.md"
    m = _KNOWLEDGE_STYLE.search(f.read_text(errors="replace")) if f.is_file() else None
    if not m or m.group(1).lower() not in arch.STYLES:
        return None
    style, conf = m.group(1).lower(), m.group(2).lower()

    def change(cfg):
        cfg["architecture"] = {**(cfg.get("architecture") or {}), "style": style, "confidence": conf, "source": "knowledge"}
    _edit_config(root, change)
    return f"architecture: {style} ({conf}) from the knowledge base written to .keel/config.yml (it said unknown)"


# ------------------------------------------------------------------ ladder repair

# The setup-doctor is read-only (content/agents/setup-doctor.md): it proposes, the person decides. The commands it
# wants keel to run come in one fenced block; the rung gate shows them and `apply_fix` runs them.
FIX_BLOCK = re.compile(r"```[ \t]*keel-fix[ \t]*\n(.*?)\n?[ \t]*```", re.S | re.I)
# 0.18.57 a rung whose own command is wrong (it must run in a container, in another folder, with other flags): the
# doctor names the corrected command in a keel-config block (`commands.<key>: <command>`), and apply_fix writes it to
# .keel/config.yml. Before, a corrected command ran once from the keel-fix block, passed, and the ladder ran the old
# command from the config again and failed on the same rung.
CONFIG_BLOCK = re.compile(r"```[ \t]*keel-config[ \t]*\n(.*?)\n?[ \t]*```", re.S | re.I)
_CONFIG_LINE = re.compile(r"^\s*(?:\$\s+)?commands\.([a-z_]+)\s*:\s*(.+?)\s*$")
DOCTOR_ASK = ("These setup ladder rungs fail. First look for a cause they share (one wrong host, port, environment "
              "variable, folder or missing tool often breaks several rungs at once; inside keel's container 'localhost' "
              "is the container itself, the host is 'host.docker.internal'). You change nothing: run read-only "
              "diagnostics, then say per rung the cause, the evidence and the exact fix, and whether the fix changes the "
              "machine, repo files or nothing. Never propose editing application code. When keel can apply the fix "
              "with shell commands in the project folder, put them in one ```keel-fix block (one command per line); "
              "the person sees them and decides. When a rung's own command is wrong (it must run in a container, in "
              "another folder or with other options), put the corrected command in one ```keel-config block as "
              "`commands.<the rung's key>: <command>` (one per line): keel writes it to .keel/config.yml, so the ladder "
              "and every flow run it from then on. The item says where keel runs the commands (runs_in): check the "
              "versions there (runtime, package manager), not only on the machine you are on, and write the keel-fix "
              "commands without that prefix (keel adds it). Compare the dependency manifest with its lock file: a lock "
              "older than the manifest needs a full update, not an update of one package. fixes_tried lists the fixes "
              "keel already ran and what they printed: never propose one of them again; say why it failed and what "
              "is different now, or answer needs-you. End with DIAGNOSIS: fixable | needs-you | unknown.")

# 0.18.63 a rung whose fix failed: the doctor proposed the same narrow fix again and again, and a plain approve took
# apply_fix each time (a seven-round loop). After FIX_ROUNDS failed fixes, or when the doctor proposes one that already
# failed, the gate offers no apply_fix and a plain approve stops init.
FIX_ROUNDS = 3


def _audit_detail(rungs: list[dict], notes_cost: str | None = None, notes_on: bool = False) -> str:
    ran = [r for r in rungs if r.get("status") == "pass"]
    notes = ("**notes**: build the project notes now (the knowledge-refresh flow: five librarians"
             + (f", {notes_cost}" if notes_cost else "") + "; recommended for large repos). "
             + ("They are already on: this writes them again." if notes_on else
                "They are off: agents read the code itself; you can also build them later from the Wiki."))
    return md.doc(
        md.section("What the ladder checked", f"The ladder checked liveness only: {len(ran)} of {len(rungs)} steps passed, and "
                   "none of them asked whether an endpoint behaves correctly. A bug hunt takes about 10-20 minutes and costs tokens."),
        md.section("Your choice", md.bullets([
            "**skip**: hand over now; the backlog stays empty and nothing checked behaviour.",
            "**hunt**: start a bug hunt now (semi mode, whole project). It changes no code: hunters propose, provers "
            "reproduce against the running stack, and only what reproduces is reported.", notes]),
            "Only you can answer this: keel never decides it by itself, in any run mode."))


def _notes_on(root: str) -> bool:
    from . import knowledge

    try:
        return knowledge.enabled(root)
    except Exception:  # noqa: BLE001 - a broken config must not hide the gate's text
        return False


async def _ladder_state(a: ActionInput, step: str, extra: dict | None = None) -> ActionResult:
    """Run the ladder; failing rungs -> failed_rungs, one setup-doctor item and the rung question; LADDER green | failing."""
    r = await ladder(a)
    rungs = r.update.get("ladder") or []
    failed = [x for x in rungs if x.get("status") == "fail"]
    base = {**a.data, **(extra or {})}
    attempts = dict(base.get("rung_attempts") or {})
    for x in failed:
        attempts[str(x["n"])] = attempts.get(str(x["n"]), 0) + 1
    data = {**base, "rung_attempts": attempts, "rung_question": None, "rung_fix": [], "rung_config": {},
            "audit_now_detail": _audit_detail(rungs, (a.init or {}).get("notes_cost"),
                                              bool((a.init or {}).get("write_sections")) or _notes_on(a.root))}
    data["failed_rungs"] = [{"id": f"rung-{x['n']}", "n": x["n"], "name": x["name"], "cmd": x["cmd"], "detail": x.get("detail"),
                             "attempts": attempts.get(str(x["n"]), 0), "key": rung_key(x)} for x in failed]
    # 0.18.15 ONE setup-doctor for all failing rungs, always the same item id: failing rungs often share one cause (a
    # wrong host, port, env or tool), one agent finds it once instead of ten agents each exploring from zero, and the
    # next round resumes the same agent's session (agent memory keys on the item), so it keeps what it learned.
    data["doctor_items"] = [{
        "id": "failing-rungs", "whole": True, "name": f"{len(failed)} failing rung{'s' if len(failed) != 1 else ''}",
        "rungs": data["failed_rungs"], "runs_on": str((a.init or {}).get("runs_on") or "this machine"),
        "runs_in": _runs_in(a), "fixes_tried": (data.get("rung_fixes_tried") or [])[-FIX_ROUNDS:],
        "ask": DOCTOR_ASK}] if failed else []
    if failed:
        x = failed[0]
        data["rung_question"] = {"n": x["n"], "name": x["name"], "cmd": x["cmd"], "detail": x.get("detail"),
                                 "rungs": [y["n"] for y in failed]}
    value = "failing" if failed else "green"
    note = r.note + (" · the setup doctor looks at it" if failed else "")
    return ActionResult(True, note, r.detail, {"ladder": rungs, "data": data, "markers": _marker(a, "LADDER", value, step)})


def _runs_in(a: ActionInput) -> str:
    """Where keel runs the rung and fix commands, in words for the doctor."""
    prefix = init_gates.run_prefix(a.root, a.init)
    if prefix:
        return f"in the container: every command runs as `{prefix} <command>`"
    if str((a.init or {}).get("runs_on") or "") == init_gates.DEV_CONTAINER:
        return "nowhere yet: the answer is dev container, but the project has no devcontainer.json keel can use"
    return "on this machine, in keel's own shell"


async def ladder_soft(a: ActionInput) -> ActionResult:
    return await _ladder_state(a, "ladder", {"rung_attempts": {}, "rung_fix_log": None, "rung_fixes_tried": []})


async def ladder_retry(a: ActionInput) -> ActionResult:
    """Runs the ladder again (kept for flows written before 0.18.22; init now asks after each doctor round)."""
    if not a.data.get("failed_rungs"):
        return ActionResult(True, "the ladder is green", update={"markers": _marker(a, "LADDER", "green", "ladder_retry")})
    return await _ladder_state(a, "ladder_retry")


def fix_commands(text: str) -> list[str]:
    """The commands of the doctor's last keel-fix block (comments and blank lines left out)."""
    blocks = FIX_BLOCK.findall(text or "")
    if not blocks:
        return []
    out = []
    for line in blocks[-1].splitlines():
        line = line.strip()
        if line.startswith("$ "):
            line = line[2:].strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out[:10]


def rung_key(rung: dict) -> str | None:
    """The .keel/config.yml command a rung runs (commands.<key>): its configured key, else the key its command goes
    to (rung 6's tests go to api_test_module, the key the flows read first). None for rungs 1 and 12."""
    from . import ladder as run_ladder

    if rung.get("key"):
        return str(rung["key"])
    n = int(rung.get("n") or 0)
    return "api_test_module" if n == 6 else run_ladder.CONFIG_KEY.get(n)


def config_fixes(text: str) -> dict[str, str]:
    """{key: command} of the doctor's last keel-config block (`commands.<key>: <command>` lines; other lines and keys
    keel does not run are left out)."""
    from . import container
    from . import ladder as run_ladder

    allowed = {*run_ladder.CONFIG_KEY.values(), *container.TEST_KEYS, *container.WRAP_KEYS, "preflight"}
    blocks = CONFIG_BLOCK.findall(text or "")
    out: dict[str, str] = {}
    for line in (blocks[-1].splitlines() if blocks else []):
        m = _CONFIG_LINE.match(line)
        if not m or m.group(1) not in allowed:
            continue
        cmd = m.group(2)
        if len(cmd) > 1 and cmd[0] == cmd[-1] and cmd[0] in "'\"":
            cmd = cmd[1:-1]
        out[m.group(1)] = cmd
    return dict(list(out.items())[:10])


def _tokens(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _is_correction(old: str, new: str) -> bool:
    """Is `new` the rung's command `old`, corrected: wrapped (`docker compose exec -T app <old>`, `cd api && <old>`,
    `env X=1 <old>`), or `old` run in a container with more options? A command that only starts or installs
    something is not."""
    from . import container

    a, b = _tokens(old), _tokens(new)
    if not a or a == b:
        return False
    if len(b) > len(a) and b[-len(a):] == a:
        return True
    ex = container.exec_of(new)
    rest = ex["rest"] if ex and not container.exec_of(old) else []
    return any(rest[i:i + len(a)] == a for i in range(len(rest) - len(a) + 1))


def corrected_commands(rungs: list[dict], ran: list[str]) -> dict[str, str]:
    """{config key: command} for each failing rung whose corrected command a fix ran with exit 0 (the last one wins)."""
    out: dict[str, str] = {}
    for x in rungs or []:
        key = rung_key(x)
        old = str(x.get("cmd") or "").strip()
        if not key or not old:
            continue
        new = next((c for c in reversed(ran) if _is_correction(old, c)), None)
        if new:
            out[key] = new
    return out


def _write_commands(a: ActionInput, sets: dict[str, str]) -> str:
    """Writes the corrected commands to .keel/config.yml. A corrected command that only runs the old one in a container
    (`docker compose exec -T app <old>`) takes the other backend commands into that container too (the toolchain lives
    there). Returns the log lines."""
    from . import container

    from .. import layout

    have = layout.raw_config(a.root).get("commands") or {}      # the project's own commands, not keel's defaults
    have = have if isinstance(have, dict) else {}
    sets = dict(sets)
    for key, cmd in list(sets.items()):
        ex = container.exec_of(cmd)
        old = str(have.get(key) or "").strip()
        if ex and old and cmd.endswith(" " + old) and not container.exec_of(old):
            prefix = cmd[:-len(old)].strip()
            sets = {**container.wrap({k: v for k, v in have.items() if k not in sets}, prefix), **sets}
    bad = [k for k, c in sets.items() if DESTRUCTIVE.search(c)]
    sets = {k: c for k, c in sets.items() if k not in bad}
    if sets:
        def change(cfg):
            cfg["commands"] = {**(cfg.get("commands") or {}), **sets}
        _edit_config(a.root, change)
    return "\n".join([f"wrote to .keel/config.yml: commands.{k}: {c}" for k, c in sets.items()]
                     + [f"not written (it deletes data or history): commands.{k}" for k in bad])


def _doctor_text(text: str) -> str:
    """The doctor's report without its keel-fix and keel-config blocks (shown on their own) and its DIAGNOSIS line."""
    text = CONFIG_BLOCK.sub("", FIX_BLOCK.sub("", text or ""))
    text = re.sub(r"(?im)^\s*DIAGNOSIS:.*$", "", text).strip()
    return text if len(text) <= 3000 else text[:3000].rsplit("\n", 1)[0] + "\n…"


async def rung_report(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_rung_report, a)


def _rung_report(a: ActionInput) -> ActionResult:
    """After the setup-doctor: the rung gate's text (the failing rungs, the DIAGNOSIS, the proposed fix, what it
    changes) and the commands apply_fix would run (data.rung_fix). Without commands the gate has no apply_fix."""
    q = a.data.get("rung_question") or {}
    if not q:
        return ActionResult(True, "no rung to decide")
    results = [x for x in a.data.get("doctor_results") or [] if isinstance(x, dict)]
    said = results[-1] if results else {}
    text = str(said.get("text") or "")
    diagnosis = str((said.get("markers") or {}).get("DIAGNOSIS") or "").strip().lower() or "unknown"
    cmds = fix_commands(text)
    sets = config_fixes(text)
    rungs = []
    for x in a.data.get("failed_rungs") or []:
        times = f" {x['attempts']} times" if x.get("attempts", 0) > 1 else ""
        out = str(x.get("detail") or "").strip()[-1200:]
        rungs.append(f"### Rung {x['n']} ({md.safe_words(x['name'])}) failed{times}\n\n{md.fence('$ ' + str(x['cmd']), 'bash')}"
                     + (f"\n\n{md.fence(out, 'text')}" if out else ""))
    parts = [md.section("Failing rungs", *rungs)]
    if a.data.get("rung_fix_log"):
        parts.append(md.section("What the last fix did", md.fence(str(a.data["rung_fix_log"])[-1500:], "text")))
    report = _doctor_text(text)
    parts.append(md.section("Diagnosis", f"**The setup doctor's DIAGNOSIS: {diagnosis}**" + {
        "fixable": " (it found the cause and a fix)", "needs-you": " (the fix needs you: the machine or a decision)",
        "unknown": " (it did not find the cause)"}.get(diagnosis, ""), md.demote(report) if report else ""))
    if cmds:
        parts.append(md.section("The proposed fix", "apply_fix runs these in the project folder, one after the other:",
                                md.fence("\n".join(f"$ {c}" for c in cmds), "bash")))
    elif not sets:
        parts.append(md.section("The proposed fix", "The doctor proposed no command keel can run: fix it yourself, then choose retry."))
    if sets:
        parts.append(md.section("The corrected commands", "apply_fix writes these to .keel/config.yml:",
                                md.fence(yaml.safe_dump({"commands": sets}, sort_keys=False).strip(), "yaml")))
    parts.append(md.section("Your choice", md.bullets(
        (["**apply_fix**: keel runs the commands above and writes a corrected rung command to .keel/config.yml, then "
          "the ladder runs again."] if cmds or sets else [])
        + ["**retry**: you fixed it (or it was a passing problem); the ladder runs again and the doctor looks again if it "
           "still fails.",
           "**own_fix**: write your own fix commands in your answer, one per line; keel runs them where the rungs "
           "run, then the ladder again.",
           f"**skip**: keep rung {q['n']} but mark it as not checked ({md.code('setup.not_checked')}); the ladder goes on "
           "with the next rungs.",
           "**stop**: end init here; nothing after the ladder runs (no knowledge check, no commit)."])))
    tried = [x for x in a.data.get("rung_fixes_tried") or [] if isinstance(x, dict)]
    failed_fixes = [x for x in tried if not x.get("ok")]
    keys = [x.get("key") for x in failed_fixes]
    # the doctor proposes a fix that failed before, or the same fix (yours, the Supervisor's) failed twice
    again = (bool(cmds or sets) and _fix_key(cmds, sets) in keys) or len(keys) != len(set(keys))
    stuck = again or len(failed_fixes) >= FIX_ROUNDS
    if stuck:
        why = ("A fix that keel already ran, and it failed, came again." if again else
               f"{len(failed_fixes)} fixes failed on this rung.")
        parts.insert(1, md.section("keel stops the loop", f"**{why}** keel does not run it again. Fix it yourself and "
                                   "choose retry, give your own commands with own_fix, skip the rung, or stop. A plain "
                                   "approve stops init."))
    lines = [md.doc(*parts)]
    data = {**a.data, "rung_fix": cmds, "rung_config": sets, "rung_gate_detail": "\n".join(lines).strip(),
            "rung_gate_without": [] if (cmds or sets) and not stuck else ["apply_fix"],
            "rung_gate_order": ["stop"] if stuck else []}
    note = ("loop stopped · " if stuck else "") + f"rung {q['n']}: DIAGNOSIS {diagnosis}" + (f", {len(cmds)} fix command(s) proposed" if cmds else
                                                      "" if sets else ", no command proposed") \
        + (f", {len(sets)} corrected command(s)" if sets else "")
    return ActionResult(True, note, update={"data": data})


# A fix keel never runs, even when the person chose apply_fix: it deletes data or history (the doctor is told so too).
DESTRUCTIVE = re.compile(r"\brm\s+-[a-z]*[rf]|\bgit\s+(reset|clean|checkout|push)\b|\bdocker\s+(system|volume|image)\s+(prune|rm)\b"
                         r"|\bdocker\s+compose\s+down\s+.*-v\b|\bmkfs\b|\bdd\s+if=|\bdrop\s+(database|table)\b|\bsudo\b", re.I)


def _apply_fix(a: ActionInput, cmds: list[str]) -> str:
    return _run_fix(a, cmds)[0]


def _run_fix(a: ActionInput, cmds: list[str]) -> tuple[str, bool]:
    """Runs the shown fix commands (the guard's setup-phase rules refuse what this phase may not do) where init's answer
    says the tools live (init_gates.run_prefix: a compose service, the dev container). Stops at the first failure.
    When all of them ran, writes the corrected rung commands to .keel/config.yml: the doctor's keel-config block, and a
    fix command that is a failing rung's command corrected (it passed, so the ladder must run it from now on).
    Returns (what happened, for the note and the next rung gate; did every command run)."""
    from . import container
    from ..tools import testcmd
    from ..tools.agent_tools import command_env

    cfg = rules.load_config(a.root)
    prefix = init_gates.run_prefix(a.root, a.init)
    if not prefix and cmds and str((a.init or {}).get("runs_on") or "") == init_gates.DEV_CONTAINER:
        return ("refused: the tools run in the dev container, but keel finds no .devcontainer/devcontainer.json to "
                "run the fix in; run it in the dev container yourself, then choose retry"), False
    out, ran = [], []
    for cmd in cmds:
        if DESTRUCTIVE.search(cmd):
            out.append(f"$ {cmd}\nrefused: keel does not run a command that deletes data or history; run it yourself "
                       "if you are sure, then choose retry")
            break
        v = rules.check_bash(a.phase or "setup", cmd, cfg, exists=lambda rel: (Path(a.root) / rel).exists())
        if not v.ok:
            out.append(f"$ {cmd}\nrefused: {v.reason}")
            break
        boxed = container.wrap_one(cmd, prefix) if prefix else cmd
        if a.fake:
            out.append(f"$ {boxed}\n(simulated)")
            ran.append(cmd)
            continue
        code, text = testcmd.run(a.root, boxed, 900, command_env())
        out.append(f"$ {boxed}\nexit {code}" + (f"\n{text.strip()[-600:]}" if text.strip() else ""))
        if code:
            break
        ran.append(cmd)
    if len(ran) == len(cmds):
        sets = {**corrected_commands(a.data.get("failed_rungs") or [], ran),
                **{str(k): str(v) for k, v in (a.data.get("rung_config") or {}).items()}}
        if sets:
            out.append(_write_commands(a, sets))
    return "\n".join(x for x in out if x), len(ran) == len(cmds)


def own_commands(ans: dict) -> list[str]:
    """The commands an own_fix answer gives: payload.commands (a list or lines), else the answer's text, one command per
    line (a leading `$ ` and comment lines left out)."""
    raw = (ans.get("payload") or {}).get("commands") or ans.get("why") or ""
    lines = raw if isinstance(raw, list) else str(raw).splitlines()
    out = []
    for line in lines:
        line = re.sub(r"^\s*\$\s+", "", str(line)).strip()
        if line and not line.startswith("#") and not line.startswith("```"):
            out.append(line)
    return out


def _fix_key(cmds: list[str], sets: dict | None = None) -> str:
    """The same fix, whatever its spacing: the commands and corrected commands, whitespace folded."""
    norm = [" ".join(str(c).split()) for c in cmds] + [f"{k}={' '.join(str(v).split())}" for k, v in sorted((sets or {}).items())]
    return "\n".join(norm)


# answers given before 0.18.22 (fix / exclude / accept) still apply
OLD_CHOICES = {"fix": "retry", "accept": "skip"}


async def rung_apply(a: ActionInput) -> ActionResult:
    ans = a.data.get("rung_gate_answer") or {}
    q = a.data.get("rung_question") or {}
    if not ans or not q:
        return ActionResult(True, "nothing to apply")
    n = int(q["n"])
    said = str(ans.get("choice") or "retry")
    choice = OLD_CHOICES.get(said, said)
    extra: dict = {"rung_gate_answer": None, "rung_fix_log": None}
    if choice in ("apply_fix", "own_fix"):
        own = choice == "own_fix"
        cmds = own_commands(ans) if own else [str(c) for c in a.data.get("rung_fix") or []]
        sets = {} if own else dict(a.data.get("rung_config") or {})
        if own:
            a.data = {**a.data, "rung_config": {}}
        if cmds or sets:
            log, ok = await asyncio.to_thread(_run_fix, a, cmds)
            tried = list(a.data.get("rung_fixes_tried") or [])
            tried.append({"by": "you" if own else "doctor", "commands": cmds, "config": sets, "key": _fix_key(cmds, sets),
                          "ok": ok, "log": log[-1500:]})
            extra.update(rung_fix_log=log, rung_fixes_tried=tried)
        else:
            extra["rung_fix_log"] = "no fix command to run" + (": write the commands in your answer, one per line" if own else "")
    elif choice in ("skip", "exclude"):
        key = "ladder_exclude" if choice == "exclude" else "not_checked"
        rungs = {n, *[int(x) for x in q.get("rungs") or [] if str(x).isdigit()]}

        def change(cfg):
            setup = cfg.setdefault("setup", {})
            setup[key] = sorted({*[int(x) for x in setup.get(key) or [] if str(x).isdigit()], *rungs})
        await asyncio.to_thread(_edit_config, a.root, change)
    a.data = {**a.data, "rung_gate_answer": None}
    r = await _ladder_state(a, "rung_apply", extra)
    r.note = f"rung {n}: {said}" + (f" ({ans.get('why')})" if ans.get("why") else "") + " · " + r.note
    if extra.get("rung_fix_log"):
        r.detail = f"{extra['rung_fix_log']}\n\n{r.detail}".strip()
    return r


# ------------------------------------------------------------------ git repo

# keel v1 BACKLOG: a `git init` with no first commit left verdicts keyed to no commit, and a commit in such a repo would
# sweep the whole untracked project into it. So the first commit is only ever the person's choice, and it refuses
# files that look like secrets or dependency folders (they belong in .gitignore first).
NOT_FIRST = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*)$|(^|/)(node_modules|\.venv|venv|vendor|target|"
                       r"build|dist)/", re.I)
NOT_FIRST_OK = re.compile(r"(^|/)\.env\.(example|sample|template|dist)$", re.I)


def _untracked(root: str) -> list[str]:
    from ..tools import git

    out = git.git(root, "ls-files", "--others", "--exclude-standard").stdout if git.is_repo(root) else ""
    return [f for f in out.splitlines() if f and f != ".keel" and not f.startswith(".keel/")]


async def git_check(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_git_check, a)


def _git_check(a: ActionInput) -> ActionResult:
    """keel v1's blocking git question: not a git repository, or one without a commit yet. Without a commit nothing can
    be committed and no verdict can name the code it checked; data.git_question makes the git_repo gate ask."""
    from ..tools import git

    repo = git.is_repo(a.root)
    if repo and git.head(a.root):
        return ActionResult(True, "a git repository", update={"data": {**a.data, "git_question": None, "git_ready": True}})
    top = sorted({p.name for p in Path(a.root).iterdir() if p.name not in (".git", ".keel")}) if Path(a.root).is_dir() else []
    what = ("This folder is not a git repository." if not repo else
            "This git repository has no commit yet (its project files are untracked).")
    detail = (f"{what} keel commits each step (the setup commit at the end of init) and stamps every verdict with a "
              "commit, so without one nothing is committed and no verdict says which code it checked.\n\n"
              f"In this folder ({len(top)} entries): {', '.join(top[:20])}{' …' if len(top) > 20 else ''}"
              + ("" if (Path(a.root) / ".gitignore").is_file() else "\nThere is no .gitignore: add one first if build "
                 "output or dependencies sit here.") + "\n\n"
              + ("init: " + ("run git init and " if not repo else "") + "commit the project as it is now as the first "
                 "commit (chore: initial commit). keel refuses when a file looks like a secret or a dependency folder.\n")
              + "retry: you made the repository and its first commit yourself; keel looks again.\n"
              "go_on: go on without commits; nothing is committed, and the hand-over says so.\n"
              "stop: end init here, for example to clone or open the right folder first.")
    return ActionResult(True, what, update={"data": {**a.data, "git_question": True, "git_ready": None,
                                                    "git_repo_detail": detail}})


async def git_init(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_git_init, a)


def _git_init(a: ActionInput) -> ActionResult:
    """When the git_repo gate's answer was init: git init (if needed) and the project's first commit, refused when a
    file looks like a secret or a dependency folder (the gate asks again and says which)."""
    from .. import rules as rules_mod
    from ..tools import git
    from .actions import commit_args

    if (a.data.get("git_repo_answer") or {}).get("choice") != "init":
        return ActionResult(True, "nothing to do")
    data = {**a.data, "git_repo_answer": None}
    if not git.is_repo(a.root):
        r = git.git(a.root, "init", "-q")
        if r.returncode:
            return ActionResult(False, "git init failed", (r.stderr or r.stdout or "").strip()[-1000:])
    files = _untracked(a.root)
    bad = [f for f in files if NOT_FIRST.search(f) and not NOT_FIRST_OK.search(f)]
    if bad or not files:
        why = (f"keel did not make the first commit: {len(bad)} file(s) look like secrets or dependencies: "
               + ", ".join(bad[:10]) + (" …" if len(bad) > 10 else "") + ". Add them to .gitignore (or remove them), "
               "then choose init again." if bad else "keel did not make the first commit: there is no file to commit.")
        detail = f"{why}\n\n{a.data.get('git_repo_detail') or ''}".strip()
        return ActionResult(True, why[:300], update={"data": {**data, "git_question": True, "git_ready": None,
                                                              "git_repo_detail": detail}})
    git.git(a.root, "add", "-A", "--", ".", ":!.keel")
    ident, trailer = commit_args(a.root, a.settings, rules_mod.load_config(a.root))
    r = git.git(a.root, *ident, "commit", "-q", "-m", "chore: initial commit", *trailer)
    if r.returncode:
        git.git(a.root, "reset", "-q")
        return ActionResult(False, "the first commit failed", (r.stderr or r.stdout or "").strip()[-1000:])
    return ActionResult(True, f"git: first commit of {len(files)} file(s)",
                        update={"data": {**data, "git_question": None, "git_ready": True}})


# ------------------------------------------------------------------ hand over

NEXT_FLOWS = [("change", "a small change to existing behaviour"), ("feature", "a new feature, spec first"),
              ("fix", "a bug: a failing test first, then the fix"),
              ("knowledge-refresh", "build the project notes, or write them again after the code changed")]


async def init_summary(a: ActionInput) -> ActionResult:
    return await asyncio.to_thread(_init_summary, a)


def _init_summary(a: ActionInput) -> ActionResult:
    """The hand-over's text (data.hand_over_detail): the rungs passed / not checked, the knowledge sections written or
    kept, what was not checked, the fast-init warning, and the flows to start next."""
    from .. import layout
    from ..tools import git
    from . import init_gates, notice

    rungs = a.state.get("ladder") or []
    by = lambda st: [r for r in rungs if r.get("status") == st]  # noqa: E731
    passed, skipped, failed = by("pass"), by("skipped"), by("fail")
    unchecked = [r for r in skipped if "not checked" in str(r.get("detail") or "") or "excluded" in str(r.get("detail") or "")]
    init = a.init or {}
    written = [s for s in (init.get("write_sections") if "write_sections" in init else init.get("knowledge_sections")) or []]
    kept = [s for s in init_gates.existing_sections(a.root) if s not in written]
    lay = layout.resolve(a.root)
    lines = [f"Setup ladder: {len(passed)} of {len(rungs)} rungs passed, {len(skipped)} skipped"
             + (f", {len(failed)} still failing" if failed else "") + f". The commands that passed are in {lay.runbook_file()}."]
    for r in unchecked:
        lines.append(f"  not checked: rung {r['n']} {r['name']} ({r.get('detail')})")
    for r in failed:
        lines.append(f"  failing: rung {r['n']} {r['name']} ($ {r.get('cmd')})")
    lines.append("Knowledge: " + (f"wrote {', '.join(written)}" if written else "wrote no section")
                 + (f"; kept {', '.join(kept)} as they were" if kept else "") + f" (in {lay.knowledge}/).")
    if not written and not kept:
        # 0.18.57 the project notes are opt-in: say they are off and how to build them
        cost = init.get("notes_cost")
        lines.append("Project notes: off. Agents read the code itself. Build them later (Wiki › Build them, or the "
                     f"knowledge-refresh flow){f', {cost}' if cost else ''}; recommended for large repos.")
    from . import project_rules as rules_mod

    lines += rules_mod.hand_over_lines(a.data.get("project_rules"), a.root)
    lines.append(f"Layout: {lay.name} — " + ("keel's files are committed with the code (docs/)." if lay.name == "repo"
                                             else "keel's files stay in .keel/, out of git."))
    lines += ["", "Not checked:", "- behaviour: the ladder checks that things start, not that endpoints do the right thing "
              "(a bug hunt checks behaviour)."]
    lines += [f"- rung {r['n']} {r['name']}" for r in unchecked + [r for r in skipped if r not in unchecked and r["n"] in ladder_optional()]]
    if not git.is_repo(a.root) or not git.head(a.root):
        lines.append("- git: this folder is not a git repository with a commit, so nothing was committed and verdicts "
                     "carry no commit.")
    if a.settings.get("fast"):
        lines += ["", "This was a fast init: rungs that passed before were re-used, so the services may not be running. "
                  "Start the stack (the services rung's command) before a bug hunt, or provers report every finding "
                  "as unproven."]
    lines += ["", "What next (start a flow):", *[f"- {name}: {about}" for name, about in NEXT_FLOWS],
              "", "Approve to finish init."]
    # 0.18.55 the notification's short lines, from the counts and names (the detail above stays the full text)
    short = notice.hand_over(len(passed), len(skipped), [r["name"] for r in failed], written, kept)
    return ActionResult(True, "hand-over summary ready",
                        update={"data": {**a.data, "hand_over_detail": "\n".join(lines), "hand_over_summary": short}})


def ladder_optional() -> set[int]:
    from . import ladder as run_ladder

    return set(run_ladder.OPTIONAL)


async def project_rules(a: ActionInput) -> ActionResult:
    """Finds the repo's own rules for coding agents (AGENTS.md, CLAUDE.md, .claude/skills, ...), writes the knowledge
    page project-rules.md and .keel/project-rules.json (which agents load each skill)."""
    from . import project_rules as rules_mod

    return await rules_mod.project_rules(a)


ACTIONS = {"arch_detect": arch_detect, "arch_set": arch_set, "project_rules": project_rules, "ladder_soft": ladder_soft, "ladder_retry": ladder_retry,
           "rung_apply": rung_apply, "rung_report": rung_report, "git_check": git_check,
           "git_init": git_init, "init_summary": init_summary}
