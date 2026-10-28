"""0.18.64 field report bugs 4 and 6: a flow's start fetches every remote and says how far the base is behind each one
(a fetch that fails is said plainly, never fails the start); keel merges or rebases a flow's branch onto a base, and a
conflict stops with the files until the resolution is committed by keel."""
import subprocess
import time
from pathlib import Path

import pytest

from conftest import decide, start, wait
from keel_engine.runtime import branch_sync
from keel_engine.tools import git as g
from keel_engine.workflows.templates import get_template


def run(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=cwd,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(cwd, name, text, msg):
    Path(cwd, name).write_text(text)
    run(cwd, "add", name)
    run(cwd, "commit", "-qm", msg)


@pytest.fixture
def local(tmp_path):
    """A local repo on main (one commit), with `origin`: a bare repo two commits ahead of it."""
    work = tmp_path / "work"
    work.mkdir()
    run(work, "init", "-q", "-b", "main")
    commit(work, "app.txt", "one\n", "first")
    bare = tmp_path / "origin.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    run(work, "remote", "add", "origin", str(bare))
    run(work, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    run(tmp_path, "clone", "-q", str(bare), str(other))
    commit(other, "team.txt", "a\n", "team: a")
    commit(other, "team.txt", "a\nb\n", "team: b")
    run(other, "push", "-q", "origin", "main")
    return work


def test_a_fetch_reports_how_far_the_base_is_behind_and_a_dead_remote_plainly(local, tmp_path):
    run(local, "remote", "add", "mirror", f"file://{tmp_path}/no-such-repo.git")
    t0 = time.monotonic()
    fetched = branch_sync.fetch_all(str(local))
    assert time.monotonic() - t0 < 15
    by = {f["remote"]: f for f in fetched}
    assert by["origin"]["ok"] is True
    assert by["mirror"]["ok"] is False and by["mirror"]["error"]
    rep = branch_sync.report(str(local), fetched, g.head(str(local)))
    origin = next(r for r in rep["remotes"] if r["remote"] == "origin")
    assert (origin["branch"], origin["behind"], origin["ahead"]) == ("origin/main", 2, 0)
    assert "main is 2 commits behind origin/main" in rep["note"]
    assert "fetch from mirror failed:" in rep["note"] and "the base may be stale" in rep["note"]
    # the flow starts on the local main: the note offers the newer one
    assert "start it with base origin/main" in rep["note"]


def test_a_remote_whose_default_branch_has_another_name_is_compared_too(local, tmp_path):
    """Local master, the other remote's main (the field report: a base behind a second remote)."""
    run(local, "branch", "-m", "main", "master")
    fetched = branch_sync.fetch_all(str(local))
    rep = branch_sync.report(str(local), fetched)
    assert rep["base"] == "master"
    assert "master is 2 commits behind origin/main (origin's default branch)" in rep["note"]


def test_up_to_date_and_no_remote_say_so_quietly(local, tmp_path):
    run(local, "pull", "-q", "--ff-only", "origin", "main")
    rep = branch_sync.report(str(local), branch_sync.fetch_all(str(local)), g.head(str(local)))
    assert rep["note"] == "main is up to date with origin/main"
    lone = tmp_path / "lone"
    lone.mkdir()
    run(lone, "init", "-q", "-b", "main")
    commit(lone, "a.txt", "a\n", "first")
    assert branch_sync.fetch_all(str(lone)) == []


def test_the_engine_endpoint_fetches_and_reports(client, local, tmp_path):
    run(local, "remote", "add", "mirror", f"file://{tmp_path}/gone.git")
    r = client.post("/remotes/fetch", json={"root": str(local)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {x["remote"]: x["ok"] for x in body["fetched"]} == {"origin": True, "mirror": False}
    assert "main is 2 commits behind origin/main" in body["note"]


def test_a_flow_start_says_how_far_its_base_is_behind_and_never_fails_on_a_fetch(client, repo, tmp_path):
    bare = tmp_path / "up.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    run(repo, "remote", "add", "origin", str(bare))
    run(repo, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    run(tmp_path, "clone", "-q", str(bare), str(other))
    commit(other, "team.txt", "a\n", "team: a")
    run(other, "push", "-q", "origin", "main")
    run(repo, "remote", "add", "mirror", f"file://{tmp_path}/missing.git")
    r = client.post("/threads", json={"project_id": "demo", "root": str(repo), "workflow": get_template("feature").model_dump(),
                                      "title": "Player ranks", "models": {"default": {"provider": "fake", "mode": "api", "model": "fake"}},
                                      "settings": {"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause"}, "mcp": [], "skills": {}})
    assert r.status_code == 200, r.text
    note = r.json()["base_note"]
    assert "main is 1 commit behind origin/main" in note and "fetch from mirror failed:" in note
    tid = r.json()["thread_id"]
    started = [e for e in client.bus.of(tid, "thread.started")]
    assert started and started[0]["data"]["note"] == note
    wait(client, tid)


def test_a_start_with_the_api_fetch_results_does_not_fetch_again(client, repo, tmp_path, monkeypatch):
    run(repo, "remote", "add", "origin", f"file://{tmp_path}/none.git")
    monkeypatch.setattr(branch_sync, "fetch_all", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched again")))
    tid = start(client, repo, data={"remotes_fetched": [{"remote": "origin", "ok": False, "error": "timed out after 20 s"}]})
    ev = client.bus.of(tid, "thread.started")[0]["data"]
    assert ev["note"] == "fetch from origin failed: timed out after 20 s; the base may be stale"
    wait(client, tid)


# ------------------------------------------------------------------ sync

@pytest.fixture
def flow_branch(local):
    """The flow's branch feat/x with a commit of its own, made from the local main before origin moved on."""
    run(local, "checkout", "-q", "-b", "feat/x")
    commit(local, "feature.txt", "x\n", "feat(AC-1): x")
    run(local, "fetch", "-q", "origin")
    return local


def test_sync_merges_the_base_into_the_flow_branch_as_keel(flow_branch):
    out = branch_sync.sync(str(flow_branch), None, "merge", "start", {"commit_author": "Dev One <dev@example.com>"})
    assert out["ok"] and out["synced"] and out["onto"] == "origin/main" and out["behind"] == 2
    assert out["commit"] and out["base_head"] == g.rev(str(flow_branch), "origin/main")
    msg = run(flow_branch, "log", "-1", "--format=%an <%ae>%n%B")
    assert msg.startswith("Dev One <dev@example.com>\nMerge origin/main into feat/x")
    assert "Co-Authored-By: KeelBot" in msg
    assert Path(flow_branch, "team.txt").read_text() == "a\nb\n"
    again = branch_sync.sync(str(flow_branch))
    assert again["ok"] and not again["synced"] and "nothing to bring in" in again["note"]


def test_sync_rebases_when_asked(flow_branch):
    out = branch_sync.sync(str(flow_branch), "origin/main", "rebase")
    assert out["ok"] and out["synced"] and "force-with-lease" in out["note"]
    assert run(flow_branch, "rev-list", "--count", "origin/main..HEAD") == "1"
    assert run(flow_branch, "merge-base", "HEAD", "origin/main") == g.rev(str(flow_branch), "origin/main")


def test_a_conflict_stops_with_the_files_and_keel_commits_the_resolution(flow_branch):
    commit(flow_branch, "team.txt", "mine\n", "feat(AC-2): mine")
    out = branch_sync.sync(str(flow_branch))
    assert out["ok"] is False and out["conflicts"] == ["team.txt"] and out["state"] == "merge"
    assert "action=continue" in out["next"]
    with pytest.raises(branch_sync.SyncError) as e:
        branch_sync.sync(str(flow_branch))               # a second start while it waits
    assert e.value.status == 409 and "team.txt" in e.value.message
    still = branch_sync.sync(str(flow_branch), action="continue")
    assert still["ok"] is False and still["conflicts"] == ["team.txt"]      # the markers are still there
    Path(flow_branch, "team.txt").write_text("a\nb\nmine\n")
    done = branch_sync.sync(str(flow_branch), action="continue")
    assert done["ok"] and done["synced"] and done["commit"]
    assert g.in_progress(str(flow_branch)) is None
    log = run(flow_branch, "log", "-1", "--format=%P%n%B")
    assert len(log.splitlines()[0].split()) == 2 and "Merge origin/main into feat/x" in log
    assert "# Conflicts" not in log


def test_abort_puts_the_branch_back(flow_branch):
    commit(flow_branch, "team.txt", "mine\n", "feat(AC-2): mine")
    head = g.head(str(flow_branch))
    assert branch_sync.sync(str(flow_branch), mode="rebase")["conflicts"] == ["team.txt"]
    out = branch_sync.sync(str(flow_branch), action="abort")
    assert out["ok"] and g.head(str(flow_branch)) == head and g.in_progress(str(flow_branch)) is None


def test_sync_refuses_uncommitted_work_an_unknown_base_and_the_branch_itself(flow_branch):
    Path(flow_branch, "feature.txt").write_text("changed\n")
    with pytest.raises(branch_sync.SyncError, match="uncommitted"):
        branch_sync.sync(str(flow_branch))
    run(flow_branch, "checkout", "-q", "--", "feature.txt")
    with pytest.raises(branch_sync.SyncError, match="no branch upstream/main"):
        branch_sync.sync(str(flow_branch), "upstream/main")
    with pytest.raises(branch_sync.SyncError, match="onto itself"):
        branch_sync.sync(str(flow_branch), "feat/x")


def test_the_engine_syncs_a_waiting_flow_and_its_next_step_starts_at_the_new_base(client, repo, tmp_path):
    tid = start(client, repo)
    s = wait(client, tid)
    assert s["status"] == "waiting"
    # the flow's branch has work of its own, and the base moved on while the flow waited (another person's commit)
    assert g.branch(str(repo)) == "feat/player-ranks"          # the preflight made it
    commit(repo, "ranks.txt", "ranks\n", "feat(AC-1): ranks")
    run(repo, "checkout", "-q", "main")
    commit(repo, "news.txt", "news\n", "news on main")
    run(repo, "checkout", "-q", "feat/player-ranks")
    r = client.post(f"/threads/{tid}/sync", json={"onto": "main"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["synced"] and Path(repo, "news.txt").exists() and body["thread"]["thread_id"] == tid
    assert body["base_head"] == g.rev(str(repo), "main")
    assert client.bus.of(tid, "branch.synced")[0]["data"]["onto"] == "main"
    applied = client.bus.of(tid, "keel.applied")[-1]["data"]
    assert applied["subject"] == "Merge main into feat/player-ranks" and applied["files"] == ["news.txt"]
    # where the flow's own work starts is kept with the thread: its next step takes it as base_head
    engine = client.app.state.engine
    assert engine.ctxs[tid].synced_base == body["base_head"]
    engine.ctxs.pop(tid)
    assert client.get(f"/threads/{tid}").status_code == 200
    assert engine.ctxs[tid].synced_base == body["base_head"]
    bad = client.post(f"/threads/{tid}/sync", json={"onto": "nope/none"})
    assert bad.status_code == 400 and "no branch nope/none" in bad.json()["error"]
    run(repo, "checkout", "-q", "main")                         # another branch in the folder: never synced
    other = client.post(f"/threads/{tid}/sync", json={})
    assert other.status_code == 409 and "not on this flow's branch feat/player-ranks" in other.json()["error"]
    run(repo, "checkout", "-q", "feat/player-ranks")
    decide(client, tid)

    async def values():
        graph = await engine._graph(tid)
        return (await graph.aget_state(engine._cfg(tid))).values

    assert client.portal.call(values)["base_head"] == body["base_head"]
