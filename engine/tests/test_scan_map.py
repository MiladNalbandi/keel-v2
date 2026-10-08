"""Stage 5/5b: verdicts in the engine DB, the knowledge check, the scan job and the code graph. The map (ER + endpoints)
is the Map plugin's: plugins/map/engine/tests."""

import json
import stat
import subprocess
import time
from pathlib import Path

from keel_engine.runtime import blockers, knowledge, scan, verdicts
from keel_engine.runtime.actions import ActionInput, knowledge_check
from keel_engine.tools import mcp


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)


def write(repo, rel, text):
    f = Path(repo) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def commit_all(repo, msg="x"):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


FAKE_CODEGRAPH = """#!/bin/sh
# A stand-in for @colbymchenry/codegraph: init/index write .codegraph/codegraph.db, status prints counts.
cmd="$1"; shift
for a in "$@"; do case "$a" in -*) ;; *) root="$a";; esac; done
echo "$cmd $*" >> "$root/../codegraph-calls.log"
case "$cmd" in
  init|index) mkdir -p "$root/.codegraph" && echo db > "$root/.codegraph/codegraph.db" && echo "Indexed 3 files" ;;
  sync) exit 0 ;;
  status) printf '{"initialized":true,"fileCount":3,"nodeCount":20,"nodesByKind":{"file":3,"import":5,"function":9,"class":3},"journalMode":"wal"}\\n' ;;
  *) echo "unknown command $cmd" >&2; exit 2 ;;
esac
"""


def fake_codegraph(tmp_path, monkeypatch, body=FAKE_CODEGRAPH):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    f = bin_dir / "codegraph"
    f.write_text(body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", str(f))
    return f


def no_codegraph(monkeypatch):
    monkeypatch.setenv("KEEL_CODEGRAPH_BIN", "")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")


# ------------------------------------------------------------------ verdicts + knowledge check

def test_verdicts_table_keeps_history_and_reads_the_latest():
    assert verdicts.latest("p", "release") is None
    verdicts.write("p", "release", False, {"summary": "2 failed"}, "a" * 40)
    verdicts.write("p", "release", True, {"tree": "t1"}, "b" * 40)
    v = verdicts.latest("p", "release")
    assert v["ok"] is True and v["commit"] == "b" * 40 and v["detail"] == {"tree": "t1"} and v["at"]
    assert verdicts.fresh(v, "b" * 40, None) and verdicts.fresh(v, "c" * 40, "t1") and not verdicts.fresh(v, "c" * 40, "t2")
    assert set(verdicts.all_latest("p")) == {"release"}


def test_knowledge_check_fails_on_a_broken_citation_and_passes_when_fixed(repo):
    write(repo, "docs/knowledge/architecture.md", "# Architecture\n\nScores live in `src/scores/__init__.py:99`.\n")
    write(repo, "docs/knowledge/data.md", "# Data\n\nOwner: {{TEAM_NAME}}. See `src/gone.py:1`.\n")
    write(repo, "docs/knowledge/domain.md", "# Domain\n\nNo evidence at all.\n")
    r = knowledge.check(str(repo))
    assert not r["pass"]
    text = "\n".join(r["problems"])
    assert "`src/scores/__init__.py:99` but that file has" in text
    assert "`src/gone.py:1`, which does not exist" in text and "{{TEAM_NAME}}" in text
    assert "domain.md: no citations at all" in text

    a = ActionInput(root=str(repo), phase="memory", title="t", ac=None, acs=[], fake=True, flow="knowledge-refresh", project="demo")
    res = knowledge_check(a)
    assert not res.ok and "problem" in res.note
    assert verdicts.latest("demo", "memory")["ok"] is False

    write(repo, "docs/knowledge/architecture.md", "# Architecture\n\nScores live in `src/scores/__init__.py:1-3`.\n")
    write(repo, "docs/knowledge/data.md", "# Data\n\nThe tests: `tests/test_scores.py:1`.\n")
    write(repo, "docs/knowledge/domain.md", "# Domain\n\nA score is a number (`src/scores/__init__.py:2`).\n")
    res = knowledge_check(a)
    assert res.ok, res.detail
    v = verdicts.latest("demo", "memory")
    assert v["ok"] and v["detail"]["content"] == knowledge.content_hash(str(repo))
    assert not (Path(repo) / ".keel" / "memory.json").exists()


def test_chosen_sections_must_exist_and_proof_terms_need_a_test(repo):
    write(repo, ".keel/config.yml", "version: 4\ninit: {knowledge_sections: [conventions]}\nmemory: {proof_required_terms: ['@Transactional']}\n")
    r = knowledge.check(str(repo))
    assert r["problems"] == ["docs/knowledge/conventions.md is missing"]
    write(repo, "docs/knowledge/conventions.md", "Use @Transactional on services (`src/scores/__init__.py:1`).\n")
    assert "with no proof" in knowledge.check(str(repo))["problems"][0]
    write(repo, "docs/knowledge/conventions.md", "Use @Transactional on services (`tests/test_scores.py:1`).\n")
    assert knowledge.check(str(repo))["pass"]
    write(repo, ".keel/config.yml", "version: 4\ninit: {knowledge_sections: []}\n")
    (Path(repo) / "docs/knowledge/conventions.md").unlink()
    assert knowledge.check(str(repo))["pass"]                 # chose none: nothing to check


def test_knowledge_gate_reads_the_memory_verdict(repo):
    write(repo, ".keel/config.yml", "version: 4\n")
    write(repo, "docs/knowledge/architecture.md", "# A\n\nSee `src/scores/__init__.py:1`.\n")
    commit_all(repo)
    gates = lambda: {b["gate"]: b for b in blockers.push_blockers(str(repo), project="demo")}   # noqa: E731
    assert gates()["knowledge"]["why"] == "no knowledge check for this commit"
    knowledge_check(ActionInput(root=str(repo), phase="memory", title="t", ac=None, acs=[], fake=True, flow="f", project="demo"))
    assert "knowledge" not in gates()
    write(repo, "src/x.py", "x = 1\n")
    commit_all(repo)
    assert "knowledge" not in gates()                         # a later commit, the same knowledge text: still current
    write(repo, "docs/knowledge/architecture.md", "# A\n\nChanged. `src/scores/__init__.py:2`\n")
    commit_all(repo)
    assert "changed since" in gates()["knowledge"]["why"]


# ------------------------------------------------------------------ scan + code graph

def scan_and_wait(client, root, rebuild=False, project="demo"):
    r = client.post(f"/projects/{project}/scan", json={"root": str(root), "rebuild": rebuild})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "indexing"
    deadline = time.time() + 20
    while time.time() < deadline:
        s = client.get(f"/projects/{project}/index").json()
        if s["status"] != "indexing":
            return s
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_scan_with_codegraph(client, repo, tmp_path, monkeypatch):
    fake_codegraph(tmp_path, monkeypatch)
    assert client.get("/projects/demo/index").json()["status"] == "idle"
    s = scan_and_wait(client, repo)
    assert s["status"] == "ready" and s["files"] == 3 and s["symbols"] == 12 and s["indexed_at"] and not s["error"]
    assert "python" in s["stack"] and s["knowledge"]["missing"] == knowledge.SECTIONS
    assert "map" not in s                                        # the map is a plugin's (plugins/map), not core's
    assert ".codegraph/" in (Path(repo) / ".git/info/exclude").read_text()
    assert git(repo, "status", "--porcelain").stdout == ""      # nothing of the index shows up as a change
    done = [e for e in client.bus.recent if e["type"] == "index.done"]
    assert done[-1]["project_id"] == "demo" and done[-1]["data"]["files"] == 3
    assert any(e["type"] == "index.progress" and e["data"].get("step") == "graph" for e in client.bus.recent)

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
    from conftest import start, wait
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
