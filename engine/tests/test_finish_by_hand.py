"""keel 0.18.74 a stopped flow the person finished themselves: "finish by hand" commits the folder's changes through
keel's secret scan, marks the criteria they name done, and ends the flow as done, with a gate log line that says so."""

from conftest import start, wait
from test_v02 import git, write


def stopped(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    assert client.post(f"/threads/{tid}/stop").json()["status"] == "stopped"
    return tid


def test_a_stopped_flow_finished_by_hand_commits_and_ends_done(client, repo):
    tid = stopped(client, repo)
    write(repo, "src/scores/ranks.py", "def rank(xs):\n    return sorted(xs, reverse=True)\n")
    r = client.post(f"/threads/{tid}/finish-by-hand", json={"done": ["AC-1"], "message": "fixed the order myself",
                                                            "by": "the dashboard"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"] == "done"
    assert {a["id"]: a["status"] for a in s["acs"]}["AC-1"] == "done"
    assert any(x.startswith("finished by hand by the dashboard: fixed the order myself · commit ") for x in s["gate_log"])
    assert git(repo, "log", "-1", "--format=%s").stdout.strip().endswith(": finished by hand")
    assert git(repo, "status", "--porcelain").stdout.strip() == ""


def test_finish_by_hand_refuses_a_running_flow_an_unknown_criterion_and_a_secret(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    assert client.post(f"/threads/{tid}/finish-by-hand", json={}).status_code == 409        # waiting, not stopped
    client.post(f"/threads/{tid}/stop")
    assert client.post(f"/threads/{tid}/finish-by-hand", json={"done": ["AC-99"]}).status_code == 400
    write(repo, "config.py", 'AWS_SECRET_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    r = client.post(f"/threads/{tid}/finish-by-hand", json={"done": []})
    assert r.status_code == 409 and "secret" in r.text
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""                # nothing left staged
