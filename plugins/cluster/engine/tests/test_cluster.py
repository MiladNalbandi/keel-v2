"""The Cluster plugin's engine part against a stand-in kubectl (fixtures/fake_kubectl.py). What matters most: names are checked before
they reach kubectl, a read-only or off context changes nothing, an agent's change on an "ask" context waits for the person's yes in
keel's Inbox, and every command and change is recorded. Run from engine/: PYTHONPATH=../plugins/cluster/engine uv run pytest
../plugins/cluster/engine/tests (scripts/test-plugins.sh)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keel_engine import approvals, extensions
from keel_engine.events import EventBus
from keel_engine.pluginhost import manifest as manifests
from keel_plugin_cluster import VERSION, core, kube, tools
from keel_plugin_cluster.routes import router
from keel_plugin_cluster.server import build

PLUGIN = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures"
IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


@pytest.fixture
def kube_env(tmp_path, monkeypatch):
    """A stand-in kubectl and a fresh KEEL_DATA; answers the list of kubectl calls made."""
    data = tmp_path / "fix"
    shutil.copytree(FIX, data)
    exe = tmp_path / "kubectl"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} -I {FIX / 'fake_kubectl.py'} \"$@\"\n")
    exe.chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("")
    monkeypatch.setenv("KEEL_KUBECTL", str(exe))
    monkeypatch.setenv("FAKE_KUBE_LOG", str(log))
    monkeypatch.setenv("FAKE_KUBE_DIR", str(data))
    monkeypatch.setenv("KEEL_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(kube, "_CTX_CACHE", (0.0, []))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()]
    calls.dir = data
    return calls


@pytest.fixture
def bus():
    return EventBus()


def call_with(bus, rules):
    return {"project": "p1", "keys": {"cluster:access": json.dumps(rules)}, "bus": bus, "thread": "t1", "session": None, "who": "keelbot"}


def answer_when_asked(bus, decision, why=""):
    broker = approvals.of(bus)
    asked: dict = {}

    def person():
        for _ in range(300):
            waiting = list(broker.questions.values())
            if waiting:
                asked.update(waiting[0])
                broker.answer(waiting[0]["id"], decision, why)
                return
            time.sleep(0.01)
    t = threading.Thread(target=person)
    t.start()
    return asked, t


def changes(calls):
    return [c for c in calls() if any(w in c for w in ("exec", "delete", "rollout", "scale"))]


def test_manifest_version_and_its_tools(monkeypatch):
    m = manifests.parse((PLUGIN / "keel-plugin.yml").read_text())
    assert m.name == "cluster" and m.version == VERSION
    raw = yaml.safe_load((PLUGIN / "keel-plugin.yml").read_text())
    assert raw["permissions"]["agent_tools"] == "act" and raw["per_project"] is True
    listed = {t.name: t.annotations.readOnlyHint for t in asyncio.run(build().list_tools())}
    assert listed == {"cluster_find": True, "cluster_logs": True, "cluster_describe": True, "cluster_exec": False, "cluster_change": False}
    monkeypatch.setenv("KEEL_PLUGIN_PATHS", str(PLUGIN / "engine"))
    monkeypatch.setenv("KEEL_PLUGIN_ADDONS", "keel_plugin_cluster")
    extensions.reload()
    p = extensions.part("cluster")
    assert set(p.read_tools) == {"cluster_find", "cluster_logs", "cluster_describe"}
    assert set(p.act_tools) == {"cluster_exec", "cluster_change"}
    monkeypatch.delenv("KEEL_PLUGIN_PATHS")
    monkeypatch.delenv("KEEL_PLUGIN_ADDONS")
    extensions.reload()


def test_contexts_give_names_and_servers_only(kube_env):
    c = kube.contexts()
    assert c["kubectl"] and c["kubectl_version"] == "v1.32.3"
    by = {r["name"]: r for r in c["contexts"]}
    assert set(by) == {"kind-dev", "staging-eu", "far-away"}
    assert by["kind-dev"]["local"] and by["kind-dev"]["current"] and not by["staging-eu"]["local"]
    assert by["staging-eu"]["namespace"] == "shop"
    assert "REDACTED" not in json.dumps(c) and "token" not in json.dumps(c)


def test_without_kubectl_it_says_how_to_get_it(monkeypatch):
    monkeypatch.setenv("KEEL_KUBECTL", "")
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert kube.contexts()["kubectl"] is False
    with pytest.raises(kube.ClusterError) as e:
        kube.snapshot("kind-dev")
    assert e.value.status == 409 and "kubectl" in e.value.hint


def test_snapshot_rows_problems_and_commits(kube_env):
    s = kube.snapshot("kind-dev")
    pods = {p["name"]: p for p in s["pods"]}
    assert pods["api-7d9f-x2kqp"]["ip"] == "10.42.1.17" and pods["api-7d9f-x2kqp"]["commit"] == "a1c94e2"
    assert pods["api-7d9f-x2kqp"]["owner"] == "replicaset/api-7d9f" and pods["api-7d9f-x2kqp"]["app"] == "api"
    w = pods["worker-6b7c-h4n2s"]
    assert w["status"] == "CrashLoopBackOff" and w["restarts"] == 7 and w["ready"] == "0/1" and w["commit"] == "c77e0a9"
    assert pods["indexer-7c6d-t2m8x"]["status"] == "Pending" and pods["indexer-7c6d-t2m8x"]["ip"] == ""
    svc = {x["name"]: x for x in s["services"]}
    assert svc["web"]["external"] == ["192.168.49.240"] and svc["web"]["ports"] == ["80→8080/TCP"]
    assert svc["grafana"]["ports"] == ["3000:31300/TCP"]
    nodes = {n["name"]: n for n in s["nodes"]}
    assert nodes["node-a"]["internal_ip"] == "192.168.49.2" and nodes["node-a"]["roles"] == ["control-plane"]
    assert nodes["node-a"]["cpu_pct"] == 41 and nodes["node-a"]["mem_pct"] == 63 and s["metrics"] is True
    assert nodes["node-a"]["pods"] == 1 and nodes["node-b"]["pods"] == 1
    assert s["ingresses"][0]["hosts"] == [{"host": "shop.local", "path": "/", "service": "web:80"}]
    assert s["namespaces"] == ["jobs", "monitoring", "shop"]
    kinds = [p["kind"] for p in s["problems"]]
    assert kinds[0] == "node" and "crash" in kinds and "waiting" in kinds and "deploy" in kinds
    crash = next(p for p in s["problems"] if p["kind"] == "crash")
    assert "keeps restarting (7 times)" in crash["title"] and "Back-off" in crash["line"]


@pytest.mark.parametrize("bad", ["shop;rm -rf /", "--all", "../x", "Shop", "a b", "$(id)"])
def test_names_are_checked_before_kubectl_runs(kube_env, bad):
    with pytest.raises(kube.ClusterError) as e:
        kube.logs("kind-dev", bad, "api-7d9f-x2kqp")
    assert e.value.status == 400
    with pytest.raises(kube.ClusterError):
        kube.change("kind-dev", "delete", "pod", "shop", bad)
    with pytest.raises(kube.ClusterError):
        kube.snapshot("kind-dev; id" if bad == "a b" else "kind-dev", bad)
    assert kube_env() == []


def test_logs_describe_and_what_is_not_described(kube_env):
    r = kube.logs("kind-dev", "shop", "worker-6b7c-h4n2s", previous=True)
    assert r["container"] == "worker" and "KeyError" in r["lines"][-1]
    assert "--previous" in kube_env()[-1] and "--tail=300" in kube_env()[-1]
    with pytest.raises(kube.ClusterError) as e:
        kube.logs("kind-dev", "shop", "worker-6b7c-h4n2s", container="other")
    assert e.value.status == 404
    assert "Name:  api" in kube.describe("kind-dev", "deployment", "shop", "api")["text"]
    for kind in ("secret", "configmap"):
        with pytest.raises(kube.ClusterError) as e:
            kube.describe("kind-dev", kind, "shop", "db-password")
        assert e.value.status == 400


def test_a_cluster_out_of_reach_is_a_sentence_not_a_crash(kube_env):
    t = kube.test("far-away")
    assert t["ok"] is False and "i/o timeout" in t["error"] and "reach" in t["hint"]
    assert kube.test("kind-dev")["ok"] is True and kube.test("kind-dev")["nodes"] == 2
    assert kube.test("not-there")["ok"] is False


def test_a_loopback_cluster_from_docker_says_why(kube_env, monkeypatch):
    monkeypatch.setattr(kube, "in_docker", lambda: True)
    assert "docker network connect kind" in kube.hint_for("kind-dev", "dial tcp 127.0.0.1:42133: connect: connection refused")
    assert "docker network" not in kube.hint_for("far-away", "i/o timeout")


def test_the_persons_command_follows_the_contexts_access_and_is_recorded(kube_env):
    with pytest.raises(kube.ClusterError) as e:
        core.person_exec("kind-dev", "shop", "api-7d9f-x2kqp", "ls", "", "read")
    assert e.value.status == 403 and "read only" in str(e.value)
    with pytest.raises(kube.ClusterError) as e:
        core.person_change("kind-dev", "delete", "pod", "shop", "api-7d9f-x2kqp", None, None)
    assert e.value.status == 403 and "turned off" in str(e.value)
    assert changes(kube_env) == []
    out = core.person_exec("kind-dev", "shop", "api-7d9f-x2kqp", "ls /app", "", "ask")
    assert out == {"output": "hello from the pod\n", "code": 0}
    assert kube_env()[-1][-4:] == ["--", "sh", "-c", "ls /app"]
    assert core.person_change("kind-dev", "scale", "deployment", "shop", "api", 3, "free")["done"]
    with pytest.raises(kube.ClusterError):
        core.person_change("kind-dev", "scale", "deployment", "shop", "api", 500, "free")
    items = core.history()["items"]
    assert [i["action"] for i in items[:3]] == ["scale", "scale", "exec: ls /app"]
    assert items[1]["target"] == "deployment/shop/api → 3" and items[1]["who"] == "person"
    assert any("read only" in i["result"] for i in items)


def test_an_agent_reads_only_contexts_that_are_on(kube_env, bus):
    c = call_with(bus, {"kind-dev": "read"})
    assert "10.42.3.4" in tools.call_tool(c, "cluster_find", {"query": "10.42.3"})
    assert "worker-6b7c-h4n2s" not in tools.call_tool(c, "cluster_find", {"query": "10.42.1"})
    assert "keeps restarting" in tools.call_tool(c, "cluster_find", {})
    assert "KeyError" in tools.call_tool(c, "cluster_logs", {"context": "kind-dev", "ns": "shop", "name": "worker-6b7c-h4n2s", "previous": True})
    off = tools.call_tool(c, "cluster_logs", {"context": "staging-eu", "ns": "shop", "name": "api"})
    assert "not open to agents" in off
    assert "No cluster is open" in tools.call_tool(call_with(bus, {}), "cluster_find", {})


def test_an_agents_change_on_read_only_is_refused_and_asks_nobody(kube_env, bus):
    out = tools.call_tool(call_with(bus, {"kind-dev": "read"}), "cluster_change", {"context": "kind-dev", "action": "delete", "ns": "shop",
                                                                                   "name": "api-7d9f-x2kqp"})
    assert "Nothing was done" in out and "read only" in out
    assert not approvals.of(bus).questions and changes(kube_env) == []
    assert core.history()["items"][0]["who"] == "agent:keelbot"


def test_an_agents_command_waits_for_the_persons_yes_and_shows_it(kube_env, bus):
    asked, t = answer_when_asked(bus, "once")
    out = tools.call_tool(call_with(bus, {"kind-dev": "ask"}), "cluster_exec", {"context": "kind-dev", "ns": "shop", "name": "api-7d9f-x2kqp",
                                                                                "command": "cat /etc/hostname"})
    t.join()
    assert asked["kind"] == "plugin" and asked["source"] == "plugin:cluster" and "cat /etc/hostname" in asked["command"]
    assert "exit 0" in out and "hello from the pod" in out
    assert len(changes(kube_env)) == 1


def test_a_no_changes_nothing(kube_env, bus):
    asked, t = answer_when_asked(bus, "deny", "not now")
    out = tools.call_tool(call_with(bus, {"kind-dev": "ask"}), "cluster_change", {"context": "kind-dev", "action": "restart", "ns": "shop",
                                                                                  "name": "api"})
    t.join()
    assert "restart" in asked["command"].lower() and "Nothing was done" in out and "not now" in out
    assert changes(kube_env) == []
    assert core.history()["items"][0]["result"] == "the person did not approve it"


def test_free_runs_without_asking(kube_env, bus):
    out = tools.call_tool(call_with(bus, {"kind-dev": "free"}), "cluster_change", {"context": "kind-dev", "action": "scale", "ns": "shop",
                                                                                   "name": "api", "replicas": 2})
    assert "asks for 2 pods" in out and not approvals.of(bus).questions


def test_without_a_way_to_ask_the_answer_is_no(kube_env):
    c = {"project": "p1", "keys": {"cluster:access": json.dumps({"kind-dev": "ask"})}, "bus": None}
    out = tools.call_tool(c, "cluster_exec", {"context": "kind-dev", "ns": "shop", "name": "api-7d9f-x2kqp", "command": "id"})
    assert "Nothing was done" in out and changes(kube_env) == []


def _repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    shas = []
    for i, msg in enumerate(["First", "Rename customer fields (#214)", "Faster order search (#212)"]):
        (work / "f.txt").write_text(str(i))
        subprocess.run(["git", "-C", str(work), "add", "f.txt"], check=True)
        subprocess.run(["git", "-C", str(work), *IDENT, "commit", "-qm", msg], check=True)
        shas.append(subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip())
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin", "git@github.com:example/shop.git"], check=True)
    return work, shas


def test_what_runs_where_against_the_projects_base(kube_env, tmp_path):
    work, shas = _repo(tmp_path)
    all_json = kube_env.dir / "all.json"
    all_json.write_text(all_json.read_text().replace("COMMIT_API", shas[2]).replace("COMMIT_OLD", shas[1][:8]))
    # origin/main is the pushed head; the remote now names GitHub, which keel turns into a web link
    r = core.runs(str(work), ["kind-dev", "far-away"])
    assert r["repo"]["base"] == "origin/main" and r["repo"]["head"] == shas[2] and r["repo"]["web"] == "https://github.com/example/shop"
    by = {a["name"]: a["by"]["kind-dev"] for a in r["apps"]}
    assert by["api"]["state"] == "same" and by["api"]["pr"] == "#212"
    assert by["worker"]["state"] == "behind" and by["worker"]["behind"] == 1 and by["worker"]["pr"] == "#214"
    assert "far-away" in r["errors"]
    assert core.runs("", ["kind-dev"])["repo"] is None


def test_the_routes_answer_and_refuse_with_a_status(kube_env):
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(kube.ClusterError)
    async def _err(_req, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": str(exc), "hint": exc.hint}, status_code=exc.status)

    with TestClient(app) as http:
        assert http.post("/plugins/cluster/contexts", json={}).json()["kubectl"] is True
        snap = http.post("/plugins/cluster/snapshot", json={"context": "kind-dev"}).json()
        assert len(snap["pods"]) == 3
        r = http.post("/plugins/cluster/exec", json={"context": "kind-dev", "ns": "shop", "name": "api-7d9f-x2kqp", "command": "id",
                                                     "access": "read"})
        assert r.status_code == 403 and "read only" in r.json()["error"]
        assert http.post("/plugins/cluster/nope", json={}).status_code == 404
        assert http.post("/plugins/cluster/history", json={}).json()["items"][0]["context"] == "kind-dev"


def test_web_urls_and_pull_requests():
    assert core.web_url("git@github.com:example/shop.git") == "https://github.com/example/shop"
    assert core.web_url("https://token@gitlab.example.net/team/app.git") == "https://gitlab.example.net/team/app"
    assert core.pr_of("Merge pull request #81 from x/y") == "#81" and core.pr_of("Fix (#7)") == "#7" and core.pr_of("Fix") is None
