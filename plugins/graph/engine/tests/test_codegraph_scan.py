"""The code graph's index at a project scan (keel's runtime/scan.py runs the plugin's on_scan hook): init, sync and
rebuild with a stand-in CodeGraph, the index outside the project when SQLite cannot live there, its MCP server only once
the index is ready, and agents with code_graph on get it, with the plugin's words about its tools. Moved from
engine/tests/test_scan_map.py and test_agent_knowledge.py."""

import subprocess
from pathlib import Path

from conftest import FAKE_CODEGRAPH, fake_codegraph, no_codegraph, scan_and_wait, start, wait
from keel_engine.runtime import agent_knowledge as ak
from keel_engine.runtime import knowledge, prompts, scan
from keel_engine.tools import mcp


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)


def test_scan_with_codegraph(client, repo, tmp_path, monkeypatch):
    fake_codegraph(tmp_path, monkeypatch)
    assert client.get("/projects/demo/index").json()["status"] == "idle"
    s = scan_and_wait(client, repo)
    assert s["status"] == "ready" and s["files"] == 3 and s["symbols"] == 12 and s["indexed_at"] and not s["error"]
    assert "python" in s["stack"] and s["knowledge"]["missing"] == knowledge.SECTIONS and s["available"] is True
    assert "map" not in s                                        # the map is a plugin's (plugins/map), not core's
    assert ".codegraph/" in (Path(repo) / ".git/info/exclude").read_text()
    assert git(repo, "status", "--porcelain").stdout == ""      # nothing of the index shows up as a change
    done = [e for e in client.bus.recent if e["type"] == "index.done"]
    assert done[-1]["project_id"] == "demo" and done[-1]["data"]["files"] == 3
    steps = [e["data"].get("step") for e in client.bus.recent if e["type"] == "index.progress"]
    assert steps == ["stack", "graph"]

    scan_and_wait(client, repo)                                  # already indexed: an incremental sync
    scan_and_wait(client, repo, rebuild=True)                    # Rebuild: a full index
    calls = [line.split()[0] for line in (Path(repo).parent / "codegraph-calls.log").read_text().splitlines()]
    assert [c for c in calls if c != "status"] == ["init", "sync", "index"]


def test_scan_without_codegraph_fails_clearly(client, repo, monkeypatch):
    no_codegraph(monkeypatch)
    s = scan_and_wait(client, repo)
    assert s["status"] == "failed" and "not installed" in s["error"] and s["available"] is False
    assert mcp.codegraph_server_spec(str(Path(repo).resolve())) is None


def test_index_moves_to_the_data_folder_when_sqlite_cannot_live_in_the_project(client, repo, tmp_path, monkeypatch):
    # The first init fails like SQLite on a file system without locks; the retry runs with .codegraph linked elsewhere.
    body = FAKE_CODEGRAPH.replace('init|index) mkdir', 'init|index) if [ ! -L "$root/.codegraph" ]; then echo "SqliteError: '
                                  'disk I/O error" >&2; exit 1; fi; mkdir')
    fake_codegraph(tmp_path, monkeypatch, body)
    s = scan_and_wait(client, repo)
    assert s["status"] == "ready", s
    link = Path(repo) / ".codegraph"
    assert link.is_symlink() and str(link.resolve()).startswith(str((tmp_path / "data" / "index").resolve()))
    assert s["index_dir"].endswith("index/demo")


def test_codegraph_mcp_entry_only_when_the_index_is_ready(client, repo, tmp_path, monkeypatch):
    root = str(Path(repo).resolve())
    exe = fake_codegraph(tmp_path, monkeypatch)
    assert mcp.codegraph_server_spec(root) is None               # never scanned
    scan_and_wait(client, repo)
    spec = mcp.codegraph_server_spec(root)
    assert spec["name"] == "codegraph" and spec["command"] == str(exe) and spec["cwd"] == root
    assert spec["args"] == ["serve", "--mcp", "--path", root, "--no-watch"]
    assert spec["env"]["CODEGRAPH_MCP_TOOLS"] == "search,callers,callees,impact" and spec["env"]["CODEGRAPH_NO_DAEMON"] == "0"
    scan._save("demo", root, "indexing")
    assert mcp.codegraph_server_spec(root) is None               # re-indexing: not handed out


def test_agents_get_the_codegraph_server_when_ready(client, repo, tmp_path, monkeypatch):
    from keel_engine.models import fake as fake_mod

    fake_codegraph(tmp_path, monkeypatch)
    scan_and_wait(client, repo)
    seen = []
    orig = fake_mod.FakeRunner.run

    async def spy(self, req, emit):
        seen.append((req.agent, [s["name"] for s in req.mcp_specs], list(req.tools_allow), req.prompt))
        return await orig(self, req, emit)

    monkeypatch.setattr(fake_mod.FakeRunner, "run", spy)
    tid = start(client, repo, workflow="knowledge-refresh", title="Refresh",
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "sections": ["architecture"]},
                agents={"librarian": {"knowledge": {"code_graph": True}}})
    assert wait(client, tid)["status"] == "done"
    agent, servers, allow, _ = seen[0]
    assert agent == "librarian" and "codegraph" in servers and "mcp:codegraph:*" in allow
    sync = [line for line in (Path(repo).parent / "codegraph-calls.log").read_text().splitlines() if line.startswith("sync")]
    assert sync                                                  # flow start / keel commit keep the index fresh


def test_an_agent_with_the_code_graph_is_told_how_to_use_its_tools(tmp_path):
    """keel's knowledge block asks the registry what an agent with the codegraph server is told: this plugin's words."""
    root = tmp_path / "p"
    (root / "docs" / "knowledge").mkdir(parents=True)
    (root / "docs" / "knowledge" / "domain.md").write_text("x" * 400)
    k = {**ak.for_agent("test-author", {}), "code_graph": True}
    p = prompts.task_prompt(agent="test-author", phase="red", step_name="red", title="t", root=str(root), ac=None, acs=[],
                            feedback=None, knowledge=k, graph=True)
    assert "codegraph_search" in p and "Memory:" in p
    # no graph server, or the graph turned off: no graph line
    assert "codegraph_search" not in prompts.task_prompt(agent="test-author", phase="red", step_name="red", title="t",
                                                         root=str(root), ac=None, acs=[], feedback=None, knowledge=k,
                                                         graph=False)
    assert "codegraph" not in ak.prompt_block(str(root), {**k, "code_graph": False}, True)
