"""A flow follows its project when keel is started another way (/workspace <-> the real path with --docker)."""
import json
import shutil
from pathlib import Path

from conftest import decide, start, wait


def test_resume_moves_the_flow_to_the_folder_the_api_sends(client, repo, tmp_path):
    tid = start(client, repo)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate"
    moved = tmp_path / "moved" / "demo"
    shutil.copytree(repo, moved)
    shutil.rmtree(repo)
    repo.mkdir()                       # what an empty /workspace looks like
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "root": str(moved)})
    assert r.status_code == 200, r.text
    s = wait(client, tid)
    st = json.loads((moved / ".keel" / "state.json").read_text())
    assert st["engine"]["thread_id"] == tid and st["phase"] != "none"
    assert not (repo / ".keel").exists(), "nothing is written in the old, empty folder"


def test_resume_refuses_an_empty_or_missing_folder(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    shutil.rmtree(repo)
    repo.mkdir()
    (repo / ".keel").mkdir()
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve"})
    assert r.status_code == 409 and "empty or missing" in r.json()["error"]
    r = client.post(f"/threads/{tid}/resume", json={"decision": "approve", "root": str(repo / "nope")})
    assert r.status_code == 409


def test_relocate_maps_workspace_and_real_paths(tmp_path, monkeypatch):
    import subprocess
    from keel_engine.runtime.service import relocate
    real = tmp_path / "Users" / "me" / "ludus-engine"
    real.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(real)], check=True)
    monkeypatch.setenv("KEEL_WORKSPACE", str(real))
    assert relocate("/workspace") == str(real)
    monkeypatch.setenv("KEEL_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws" / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tmp_path / "ws" / "sub")], check=True)
    assert relocate("/workspace/sub") == str(tmp_path / "ws" / "sub")
    assert relocate("/workspace/none") is None
