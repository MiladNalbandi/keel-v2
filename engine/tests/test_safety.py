"""Safety & History (keel_engine/safety): keel-gate's tiers in keel's guard, and the per-project history the engine
writes from its own events."""

import json
import uuid

import pytest

from keel_engine import hook
from keel_engine.events import EventBus
from keel_engine.runtime import guard_ctx, permissions
from keel_engine.safety import Tier, history, judge
from keel_engine.safety.recorder import Recorder, line_of


def ctx_file(tmp_path, root, phase="green", **kw) -> str:
    return guard_ctx.write_context(tmp_path / "ctx" / uuid.uuid4().hex / "guard.json", root=str(root), phase=phase,
                                   ac=None, lane=None, unlocks=[], agent="dev", thread="t1", **kw)


def run(capsys, root, tmp_path, tool, **ti) -> tuple[int, str]:
    raw = json.dumps({"tool_name": tool, "tool_input": ti, "cwd": str(root)})
    code = hook.pre_tool(raw, ctx_file(tmp_path, root))
    return code, capsys.readouterr().err


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    (r / "src" / "app.py").write_text("x = 1\n")
    return r


# ------------------------------------------------------------------ the guard: T3 is refused, in any disguise

CATASTROPHIC = [
    "rm -rf /",
    "rm -rf ~",
    "r''m -r'f' /",
    "rm${IFS}-rf${IFS}/",
    "echo cm0gLXJmIC8= | base64 -d | sh",
    "printf '\\x72\\x6d\\x20\\x2d\\x72\\x66\\x20\\x2f' | sh",
    "eval 'rm -rf /'",
    "sh -c 'cd / && rm -rf *'",
    "npm test; rm -rf ~",
    "$(echo rm) -rf /",
    "bash <<'EOF'\nrm -rf /\nEOF",
    ":(){ :|:& };:",
    "dd if=/dev/zero of=/dev/nvme0n1",
    "psql -c 'DROP TABLE customers'",
    "terraform destroy -auto-approve",
    "kubectl delete namespace prod",
    "git push origin --delete main",
    "history -c && unset HISTFILE",
]


@pytest.mark.parametrize("command", CATASTROPHIC)
def test_a_command_that_cannot_be_undone_is_refused_however_it_is_written(capsys, root, tmp_path, command):
    code, err = run(capsys, root, tmp_path, "Bash", command=command)
    assert code == 2, command
    assert "[keel guard]" in err and "T3" in err and "Do not try another way" in err


EVERYDAY = [
    "ls -la", "git status", "git diff HEAD~1", "pytest -q", "npm test", "rm -rf node_modules", "rm -rf build dist",
    "grep -rn TODO src", "cat src/app.py", "python -m pytest tests/test_app.py -x", "git log --oneline -5",
    "echo hello > /tmp/out.txt", "find . -name '*.py' | xargs wc -l",
]


@pytest.mark.parametrize("command", EVERYDAY)
def test_everyday_commands_still_run(capsys, root, tmp_path, command):
    assert run(capsys, root, tmp_path, "Bash", command=command) == (0, "")


def test_an_edit_of_a_login_file_is_refused_and_a_project_edit_runs(capsys, root, tmp_path):
    code, err = run(capsys, root, tmp_path, "Write", file_path="~/.ssh/authorized_keys", content="ssh-ed25519 AAAA x")
    assert code == 2 and "T3" in err
    assert run(capsys, root, tmp_path, "Edit", file_path=str(root / "src/app.py"), old_string="x = 1",
               new_string="x = 2")[0] == 0


def test_a_project_can_switch_the_rule_off(capsys, root, tmp_path):
    (root / ".keel").mkdir()
    (root / ".keel" / "config.yml").write_text('rules:\n  "off": [catastrophic]\n')   # YAML reads a bare off as false
    assert run(capsys, root, tmp_path, "Bash", command="psql -c 'DROP TABLE scratch'") == (0, "")


def test_a_run_that_may_ask_asks_before_a_hard_to_undo_command(monkeypatch, root):
    asked = []
    monkeypatch.setattr(permissions, "ask_engine", lambda ask, kind, command, path="": (asked.append(command), (False, "no"))[1])
    ctx = {"root": str(root), "phase": "none", "lane": None, "unlocks": [], "agent": "KeelBot",
           "ask": {"url": "http://x/ask", "key": "k", "session": "s"}}
    # `git push origin main` (T2) asks; plain `pytest` (nothing changes) does not; `rm -rf /` (T3) is refused unasked
    assert hook.decide("Bash", {"command": "git push origin main"}, dict(ctx)) == "no"
    assert hook.decide("Bash", {"command": "pytest -q"}, dict(ctx)) is None
    assert "T3" in hook.decide("Bash", {"command": "rm -rf /"}, dict(ctx))
    assert asked == ["git push origin main"]


def test_tiers_of_the_built_in_kinds():
    assert judge("Bash", {"command": "ls"}).tier == Tier.T0
    assert judge("Edit", {"file_path": "src/app.py"}).tier == Tier.T1
    assert judge("Bash", {"command": "git push origin main"}).tier == Tier.T2
    assert judge("Bash", {"command": "echo cm0gLXJmIC8= | base64 -d | sh"}).tier == Tier.T3
    assert judge("Grep", {"pattern": "x"}).tier == Tier.T0
    assert judge("mcp__github__create_issue", {}) is None


# ------------------------------------------------------------------ the history file

def test_the_chain_finds_a_changed_a_removed_and_a_moved_line(tmp_path):
    p = history.path_for(tmp_path, "proj-1")
    for i in range(4):
        history.append(p, {"at": f"t{i}", "text": f"call {i}", "action": "allow"})
    assert history.verify(p) == (True, "4 lines, chain intact")
    lines = p.read_text().splitlines()

    p.write_text("\n".join([lines[0], lines[1].replace("call 1", "call X"), *lines[2:]]) + "\n")
    assert history.verify(p) == (False, "line 2 was changed after it was written")
    p.write_text("\n".join([lines[0], *lines[2:]]) + "\n")
    assert history.verify(p)[1].startswith("line 2 does not follow line 1")
    p.write_text("\n".join([lines[1], lines[0], *lines[2:]]) + "\n")
    assert not history.verify(p)[0]


def test_secret_values_are_masked_and_odd_project_ids_never_make_a_path(tmp_path):
    p = history.path_for(tmp_path, "demo")
    rec = history.append(p, {"text": "curl -H 'Authorization: Bearer ghp_" + "a" * 36 + "' https://api.example"})
    assert "ghp_" not in p.read_text() and "[secret]" in rec["text"]
    assert history.path_for(tmp_path, "../../etc/passwd").parent == tmp_path / "history"
    assert history.path_for(tmp_path, "../../etc/passwd").name.startswith("p-")


def test_nothing_recorded_yet_is_fine(tmp_path):
    assert history.verify(history.path_for(tmp_path, "new")) == (True, "0 lines, nothing recorded yet")


# ------------------------------------------------------------------ the recorder: the engine's own events

def step(kind, text, ok=True, output="", **extra):
    return {"n": 1, "kind": kind, "text": text, "ok": ok, "output": output, **extra}


def test_the_recorder_writes_each_tool_call_with_its_tier_per_project(tmp_path):
    bus = EventBus()
    rec = Recorder(tmp_path)
    bus.listen(rec.hear)
    bus.emit("agent.started", "t-1", "shop", step="green", call_id="c1", data={"agent": "dev"})
    bus.emit("agent.step", "t-1", "shop", step="green", call_id="c1", data=step("read", "x = 1\n", path="src/app.py"))
    bus.emit("agent.step", "t-1", "shop", step="green", call_id="c1",
             data=step("tool", "git push origin main", tool="Bash"))
    # a call the hook refused: the runner's `guard` step (models/cli_runners.py), with the command
    refusal = "It drops or truncates a database object. That data is gone. This cannot be undone, so keel never runs it " \
        "(builtin.sql-drop, T3). Do not try another way to do this."
    bus.emit("agent.step", "t-1", "shop", step="green", call_id="c1",
             data=step("guard", refusal, ok=False, tool="Bash", command="psql -c 'DROP TABLE orders'"))
    # keel's own rules refused a write (an API-key model's ToolBox says REFUSED:)
    bus.emit("agent.step", "t-1", "shop", step="green", call_id="c1",
             data=step("guard", "REFUSED: .env holds secrets.", ok=False, tool="write_file", path=".env"))
    bus.emit("agent.step", "t-1", "shop", step="green", call_id="c1", data=step("tool", "pytest", ok=False,
                                                                                 output="1 failed", tool="Bash"))
    bus.emit("helper.step", "s-9", "blog", data=step("edit", "Edit README.md (+1 -0)", path="README.md"))
    bus.emit("agent.delta", "t-1", "shop", data={"text": "thinking"})
    rec.flush()

    shop = history.read(history.path_for(tmp_path, "shop"))
    assert [(x["kind"], x["action"], x["tier"]) for x in shop] == [
        ("read", "allow", "T0"), ("tool", "allow", "T2"), ("tool", "deny", "T3"), ("write", "deny", "T3"),
        ("tool", "allow", "T0")]
    assert shop[0]["agent"] == "dev" and shop[0]["source"] == "flow" and shop[0]["thread"] == "t-1"
    assert shop[0]["text"] == "src/app.py", "a read keeps the file's name, never its content"
    assert shop[2]["rule"] == "builtin.sql-drop" and shop[2]["reason"].startswith("It drops")
    assert shop[2]["text"] == "psql -c 'DROP TABLE orders'"
    assert shop[3]["rule"] == "keel-rules" and shop[3]["reason"] == ".env holds secrets." and shop[3]["tool"] == "Write"
    assert shop[4]["ok"] is False and shop[4]["action"] == "allow"    # a failed test run is not a refusal
    assert rec.errors == 0
    blog = history.read(history.path_for(tmp_path, "blog"))
    assert blog[0]["agent"] == "KeelBot" and blog[0]["tier"] == "T1"
    assert history.verify(history.path_for(tmp_path, "shop"))[0]


def test_an_answer_to_a_question_is_recorded(tmp_path):
    ev = {"type": "approval.answered", "thread_id": "s-1", "project_id": "shop", "at": "now", "step": "approval",
          "data": {"id": "p_1", "kind": "command", "command": "git push origin main", "decision": "once",
                   "status": "approved", "by": "person", "source": "keelbot", "why": ""}}
    line = line_of(ev)
    assert line["kind"] == "approval" and line["action"] == "allow" and line["answer"] == "once"
    ev["data"].update(decision=None, status="expired", by="keel", why="Nobody answered in 10 minutes")
    assert line_of(ev)["action"] == "deny"


def test_the_line_hash_is_the_one_the_api_checks():
    # api-py/tests/test_safety_history.py checks the same line with its own reader: the two must agree
    body = {"seq": 1, "prev": history.GENESIS, "text": "git push origin main", "tier": "T2"}
    assert history.line_hash(history.GENESIS, body) == "032c53b6f4d92afd48fa998501ac77a8d3c90504b7ff59379d2ec0860c913054"
    assert history.path_for("/d", "shop").as_posix() == "/d/history/shop.jsonl"


def test_an_api_key_models_toolbox_judges_the_same_tiers(root):
    from keel_engine.tools.agent_tools import ToolBox

    asked = []
    box = ToolBox(str(root), "green", asker=lambda c: (asked.append(c), (False, "The person said no."))[1])
    assert box.run_command("echo cm0gLXJmIC8= | base64 -d | sh").startswith("REFUSED: It deletes")
    assert "T3" in box.run_command("rm -rf ~")
    assert box.run_command("git push origin main") == "REFUSED: The person said no."
    assert asked == ["git push origin main"]
    assert "T3" in box.write_file("~/.ssh/authorized_keys", "ssh-ed25519 AAAA x")
    assert box.write_file("src/new.py", "y = 2\n").startswith("Wrote src/new.py")
