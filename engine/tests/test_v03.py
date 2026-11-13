"""v0.3: agent step quality (read/edit/write/tool steps with output and diffs) and the model catalog."""
import stat

import pytest

from keel_engine.models import catalog


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


def test_agent_step_events_carry_output_and_diff(client, repo):
    from conftest import start, wait

    tid = start(client, repo)
    wait(client, tid)
    steps = [e["data"] for e in client.bus.of(tid, "agent.step")]
    assert any(d["kind"] == "tool" and d.get("output") for d in steps)
    assert any(d["kind"] == "read" and d.get("path") and "\n" in d.get("text", "") for d in steps)


# ------------------------------------------------------------------ catalog

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


def test_the_catalog_lists_api_models_only(no_clis):
    # #101 keel's harness runs every model on its API with an API key: no CLI is asked, one mode per provider
    cat = catalog.build_catalog()
    check_shape(cat)
    assert [p["source"] for p in cat.values()] == ["builtin"] * 4
    assert all(set(p["modes"]) == {"api"} for p in cat.values())
    assert cat["claude"]["label"] == "Anthropic (Claude)" and cat["claude"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert {"opus", "sonnet", "haiku", "claude-opus-5-5"} <= {m["id"] for m in cat["claude"]["modes"]["api"]}
    assert cat["codex"]["label"] == "OpenAI" and cat["codex"]["efforts"] == ["minimal", "low", "medium", "high"]
    assert cat["copilot"]["label"] == "GitHub Models" and cat["copilot"]["efforts"] == []
    assert cat["fake"]["modes"] == {"api": catalog.MODELS["fake"]}


def test_models_endpoint_uses_the_catalog(client, no_clis):
    cat = client.get("/providers/models").json()
    check_shape(cat)
