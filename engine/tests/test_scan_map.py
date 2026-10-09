"""Stage 5/5b: verdicts in the engine DB, the knowledge check and the scan job. The code graph's index is the Graph
plugin's (plugins/graph/engine/tests), the map (ER + endpoints) the Map plugin's (plugins/map/engine/tests)."""

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


# ------------------------------------------------------------------ scan

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


def test_a_scan_without_a_code_index_part_says_why(client, repo):
    """keel's core alone (no plugins/graph): the scan finds the stack and the knowledge sections, and its index row says
    that no part builds a code index (agents then use grep)."""
    assert client.get("/projects/demo/index").json() == {"project": "demo", "status": "idle", "files": 0, "symbols": 0,
                                                         "indexed_at": None, "error": None, "available": False}
    s = scan_and_wait(client, repo)
    assert s["status"] == "failed" and s["error"] == scan.NO_INDEX["error"] and s["available"] is False
    assert "python" in s["stack"] and s["knowledge"]["missing"] == knowledge.SECTIONS
    assert "map" not in s and s["files"] == 0                    # the map and the index are plugins' (plugins/map, graph)
    steps = [e["data"].get("step") for e in client.bus.recent if e["type"] == "index.progress"]
    assert steps == ["stack"]
    done = [e for e in client.bus.recent if e["type"] == "index.done"]
    assert done[-1]["project_id"] == "demo" and done[-1]["data"]["status"] == "failed"
    assert mcp.codegraph_server_spec(str(Path(repo).resolve())) is None
