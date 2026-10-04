"""v0.3: agent step quality (read/edit/write/tool steps with output and diffs) and the model catalog."""
import json
import stat

import pytest

from keel_engine.models import catalog
from keel_engine.models.base import AgentRequest
from keel_engine.models.cli_runners import (ClaudeCLIRunner, ClaudeStream, CodexCLIRunner, Steps, codex_line,
                                            opencode_tool, strip_line_numbers)
from keel_engine.tools.agent_tools import ToolBox


def script(tmp_path, name, body):
    f = tmp_path / name
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return str(f)


def collect():
    steps = []
    return steps, (lambda kind, text="", **kw: steps.append({"kind": kind, "text": text, **kw}))


# Shapes copied from a real `claude -p --output-format stream-json --verbose` run (Claude Code 2.1), paths replaced.
def claude_stream(root):
    r = str(root)
    return [
        {"type": "system", "subtype": "init", "cwd": r, "tools": ["Read", "Edit", "Write", "Bash"]},
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "", "signature": "x"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Plan:\n\n- read\n- edit"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": f"{r}/a.txt"}, "caller": {"type": "direct"}}]}},
        {"type": "user", "message": {"content": [
            {"tool_use_id": "toolu_read", "type": "tool_result", "content": "1\tline one\n2\tline two\n3\tline three\n4\t"}]},
         "tool_use_result": {"type": "text", "file": {"filePath": f"{r}/a.txt", "content": "line one\nline two\nline three\n",
                                                      "numLines": 4, "startLine": 1, "totalLines": 4}}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_edit", "name": "Edit",
             "input": {"replace_all": False, "file_path": f"{r}/a.txt", "old_string": "line two", "new_string": "line 2"}}]}},
        {"type": "user", "message": {"content": [
            {"tool_use_id": "toolu_edit", "type": "tool_result", "content": f"The file {r}/a.txt has been updated successfully."}]},
         "tool_use_result": {"filePath": f"{r}/a.txt", "oldString": "line two", "newString": "line 2",
                             "originalFile": "line one\nline two\nline three\n",
                             "structuredPatch": [{"oldStart": 1, "oldLines": 3, "newStart": 1, "newLines": 3,
                                                  "lines": [" line one", "-line two", "+line 2", " line three"]}],
                             "userModified": False, "replaceAll": False}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_write", "name": "Write", "input": {"file_path": f"{r}/c.txt", "content": "new content\nsecond\n"}}]}},
        {"type": "user", "message": {"content": [
            {"tool_use_id": "toolu_write", "type": "tool_result", "content": f"The file {r}/c.txt has been updated successfully."}]},
         "tool_use_result": {"type": "update", "filePath": f"{r}/c.txt", "content": "new content\nsecond\n",
                             "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 2,
                                                  "lines": ["-old", "+new content", "+second"]}], "originalFile": "old\n"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_new", "name": "Write", "input": {"file_path": f"{r}/d.txt", "content": "fresh\n"}}]}},
        {"type": "user", "message": {"content": [
            {"tool_use_id": "toolu_new", "type": "tool_result", "content": f"File created successfully at: {r}/d.txt"}]},
         "tool_use_result": {"type": "create", "filePath": f"{r}/d.txt", "content": "fresh\n", "structuredPatch": [], "originalFile": None}},
        # two parallel Bash calls; their results come back in the other order
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_cat", "name": "Bash",
             "input": {"command": f"cat {r}/a.txt; echo ---; cat {r}/b.txt", "description": "Print a.txt and b.txt"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_ls", "name": "Bash", "input": {"command": "ls nonexistent_dir"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "Exit code 1\nls: nonexistent_dir: No such file or directory", "is_error": True,
             "tool_use_id": "toolu_ls"}]}, "tool_use_result": "Error: Exit code 1\nls: nonexistent_dir: No such file or directory"},
        {"type": "user", "message": {"content": [
            {"tool_use_id": "toolu_cat", "type": "tool_result", "content": "line one\nline 2\nline three\n---\nalpha\nbeta", "is_error": False}]},
         "tool_use_result": {"stdout": "line one\nline 2\nline three\n---\nalpha\nbeta", "stderr": "", "interrupted": False}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_mcp", "name": "mcp__keel__keel_next", "input": {"ac": "AC-1"}}]}},
        {"type": "user", "message": {"content": [
            {"tool_use_id": "toolu_mcp", "type": "tool_result", "content": [{"type": "text", "text": "phase: red\nnext: write the test"}]}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
        {"type": "result", "subtype": "success", "result": "## Summary\n\n- edited a.txt\n- wrote c.txt", "is_error": False,
         "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01},
    ]


def test_claude_stream_gives_read_edit_write_and_tool_steps(tmp_path):
    steps, emit = collect()
    s = ClaudeStream(emit, str(tmp_path))
    for ev in claude_stream(tmp_path):
        s.line(json.dumps(ev))
    kinds = [x["kind"] for x in steps]
    assert kinds == ["text", "read", "edit", "write", "write", "tool", "tool", "tool", "text"]
    text, read, edit, upd, new, ls, cat, mcp, _ = steps
    assert text["text"] == "Plan:\n\n- read\n- edit"                       # Markdown, newlines kept
    assert read["path"] == "a.txt" and read["text"] == "line one\nline two\nline three" and read["ok"]
    assert edit["path"] == "a.txt" and edit["diff"].startswith("--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n")
    assert "-line two\n+line 2\n" in edit["diff"] and edit["text"] == "Edit a.txt (+1 -1)"
    assert upd["path"] == "c.txt" and "-old\n+new content\n+second\n" in upd["diff"]
    assert new["path"] == "d.txt" and new["diff"].startswith("--- /dev/null\n+++ b/d.txt") and "+fresh" in new["diff"]
    # results are paired by id, not by order
    assert ls["text"] == "ls nonexistent_dir" and ls["ok"] is False and "No such file" in ls["output"]
    assert cat["tool"] == "Bash" and cat["text"].startswith("cat ") and cat["ok"] is True
    assert cat["output"] == "line one\nline 2\nline three\n---\nalpha\nbeta" and isinstance(cat["ms"], int)
    assert mcp["server"] == "keel" and mcp["tool"] == "keel_next" and mcp["output"] == "phase: red\nnext: write the test"
    assert json.loads(mcp["text"]) == {"ac": "AC-1"}
    assert s.result["result"].startswith("## Summary")


def test_claude_diffs_without_structured_results(tmp_path):
    """Older CLIs (or MultiEdit) carry no tool_use_result: diff against the file as it was at tool_use time."""
    (tmp_path / "x.py").write_text("a = 1\nb = 2\nc = 3\n")
    (tmp_path / "y.py").write_text("old body\n")
    steps, emit = collect()
    s = ClaudeStream(emit, str(tmp_path))
    s.line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "e", "name": "MultiEdit", "input": {"file_path": str(tmp_path / "x.py"), "edits": [
            {"old_string": "b = 2", "new_string": "b = 20"}, {"old_string": "c = 3", "new_string": "c = 30"}]}},
        {"type": "tool_use", "id": "w", "name": "Write", "input": {"file_path": str(tmp_path / "y.py"), "content": "new body\n"}}]}}))
    (tmp_path / "y.py").write_text("new body\n")   # the tool ran
    s.line(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "e", "content": "ok"}, {"type": "tool_result", "tool_use_id": "w", "content": "ok"}]}}))
    edit, write = steps
    assert edit["kind"] == "edit" and "@@ -1,3 +1,3 @@" in edit["diff"]
    assert "-b = 2\n-c = 3\n+b = 20\n+c = 30\n" in edit["diff"] and " a = 1\n" in edit["diff"]
    assert write["kind"] == "write" and "-old body\n+new body\n" in write["diff"]


def test_read_strips_line_numbers_and_is_bounded(tmp_path):
    assert strip_line_numbers("     1→import os\n     2→\n     3→x = 1\n") == "import os\n\nx = 1"
    assert strip_line_numbers("00001| a\n00002| b\n") == "a\nb"
    assert strip_line_numbers("plain\ntext") == "plain\ntext"
    big = "\n".join(f"{i:6d}→line {i}" for i in range(1, 1001))
    steps, emit = collect()
    s = ClaudeStream(emit, str(tmp_path))
    s.line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": str(tmp_path / "big.py")}},
        {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "seq 1000"}}]}}))
    s.line(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "r", "content": big},
        {"type": "tool_result", "tool_use_id": "b", "content": "\n".join(str(i) for i in range(1, 1001))}]}}))
    read, bash = steps
    lines = read["text"].splitlines()
    assert lines[0] == "line 1" and lines[399] == "line 400" and lines[400] == "… (600 more lines)"
    assert bash["output"].splitlines()[299] == "300" and bash["output"].endswith("(700 more lines)")
    assert len(read["text"]) <= 20_100


async def test_claude_runner_answer_keeps_newlines(tmp_path, monkeypatch):
    out = "\n".join(json.dumps(x) for x in claude_stream(tmp_path))
    (tmp_path / "claude.out").write_text(out + "\n")
    monkeypatch.setenv("KEEL_CLAUDE_BIN", script(tmp_path, "claude", f"cat >/dev/null\ncat {tmp_path}/claude.out\n"))
    steps, emit = collect()
    req = AgentRequest(agent="implementer", system="", prompt="go", root=str(tmp_path), phase="green",
                       model={"provider": "claude", "mode": "subscription", "model": "sonnet"}, toolbox=ToolBox(str(tmp_path), "green"),
                       workdir=str(tmp_path))
    await ClaudeCLIRunner().run(req, emit)
    assert steps[-1] == {"kind": "answer", "text": "## Summary\n\n- edited a.txt\n- wrote c.txt"}


def test_agent_step_events_carry_output_and_diff(client, repo):
    from conftest import start, wait

    tid = start(client, repo)
    wait(client, tid)
    steps = [e["data"] for e in client.bus.of(tid, "agent.step")]
    assert any(d["kind"] == "tool" and d.get("output") for d in steps)
    assert any(d["kind"] == "read" and d.get("path") and "\n" in d.get("text", "") for d in steps)


def test_codex_pairs_commands_and_diffs_file_changes(tmp_path):
    (tmp_path / "b.py").write_text("x = 1\ny = 2\n")
    steps, emit = collect()
    state = {}
    for ev in [
        {"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "bash -lc 'cat a; echo ---; cat b'",
                                          "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "bash -lc 'cat a; echo ---; cat b'",
                                            "aggregated_output": "a\n---\nb\n", "exit_code": 0, "status": "completed"}},
        {"type": "item.completed", "item": {"id": "item_2", "type": "file_change", "status": "completed",
                                            "changes": [{"path": str(tmp_path / "b.py"), "kind": "add"}]}},
        {"type": "item.completed", "item": {"id": "item_3", "type": "mcp_tool_call", "server": "keel", "tool": "keel_next",
                                            "arguments": {"ac": "AC-1"}, "status": "completed",
                                            "result": {"content": [{"type": "text", "text": "phase: red"}]}}},
        {"type": "item.completed", "item": {"id": "item_4", "type": "agent_message", "text": "Done:\n\n- one\n- two"}},
    ]:
        codex_line(emit, str(tmp_path), ev, state)
    cmd, write, mcp, text = steps
    assert cmd["kind"] == "tool" and cmd["tool"] == "Bash" and cmd["output"] == "a\n---\nb" and cmd["ok"] and isinstance(cmd["ms"], int)
    assert write["kind"] == "write" and write["path"] == "b.py" and "+x = 1\n+y = 2\n" in write["diff"]
    assert mcp["server"] == "keel" and mcp["output"] == "phase: red"
    assert text["text"] == "Done:\n\n- one\n- two"


async def test_codex_passes_effort(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_CODEX_BIN", script(tmp_path, "codex", f'cat >/dev/null\nfor a in "$@"; do echo "$a"; done > {tmp_path}/argv\n'))
    req = AgentRequest(agent="implementer", system="", prompt="go", root=str(tmp_path), phase="green",
                       model={"provider": "codex", "mode": "subscription", "model": "gpt-5.5", "effort": "high"},
                       toolbox=ToolBox(str(tmp_path), "green"), workdir=str(tmp_path))
    await CodexCLIRunner().run(req, lambda *a, **k: None)
    argv = (tmp_path / "argv").read_text().splitlines()
    i = argv.index("model_reasoning_effort=high")
    assert argv[i - 1] == "-c" and argv[argv.index("-m") + 1] == "gpt-5.5"


def test_opencode_tool_parts(tmp_path):
    steps, emit = collect()
    st = Steps(emit, str(tmp_path))
    opencode_tool(st, {"tool": "bash", "state": {"status": "running", "input": {"command": "ls"}}})
    opencode_tool(st, {"tool": "bash", "state": {"status": "completed", "input": {"command": "ls"}, "output": "a\nb\n",
                                                 "time": {"start": 100, "end": 160}}})
    opencode_tool(st, {"tool": "read", "state": {"status": "completed", "input": {"filePath": str(tmp_path / "a.py")},
                                                 "output": "<file>\n00001| x = 1\n00002| y = 2\n</file>"}})
    opencode_tool(st, {"tool": "edit", "state": {"status": "completed", "input": {"filePath": str(tmp_path / "a.py"),
                                                                                  "oldString": "x = 1", "newString": "x = 2"},
                                                 "metadata": {"diff": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"}}})
    opencode_tool(st, {"tool": "keel_keel_next", "state": {"status": "error", "input": {}, "error": "boom"}})
    bash, read, edit, mcp = steps
    assert bash["tool"] == "Bash" and bash["output"] == "a\nb" and bash["ms"] == 60
    assert read["kind"] == "read" and read["text"] == "x = 1\ny = 2"
    assert edit["kind"] == "edit" and "+x = 2" in edit["diff"]
    assert mcp["server"] == "keel" and mcp["tool"] == "keel_next" and mcp["ok"] is False and mcp["output"] == "boom"


# ------------------------------------------------------------------ catalog

CODEX_MODELS = {"models": [
    {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide", "priority": 0},
    {"slug": "gpt-b", "display_name": "GPT-B", "visibility": "list", "priority": 2, "default_reasoning_level": "medium",
     "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}], "supported_in_api": True},
    {"slug": "gpt-a", "display_name": "GPT-A", "visibility": "list", "priority": 1, "default_reasoning_level": "high",
     "supported_reasoning_levels": [{"effort": "minimal"}, {"effort": "high"}], "supported_in_api": False},
]}


@pytest.fixture
def no_clis(tmp_path, monkeypatch):
    catalog.clear_cache()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    for tool in ("codex", "copilot", "opencode"):
        monkeypatch.setenv(f"KEEL_{tool.upper()}_BIN", str(tmp_path / f"missing-{tool}"))
    yield
    catalog.clear_cache()


def check_shape(cat):
    assert set(cat) == {"claude", "codex", "copilot", "fake"}
    for p in cat.values():
        assert set(p) == {"label", "modes", "efforts", "default", "source"}
        assert p["source"] in ("cli", "cache", "builtin")
        assert p["default"]["mode"] in p["modes"]
        assert p["default"]["model"] in [m["id"] for m in p["modes"][p["default"]["mode"]]]
        for items in p["modes"].values():
            assert items and all({"id", "label"} <= set(m) <= {"id", "label", "efforts"} for m in items)


def test_catalog_builtin_when_no_cli(no_clis):
    cat = catalog.build_catalog()
    check_shape(cat)
    assert [p["source"] for p in cat.values()] == ["builtin"] * 4
    assert cat["claude"]["label"] == "Claude" and set(cat["claude"]["modes"]) == {"subscription", "api"}
    assert cat["claude"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert {"opus", "sonnet", "haiku", "claude-opus-5-5"} <= {m["id"] for m in cat["claude"]["modes"]["api"]}
    assert cat["codex"]["label"] == "GPT / Codex" and cat["codex"]["efforts"] == ["minimal", "low", "medium", "high"]
    assert cat["copilot"]["label"] == "GitHub Copilot" and set(cat["copilot"]["modes"]) == {"subscription", "opencode", "api"}
    assert cat["copilot"]["efforts"] == [] and cat["fake"]["modes"] == {"api": catalog.MODELS["fake"]}


def test_catalog_codex_from_cache_file(no_clis, tmp_path):
    (tmp_path / "codex-home").mkdir()
    (tmp_path / "codex-home" / "models_cache.json").write_text(json.dumps({"fetched_at": "x", **CODEX_MODELS}))
    codex = catalog.build_catalog()["codex"]
    assert codex["source"] == "cache"
    assert codex["modes"]["subscription"] == [{"id": "gpt-a", "label": "GPT-A", "efforts": ["minimal", "high"]},
                                              {"id": "gpt-b", "label": "GPT-B", "efforts": ["low", "medium"]}]
    assert [m["id"] for m in codex["modes"]["api"]] == ["gpt-b"]
    assert codex["default"] == {"mode": "subscription", "model": "gpt-a", "effort": "high"}
    assert codex["efforts"] == ["minimal", "high", "low", "medium"]


def test_catalog_from_stubbed_clis_and_cached(no_clis, tmp_path, monkeypatch):
    (tmp_path / "models.json").write_text(json.dumps(CODEX_MODELS))
    monkeypatch.setenv("KEEL_CODEX_BIN", script(tmp_path, "codex", f'echo run >> {tmp_path}/codex-runs\n'
                                                                   f'[ "$1 $2" = "debug models" ] && cat {tmp_path}/models.json\n'))
    monkeypatch.setenv("KEEL_COPILOT_BIN", script(tmp_path, "copilot", """cat <<'EOF'
Usage: copilot [options]
  --model <model>        Set the AI model to use (choices: "claude-sonnet-4.5",
                         "gpt-5", "claude-haiku-4.5")
  --no-color             Disable color
EOF
"""))
    monkeypatch.setenv("KEEL_OPENCODE_BIN", script(tmp_path, "opencode", 'echo github-copilot/gpt-5\necho github-copilot/o3-mini\n'))
    cat = catalog.build_catalog()
    check_shape(cat)
    assert cat["codex"]["source"] == "cli" and cat["codex"]["modes"]["subscription"][0]["id"] == "gpt-a"
    assert cat["copilot"]["source"] == "cli"
    assert [m["id"] for m in cat["copilot"]["modes"]["subscription"]] == ["claude-sonnet-4.5", "gpt-5", "claude-haiku-4.5"]
    assert [m["id"] for m in cat["copilot"]["modes"]["opencode"]] == ["gpt-5", "o3-mini"]
    assert cat["copilot"]["default"]["model"] == "claude-sonnet-4.5"
    catalog.build_catalog()
    assert (tmp_path / "codex-runs").read_text().count("run") == 1        # the CLI answer is cached


def test_catalog_probe_never_blocks_long(no_clis, tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(catalog, "PROBE_TIMEOUT", 0.5)
    monkeypatch.setenv("KEEL_COPILOT_BIN", script(tmp_path, "copilot", "sleep 10\n"))
    t0 = time.monotonic()
    cat = catalog.build_catalog()
    assert time.monotonic() - t0 < 3 and cat["copilot"]["source"] == "builtin"


def test_models_endpoint_uses_the_catalog(client, no_clis):
    cat = client.get("/providers/models").json()
    check_shape(cat)
