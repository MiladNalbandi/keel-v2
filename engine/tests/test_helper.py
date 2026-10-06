"""keel's Helper (runtime/helper.py, runtime/plugins.py): sessions, turns on the fake model, read-only Ask, plugins."""

import time
from pathlib import Path

import pytest

from keel_engine.runtime import helper, plugins

FAKE = {"provider": "fake", "mode": "api", "model": "fake"}


def new_session(client, repo, **extra):
    r = client.post("/helper/sessions", json={"project_id": "demo", "root": str(repo), "model": FAKE, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def wait_idle(client, sid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/helper/sessions/{sid}").json()
        if not s["busy"] and s["status"] != "running":
            return s
        time.sleep(0.02)
    raise AssertionError(f"session {sid} still busy: {s}")


def ask(client, sid, text, **extra):
    r = client.post(f"/helper/sessions/{sid}/turn", json={"text": text, **extra})
    assert r.status_code == 200, r.text
    return r.json(), wait_idle(client, sid)


def test_a_session_answers_and_keeps_the_conversation(client, repo):
    s = new_session(client, repo)
    assert s["mode"] == "ask" and s["status"] == "idle" and s["title"] == "New chat"
    started, s = ask(client, s["id"], "Where does the package start?")
    assert started["n"] == 1 and started["call_id"]
    roles = [(m["role"], m["text"]) for m in s["messages"]]
    assert roles[0] == ("user", "Where does the package start?")
    assert roles[1][0] == "helper" and "src/scores/__init__.py:1" in roles[1][1] and "answer 1" in roles[1][1]
    assert s["title"] == "Where does the package start?" and s["turns"] == 1 and s["tokens_in"] > 0
    _, s = ask(client, s["id"], "And the tests?")
    assert "answer 2" in s["messages"][-1]["text"]                    # the model got the earlier turn
    assert [x["id"] for x in client.get("/helper/sessions", params={"project": "demo"}).json()] == [s["id"]]


def test_a_turn_is_events_of_its_own_never_a_flow(client, repo):
    s = new_session(client, repo)
    started, _ = ask(client, s["id"], "What is here?")
    evs = [e for e in client.bus.recent if e["thread_id"] == s["id"]]
    types = [e["type"] for e in evs]
    assert types[0] == "helper.started" and types[-1] == "helper.finished" and "helper.step" in types
    assert all(e["call_id"] == started["call_id"] and e["step"] == "helper" and e["project_id"] == "demo" for e in evs)
    assert evs[0]["data"]["agent"] == "helper" and evs[0]["data"]["phase"] == "helper-ask"
    assert evs[-1]["data"]["status"] == "done" and evs[-1]["data"]["tokens_in"] > 0
    assert not any(e["type"].startswith(("agent.", "thread.")) for e in evs)


def test_ask_changes_nothing_even_when_the_engine_writes_behind_keels_back(client, repo):
    s = new_session(client, repo, model={"provider": "fake", "mode": "api", "model": "fake-rogue"})
    ask(client, s["id"], "Look around.")
    assert not (Path(repo) / "tests" / "test_rogue_helper.py").exists()
    put_back = [e for e in client.bus.recent if e["type"] == "helper.step" and e["data"]["kind"] == "guard"]
    assert put_back and "Ask mode changes nothing" in put_back[-1]["data"]["text"]


def test_a_slash_command_sends_its_prompt(client, repo):
    s = new_session(client, repo)
    started, s = ask(client, s["id"], "/where the score table")
    assert started["command"] == "where"
    assert s["messages"][0]["text"] == "/where the score table"           # kept as typed
    assert "Where is the score table in this project?" in s["messages"][1]["text"]


def test_stop_and_busy(client, repo, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE_DELAY", "0.3")
    s = new_session(client, repo)
    r = client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "Slow one"})
    assert r.status_code == 200
    again = client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "Another"})
    assert again.status_code == 409 and "still answering" in again.json()["error"]
    client.post(f"/helper/sessions/{s['id']}/stop")
    s = wait_idle(client, s["id"])
    assert s["messages"][-1]["role"] == "note" and s["messages"][-1]["data"]["status"] == "stopped"


def test_bad_requests_and_delete(client, repo):
    assert client.post("/helper/sessions", json={"project_id": "demo", "root": str(repo), "mode": "edit-everything"}).status_code == 400
    assert client.get("/helper/sessions/h_nope").status_code == 404
    s = new_session(client, repo)
    assert client.post(f"/helper/sessions/{s['id']}/turn", json={"text": ""}).status_code == 400
    r = client.patch(f"/helper/sessions/{s['id']}", json={"title": "Scores", "model": {"provider": "claude", "mode": "subscription", "model": "haiku"}})
    assert r.json()["title"] == "Scores"
    assert client.delete(f"/helper/sessions/{s['id']}").json() == {"ok": True}
    assert client.get(f"/helper/sessions/{s['id']}").status_code == 404


def test_the_prompt_carries_the_flow_what_the_person_points_at_and_the_conversation():
    p = helper.build_prompt(
        mode="ask", root="/w", question="Why does AC-2 fail?", know={"sections": [], "code_graph": False, "memory": False},
        graph=False, flow={"title": "Discount codes", "status": "waits", "phase": "green", "spec": "docs/specs/d.md",
                           "acs": [{"id": "AC-2", "layer": "API", "title": "an unknown code is refused", "status": "red"}],
                           "waiting": {"title": "AC gate", "detail": "tests 1/2 pass"}},
        mentions=[{"kind": "symbol", "value": "checkout", "file": "src/app/checkout.js", "line": 4}],
        selection={"path": "src/app/checkout.js", "from": 10, "to": 12, "text": "const off = discount(code)"},
        open_file="src/app/checkout.js", transcript=helper._transcript([("user", "hi"), ("assistant", "hello")]))
    assert p.startswith("Mode: Ask. Read only")
    assert 'A flow waits in this project: "Discount codes" (phase green).' in p
    assert "- AC-2 [API] an unknown code is refused (red)" in p and "It waits for the person at a gate: AC gate." in p
    assert "- symbol checkout (src/app/checkout.js:4)" in p
    assert "- selected lines src/app/checkout.js:10-12:\n```\nconst off = discount(code)\n```" in p
    assert "Person: hi\n\nHelper: hello" in p and p.endswith("Question: Why does AC-2 fail?")


def test_plugins_keel_and_project_commands(tmp_path):
    names = [c["name"] for c in plugins.commands(None)]
    assert {"explain", "where", "review", "plan", "gate", "test"} <= set(names)
    root = tmp_path / "p"
    (root / ".keel" / "plugins" / "team").mkdir(parents=True)
    (root / ".keel" / "plugins" / "team" / "plugin.yml").write_text(
        "name: team\ncommands:\n  - {name: where, description: ours, prompt: 'Find {{args}} in our monorepo'}\n"
        "  - {name: Bad, prompt: x}\ncontext: [docs/ARCH.md, ../secret]\n")
    (root / ".keel" / "plugins" / "broken").mkdir()
    (root / ".keel" / "plugins" / "broken" / "plugin.yml").write_text("commands: [")
    (root / "docs").mkdir()
    (root / "docs" / "ARCH.md").write_text("# arch")
    loaded = {p["name"]: p for p in plugins.load(str(root))}
    assert loaded["broken"]["problems"] and "does not parse" in loaded["broken"]["problems"][0]
    assert any("lowercase letters" in x for x in loaded["team"]["problems"])
    assert any("inside the project" in x for x in loaded["team"]["problems"])
    where = next(c for c in plugins.commands(str(root)) if c["name"] == "where")
    assert where["source"] == "project" and where["description"] == "ours"
    assert plugins.expand(str(root), "/where the cart") == ("Find the cart in our monorepo", "where")
    assert plugins.expand(str(root), "/nope x") == ("/nope x", None)
    assert plugins.expand(str(root), "/explain")[0].startswith("Explain the selected lines or the open file")
    assert plugins.context_files(str(root)) == ["docs/ARCH.md"]


def test_commands_endpoint(client, repo):
    got = client.post("/helper/commands", json={"root": str(repo)}).json()
    assert {"name": "explain", "description": "Explain a file, a symbol or the selected lines in plain words",
            "plugin": "core", "source": "keel"} in got


@pytest.mark.parametrize("provider,resumes", [("claude", True), ("codex", True), ("copilot", False)])
def test_which_engines_continue_their_own_session(provider, resumes):
    assert (provider in helper.RESUMABLE) is resumes
