"""0.32 context management in keel's harness: one output policy, old tool results cleared, compaction that keeps a
recent tail and what keel itself knows, a short helper answer. No network: the scripted model of test_harness.py."""
# ruff: noqa: F811, RUF059
from __future__ import annotations

from keel_engine.harness import runner as runner_mod
from keel_engine.harness import tools as tools_mod
from keel_engine.harness import transcript
from tests.test_harness import Scripted, call, project, request, run, text, turn  # noqa: F401  (fixtures)


def _result(i, size, name="Read"):
    return [{"role": "assistant", "content": [{"type": "tool_call", "id": f"c{i}", "name": name, "input": {"file_path": f"f{i}.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "id": f"c{i}", "content": "line\n" * (size // 5), "is_error": False}]}]


def test_a_long_output_keeps_its_start_and_end_and_the_whole_output_is_saved_for_read(project):
    big = "\n".join(f"line {n}" for n in range(1, 20001))
    out = tools_mod.cap(big, 3000)
    assert out.startswith("line 1\n") and out.rstrip().endswith("line 20000") and "characters left out" in out
    path = out.split(" is in ")[1].split(":")[0]
    assert tools_mod.saved_output(path) is not None
    ts = tools_mod.ToolSet(tools_mod_box(project))
    part = __import__("asyncio").run(ts.tools["Read"].run({"file_path": path, "offset": 10000, "limit": 2}))
    assert "line 10000" in part and "line 10001" in part and "more lines" in part
    assert tools_mod.saved_output(str(project / "src" / "app.py")) is None          # only keel's own saved outputs


def tools_mod_box(project):
    from keel_engine.tools.agent_tools import ToolBox

    return ToolBox(str(project), "none")


def test_every_tool_result_is_capped_and_a_read_is_cut_without_a_file(project):
    p = Scripted([turn(call("Bash", 1, command="python3 -c \"print('y' * 90000)\"")), turn(text("ok"))])
    res, steps = run(p, request(project))
    got = p.requests[1].messages[-1]["content"][0]["content"]
    # Bash keeps its own smaller cap (MAX_RESULT), now with the whole output saved for the model to read
    assert len(got) < tools_mod.MAX_RESULT + 1000 and "characters left out" in got and " is in " in got
    # the file is in keel's data folder: the agent's commands cannot see it (its own user, the sandbox natively)
    assert "Read it with offset and limit" in got and "Bash and Grep cannot reach that file" in got


def test_old_tool_results_are_cleared_in_batches_and_the_newest_stay():
    msgs = []
    for i in range(10):
        msgs += _result(i, 4000 * 4)                   # 10 results of about 4k tokens each
    stubs = runner_mod.clear_old_results(msgs, keep_tokens=12_000, minimum=6_000)
    assert set(stubs) == {f"c{i}" for i in range(7)}                      # the newest 3 (12k tokens) stay
    assert stubs["c0"].startswith("[keel cleared this old result") and "Read f0.py" in stubs["c0"]
    assert runner_mod.clear_old_results(msgs, keep_tokens=12_000, minimum=60_000) == {}   # not worth a cache break yet


def test_a_cleared_result_stays_cleared_after_a_reload(project):
    s = transcript.Session.open(None, provider="codex", model="m")
    for m in _result(1, 100) + _result(2, 100):
        s.add(m["role"], m["content"])
    s.clear_results({"c1": "[keel cleared this old result to keep the context small: Read f1.py. Run it again if you need it.]"})
    again = transcript.Session.load(s.id)
    assert again.messages[1]["content"][0]["content"].startswith("[keel cleared") and again.messages[3]["content"][0]["content"].startswith("line")


def test_the_loop_clears_old_results_before_it_compacts(project, monkeypatch):
    monkeypatch.setattr(runner_mod, "CLEAR_KEEP", 300)
    monkeypatch.setattr(runner_mod, "CLEAR_MIN", 100)
    monkeypatch.setattr(runner_mod, "CLEAR_KEEP_USES", 1)
    (project / "big.txt").write_text("z" * 3000)
    p = Scripted([turn(call("Read", i, file_path="big.txt")) for i in range(1, 5)] + [turn(text("done"))])
    res, steps = run(p, request(project))
    last = p.requests[-1].messages
    results = [b["content"] for m in last if m["role"] == "user" for b in m["content"] if b.get("type") == "tool_result"]
    assert results[0].startswith("[keel cleared") and not results[-1].startswith("[keel cleared")
    assert any("cleared" in t for k, t, _e in steps)


def test_a_chat_keeps_a_recent_tail_in_tokens():
    msgs = []
    for i in range(6):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"question {i} " + "q" * 4000}]})
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"answer {i} " + "a" * 4000}]})
    cut = runner_mod.chat_cut(msgs, tail_tokens=5_000)
    kept = msgs[cut:]
    assert kept[0]["content"][0]["text"].startswith("question") and 2 <= len(kept) <= 6


def test_a_compaction_keeps_keels_own_progress(project, monkeypatch):
    monkeypatch.setattr(runner_mod.catalog, "context_window", lambda provider, model: 1200)
    (project / "AGENTS.md").write_text("Always run make test.")
    big = "x" * 1200
    script = [turn(text(big), call("Read", 1, file_path="src/app.py")), turn(text(big), call("Glob", 2, pattern="*"))]
    script += [turn(text(big), call("Glob", i, pattern=f"p{i}")) for i in range(3, 7)]
    script.insert(5, lambda req: turn(text("## Goal\nlook around"), usage=(10, 5)))
    script.append(turn(text("done")))
    p = Scripted(script)
    res, steps = run(p, request(project))
    head = p.requests[-1].messages[0]["content"][0]["text"]
    assert "## Progress kept by keel" in head and "src/app.py" in head and "Always run make test." in head


def test_a_helper_answer_is_short(project):
    long = "found it " * 3000
    p = Scripted([turn(call("Task", 1, description="find", prompt="where?")), turn(text("ok"))], helper=[turn(text(long))])
    res, steps = run(p, request(project))
    main = [r for r in p.requests if r.effort != "low"]
    got = main[1].messages[-1]["content"][0]["content"]
    assert len(got) < runner_mod.SUB_ANSWER + 600 and "characters left out" in got
