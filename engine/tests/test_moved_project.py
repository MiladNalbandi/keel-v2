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


async def test_running_threads_wait_for_the_api_after_a_restart(tmp_path, monkeypatch, repo):
    """After a restart a running thread continues only when the api sends its logins (or after the grace time)."""
    from keel_engine.events import EventBus
    from keel_engine.runtime.service import Engine
    eng = Engine(EventBus(), tmp_path / "eng")
    await eng.open(resume_running=False)
    await eng.conn.execute("insert into keel_threads (thread_id, project_id, root, workflow_id, title, status, body, created_at, updated_at, error) "
                           "values ('t1', 'p', ?, 'feature', 'x', 'running', '{}', '', '', null)", (str(repo),))
    await eng.conn.commit()
    await eng.close()
    eng2 = Engine(EventBus(), tmp_path / "eng")
    monkeypatch.setattr("keel_engine.runtime.service.CONTINUE_GRACE", 3600)
    launched = []
    monkeypatch.setattr(eng2, "_launch", lambda tid, inp, cfg=None: launched.append(tid))
    monkeypatch.setattr(eng2, "state", lambda tid: _async({"thread_id": tid}))
    await eng2.open(resume_running=True)
    assert eng2.pending == {"t1"} and launched == []
    await eng2.continue_after_restart("t1", {"claude_oauth": "tok"}, str(repo))
    assert launched == ["t1"] and eng2.keys["t1"] == {"claude_oauth": "tok"} and not eng2.pending
    await eng2.close()


async def _async(v):
    return v
