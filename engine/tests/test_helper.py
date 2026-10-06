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


# ------------------------------------------------------------------ Fix mode (v0.6.x M2)

import threading
import subprocess as _sp

from keel_engine import hook
from keel_engine.runtime import permissions

FLOW = {"thread_id": "t-gate", "phase": "green", "ac": {"id": "AC-1", "layer": "API", "title": "the main case works"},
        "acs": [{"id": "AC-1", "layer": "API", "title": "the main case works", "status": "green"}], "unlocks": [],
        "workflow": "feature", "run_mode": "manual", "status": "waits", "title": "Player ranks"}


def fix_session(client, repo):
    return new_session(client, repo, mode="fix", thread_id="t-gate")


def test_fix_needs_the_waiting_flow(client, repo):
    r = client.post("/helper/sessions", json={"project_id": "demo", "root": str(repo), "mode": "fix", "model": FAKE})
    assert r.status_code == 400 and "flow that waits" in r.json()["error"]


def test_fix_changes_files_the_phase_allows_and_undo_puts_them_back(client, repo):
    s = fix_session(client, repo)
    _, s = ask(client, s["id"], "Add the small helper the gate asks for.", flow=FLOW)
    assert "helper_fix.py" in s["messages"][-1]["text"]
    ch = client.get(f"/helper/sessions/{s['id']}/changes").json()
    assert [(c["path"], c["status"], c["added"]) for c in ch] == [("src/scores/helper_fix.py", "added", 2)]
    assert "+def helped():" in ch[0]["diff"]
    assert client.post(f"/helper/sessions/{s['id']}/undo", json={}).json() == []
    assert not (Path(repo) / "src" / "scores" / "helper_fix.py").exists()


def test_fix_puts_back_what_the_phase_forbids(client, repo):
    # fake-rogue also writes a test file behind keel's back: tests are frozen in green, so it is put back
    s = new_session(client, repo, mode="fix", thread_id="t-gate", model={"provider": "fake", "mode": "api", "model": "fake-rogue"})
    ask(client, s["id"], "Fix it.", flow=FLOW)
    assert not (Path(repo) / "tests" / "test_rogue_helper.py").exists()
    assert (Path(repo) / "src" / "scores" / "helper_fix.py").exists()          # what green allows stays
    put_back = [e["data"]["text"] for e in client.bus.recent if e["type"] == "helper.step" and e["data"]["kind"] == "guard"]
    assert any("test_rogue_helper.py" in t for t in put_back)


def test_done_runs_the_checks_and_makes_keels_commit_of_only_the_helpers_files(client, repo):
    (Path(repo) / "NOTES.md").write_text("the person's own unsaved notes\n")      # never part of the Helper's commit
    s = fix_session(client, repo)
    ask(client, s["id"], "Add the helper.", flow=FLOW)
    r = client.post(f"/helper/sessions/{s['id']}/done", json={"flow": FLOW, "message": "Ranks use  the new helper"}).json()
    assert r["ok"] is True, r
    assert r["files"] == ["src/scores/helper_fix.py"] and r["sha"]
    log = _sp.run(["git", "log", "-1", "--format=%s", "--name-only"], cwd=repo, capture_output=True, text=True).stdout
    # the gate's criterion names it, in the commit type of the phase the chat works in (green: feat)
    assert log.startswith("feat(AC-1): Ranks use the new helper\n"), log
    assert "src/scores/helper_fix.py" in log and "NOTES.md" not in log
    assert client.get(f"/helper/sessions/{s['id']}/changes").json() == []
    ev = [e for e in client.bus.recent if e["type"] == "helper.commit"][-1]
    assert ev["thread_id"] == "t-gate" and ev["data"]["sha"] == r["sha"] and ev["data"]["session"] == s["id"]
    assert client.get(f"/helper/sessions/{s['id']}").json()["messages"][-1]["data"]["status"] == "committed"
    nothing = client.post(f"/helper/sessions/{s['id']}/done", json={"flow": FLOW}).json()
    assert nothing["ok"] is False and nothing["step"] == "changes"


def test_done_stops_when_the_checks_fail(client, repo):
    s = fix_session(client, repo)
    ask(client, s["id"], "Add the helper.", flow=FLOW)
    (Path(repo) / "src" / "scores" / "__init__.py").write_text("raise SystemExit('broken')\n")    # the suite fails now
    r = client.post(f"/helper/sessions/{s['id']}/done", json={"flow": FLOW}).json()
    assert r["ok"] is False and r["step"] == "checks" and r["command"]
    assert client.get(f"/helper/sessions/{s['id']}/changes").json()                              # nothing committed


def test_a_permission_card_waits_for_the_person(client, repo):
    s = fix_session(client, repo)
    runner = client.app.state.helper
    runner.ask_keys[s["id"]] = "k1"
    wrong = client.post("/helper/permissions/ask", json={"session": s["id"], "key": "nope", "command": "rm -rf build"}).json()
    assert wrong["decision"] == "deny"
    got = {}
    t = threading.Thread(target=lambda: got.update(client.post("/helper/permissions/ask", json={
        "session": s["id"], "key": "k1", "command": "npm install left-pad"}).json()))
    t.start()
    deadline = time.time() + 10
    while not client.get("/helper/permissions", params={"project": "demo"}).json() and time.time() < deadline:
        time.sleep(0.02)
    q = client.get("/helper/permissions", params={"project": "demo"}).json()[0]
    assert q["command"] == "npm install left-pad" and q["session"] == s["id"]
    assert client.post(f"/helper/permissions/{q['id']}", json={"decision": "always"}).json()["decision"] == "always"
    t.join(10)
    assert got == {"decision": "allow", "why": ""}
    again = client.post("/helper/permissions/ask", json={"session": s["id"], "key": "k1", "command": "npm install left-pad"}).json()
    assert again == {"decision": "allow"}                                                       # "always" remembered
    assert client.get(f"/helper/sessions/{s['id']}").json()["grants"] == ["npm install left-pad"]
    # a deny carries the person's reason
    t = threading.Thread(target=lambda: got.update(client.post("/helper/permissions/ask", json={
        "session": s["id"], "key": "k1", "command": "git push"}).json()))
    t.start()
    while not client.get("/helper/permissions").json() and time.time() < deadline:
        time.sleep(0.02)
    q = client.get("/helper/permissions").json()[0]
    client.post(f"/helper/permissions/{q['id']}", json={"decision": "deny", "why": "keel pushes, not you"})
    t.join(10)
    assert got == {"decision": "deny", "why": "keel pushes, not you"}
    assert client.post(f"/helper/permissions/{q['id']}", json={"decision": "once"}).status_code == 404


def test_the_hook_asks_only_for_commands_that_change_something(repo, monkeypatch):
    asked = []
    monkeypatch.setattr(permissions, "ask_engine", lambda a, kind, cmd, path="": (asked.append(cmd) or (False, "said no")))
    ctx = {"root": str(repo), "phase": "green", "unlocks": [], "ask": {"url": "http://x", "key": "k", "session": "h_1"}}
    assert hook.decide("Bash", {"command": "ls src && git status"}, dict(ctx)) is None
    assert hook.decide("Bash", {"command": "python -m pytest -q"}, dict(ctx)) is None              # tests need no OK
    assert hook.decide("Bash", {"command": "rm -rf build"}, dict(ctx)) == "said no"
    assert asked == ["rm -rf build"]
    no_ask = {k: v for k, v in ctx.items() if k != "ask"}
    assert hook.decide("Bash", {"command": "rm -rf build"}, no_ask) is None                         # a flow's agent: rules alone


def test_permission_rules():
    assert not permissions.needs_ask("grep -rn score src")
    assert permissions.needs_ask("npm install x") and permissions.needs_ask("echo hi > out.txt")
    assert permissions.granted("npm test -- --watch=false", ["npm test *"])
    assert permissions.granted("git status", ["git status"]) and not permissions.granted("git push", ["git status"])


def test_at_a_gate_fix_works_in_the_phase_of_the_work_under_review(client, repo):
    from keel_engine import rules
    assert not rules.edits_code("gate") and rules.edits_code("green") and rules.edits_code("spec")
    assert helper.fix_phase({"phase": "gate", "phases_before": ["gate", "green", "red"]}) == "green"
    assert helper.fix_phase({"phase": "spec", "phases_before": ["spec"]}) == "spec"
    assert helper.fix_phase({"phase": "gate"}) == "gate"            # nothing earlier lets code change: the gate's own
    gate = {**FLOW, "phase": "gate", "phases_before": ["gate", "green", "red"]}
    s = new_session(client, repo, mode="fix", thread_id="t-gate", flow=gate)
    assert s["phase"] == "green"
    _, s = ask(client, s["id"], "Fix it at the AC gate.", flow=gate)
    assert (Path(repo) / "src" / "scores" / "helper_fix.py").exists()     # green lets source change; "gate" would not


async def test_an_older_helper_table_gets_its_new_columns(tmp_path):
    import aiosqlite
    from keel_engine.runtime import migrate
    async with aiosqlite.connect(tmp_path / "m.db") as conn:
        # the table as the first Helper build made it: no grants_json, no phase
        await conn.execute("""create table helper_sessions (
          id text primary key, project text not null, root text not null, mode text not null, title text not null,
          model_json text not null, engine_session text, status text not null, error text, thread_id text,
          tokens_in integer not null default 0, tokens_out integer not null default 0, tokens_cached integer not null default 0,
          cost_usd real not null default 0, turns integer not null default 0, created_at text not null, updated_at text not null)""")
        await conn.execute("insert into helper_sessions (id, project, root, mode, title, model_json, status, created_at, updated_at) "
                           "values ('h_old', 'p', '/w', 'ask', 't', '{}', 'idle', 'x', 'x')")
        await migrate.migrate(conn)
        await migrate.migrate(conn)
        async with conn.execute("select grants_json, phase from helper_sessions where id = 'h_old'") as cur:
            assert await cur.fetchone() == ("[]", None)
