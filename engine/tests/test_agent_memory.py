"""Agents remember their step: a restart, a try again or a send-back continues the agent's own session."""
import json

import aiosqlite

from conftest import decide, start, wait
from keel_engine.models.base import AgentRequest
from keel_engine.runtime import memory as mem
from keel_engine.tools.agent_tools import ToolBox
from scripted_model import scripted
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


def test_a_send_back_continues_the_explorers_own_session(client, repo, monkeypatch):
    # #101 the harness keeps the session itself: the spec step's second run reopens the first one's conversation
    sm = scripted(monkeypatch, "- **AC-1** [API] A player's rank is shown next to their best score")
    tid = start(client, repo, models={"default": {"provider": "claude", "mode": "subscription", "model": "haiku"}})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate", s
    first = sm.runs_of("spec")[0]
    assert first.session and not first.resume
    n, runs = len(sm.calls), len(sm.runs_of("spec"))      # a re-ask for a missing marker continues the session too
    s = decide(client, tid, "reject", why="ties must share a rank")
    again = sm.runs_of("spec")[runs]
    assert again.session == first.session and again.resume
    call = next(c for c in sm.calls[n:] if "ties must share a rank" in c.last_user_text())
    assert "earlier conversation" in call.last_user_text()
    told = [m for m in call.req.messages if m["role"] == "user"]
    assert any("Step: spec (keel phase: spec)" in (b.get("text") or "") for m in told for b in m["content"])
