"""v0.11.0 the CI/CD plugin (plugins/ci, keel_plugin_ci; moved from keel_engine/plugins/ci): the pipelines through gh,
why a run failed, a re-run, the steps a workflow uses, the read tools for models, and the ci-fix flow. A fake `gh` on
PATH answers from a JSON file."""

import json
import stat
import subprocess
import textwrap

import pytest

from conftest import start, wait
from keel_engine import extensions
from keel_engine.runtime import plugins as manifests
from keel_plugin_ci import core as ci
from keel_engine.workflows.model import from_dict, load_yaml
from keel_engine.workflows.validate import validate

FAKE_GH = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    state = json.load(open(os.environ["FAKE_GH_STATE"]))
    open(os.environ["FAKE_GH_STATE"] + ".calls", "a").write(" ".join(sys.argv[1:]) + "\\n")
    if not os.environ.get("GH_TOKEN"):
        print("gh: not logged in", file=sys.stderr); sys.exit(4)
    a = sys.argv[1:]
    if a[:2] == ["run", "list"]:
        runs = state["runs"]
        if "--branch" in a:
            runs = [r for r in runs if r["headBranch"] == a[a.index("--branch") + 1]]
        print(json.dumps(runs))
    elif a[:2] == ["run", "view"] and "--log-failed" in a:
        print("test\\trun the tests\\t2026-10-07T10:00:00.000Z FAIL tests/test_scores.py::test_total - assert 3 == 4")
    elif a[:2] == ["run", "view"]:
        rid = int(a[2])
        run = next(r for r in state["runs"] if r["databaseId"] == rid)
        print(json.dumps({**run, "jobs": state.get("jobs", {}).get(str(rid), [])}))
    elif a[:2] == ["run", "rerun"]:
        state["reruns"] = state.get("reruns", []) + [int(a[2])]
        json.dump(state, open(os.environ["FAKE_GH_STATE"], "w"))
    else:
        print("unknown", a, file=sys.stderr); sys.exit(1)
''')


def run(rid, branch, sha, conclusion, status="completed", workflow="ci"):
    return {"databaseId": rid, "headBranch": branch, "headSha": sha, "status": status, "conclusion": conclusion,
            "workflowName": workflow, "event": "push", "createdAt": "2026-10-07T10:00:00Z", "updatedAt": "2026-10-07T10:05:00Z",
            "url": f"https://github.com/o/r/actions/runs/{rid}", "displayTitle": "feat: ranks", "attempt": 1}


@pytest.fixture
def gh(tmp_path, monkeypatch, repo):
    """A fake gh on PATH; the demo repo on feat/ranks with a remote, its HEAD failed in CI, main passed."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe = bin_ / "gh"
    exe.write_text(FAKE_GH)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_}:{__import__('os').environ['PATH']}")
    subprocess.run(["git", "switch", "-q", "-c", "feat/ranks"], cwd=repo, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    state = tmp_path / "gh.json"
    state.write_text(json.dumps({"runs": [run(11, "feat/ranks", head, "failure"), run(10, "main", "abc123", "success")],
                                 "jobs": {"11": [{"databaseId": 111, "name": "test", "status": "completed", "conclusion": "failure",
                                                  "url": "u", "steps": [{"name": "run the tests", "conclusion": "failure"}]}]}}))
    monkeypatch.setenv("FAKE_GH_STATE", str(state))

    class G:
        path, root = state, repo

        def load(self):
            return json.loads(state.read_text())

        def set_runs(self, runs):
            d = self.load()
            d["runs"] = runs
            state.write_text(json.dumps(d))

        sha = head
    return G()


def test_the_runs_why_one_failed_and_a_rerun(gh):
    rs = ci.runs(str(gh.root), "t")
    assert [(r["id"], r["branch"], r["failed"]) for r in rs] == [(11, "feat/ranks", True), (10, "main", False)]
    assert [r["id"] for r in ci.runs(str(gh.root), "t", "main")] == [10]
    r = ci.run(str(gh.root), "t", 11)
    assert r["jobs"] == [{"id": 111, "name": "test", "status": "completed", "conclusion": "failure", "url": "u",
                          "failed_steps": ["run the tests"]}]
    assert r["log"] == "test · run the tests | FAIL tests/test_scores.py::test_total - assert 3 == 4"
    assert ci.rerun(str(gh.root), "t", 11) == {"id": 11, "rerun": True} and gh.load()["reruns"] == [11]
    with pytest.raises(ci.CiError, match="There is no GitHub token"):
        ci.runs(str(gh.root), None)


def test_the_engine_calls_and_the_read_tools_for_models(client, gh):
    body = {"root": str(gh.root), "keys": {"github": "t"}}
    assert [r["id"] for r in client.post("/plugins/ci/runs", json=body).json()] == [11, 10]
    assert client.post("/plugins/ci/run", json={**body, "run": 11}).json()["jobs"][0]["failed_steps"] == ["run the tests"]
    no = client.post("/plugins/ci/runs", json={"root": str(gh.root)})
    assert no.status_code == 400 and "Connections › GitHub" in no.json()["hint"]
    key = extensions.open_call(project="demo", root=str(gh.root), keys={"github": "t"}, plugins=["ci"], who="keelbot")
    text = client.post("/plugins/call", json={"key": key, "tool": "ci_failure", "args": {}}).json()["text"]
    assert text.startswith("Run #11 ci on feat/ranks") and "- job test failed at: run the tests" in text and "assert 3 == 4" in text
    runs = client.post("/plugins/call", json={"key": key, "tool": "ci_runs", "args": {"branch": "main"}}).json()["text"]
    assert runs.startswith("- #10 ci on main (abc123): success")
    extensions.close_call(key)


def ci_flow(client, repo, steps, on=("ci",), extra=None):
    wf = from_dict({"name": "ci", "keel_rules": False, "steps": steps})
    return start(client, repo, workflow=wf, keys={"github": "t"},
                 settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "plugins": list(on), **(extra or {})})


def test_the_ci_steps_read_the_failure_rerun_and_check_status(client, gh):
    tid = ci_flow(client, gh.root, [
        {"id": "logs", "kind": "code", "name": "why", "action": "ci:logs"},
        {"id": "again", "kind": "code", "name": "rerun", "action": "ci:rerun"},
        {"id": "status", "kind": "code", "name": "status", "action": "ci:status", "soft": True},
        {"id": "look", "kind": "gate", "name": "look"}])
    s = wait(client, tid)
    assert s["waiting"]["step"] == "look", s
    notes = {e["step"]: e["data"].get("note") for e in client.bus.of(tid, "step.finished")}
    assert notes["logs"].startswith("Read the failure: Run #11 ci on feat/ranks")
    assert notes["again"] == "The failed jobs of run #11 run again." and gh.load()["reruns"] == [11]
    assert "RESULT" not in str(notes)                   # a soft failure records itself as a marker, the flow goes on


def test_the_fix_flow_is_the_ci_plugins_own_and_valid():
    wf = next(w for w in manifests.plugin_workflows() if w["id"] == "ci-fix")
    assert wf["plugin"] == "ci" and validate(load_yaml(wf["yaml"])) == []
    assert [s["action"] for s in wf["steps"] if s["kind"] == "code"] == ["ci:logs", "commit", "git:push", "ci:wait"]
    assert "{{data.ci_failure}}" in next(s for s in wf["steps"] if s["kind"] == "agent")["instructions"]


def test_waiting_for_ci_after_a_push(gh):
    seen = []
    flips = iter([[run(12, "feat/ranks", gh.sha, None, "in_progress")], [run(12, "feat/ranks", gh.sha, "success")]])

    def tick(_s):
        seen.append(1)
        gh.set_runs(next(flips))
    gh.set_runs([])
    r = ci.wait(str(gh.root), "t", every_s=0, sleep=tick, grace_s=60)
    assert r["ok"] and [x["id"] for x in r["runs"]] == [12] and len(seen) == 2
    gh.set_runs([])
    with pytest.raises(ci.CiError, match="No pipeline ran for this commit"):
        ci.wait(str(gh.root), "t", every_s=0, sleep=lambda _s: None, grace_s=0)
