"""KeelBot (keel_plugin_keelbot.helper, keel's runtime/plugins.py): sessions, turns on the fake model, read-only Ask,
slash commands, Fix mode and side sessions. What core keeps for them (the hook, runtime/permissions.py, the helper
tables) is tested in engine/tests."""

import time
from pathlib import Path

import pytest

from keel_engine.runtime import plugins
from keel_plugin_keelbot import helper

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


def test_the_answer_streams_as_helper_delta_and_the_whole_answer_is_the_record(client, repo):
    s = new_session(client, repo)
    started, _ = ask(client, s["id"], "Where does it start?")
    evs = [e for e in client.bus.recent if e["thread_id"] == s["id"]]
    deltas = [e for e in evs if e["type"] == "helper.delta"]
    assert deltas and all(e["call_id"] == started["call_id"] and e["step"] == "helper" for e in deltas)
    answer = [e for e in evs if e["type"] == "helper.step" and e["data"]["kind"] == "answer"][-1]["data"]["text"]
    texts = [e["data"]["text"] for e in deltas]
    # the text so far grows, each a start of the whole answer; the last one is all of it
    assert all(answer.startswith(t) for t in texts) and texts[-1] == answer
    assert [e["data"]["n"] for e in deltas] == list(range(1, len(deltas) + 1))
    # deltas come before the answer step and are never steps themselves
    assert evs.index(deltas[-1]) < evs.index([e for e in evs if e["type"] == "helper.step" and e["data"]["kind"] == "answer"][-1])
    assert not any(e["type"] == "helper.step" and e["data"]["kind"] == "delta" for e in evs)


def test_thinking_streams_apart_from_the_answer_and_never_reaches_the_next_turn(client, repo, monkeypatch):
    """0.18.23 KeelBot's thinking, live like opencode's: helper.thinking.delta events (the thinking so far), apart from
    helper.delta; the answer text is unchanged; the thinking is kept with the answer's data, never in the history."""
    from keel_engine.models import fake
    from keel_plugin_keelbot import helper

    s = new_session(client, repo)
    started, s = ask(client, s["id"], "Where does it start?")
    evs = [e for e in client.bus.recent if e["thread_id"] == s["id"]]
    thinks = [e for e in evs if e["type"] == "helper.thinking.delta"]
    thought = fake.FAKE_THINKING.format(question="Where does it start?")
    assert thinks and all(e["call_id"] == started["call_id"] and e["step"] == "helper" for e in thinks)
    texts = [e["data"]["text"] for e in thinks]
    assert all(thought.startswith(t) for t in texts) and texts[-1] == thought
    # thinking comes before the answer and never mixes into it
    first_delta = next(e for e in evs if e["type"] == "helper.delta")
    assert evs.index(thinks[0]) < evs.index(first_delta)
    assert not any(thought[:20] in e["data"]["text"] for e in evs if e["type"] == "helper.delta")
    answer = s["messages"][-1]
    assert "The person asks" not in answer["text"] and "src/scores/__init__.py:1" in answer["text"]
    assert answer["data"]["thinking"] == thought and answer["data"]["thinking_ms"] >= 0
    assert not any(e["type"] == "helper.step" and e["data"]["kind"] == "thinking_delta" for e in evs)
    # the next turn's conversation: the questions and answers, never the thinking
    seen = []
    real = fake._plan
    monkeypatch.setattr(fake, "_plan", lambda req: (seen.append(req), real(req))[1])
    ask(client, s["id"], "And the tests?")
    hist = seen[-1].history
    assert hist and not any("The person asks" in t for _, t in hist)
    assert not any("The person asks" in t for _, t in helper.history(s["id"]))


def test_each_tool_call_is_a_helper_tool_event_and_never_reaches_the_next_turn(client, repo, monkeypatch):
    """keel TUI's tool lines: each tool call of a turn is one helper.tool event (name, args, status, summary, output),
    next to its helper.step; the next turn's conversation has none of their output."""
    from keel_engine.models import fake

    s = new_session(client, repo)
    started, s = ask(client, s["id"], "What is here?")
    evs = [e for e in client.bus.recent if e["thread_id"] == s["id"]]
    tools = [e for e in evs if e["type"] == "helper.tool"]
    assert tools and all(e["call_id"] == started["call_id"] and e["step"] == "helper" for e in tools)
    by_name = {e["data"]["name"]: e["data"] for e in tools}
    nxt = by_name["keel_next"]
    assert nxt["server"] == "keel" and nxt["args"] == {} and nxt["status"] == "ok" and not nxt["shell"]
    assert nxt["output"].startswith("next step is") and nxt["lines"] == 1 and nxt["summary"] == nxt["output"]
    read = by_name["Read"]
    assert read["args"] == read["path"] and read["lines"] > 1 and read["summary"] == f"{read['lines']} lines"
    # each record is the step with the same n: keel TUI joins them
    steps = {e["data"]["n"]: e["data"] for e in evs if e["type"] == "helper.step"}
    assert steps[nxt["n"]]["tool"] == "keel_next" and steps[read["n"]]["kind"] == "read"
    # the answer and the stored messages keep no tool output
    assert all("next step is" not in m["text"] for m in s["messages"])
    seen = []
    real = fake._plan
    monkeypatch.setattr(fake, "_plan", lambda req: (seen.append(req), real(req))[1])
    ask(client, s["id"], "And now?")
    assert seen[-1].history and not any("next step is" in t for _, t in seen[-1].history)
    assert not any("next step is" in t for _, t in helper.history(s["id"]))


def test_a_tool_record_folds_long_output_and_says_what_failed():
    long = "\n".join(f"line {i}" for i in range(300)) + "\n… (120 more lines)"
    t = helper.tool_event(4, "tool", "git status --short", {"tool": "Bash", "output": long, "ok": True, "ms": 30})
    assert t["shell"] and t["args"] == "git status --short" and t["lines"] == 420 and t["summary"] == "420 lines"
    bad = helper.tool_event(5, "tool", '{"project": "app"}', {"tool": "keel_status", "server": "keel", "output": "\nboom: no project\nmore",
                                                                  "ok": False})
    assert bad["status"] == "error" and bad["summary"] == "boom: no project" and bad["args"] == {"project": "app"}
    refused = helper.tool_event(6, "guard", "Ask mode is read only.", {"tool": "Write", "path": "a.py", "ok": False})
    assert refused["status"] == "refused" and refused["summary"] == "refused: Ask mode is read only." and refused["args"] == "a.py"
    edit = helper.tool_event(7, "edit", "Edit a.py (+3 -1)", {"path": "a.py", "diff": "--- a\n+++ b\n", "ok": True})
    assert edit["summary"] == "+3 -1" and edit["output"].startswith("--- a")
    huge = helper.tool_event(8, "tool", "cat big", {"tool": "Bash", "output": "x" * (helper.TOOL_OUTPUT_MAX + 500)})
    assert len(huge["output"]) < helper.TOOL_OUTPUT_MAX + 40 and huge["output"].endswith("(500 more characters)")


def test_thinking_kept_with_an_answer_is_capped():
    from keel_plugin_keelbot import helper

    got = helper._thinking_data({"text": "x" * (helper.THINKING_MAX + 50), "last": 12.5}, 10.0)
    assert len(got["thinking"]) <= helper.THINKING_MAX + 2 and got["thinking"].endswith(" …") and got["thinking_ms"] == 2500
    assert helper._thinking_data({"text": "  "}, 0.0) == {}


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
    # 0.18.28 no "still answering" refusal: the message waits for this answer
    again = client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "Another"})
    assert again.status_code == 200 and again.json()["queued"] is True and again.json()["position"] == 1
    assert client.get(f"/helper/sessions/{s['id']}").json()["queued"] == ["Another"]
    client.post(f"/helper/sessions/{s['id']}/stop")
    # the stop ends the first answer; the queued message goes then, and is answered
    deadline = time.time() + 20
    while time.time() < deadline:
        cur = client.get(f"/helper/sessions/{s['id']}").json()
        if not cur["busy"] and cur["status"] != "running" and any(m["text"] == "Another" for m in cur["messages"]):
            break
        time.sleep(0.05)
    s = wait_idle(client, s["id"])
    roles = [(m["role"], m["text"][:12]) for m in s["messages"]]
    assert roles[0] == ("user", "Slow one") and roles[1][0] == "note"            # stopped
    assert roles[2] == ("user", "Another") and roles[3][0] == "helper"           # then the queued one, answered
    assert s["queued"] == []


def test_a_message_with_interrupt_stops_the_answer_and_goes_now(client, repo, monkeypatch):
    monkeypatch.setenv("KEEL_FAKE_DELAY", "0.3")
    s = new_session(client, repo)
    client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "Slow one"})
    r = client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "Do this instead", "interrupt": True})
    assert r.status_code == 200 and not r.json().get("queued")
    s = wait_idle(client, s["id"])
    roles = [(m["role"], m["text"][:15]) for m in s["messages"]]
    assert roles == [("user", "Slow one"), ("note", "Stopped."), ("user", "Do this instead"), ("helper", roles[3][1])]


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
    assert "Person: hi\n\nKeelBot: hello" in p and p.endswith("Question: Why does AC-2 fail?")


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


def test_a_file_changed_on_another_branch_leaves_the_changed_files(client, repo):
    """0.18.71 after a switch to a branch without KeelBot's change, the file showed as "the whole file deleted"."""
    s = fix_session(client, repo)
    ask(client, s["id"], "Add the small helper the gate asks for.", flow=FLOW)
    assert [c["path"] for c in client.get(f"/helper/sessions/{s['id']}/changes").json()] == ["src/scores/helper_fix.py"]
    git = lambda *a: _sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, capture_output=True, text=True)
    git("checkout", "-q", "-b", "elsewhere")
    git("add", "-A")
    git("commit", "-q", "-m", "the person keeps it on another branch")
    assert client.get(f"/helper/sessions/{s['id']}/changes").json() == []


def test_fix_puts_back_what_the_phase_forbids(client, repo):
    # fake-rogue also writes a test file behind keel's back: tests are frozen in green, so it is put back
    s = new_session(client, repo, mode="fix", thread_id="t-gate", model={"provider": "fake", "mode": "api", "model": "fake-rogue"})
    ask(client, s["id"], "Fix it.", flow=FLOW)
    assert not (Path(repo) / "tests" / "test_rogue_helper.py").exists()
    assert (Path(repo) / "src" / "scores" / "helper_fix.py").exists()          # what green allows stays
    put_back = [e["data"]["text"] for e in client.bus.recent if e["type"] == "helper.step" and e["data"]["kind"] == "guard"]
    assert any("test_rogue_helper.py" in t for t in put_back)


def test_done_runs_the_checks_and_makes_keels_commit_of_only_the_helpers_files(client, repo):
    (Path(repo) / "NOTES.md").write_text("the person's own unsaved notes\n")      # never part of KeelBot's commit
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
    # the flow's PR body lists KeelBot's commit
    from keel_engine.runtime import verdict_actions
    assert helper.commits_for("t-gate")[0]["subject"] == "feat(AC-1): Ranks use the new helper"
    body = verdict_actions.pr_body(str(repo), "demo", {}, "Player ranks", None, thread_id="t-gate")
    assert "## KeelBot changes" in body and f"`{r['sha'][:7]}` feat(AC-1): Ranks use the new helper (src/scores/helper_fix.py)" in body
    assert "## KeelBot changes" not in verdict_actions.pr_body(str(repo), "demo", {}, "Player ranks", None, thread_id="t-other")
    from keel_engine.runtime import ship
    assert f"`{r['sha'][:7]}` feat(AC-1)" in ship.final_report(str(repo), "demo", {}, "Player ranks", "t-gate")
    nothing = client.post(f"/helper/sessions/{s['id']}/done", json={"flow": FLOW}).json()
    assert nothing["ok"] is False and nothing["step"] == "changes"


def test_keep_one_file_commits_only_it_and_leaves_the_others_to_keep_or_undo(client, repo):
    s = fix_session(client, repo)
    ask(client, s["id"], "Add the helper.", flow=FLOW)
    # a second file KeelBot changed in this chat (recorded the way its guarded tools record one)
    (Path(repo) / "README.md").write_text("# demo\nchanged by KeelBot\n")
    with helper.db.connect() as conn:
        conn.execute("insert into helper_files(session_id, path, existed, content, at) values (?, ?, 1, ?, ?)",
                     (s["id"], "README.md", b"# demo\n", helper.db.now()))
    paths = [c["path"] for c in client.get(f"/helper/sessions/{s['id']}/changes").json()]
    assert paths == ["README.md", "src/scores/helper_fix.py"]
    bad = client.post(f"/helper/sessions/{s['id']}/done", json={"flow": FLOW, "paths": ["nope.py"]})
    assert bad.status_code == 404 and "nope.py" in bad.json()["error"]
    r = client.post(f"/helper/sessions/{s['id']}/done", json={"flow": FLOW, "paths": ["src/scores/helper_fix.py"]}).json()
    assert r["ok"] is True and r["files"] == ["src/scores/helper_fix.py"], r
    log = _sp.run(["git", "log", "-1", "--name-only", "--format="], cwd=repo, capture_output=True, text=True).stdout.split()
    assert log == ["src/scores/helper_fix.py"]
    # README.md is still KeelBot's change: it can be kept or undone later
    assert [c["path"] for c in client.get(f"/helper/sessions/{s['id']}/changes").json()] == ["README.md"]
    assert client.post(f"/helper/sessions/{s['id']}/undo", json={"path": "README.md"}).json() == []
    assert (Path(repo) / "README.md").read_text() == "# demo\n"


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
    assert client.get(f"/helper/sessions/{s['id']}").json()["grants"] == ["npm install *"]       # its family (0.18.18)
    # a deny carries the person's reason
    t = threading.Thread(target=lambda: got.update(client.post("/helper/permissions/ask", json={
        "session": s["id"], "key": "k1", "command": "git push"}).json()))
    t.start()
    while not client.get("/helper/permissions").json() and time.time() < deadline:
        time.sleep(0.02)
    q = client.get("/helper/permissions").json()[0]
    client.post(f"/helper/permissions/{q['id']}", json={"decision": "deny", "why": "keel pushes, not you"})
    t.join(10)
    # the person's reason, and to go on from it (keel 0.18.18 said_no)
    assert got["decision"] == "deny" and got["why"].startswith("The person said no to this command: keel pushes, not you")
    assert "Do not run it again" in got["why"]
    assert client.post(f"/helper/permissions/{q['id']}", json={"decision": "once"}).status_code == 404


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


# ------------------------------------------------------------------ side sessions (their own worktree and branch)

def _git(repo, *args):
    return _sp.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout


def side_session(client, repo):
    s = new_session(client, repo, mode="side")
    assert s["mode"] == "side" and s["branch"].startswith("keel/helper/") and s["base_sha"] == _git(repo, "rev-parse", "HEAD").strip()
    assert Path(s["worktree"]).is_dir() and Path(s["worktree"]).is_relative_to(Path(repo).resolve() / ".keel" / "worktrees")
    return s


def test_a_side_session_works_in_its_own_worktree_and_never_touches_the_main_folder(client, repo):
    head = _git(repo, "rev-parse", "HEAD").strip()
    s = side_session(client, repo)
    _, s2 = ask(client, s["id"], "Add a small helper.")
    wt = Path(s["worktree"])
    assert (wt / "src" / "scores" / "helper_fix.py").exists()
    assert not (Path(repo) / "src" / "scores" / "helper_fix.py").exists()
    assert _git(repo, "status", "--porcelain", "--untracked-files=all").strip() == ""      # the worktree is excluded
    ch = client.get(f"/helper/sessions/{s['id']}/changes").json()
    assert [(c["path"], c["status"]) for c in ch] == [("src/scores/helper_fix.py", "added")]
    # Keep: the checks, then keel's commit on the side branch; the main folder's branch does not move
    r = client.post(f"/helper/sessions/{s['id']}/done", json={"flow": {}, "message": "a helper for ranks",
                                                            "commit": {"commit_author": "Ada Lovelace <ada@example.com>"}}).json()
    assert r["ok"] is True, r
    assert _git(wt, "log", "-1", "--format=%s").strip() == "fix(helper): a helper for ranks"
    # by the person the project's settings name, with KeelBot as the co-author (on by default)
    assert _git(wt, "log", "-1", "--format=%an <%ae>%n%(trailers:key=Co-Authored-By,valueonly)").split() == \
        ["Ada", "Lovelace", "<ada@example.com>", "KeelBot", "<keel.dev.bot@gmail.com>"]
    assert _git(repo, "rev-parse", "HEAD").strip() == head
    assert client.get(f"/helper/sessions/{s['id']}/changes").json() == []
    assert "on branch keel/helper/" in client.get(f"/helper/sessions/{s['id']}").json()["messages"][-1]["text"]
    h = client.get(f"/helper/sessions/{s['id']}/handover").json()
    assert [c["subject"] for c in h["commits"]] == ["fix(helper): a helper for ranks"] and h["uncommitted"] == []


def test_undo_hand_over_and_throw_away(client, repo):
    s = side_session(client, repo)
    ask(client, s["id"], "Add it.")
    assert client.post(f"/helper/sessions/{s['id']}/undo", json={"path": "src/scores/helper_fix.py"}).json() == []
    ask(client, s["id"], "Add it again.")
    # not kept yet: no hand-over
    r = client.post(f"/helper/sessions/{s['id']}/release")
    assert r.status_code == 409 and "not kept yet" in r.json()["error"]
    assert client.post(f"/helper/sessions/{s['id']}/done", json={"flow": {}}).json()["ok"] is True
    h = client.post(f"/helper/sessions/{s['id']}/release").json()
    assert len(h["commits"]) == 1 and not Path(s["worktree"]).exists()
    assert _git(repo, "branch", "--list", s["branch"]).strip()                        # the branch stays for a flow
    assert client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "more"}).status_code == 409
    # throw away: the worktree and its branch go
    t = side_session(client, repo)
    assert client.delete(f"/helper/sessions/{t['id']}").status_code == 200
    assert not Path(t["worktree"]).exists() and not _git(repo, "branch", "--list", t["branch"]).strip()


def test_a_side_session_needs_git(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    r = client.post("/helper/sessions", json={"project_id": "demo", "root": str(plain), "mode": "side", "model": FAKE})
    assert r.status_code == 409 and "not a git repository" in r.json()["error"]


def test_a_side_session_and_a_flow_never_touch_each_others_files(client, repo):
    from conftest import start, to_loop, wait
    tid = start(client, repo)
    s = side_session(client, repo)
    ask(client, s["id"], "Add a small helper.")                       # while the flow waits at its spec gate
    st = to_loop(client, tid, wait(client, tid))                       # the flow goes on into its criteria loop
    assert st["status"] in ("waiting", "running", "done"), st
    wt = Path(s["worktree"])
    # the flow wrote and committed in the main folder only; the side session's file is in its worktree only
    assert not (Path(repo) / "src" / "scores" / "helper_fix.py").exists()
    assert "helper_fix.py" not in _git(repo, "log", "--name-only", "--format=")
    assert [c["path"] for c in client.get(f"/helper/sessions/{s['id']}/changes").json()] == ["src/scores/helper_fix.py"]
    assert not any(".keel/worktrees" in line for line in _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines())
    # the files the flow created since the worktree began are not in the worktree
    flow_files = set(_git(repo, "log", "--name-only", "--format=", f"{s['base_sha']}..HEAD").split())
    created = [f for f in flow_files if _sp.run(["git", "cat-file", "-e", f"{s['base_sha']}:{f}"], cwd=repo).returncode != 0]
    assert created and not any((wt / f).exists() for f in created)


def test_a_chat_title_is_cut_at_a_word():
    assert helper.short_title("Where is a score saved?") == "Where is a score saved?"
    long = "Add formatUsd(cents) next to formatEuro in src/domain/money.js (returns $12.50 for 1250) and a test"
    t = helper.short_title(long)
    assert t == "Add formatUsd(cents) next to formatEuro in src/domain/money.js (returns $12.50…" and len(t) <= 80
    assert helper.short_title("x" * 120) == "x" * 79 + "…"


# ------------------------------------------------------------------ keel 0.18.7 Edit mode: the project folder, no flow

def test_edit_changes_the_project_folder_with_no_flow_and_keep_commits_only_its_files(client, repo):
    (Path(repo) / "NOTES.md").write_text("the person's own unsaved notes\n")      # never part of KeelBot's commit
    s = new_session(client, repo, mode="edit")
    assert s["mode"] == "edit" and not s.get("worktree")
    _, s2 = ask(client, s["id"], "Add a small helper.")
    assert (Path(repo) / "src" / "scores" / "helper_fix.py").exists()           # in the project folder itself
    ch = client.get(f"/helper/sessions/{s['id']}/changes").json()
    assert [(c["path"], c["status"]) for c in ch] == [("src/scores/helper_fix.py", "added")]
    r = client.post(f"/helper/sessions/{s['id']}/done", json={"flow": {}, "message": "a helper for ranks"}).json()
    assert r["ok"] is True, r
    log = _git(repo, "log", "-1", "--format=%s", "--name-only")
    assert log.startswith("fix(helper): a helper for ranks\n") and "helper_fix.py" in log and "NOTES.md" not in log
    assert client.get(f"/helper/sessions/{s['id']}/changes").json() == []


def test_edit_undo_puts_the_file_back_and_the_turn_is_checked_after_it_too(client, repo):
    s = new_session(client, repo, mode="edit")
    ask(client, s["id"], "Add it.")
    assert client.post(f"/helper/sessions/{s['id']}/undo", json={}).json() == []
    assert not (Path(repo) / "src" / "scores" / "helper_fix.py").exists()
    # the rogue model also writes a file behind keel's back: the diff guard checks it after the turn (no flow's phase in
    # Edit, so an ordinary file stays; keel's always-on rules are the guard's own, tested in the engine's guard tests)
    r = new_session(client, repo, mode="edit", model={"provider": "fake", "mode": "api", "model": "fake-rogue"})
    ask(client, r["id"], "Fix it.")
    assert (Path(repo) / "src" / "scores" / "helper_fix.py").exists()
    assert (Path(repo) / "tests" / "test_rogue_helper.py").exists()
    assert "Mode: Edit" in helper.MODE_TEXT["edit"] and "edit" in helper.MODES


def test_the_effort_goes_with_each_turn_and_its_answer(client, repo):
    """keel 0.18.9: KeelBot's model may carry a thinking effort: the runner gets it, helper.started and the answer say it;
    a model change (PATCH) can set or clear it."""
    s = new_session(client, repo, model={**FAKE, "effort": "high"})
    assert s["model"]["effort"] == "high"
    _, s = ask(client, s["id"], "What is here?")
    evs = [e for e in client.bus.recent if e["thread_id"] == s["id"]]
    assert evs[0]["type"] == "helper.started" and evs[0]["data"]["effort"] == "high"
    assert any(e["type"] == "helper.step" and e["data"]["text"] == "Thinking effort: high." for e in evs)
    assert s["messages"][-1]["data"]["effort"] == "high"
    r = client.patch(f"/helper/sessions/{s['id']}", json={"model": FAKE})
    assert r.status_code == 200 and "effort" not in r.json()["model"]
    _, s = ask(client, s["id"], "And now?")
    assert "effort" not in s["messages"][-1]["data"]


def test_keelbot_sees_the_flows_newest_jobs_and_what_they_ended_with():
    """0.18.15 "what was the doctor's result?": KeelBot reads the step it is at and each job's result, not only names."""
    from keel_plugin_keelbot.helper import _flow_block
    text = _flow_block({"title": "Init this project", "status": "runs", "phase": "setup", "current": "ladder_retry",
                        "jobs": [{"agent": "keel", "step": "ladder_retry", "status": "running"},
                                 {"agent": "setup-doctor", "step": "doctor", "status": "done",
                                  "result": "api_health_check uses localhost inside the container.\nDIAGNOSIS: needs-you"}]})
    assert "The step it is at now: ladder_retry." in text
    assert "setup-doctor at doctor: done" in text and "DIAGNOSIS: needs-you" in text
    assert "agent \"keel\" is a code step" in text


def test_ask_mode_says_how_to_switch_instead_of_trying_a_change():
    """0.18.16 a person asked again and again for `git checkout main` in Ask mode; KeelBot kept trying instead of saying
    that Ask is read only and how to switch."""
    from keel_plugin_keelbot.helper import MODE_TEXT
    ask = MODE_TEXT["ask"]
    assert "do not try it" in ask and "/mode edit" in ask and "/mode side" in ask and "git checkout main" in ask


# ------------------------------------------------------------------ P1-1 context meter (AC-1)

def test_ac_1_a_session_with_a_known_model_returns_ctx_window_and_ctx_tokens(client, repo):
    s = new_session(client, repo, model={"provider": "claude", "mode": "subscription", "model": "opus"})
    assert s["ctx_window"] == 200_000                  # Claude Opus's default context window
    assert s["ctx_tokens"] == 0                         # no turn yet: nothing in the window so far
    got = client.get(f"/helper/sessions/{s['id']}").json()
    assert got["ctx_window"] == 200_000 and got["ctx_tokens"] == 0


# ------------------------------------------------------------------ P1-1 context meter (AC-2)

def test_ac_2_after_a_turn_the_session_reports_the_last_turns_ctx_tokens_and_its_ratio(client, repo):
    s = new_session(client, repo)
    _, s = ask(client, s["id"], "Where does it start?")
    last = s["messages"][-1]["data"]                   # the fake model's own report of that turn's usage
    turn_tokens = last["tokens_in"] + last["tokens_out"] + last["tokens_cached"]
    assert s["ctx_tokens"] == turn_tokens
    assert s["ctx_tokens"] / s["ctx_window"] == pytest.approx(turn_tokens / 200_000)
    # a second turn: ctx_tokens still reflects only the *last* turn, not the session's running total
    _, s = ask(client, s["id"], "And the tests?")
    last2 = s["messages"][-1]["data"]
    turn2_tokens = last2["tokens_in"] + last2["tokens_out"] + last2["tokens_cached"]
    assert s["ctx_tokens"] == turn2_tokens
    assert s["ctx_tokens"] != s["tokens_in"] + s["tokens_out"] + s["tokens_cached"]   # that sum is the two turns combined
    assert s["ctx_tokens"] / s["ctx_window"] == pytest.approx(turn2_tokens / 200_000)


# ------------------------------------------------------------------ P1-2 /compact (AC-1)

@pytest.mark.asyncio
async def test_ac_1_compact_summarises_keeps_every_row_and_starts_a_fresh_session(client, repo):
    """helper.compact(sid): asks the model for a summary of the stored history, stores it as a note message, starts
    a fresh engine_session and resets ctx_tokens to the summary's own size — never deleting a row."""
    s = new_session(client, repo)
    _, s = ask(client, s["id"], "Where does it start?")
    _, s = ask(client, s["id"], "And the tests?")
    sid = s["id"]
    before = helper.get(sid)
    before_rows = before["messages"]
    before_session = before["engine_session"]

    await helper.compact(sid)

    after = helper.get(sid)
    after_rows = after["messages"]
    # every earlier row is still there, unchanged, in the same order: compacting never deletes from helper_messages
    assert after_rows[:len(before_rows)] == before_rows
    # exactly one new row: the model's summary, stored as a note (never a user or helper turn)
    assert len(after_rows) == len(before_rows) + 1
    note = after_rows[-1]
    assert note["role"] == "note"
    assert note["text"].strip()
    # no engine session to resume: the next turn starts a new one (a made-up id would "resume" nothing, with no
    # transcript, and the model would lose the whole chat)
    assert after["engine_session"] is None
    # ctx_tokens resets to the summary's own size, not the size of the summary call (that read the whole old chat)
    data = note["data"]
    assert data["compact"] is True and after["ctx_tokens"] == data["after"] < data["tokens_in"] + data["after"]
    # the next turn's history starts from the summary, then only what comes after it; nothing is lost
    hist = helper.history(sid)
    assert hist[0][0] == "user" and note["text"].strip() in hist[0][1] and len(hist) == 1
    _, s = ask(client, sid, "Go on.")
    hist = helper.history(sid)
    assert note["text"].strip() in hist[0][1] and [r for r, _ in hist[1:]] == ["user", "assistant"]


# ------------------------------------------------------------------ P1-2 /compact (AC-2)

def test_ac_2_a_turn_above_85_percent_of_the_window_compacts_itself_before_it_runs(client, repo):
    """A session whose ctx_tokens is already above 85% of its model's ctx_window compacts on its own, right before
    the next turn runs — the person never has to ask for it."""
    s = new_session(client, repo)
    sid = s["id"]
    before_session = helper.get(sid)["engine_session"]
    helper.update(sid, ctx_tokens=int(s["ctx_window"] * 0.9))   # above the 85% threshold

    _, s = ask(client, sid, "Where does it start?")

    rows = s["messages"]
    # a note (the summary) was stored before the user's own message for this turn
    roles = [m["role"] for m in rows]
    assert "note" in roles
    assert roles.index("note") < roles.index("user")
    # the turn compacted first: the note is a compact summary, and the window is far from full again
    note = next(m for m in rows if m["role"] == "note")
    assert note["data"]["compact"] is True and note["data"]["after"] < int(s["ctx_window"] * 0.9)


def test_ac_2_a_turn_below_the_threshold_never_compacts(client, repo):
    """The same session, well under 85%, keeps its own engine_session and stores no summary note."""
    s = new_session(client, repo)
    sid = s["id"]
    before_session = helper.get(sid)["engine_session"]
    helper.update(sid, ctx_tokens=int(s["ctx_window"] * 0.1))   # far under the threshold

    _, s = ask(client, sid, "Where does it start?")

    assert "note" not in [m["role"] for m in s["messages"]]
    assert s["engine_session"] == before_session


@pytest.mark.asyncio
async def test_ac_2_the_compact_route_answers_before_and_after_tokens(client, repo):
    """POST /helper/sessions/{sid}/compact compacts on request and answers the tokens it had before and after."""
    s = new_session(client, repo)
    _, s = ask(client, s["id"], "Where does it start?")
    _, s = ask(client, s["id"], "And the tests?")
    sid = s["id"]
    before_tokens = helper.get(sid)["ctx_tokens"]

    r = client.post(f"/helper/sessions/{sid}/compact")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["before"] == before_tokens
    after = helper.get(sid)
    assert body["after"] == after["ctx_tokens"]
    # the route actually ran the compaction, it did not just report numbers
    assert "note" in [m["role"] for m in after["messages"]]


# ------------------------------------------------------------------ P1-3 sessions: picker (AC-1)

def test_ac_1_the_session_list_has_each_chats_last_message_and_size_newest_first(client, repo):
    """GET /helper/sessions: every chat with a short preview of its last message and how many messages it stores,
    the chat with the latest activity first — what keel TUI's /chats picker shows and filters on."""
    old = new_session(client, repo)
    ask(client, old["id"], "Where does the package start?")
    new = new_session(client, repo)
    _, new = ask(client, new["id"], "And the tests? " + "word " * 60)
    empty = new_session(client, repo)                 # nothing said yet
    with helper.db.connect() as conn:                  # updated_at is to the second: set the order the test needs
        for at, sid in (("2026-10-01T10:00:00Z", old["id"]), ("2026-10-02T10:00:00Z", new["id"]),
                        ("2026-10-03T10:00:00Z", empty["id"])):
            conn.execute("update helper_sessions set updated_at = ? where id = ?", (at, sid))

    rows = client.get("/helper/sessions", params={"project": "demo"}).json()
    assert [r["id"] for r in rows] == [empty["id"], new["id"], old["id"]]
    by = {r["id"]: r for r in rows}
    assert by[empty["id"]]["last_message"] == "" and by[empty["id"]]["message_count"] == 0
    assert by[new["id"]]["message_count"] == len(new["messages"]) == 2
    last = " ".join(new["messages"][-1]["text"].split())
    preview = by[new["id"]]["last_message"]
    assert len(preview) <= 80 and "\n" not in preview
    assert preview == last or (preview.endswith("…") and last.startswith(preview[:-1].rstrip()))


# ------------------------------------------------------------------ P1-3 sessions: /rename (AC-2)

def test_ac_2_rename_through_the_patch_route_names_the_chat_in_the_session_list(client, repo):
    """keel TUI's /rename sends the existing PATCH {title}: the chat keeps that name, its first turn does not
    replace it with the question, and the session list (/chats) shows it."""
    s = new_session(client, repo)
    r = client.patch(f"/helper/sessions/{s['id']}", json={"title": "  Health endpoint work  "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Health endpoint work"
    _, s = ask(client, s["id"], "Where does the package start?")
    assert s["title"] == "Health endpoint work"
    rows = client.get("/helper/sessions", params={"project": "demo"}).json()
    assert [(x["id"], x["title"]) for x in rows] == [(s["id"], "Health endpoint work")]
    # renaming again, after the turn, works the same way
    client.patch(f"/helper/sessions/{s['id']}", json={"title": "Health, done"})
    assert client.get("/helper/sessions", params={"project": "demo"}).json()[0]["title"] == "Health, done"


# ------------------------------------------------------------------ P1-3 sessions: /export (AC-3)

@pytest.mark.asyncio
async def test_ac_3_export_writes_every_message_as_markdown_and_leaves_the_session_unchanged(client, repo):
    """POST /helper/sessions/{sid}/export writes the chat as Markdown into the project's .keel/chats/<sid>.md (every
    stored message in order, each with its role and time) and answers the path; the session reads back the same."""
    s = new_session(client, repo)
    sid = s["id"]
    client.patch(f"/helper/sessions/{sid}", json={"title": "Where things start"})
    ask(client, sid, "Where does the package start?")
    await helper.compact(sid)                          # a note too: every stored message, not only the turns
    _, s = ask(client, sid, "And the tests?")
    before = client.get(f"/helper/sessions/{sid}").json()["messages"]
    assert [m["role"] for m in before] == ["user", "helper", "note", "user", "helper"]

    r = client.post(f"/helper/sessions/{sid}/export")
    assert r.status_code == 200, r.text
    body = r.json()
    path = Path(body["path"])
    assert path == Path(repo).resolve() / ".keel" / "chats" / f"{sid}.md" and body["messages"] == len(before)
    md = path.read_text()
    assert md.startswith("# Where things start\n")
    heads = {"user": "user", "helper": "KeelBot", "note": "note"}
    at = 0
    for m in before:                                   # in order: each message's heading (role, time), then its text
        head = f"## {m['n']}. {heads[m['role']]} · {m['at']}"
        assert md.index(head, at) >= at
        at = md.index(head, at) + len(head)
        assert md.index(m["text"].strip(), at) >= at
        at = md.index(m["text"].strip(), at)

    # another reader (another computer) opens the chat afterwards: the very same messages, unchanged
    assert client.get(f"/helper/sessions/{sid}").json()["messages"] == before
    # exporting again overwrites the same file with the same content
    assert client.post(f"/helper/sessions/{sid}/export").json()["path"] == str(path)
    assert client.post("/helper/sessions/h_nope/export").status_code == 404


# ------------------------------------------------------------------ 0.18.18 prompt history (keel TUI's ↑ / ↓)

def test_the_prompt_history_is_the_projects_own_user_messages_newest_first_once_each(client, repo):
    """GET /helper/prompts?project= answers what the person asked in this project's chats, newest first, each text
    once (its newest place): keel TUI walks it with ↑ / ↓, so it survives a restart and another computer."""
    a = new_session(client, repo)["id"]
    ask(client, a, "Where does the package start?")
    b = new_session(client, repo)["id"]
    ask(client, b, "And the tests?")
    ask(client, a, "Where does the package start?")       # asked again: it moves to the top, once
    ask(client, a, "line one\nline two")                  # a prompt of several lines comes back whole
    other = client.post("/helper/sessions", json={"project_id": "elsewhere", "root": str(repo), "model": FAKE}).json()["id"]
    ask(client, other, "Not this project's")
    r = client.get("/helper/prompts", params={"project": "demo"})
    assert r.status_code == 200, r.text
    assert r.json() == ["line one\nline two", "Where does the package start?", "And the tests?"]
    # KeelBot's answers and notes are never prompts; the limit cuts the oldest
    assert client.get("/helper/prompts", params={"project": "demo", "limit": 1}).json() == ["line one\nline two"]
    assert client.get("/helper/prompts", params={"project": "nobody"}).json() == []


# ------------------------------------------------------------------ 0.18.18 keel TUI's `!command`

def test_a_command_that_changes_nothing_runs_in_the_project_folder_and_answers_its_output(client, repo):
    """POST /helper/sessions/{sid}/run runs the person's command in the chat's folder through keel's guard
    (ToolBox.run_command): one that changes nothing runs at once and answers its exit and output."""
    s = new_session(client, repo, mode="edit")
    r = client.post(f"/helper/sessions/{s['id']}/run", json={"command": "ls src"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["command"] == "ls src" and body["exit"] == 0 and body["refused"] is None
    assert "scores" in body["output"]
    bad = client.post(f"/helper/sessions/{s['id']}/run", json={"command": "ls no-such-dir"}).json()
    assert bad["exit"] != 0 and bad["refused"] is None
    assert client.post("/helper/sessions/h_nope/run", json={"command": "ls"}).status_code == 404
    assert client.post(f"/helper/sessions/{s['id']}/run", json={"command": "  "}).status_code == 400
    # nothing is stored in the chat: the output goes with the person's next question (keel TUI keeps it)
    assert client.get(f"/helper/sessions/{s['id']}").json()["messages"] == []


def test_a_command_that_changes_something_waits_for_the_persons_ok_as_keelbots_own_do(client, repo):
    """Edit (and Fix, side): a command that changes something waits for the person's OK on a permission card, as
    KeelBot's own commands do; a deny answers the reason and the command never runs."""
    s = new_session(client, repo, mode="edit")
    got = {}
    t = threading.Thread(target=lambda: got.update(client.post(f"/helper/sessions/{s['id']}/run",
                                                                json={"command": "touch made-by-bang.txt"}).json()))
    t.start()
    deadline = time.time() + 10
    while not client.get("/helper/permissions", params={"project": "demo"}).json() and time.time() < deadline:
        time.sleep(0.02)
    q = client.get("/helper/permissions", params={"project": "demo"}).json()[0]
    assert q["command"] == "touch made-by-bang.txt" and q["session"] == s["id"]
    assert client.get(f"/helper/sessions/{s['id']}").json()["busy"] is True        # no turn starts meanwhile
    client.post(f"/helper/permissions/{q['id']}", json={"decision": "deny", "why": "not now"})
    t.join(10)
    # 0.18.18 the reason comes back inside the "go on from what they said" guidance (KeelBot recover)
    assert "not now" in got["refused"] and "Do not run it again" in got["refused"] and got["exit"] is None
    assert not (Path(repo) / "made-by-bang.txt").exists()
    # allowed once: it runs
    t = threading.Thread(target=lambda: got.update(client.post(f"/helper/sessions/{s['id']}/run",
                                                                json={"command": "touch made-by-bang.txt"}).json()))
    t.start()
    while not client.get("/helper/permissions").json() and time.time() < deadline:
        time.sleep(0.02)
    client.post(f"/helper/permissions/{client.get('/helper/permissions').json()[0]['id']}", json={"decision": "once"})
    t.join(10)
    assert got["exit"] == 0 and got["refused"] is None and (Path(repo) / "made-by-bang.txt").exists()
    assert client.get(f"/helper/sessions/{s['id']}").json()["busy"] is False


def test_ask_mode_refuses_a_command_that_changes_something_and_says_how_to_go_on(client, repo):
    s = new_session(client, repo)                                            # Ask: read only
    body = client.post(f"/helper/sessions/{s['id']}/run", json={"command": "touch nope.txt"}).json()
    assert body["refused"] and body["exit"] is None and "/mode edit" in body["refused"]
    assert not (Path(repo) / "nope.txt").exists()
    assert client.post(f"/helper/sessions/{s['id']}/run", json={"command": "cat README.md"}).json()["exit"] == 0


def test_the_output_of_the_persons_command_goes_into_the_next_turns_prompt():
    p = helper.build_prompt(mode="ask", root="/r", question="why does it fail?", know={"sections": [], "code_graph": False, "memory": False}, graph=False, flow=None,
                            mentions=None, selection=None, open_file=None, transcript="",
                            shell={"command": "npm test", "exit": 1, "output": "1 failed: test_rank"})
    assert "The person ran `npm test` in the project folder (exit 1)" in p and "1 failed: test_rank" in p
    assert p.index("1 failed") < p.index("Question: why does it fail?")


# ------------------------------------------------------------------ 0.18.18 images with a turn

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
CLAUDE = {"provider": "claude", "mode": "cli", "model": "claude-sonnet-4-5"}


def test_images_of_the_project_go_to_a_model_that_can_see_them_and_others_say_why_not(repo, tmp_path):
    """helper.attach_images: an image file of the chat's folder (relative or absolute) goes to Claude Code, which reads
    it with its Read tool; one outside the folder, missing, not a picture, too big, or for a model that cannot see
    images, does not go, with the reason in words."""
    root = Path(repo).resolve()
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "shot.png").write_bytes(PNG)
    (root / "notes.txt").write_text("hi")
    (tmp_path / "outside.png").write_bytes(PNG)
    s = {"root": str(root), "worktree": None}
    got = helper.attach_images(s, CLAUDE, ["docs/shot.png", str(root / "docs" / "shot.png"), "missing.png",
                                          str(tmp_path / "outside.png"), "notes.txt"])
    assert got["images"] == [str(root / "docs" / "shot.png")]                 # once
    why = {x["path"]: x["why"] for x in got["skipped"]}
    assert "does not exist" in why["missing.png"]
    assert "outside the project folder" in why[str(tmp_path / "outside.png")]
    assert "not an image" in why["notes.txt"]
    fake = helper.attach_images(s, FAKE, ["docs/shot.png"])
    assert fake["images"] == [] and "cannot see images" in fake["skipped"][0]["why"]
    api = helper.attach_images(s, {"provider": "anthropic", "mode": "api", "model": "claude-sonnet-4-5"}, ["docs/shot.png"])
    assert api["images"] == [] and "cannot see images" in api["skipped"][0]["why"]


def test_the_prompt_asks_the_model_to_look_at_the_attached_images():
    p = helper.build_prompt(mode="ask", root="/r", question="what is wrong on this screen?",
                            know={"sections": [], "code_graph": False, "memory": False}, graph=False, flow=None,
                            mentions=None, selection=None, open_file=None, transcript="", images=["/r/docs/shot.png"])
    assert "The person attached these images" in p and "- /r/docs/shot.png" in p and "Read tool" in p
    assert p.index("/r/docs/shot.png") < p.index("Question:")


def test_a_turn_answers_which_images_went_with_it_and_which_did_not(client, repo):
    (Path(repo) / "shot.png").write_bytes(PNG)
    s = new_session(client, repo)                                          # the fake model: it cannot see images
    r = client.post(f"/helper/sessions/{s['id']}/turn", json={"text": "look at shot.png", "images": ["shot.png"]})
    assert r.status_code == 200, r.text
    assert r.json()["images"] == [] and r.json()["images_skipped"][0]["path"] == "shot.png"
    s = wait_idle(client, s["id"])
    assert s["messages"][0]["data"]["images_skipped"][0]["path"] == "shot.png"


# ------------------------------------------------------------------ 0.18.54 compact runs with the chat's login

def _spy_runner(monkeypatch, fail_on=None):
    """Records each AgentRequest the helper sends to a model; fail_on=<step_name> makes that step fail like a CLI."""
    seen = []
    real = helper.models.runner_for

    def runner_for(model):
        inner = real(model)

        class Spy:
            async def run(self, req, on_event):
                seen.append(req)
                if req.step_name == fail_on:
                    raise helper.models.ModelError("`claude` is not logged in inside the container.", "Save a login.")
                return await inner.run(req, on_event)
        return Spy()
    monkeypatch.setattr(helper.models, "runner_for", runner_for)
    return seen


def test_an_automatic_compact_gets_the_turns_logins(client, repo, monkeypatch):
    """The summary before a turn runs the chat's model with the logins the api sent for the turn (it ran with none,
    so a Claude login saved in Connections was missing and the turn failed with a 500)."""
    seen = _spy_runner(monkeypatch)
    s = new_session(client, repo)
    helper.update(s["id"], ctx_tokens=int(s["ctx_window"] * 0.9))
    ask(client, s["id"], "Where does it start?", keys={"claude_oauth": "test-login"})
    compact = next(r for r in seen if r.step_name == "compact")
    assert compact.keys.get("claude_oauth") == "test-login"


def test_a_failed_automatic_compact_is_a_note_and_the_answer_still_goes(client, repo, monkeypatch):
    _spy_runner(monkeypatch, fail_on="compact")
    s = new_session(client, repo)
    helper.update(s["id"], ctx_tokens=int(s["ctx_window"] * 0.9))
    _, s = ask(client, s["id"], "Where does it start?")
    notes = [m for m in s["messages"] if m["role"] == "note"]
    assert notes and notes[0]["text"].startswith("Could not compact this chat: `claude` is not logged in")
    assert s["messages"][-1]["role"] == "helper"


def test_the_compact_route_passes_the_logins_and_answers_a_model_error_in_words(client, repo, monkeypatch):
    seen = _spy_runner(monkeypatch)
    s = new_session(client, repo)
    _, s = ask(client, s["id"], "Where does it start?")
    r = client.post(f"/helper/sessions/{s['id']}/compact", json={"keys": {"claude_oauth": "test-login"}})
    assert r.status_code == 200, r.text
    assert seen[-1].step_name == "compact" and seen[-1].keys.get("claude_oauth") == "test-login"
    assert client.post(f"/helper/sessions/{s['id']}/compact").status_code == 200       # an old api sends no body

    _spy_runner(monkeypatch, fail_on="compact")
    r = client.post(f"/helper/sessions/{s['id']}/compact")
    assert r.status_code == 502
    assert "not logged in" in r.json()["error"] and r.json()["hint"] == "Save a login."
