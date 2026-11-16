"""keel 0.19.0 harness mode: the harness flow's own steps (content/workflows/harness.yaml).

keel's harness plans and builds in one session of its own (`session: thread` on the agent steps; #101 keel's own
harness, on the default model's API, where 0.19.0 handed the session to a coding-agent app); these steps are keel's part
around it: what the person asked for, the plan and the change shown at the gates, and keel's own run of the tests.
"""

from __future__ import annotations

from ..tools import git, testcmd
from ..tools.agent_tools import command_env
from . import md
from .flow_actions import _fail, _ok

# What each of keel's own flows expects, said to the planner when the person asked for that flow (data.intent)
INTENTS = {
    "change": "This is a change to existing behaviour: keep it as small as the request allows.",
    "feature": "This is a new feature: list every acceptance criterion it needs, each with its own test.",
    "bug": "This is a bug: plan a test that reproduces it first (it fails today), then the fix that makes it pass, "
           "and name the root cause.",
    "fix": "This is a bug: plan a test that reproduces it first (it fails today), then the fix that makes it pass, "
           "and name the root cause.",
    "review-fix": "This fixes review findings: plan one change per finding, no other changes.",
}
OUTPUT_MAX = 6000


def intent(a):
    """The flow the person asked for (data.intent, set by the api when Settings › Who does the work sends new work to
    the harness) as a line for the planner's instructions ({{data.intent_note}}), and plan mode (data.plan_first: yes
    unless the api said no)."""
    kind = str(a.data.get("intent") or "change")
    note = INTENTS.get(kind, INTENTS["change"])
    plan = "no" if str(a.data.get("plan_first") or "yes").lower() in ("no", "false", "0") else "yes"
    return _ok(f"asked for: {kind}" + ("" if plan == "yes" else " (no plan first)"),
               update={"data": {**a.data, "intent": kind, "intent_note": note, "plan_first": plan}})


def plan_show(a):
    """What the plan gate shows: the harness's plan, word for word, and how to open its session."""
    plan = str(a.data.get("plan_text") or a.state.get("last_answer") or "").strip()
    if not plan:
        return _fail("The harness wrote no plan.", "Run the plan step again, or send it a note.")
    parts = [md.section("The plan", md.demote(plan))]
    opened = _open_line(a.data)
    if opened:
        parts.append(md.section("This session", opened))
    session = a.data.get("runner") == "session"
    who = "the connected session" if session else "the same harness session"
    if session:
        parts.append(md.section("Who planned it", _session_line(a.data, "plan")))
    parts.append(md.section("Your choice", md.bullets([
        f"**approve**: {who} builds this plan",
        f"**change**: your note goes back to {who}, which plans again",
        "**goldfish**: fresh reviewers read the plan in a flow of their own (design review); this gate waits here",
        "**reject**: stop here; nothing was changed"])))
    return _ok("plan ready for your look", update={"show": md.doc(*parts)})


def check(a):
    """keel's own run of the project's tests after the harness built the change. A project without a test command
    keel knows (no .keel/config.yml commands, no build file) passes with a note: the harness ran its tests itself."""
    cmd, rel = testcmd.located(a.root, None, "API")
    if not cmd:
        return _ok("no test command keel knows: the harness ran the tests itself (see its summary)",
                   update={"markers": {**(a.state.get("markers") or {}), a.step: {"RESULT": "skipped"}}})
    folder = f"{a.root}/{rel}" if rel else a.root
    code, out = testcmd.run(folder, cmd, 1800, command_env())
    shown = testcmd.shown(cmd, rel)
    if code == 0 and not testcmd.ran_no_tests(out):
        return _ok(f"tests pass: `{shown}`", tail(out, 1500))
    if why := testcmd.env_down(code, out):
        # 0.22.0 Docker or the database the tests need is down: the build session cannot fix that, so no round back to
        # it; the person decides (#67)
        from .actions import env_failure

        return env_failure(a, shown, out, "the change", why)
    why = "ran no tests" if code == 0 else f"exited {code}"
    return _fail(f"The project's tests failed: `{shown}` {why}.",
                 f"keel ran `{shown}` after your change and it {why}. Fix the cause in this session and run it again.\n\n"
                 + tail(out, OUTPUT_MAX))


def done_show(a):
    """What the done gate shows: the harness's summary, the change (not committed yet), and how to open the session."""
    from .feature_actions import show_diff

    diff = show_diff(a)
    summary = str(a.state.get("last_answer") or "").strip()
    session = a.data.get("runner") == "session"
    parts = []
    if summary:
        parts.append(md.section("What the connected session says it did" if session else "What the harness says it did",
                                md.demote(summary[:4000])))
    if session:
        parts.append(md.section("Who built it", _session_line(a.data, "build")))
        parts.append(md.section("What keel checked after the session's steps", _findings(a.data)))
    dirty = git.dirty(a.root) if git.is_repo(a.root) else {}
    if not dirty:
        parts.append(md.section("Change", "The harness changed no file."))
    parts.append(str((diff.update or {}).get("show") or ""))
    opened = _open_line(a.data)
    if opened:
        parts.append(md.section("This session", opened))
    parts.append(md.section("Your choice", md.bullets([
        "**commit**: keel commits the change (secret scan first), then the PR steps",
        f"**more**: your note goes back to {'the connected session' if session else 'the same session'}, which changes "
        "the code again",
        "**reject**: stop here; the change stays in the folder, not committed"])))
    return _ok(f"{len(dirty)} file(s) changed", update={"show": md.doc(*[p for p in parts if p])})


def _session_line(data: dict, kind: str) -> str:
    """0.29 the connected session's turn for this kind of step: who did it, and what it said it did."""
    log = [x for x in data.get("session_log") or [] if (x.get("kind") == "plan") == (kind == "plan")]
    if not log:
        return "The connected session (no report yet)."
    last = log[-1]
    todos = last.get("todos") or []
    done = sum(1 for t in todos if t.get("status") == "done")
    line = f"**{last.get('by') or 'the connected session'}**, reported {last.get('at') or ''}".rstrip(", ")
    if last.get("status") == "blocked":
        line += " · **blocked**"
    if todos:
        line += f" · its list: {done} of {len(todos)} done"
    return line + (". keel cannot check a session's tools before they run; it checks the change after each step, and "
                   "keel makes the commits.")


def _findings(data: dict) -> str:
    """0.29 what keel's checks after the connected session's steps found (session_steps.py): files put back, edit-tool
    findings, a HEAD that moved."""
    found = data.get("session_findings") or []
    if not found:
        return ("Nothing: every file it changed is allowed in its step's phase, and HEAD did not move (keel's diff guard and "
                "the project's edit tools ran after each step).")
    return md.bullets([f"{f.get('step')}: {f.get('text')}" for f in found[-30:]])


def _open_line(data: dict) -> str:
    h = data.get("harness") or {}
    if not h.get("open"):
        return ""
    return (f"The {h.get('provider')} session keel keeps for this flow. Open it in your own terminal to look or to "
            f"go on by hand:\n\n" + md.fence(h["open"], "sh"))


def tail(text: str, limit: int) -> str:
    from .actions import tail as _tail

    return _tail(text, limit)


ACTIONS = {"harness_intent": intent, "harness_plan_show": plan_show, "harness_check": check,
           "harness_done_show": done_show}
