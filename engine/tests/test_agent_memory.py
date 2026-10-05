"""Agents remember their step: a restart, a try again or a send-back continues the agent's own session."""
import json

import aiosqlite

from conftest import decide, start, wait
from keel_engine.models.base import AgentRequest
from keel_engine.models.cli_runners import ClaudeCLIRunner
from keel_engine.runtime import memory as mem
from keel_engine.tools.agent_tools import ToolBox
from test_v02 import script

RESULT = {"type": "result", "result": "done", "usage": {"input_tokens": 10, "output_tokens": 2}}


async def test_store_keeps_session_trail_and_status(tmp_path):
    async with aiosqlite.connect(tmp_path / "m.db") as conn:
        await conn.execute(mem.SCHEMA)
        m = mem.AgentMemory(conn, "t1")
        k = mem.attempt_key("spec", None, "explorer", 0, None)
        await m.start(k, "claude", "/r", "sess-1", keep_trail=False)
        m.note(k, "read docs/roadmap.md")
        m.set_session(k, "sess-1")
        await m.flush()
        got = await m.get(k)
        assert got["status"] == "running" and got["session"] == "sess-1" and got["trail"] == ["read docs/roadmap.md"]
        await m.finish(k, "done", "spec written")
        assert (await m.get(k))["said"] == "spec written"
        await m.clear()
        assert await m.get(k) is None


def test_what_the_agent_is_told():
    assert "keel was restarted while you were working" in mem.resume_note({"status": "running"}, True)
    assert "earlier conversation" in mem.resume_note({"status": "done"}, True)
    note = mem.resume_note({"status": "running", "trail": ["read a.md", "Bash: ls"], "said": ""}, False)
    assert "stopped by a keel restart" in note and "- read a.md" in note
    assert mem.trail_line("read", "x", {"path": "src/a.py"}) == "read src/a.py"
    assert mem.trail_line("text", "hello", {}) is None


def _claude(tmp_path, out_lines):
    (tmp_path / "claude.out").write_text("\n".join(json.dumps(x) for x in out_lines) + "\n")
    return script(tmp_path, "claude", f"""cat > {tmp_path}/prompt
(echo "=== PROMPT"; cat {tmp_path}/prompt) >> {tmp_path}/prompts.log
echo "=== CALL $@" >> {tmp_path}/argv.log
cat {tmp_path}/claude.out
""")


def calls(tmp_path):
    return [c for c in (tmp_path / "argv.log").read_text().split("=== CALL")[1:]]


def _req(root, **kw):
    return AgentRequest(agent="explorer", system="", prompt="go", root=str(root), phase="spec",
                        model={"provider": "claude", "mode": "subscription", "model": "haiku"},
                        toolbox=ToolBox(str(root), "spec"), workdir=str(root), **kw)


async def test_claude_new_session_resume_and_lost_session(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("KEEL_CLAUDE_BIN", _claude(tmp_path, [RESULT]))
    await ClaudeCLIRunner().run(_req(repo, session="s-1"), lambda *a, **k: None)
    await ClaudeCLIRunner().run(_req(repo, session="s-1", resume=True), lambda *a, **k: None)
    log = calls(tmp_path)
    assert "--session-id s-1" in log[0] and "--no-session-persistence" not in log[0]
    assert "--resume s-1" in log[1]
    assert (tmp_path / "data" / "agent-home" / "claude" / ".claude.json").is_file()

    # The saved session is gone: keel starts a new one instead of failing the step.
    lost = {"type": "result", "subtype": "error_during_execution", "is_error": True,
            "errors": ["No conversation found with session ID: s-9"]}
    monkeypatch.setenv("KEEL_CLAUDE_BIN", script(tmp_path, "claude", f"""cat > /dev/null
echo "=== CALL $@" >> {tmp_path}/argv.log
case "$*" in *--resume*) echo '{json.dumps(lost)}' ;; *) echo '{json.dumps(RESULT)}' ;; esac
"""))
    new = []
    res = await ClaudeCLIRunner().run(_req(repo, session="s-9", resume=True, on_session=new.append), lambda *a, **k: None)
    assert res.text == "done" and len(new) == 1
    assert f"--session-id {new[0]}" in calls(tmp_path)[-1]


def test_a_send_back_continues_the_explorers_own_session(client, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    spec = {"type": "result", "result": "- **AC-1** [API] A player's rank is shown next to their best score",
            "usage": {"input_tokens": 10, "output_tokens": 2}}
    monkeypatch.setenv("KEEL_CLAUDE_BIN", _claude(tmp_path, [spec]))
    tid = start(client, repo, models={"default": {"provider": "claude", "mode": "subscription", "model": "haiku"}})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate", s
    first = calls(tmp_path)[0]
    sid = first.split("--session-id ")[1].split()[0]
    n = len(calls(tmp_path))          # the spec, one explorer per area, the plan
    s = decide(client, tid, "reject", why="ties must share a rank")
    second = calls(tmp_path)[n]         # the spec step again, after the spec gate's reject
    assert f"--resume {sid}" in second
    prompt = (tmp_path / "prompts.log").read_text().split("=== PROMPT")[1:][n]
    assert "earlier conversation" in prompt and "ties must share a rank" in prompt
