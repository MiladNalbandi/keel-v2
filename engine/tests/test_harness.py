"""#101 keel's own agent harness, end to end with a scripted provider (the model's turns written in the test) on a real
project folder and a real ToolBox: tools under keel's rules, read before write, sessions that continue after a stop,
retries, cut-off replies, stuck loops, compaction, the Task helper. No network."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from keel_engine.harness import runner as runner_mod
from keel_engine.harness import tools as tools_mod
from keel_engine.harness import transcript
from keel_engine.harness.providers.base import END, MAX_TOKENS, REFUSAL, TOOL_USE, ProviderError, Turn, Usage
from keel_engine.harness.runner import SUB_SYSTEM, HarnessRunner
from keel_engine.models.base import AgentRequest, ModelError
from keel_engine.tools.agent_tools import ToolBox


def call(name, n=1, **inp):
    return {"type": "tool_call", "id": f"c{n}_{name}", "name": name, "input": inp}


def turn(*blocks, stop=None, usage=(100, 20)):
    has_calls = any(b["type"] == "tool_call" for b in blocks)
    return Turn(blocks=list(blocks), stop=stop or (TOOL_USE if has_calls else END), usage=Usage(input=usage[0], output=usage[1]),
                raw=None, model="scripted")


def text(t):
    return {"type": "text", "text": t}


class Scripted:
    """A provider whose turns the test wrote; it records every request. Helper (Task) requests get `helper` turns."""
    name = "openai"
    model = "scripted"

    def __init__(self, turns, helper=None):
        self.turns = list(turns)
        self.helper = list(helper or [])
        self.requests = []

    async def turn(self, req, emit):
        self.requests.append(req)
        src = self.helper if (req.system == SUB_SYSTEM or req.effort == "low") else self.turns     # helpers run at low effort
        nxt = src.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if callable(nxt):
            return nxt(req)
        emit("delta", "…")
        return nxt


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def total(xs):\n    return sum(xs)\n")
    (root / "README.md").write_text("# demo\n")
    return root


def request(project, prompt="Fix it", **kw):
    box = kw.pop("box", None) or ToolBox(str(project), "none")
    return AgentRequest(agent=kw.pop("agent", "implementer"), system="You are keel's implementer.", prompt=prompt,
                        root=str(project), phase="none", model={"provider": "codex", "model": "gpt-5.5"}, toolbox=box,
                        timeout=kw.pop("timeout", 600), **kw)


def run(provider, req):
    steps = []
    res = asyncio.run(HarnessRunner(provider).run(req, lambda kind, txt="", **extra: steps.append((kind, txt, extra))))
    return res, steps


def kinds(steps):
    return [k for k, _t, _e in steps if k not in ("delta", "usage")]


# ------------------------------------------------------------------ the loop and its tools

def test_a_run_reads_searches_edits_and_answers(project):
    p = Scripted([
        turn(text("Looking."), call("Read", 1, file_path="src/app.py"), call("Grep", 2, pattern="def total")),
        turn(call("Edit", 3, file_path="src/app.py", old_string="return sum(xs)", new_string="return sum(xs or [])")),
        turn(text("Done: total() takes an empty list now.")),
    ])
    req = request(project, on_session=lambda sid: sessions.append(sid))
    sessions = []
    res, steps = run(p, req)
    assert (project / "src" / "app.py").read_text() == "def total(xs):\n    return sum(xs or [])\n"
    assert res.text == "Done: total() takes an empty list now."
    assert res.tokens_in == 300 and res.tokens_out == 60 and res.data["session"] == sessions[0]
    ks = kinds(steps)
    assert ks[:4] == ["text", "read", "tool", "edit"] and ks[-1] == "answer"
    edit = next(e for k, _t, e in steps if k == "edit")
    assert edit["path"] == "src/app.py" and "+    return sum(xs or [])" in edit["diff"]
    # what the model read: numbered lines; the Grep found the file
    second = p.requests[1].messages[-1]["content"]
    assert second[0]["content"].startswith("     1\tdef total(xs):") and second[1]["content"] == "src/app.py"
    # the first message says where it works; the guide is in the system prompt
    first = p.requests[0].messages[0]["content"][0]["text"]
    assert f"Project folder: {project}" in first and first.endswith("Fix it")
    assert "keel's harness" in p.requests[0].system and p.requests[0].system.startswith("You are keel's implementer.")
    assert {t.name for t in p.requests[0].tools} >= {"Read", "Write", "Edit", "Glob", "Grep", "Bash", "RunTests", "TodoWrite", "Task"}
    saved = transcript.Session.load(sessions[0])
    assert [m["role"] for m in saved.messages] == ["user", "assistant", "user", "assistant", "user", "assistant"]


def test_write_and_edit_need_a_read_first_and_a_file_that_did_not_change(project):
    p = Scripted([
        turn(call("Edit", 1, file_path="src/app.py", old_string="sum", new_string="len"),
             call("Write", 2, file_path="src/new.py", content="X = 1\n")),
        turn(call("Read", 3, file_path="src/app.py")),
        lambda req: (Path(project / "src" / "app.py").write_text("changed by someone\n"), turn(
            call("Edit", 4, file_path="src/app.py", old_string="changed", new_string="x")))[1],
        turn(text("ok")),
    ])
    res, steps = run(p, request(project))
    results = p.requests[1].messages[-1]["content"]
    assert results[0]["is_error"] and "Read src/app.py before you change it" in results[0]["content"]
    assert not results[1]["is_error"] and (project / "src" / "new.py").read_text() == "X = 1\n"     # a new file needs no read
    stale = p.requests[3].messages[-1]["content"][0]
    assert stale["is_error"] and "changed since you read it" in stale["content"]
    assert (project / "src" / "app.py").read_text() == "changed by someone\n"


def test_keels_rules_refuse_a_call_and_the_run_goes_on(project):
    refused = []
    box = ToolBox(str(project), "none", readonly=True, on_refuse=lambda *a: refused.append(a))
    p = Scripted([turn(call("Write", 1, file_path="src/x.py", content="x\n"), call("Bash", 2, command="rm -rf src")),
                  turn(text("I could not change anything here."))])
    res, steps = run(p, request(project, box=box))
    out = p.requests[1].messages[-1]["content"]
    assert all(r["content"].startswith("REFUSED") for r in out)
    assert [k for k in kinds(steps) if k == "guard"] == ["guard", "guard"] and len(refused) == 2
    assert not (project / "src" / "x.py").exists() and (project / "src" / "app.py").exists()
    assert res.text == "I could not change anything here."


def test_a_bad_input_or_unknown_tool_is_answered_not_run(project):
    p = Scripted([turn(call("Edit", 1, file_path="src/app.py"), call("Nope", 2, x=1),
                       {"type": "tool_call", "id": "c3", "name": "Read", "input": {"INVALID_JSON": "{\"file_pa"}}),
                  turn(text("fine"))])
    run(p, request(project))
    a, b, c = p.requests[1].messages[-1]["content"]
    assert a["is_error"] and "does not fit its schema" in a["content"] and "old_string" in a["content"]
    assert b["is_error"] and "there is no tool Nope" in b["content"]
    assert c["is_error"] and "not valid JSON" in c["content"]


def test_read_only_calls_run_side_by_side_and_answers_keep_their_order(project, monkeypatch):
    seen = []

    async def slow_glob(self_, a):   # noqa: ANN001
        seen.append(("start", a["pattern"]))
        await asyncio.sleep(0.2 if a["pattern"] == "*.md" else 0.01)
        seen.append(("end", a["pattern"]))
        return a["pattern"]

    p = Scripted([turn(call("Glob", 1, pattern="*.md"), call("Glob", 2, pattern="*.py")), turn(text("ok"))])
    req = request(project)
    orig = tools_mod.ToolSet._build

    def build(self, *a, **k):
        orig(self, *a, **k)
        g = self.tools["Glob"]
        self.tools["Glob"] = tools_mod.Tool(g.name, g.description, g.schema, lambda a: slow_glob(self, a), read_only=True)

    monkeypatch.setattr(tools_mod.ToolSet, "_build", build)
    run(p, req)
    assert seen[:2] == [("start", "*.md"), ("start", "*.py")]            # both started before either ended
    assert [r["content"] for r in p.requests[1].messages[-1]["content"]] == ["*.md", "*.py"]


# ------------------------------------------------------------------ when things go wrong

def test_a_passing_error_is_tried_again_a_fatal_one_says_what_to_fix(project, monkeypatch):
    p = Scripted([ProviderError("rate limit", retry=True, after=0), turn(text("ok"))])
    res, steps = run(p, request(project))
    assert res.text == "ok" and any("Trying again" in t for k, t, _e in steps if k == "text")
    p = Scripted([ProviderError("Anthropic did not accept the API key.", hint="Check the key", status=401)])
    with pytest.raises(ModelError) as e:
        run(p, request(project))
    assert str(e.value) == "Anthropic did not accept the API key." and e.value.hint == "Check the key"


def test_a_cut_off_reply_goes_on_and_a_cut_off_tool_input_is_never_run(project):
    p = Scripted([turn(text("Part one"), stop=MAX_TOKENS),
                  turn(call("Write", 1, file_path="src/big.py", content="half"), stop=MAX_TOKENS),
                  turn(text("Part two"))])
    res, _ = run(p, request(project))
    assert "cut off by the length limit. Go on" in p.requests[1].messages[-1]["content"][0]["text"]
    cut = p.requests[2].messages[-1]["content"][0]
    assert cut["is_error"] and "was not run" in cut["content"] and not (project / "src" / "big.py").exists()
    assert res.text == "Part two"


def test_a_stuck_agent_gets_a_note_then_is_stopped(project):
    same = [turn(call("Grep", i, pattern="zzz")) for i in range(1, 8)]
    for i, t in enumerate(same):         # the same call with the same id pattern: identical calls each turn
        t.blocks[0]["id"] = f"c{i}"
    p = Scripted(same)
    with pytest.raises(ModelError, match="stuck"):
        run(p, request(project))
    third = p.requests[3].messages[-1]["content"][0]["content"]
    assert "NOTE from keel: you made this same call 3 times" in third


def test_a_refusal_ends_the_run_with_its_reason(project):
    t = turn(text(""), stop=REFUSAL)
    t.note = "The model declined this request (cyber)."
    with pytest.raises(ModelError, match="declined"):
        run(Scripted([t]), request(project))


def test_the_turn_limit_keeps_the_session_so_the_step_can_continue(project, monkeypatch):
    monkeypatch.setattr(runner_mod, "MAX_TURNS", 2)
    p = Scripted([turn(call("Glob", 1, pattern="*")), turn(call("Glob", 2, pattern="**/*.py"))])
    with pytest.raises(ModelError) as e:
        run(p, request(project, agent="no-such-agent"))
    assert e.value.max_turns and "used all 2 turns" in str(e.value) and e.value.usage["tokens_out"] == 40


# ------------------------------------------------------------------ sessions

def test_a_step_continues_its_session_and_a_crash_mid_tool_is_answered(project):
    sid = transcript.new_id()
    p = Scripted([turn(text("first answer"))])
    run(p, request(project, session=sid))
    p2 = Scripted([turn(text("second answer"))])
    res, _ = run(p2, request(project, prompt="And now?", session=sid, resume=True))
    msgs = p2.requests[0].messages
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"] and msgs[-1]["content"][0]["text"] == "And now?"
    assert res.data["session"] == sid
    # the process stopped while a tool ran: the call gets an error result when the session is loaded again
    s = transcript.Session.load(sid)
    s.add("assistant", [call("Bash", 9, command="make")])
    again = transcript.Session.load(sid)
    assert again.messages[-1]["content"][0]["is_error"] and "keel stopped before this tool finished" in \
        again.messages[-1]["content"][0]["content"]


def test_a_session_that_is_gone_starts_again_and_says_so(project):
    got = []
    p = Scripted([turn(text("ok"))])
    res, steps = run(p, request(project, session="gone-session", resume=True, on_session=got.append))
    assert any("Could not continue the earlier session" in t for k, t, _e in steps)
    assert got and got[0] != "gone-session" and res.data["session"] == got[0]


def test_keelbot_history_seeds_a_new_session(project):
    p = Scripted([turn(text("ok"))])
    run(p, request(project, history=[("user", "hi"), ("assistant", "hello")]))
    roles = [(m["role"], m["content"][0]["text"][:5]) for m in p.requests[0].messages]
    assert roles[:2] == [("user", "hi"), ("assistant", "hello")]


def test_old_sessions_are_swept():
    s = transcript.Session.open(None, provider="codex")
    old = time.time() - (transcript.KEEP_DAYS + 1) * 86400
    import os

    os.utime(transcript._path(s.id), (old, old))
    assert transcript.sweep() == 1 and transcript.Session.load(s.id) is None


def test_a_long_conversation_is_summarized_and_goes_on_from_the_summary(project, monkeypatch):
    monkeypatch.setattr(runner_mod.catalog, "context_window", lambda provider, model: 1200)
    big = "x" * 1200
    script = [turn(text(big), call("Glob", i, pattern=f"p{i}")) for i in range(1, 7)]
    script.insert(5, lambda req: turn(text("SUMMARY: looked at six patterns."), usage=(10, 5)))
    script.append(turn(text("done")))
    p = Scripted(script)
    res, steps = run(p, request(project))
    summary_req = next(r for r in p.requests if r.messages[-1]["content"][0].get("text", "").startswith("Write a summary"))
    assert summary_req.tools == []
    after = p.requests[-1].messages
    assert after[0]["content"][0]["text"].startswith("Summary of the work so far") and "SUMMARY: looked at six patterns." in \
        after[0]["content"][0]["text"]
    assert any("keel summarized its older part" in t for k, t, _e in steps)
    assert res.text == "done"


# ------------------------------------------------------------------ keel's own tools

def test_todo_write_keeps_the_task_list_in_the_session(project):
    p = Scripted([turn(call("TodoWrite", 1, todos=[{"content": "read", "status": "completed"},
                                                    {"content": "fix", "status": "in_progress"}])), turn(text("ok"))])
    res, steps = run(p, request(project))
    assert transcript.Session.load(res.data["session"]).todos[1] == {"content": "fix", "status": "in_progress"}
    assert any(t.startswith("Task list:\n- [x] read\n- [~] fix") for k, t, _e in steps if k == "text")


def test_task_hands_a_search_to_a_helper_that_only_reads(project):
    p = Scripted(
        [turn(call("Task", 1, description="find total", prompt="Where is total defined?")), turn(text("It is in src/app.py."))],
        helper=[turn(call("Grep", 1, pattern="def total", output_mode="content"), call("Write", 2, file_path="x.py", content="x")),
                turn(text("src/app.py:1 defines total."))])
    res, steps = run(p, request(project))
    helper_req = p.requests[1]
    assert helper_req.system == SUB_SYSTEM and helper_req.effort == "low"
    assert {t.name for t in helper_req.tools} >= {"Read", "Grep", "Glob", "Bash", "Skill"} and "Task" not in {t.name for t in helper_req.tools}
    wrote = p.requests[2].messages[-1]["content"][1]
    assert wrote["content"].startswith("REFUSED") and not (project / "x.py").exists()      # the helper only reads
    answer = p.requests[3].messages[-1]["content"][0]
    assert answer["content"] == "src/app.py:1 defines total." and res.tokens_out == 80      # its tokens count too
    assert any(e.get("tool") == "Task·Grep" for k, _t, e in steps if k == "tool")


def test_grep_and_glob_keep_secret_files_out(project):
    (project / ".env").write_text("TOKEN=ghp_secretvalue\n")
    box = ToolBox(str(project), "none")
    refused = box.grep("TOKEN")
    assert refused.startswith("REFUSED") and ".env" in refused
    assert box.grep("TOKEN", glob="*.py").startswith("No matches")
    assert box.grep("def total", glob="*.py", mode="content") == "src/app.py:1:def total(xs):"
    assert box.glob("**/*.py") == "src/app.py"
    assert box.grep("x", path="../../etc").startswith("REFUSED")


def test_web_fetch_never_reaches_this_computer():
    out = asyncio.run(tools_mod.web_fetch("http://127.0.0.1:8080/api/projects"))
    assert out.startswith("REFUSED") and "this computer or a private network" in out
    assert asyncio.run(tools_mod.web_fetch("file:///etc/passwd")).startswith("REFUSED")
    assert tools_mod.html_text("<html><head><title>t</title><script>x()</script></head><body><h1>Hi</h1><p>A "
                               "<a href='https://e.com'>link</a></p></body></html>") == "Hi\nA [link](https://e.com)"


def test_mcp_tools_are_judged_by_keels_mcp_rules(project, monkeypatch):
    class T:
        name = "mcp__db__run_query"
        description = "query"
        args_schema = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}
        metadata = {}

        async def ainvoke(self, a):
            return f"ran {a['sql']}"

    ts = tools_mod.ToolSet(ToolBox(str(project), "none"), mcp=[T()],
                           mcp_check=lambda name, a: "no database servers" if "db" in name else None)
    out = asyncio.run(ts.tools["mcp__db__run_query"].run({"sql": "select 1"}))
    assert out == "REFUSED: no database servers"
