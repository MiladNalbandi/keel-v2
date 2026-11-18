"""0.40 (#201) Rules: Normal | Safety only, and the loop guard.

Safety only turns keel's blocking rules off for the agents (phase rules, rule packs, read limits ...) and keeps the
safety rules: keel-gate's T3 is refused in every mode (and cannot be switched off), secrets and keel's keys stay out.
The loop guard stops a step whose agent the same rule blocked 3 times and asks the person: allow it once, switch the
flow to Safety only, or stop (only try again or stop for a rule Safety only keeps). Off by default: Normal."""

import json

import pytest
from conftest import decide, start, wait

from keel_engine import hook, rules
from keel_engine.app import Settings
from keel_engine.harness import providers as providers_mod
from keel_engine.harness.providers.base import END, TOOL_USE, Turn, Usage
from keel_engine.runtime import guard_ctx, rules_loop
from keel_engine.tools.agent_tools import ToolBox
from keel_engine.workflows.model import from_dict

PROD = "src/scores/ranks.py"
T3 = ["psql -c 'DROP TABLE users'", "rm -rf ~", "rm -rf /", "git push origin --delete main", "git push origin :main",
      "terraform destroy -auto-approve", "mkfs.ext4 /dev/sda1"]


def so(cfg: dict) -> dict:
    return rules.with_mode(cfg, rules.SAFETY_ONLY)


# ---------------------------------------------------------------- the mode: default, config, flow

def test_normal_is_the_default_for_a_project_and_a_flow(tmp_path):
    assert rules.rules_mode(rules.make_config()) == rules.NORMAL
    assert rules.rules_mode(rules.load_config(tmp_path)) == rules.NORMAL      # a new project: no .keel/config.yml
    assert Settings().rules_mode is None                                        # a flow started with no choice: the project's
    assert rules.normalize_mode(None) == rules.normalize_mode("") == rules.normalize_mode("anything") == rules.NORMAL


def test_the_project_turns_it_on_in_its_config_and_a_flow_may_choose_its_own(tmp_path):
    (tmp_path / ".keel").mkdir()
    (tmp_path / ".keel" / "config.yml").write_text("rules:\n  mode: safety-only\n")
    cfg = rules.load_config(tmp_path)
    assert rules.safety_only(cfg)
    assert not rules.safety_only(rules.with_mode(cfg, "normal"))            # the flow's choice wins
    assert rules.with_mode(cfg, None) is cfg                                  # no choice: the project's stays
    assert rules.normalize_mode("Safety only") == rules.normalize_mode("safety_only") == rules.SAFETY_ONLY


# ---------------------------------------------------------------- what Safety only lets through, and what it keeps

def test_a_phase_rule_blocks_in_normal_and_passes_in_safety_only(tmp_path):
    cfg = rules.load_config(tmp_path)
    assert not rules.check_edit("red", PROD, cfg).ok                        # RED: tests only
    assert not rules.check_edit("review", "README.md", cfg).ok              # a review changes nothing
    assert rules.check_edit("red", PROD, so(cfg)).ok
    assert rules.check_edit("review", "README.md", so(cfg)).ok
    # and in a shell write
    assert not rules.check_bash("red", f"echo x > {PROD}", cfg).ok
    assert rules.check_bash("red", f"echo x > {PROD}", so(cfg)).ok


def test_rule_packs_read_limits_and_gates_are_off_in_safety_only(tmp_path):
    cfg = rules.make_config({"guards": {"read_guard_max_lines": 5}})
    cfg["packs"] = [{"id": "house", "rules": [{"id": "no-src", "kind": "edit", "paths": ["src/**"], "reason": "Not here."}]}]
    big = tmp_path / "big.txt"
    big.write_text("x\n" * 50)
    assert not rules.check_read("big.txt", cfg, full=big).ok
    assert rules.check_read("big.txt", so(cfg), full=big).ok
    assert rules.check_read("node_modules/x/index.js", so(cfg)).ok
    assert rules.pack_verdict(so(cfg), "edit", "green", "src/a.py") is None
    for rid in ("phase-matrix", "read-twice", "big-file-read", "new-dependency", "push-gate", "mcp-writes", "generated"):
        assert rules.is_off(so(cfg), rid), rid
    assert rules.check_bash("green", "npm install left-pad", so(cfg)).ok      # a new dependency is no safety rule


def test_safety_only_keeps_secrets_keys_rule_files_git_settings_and_the_flows_commits(tmp_path):
    cfg = so(rules.load_config(tmp_path))
    assert not rules.check_read(".env", cfg).ok
    assert not rules.check_edit("green", ".env", cfg).ok
    assert not rules.check_edit("green", ".keel/config.yml", cfg).ok          # an agent never changes its own rules
    assert not rules.check_edit("green", ".git/config", cfg).ok
    assert not rules.check_bash("green", "cat .env", cfg).ok
    assert not rules.check_bash("green", "git config core.hooksPath /tmp/x", cfg).ok
    assert not rules.check_bash("green", "git commit -m x", cfg).ok           # keel makes the flow's commits
    assert not rules.check_bash("green", "git commit --no-verify -m x", cfg).ok
    assert not rules.check_bash("green", "docker run -v /:/host alpine", cfg).ok
    for rid in rules.SAFETY_KEEP:
        assert not rules.is_off(cfg, rid), rid


@pytest.mark.parametrize("command", T3)
def test_t3_is_refused_in_safety_only_even_when_the_project_switched_catastrophic_off(tmp_path, command):
    (tmp_path / ".keel").mkdir()
    (tmp_path / ".keel" / "config.yml").write_text("rules:\n  mode: safety-only\n  off: [catastrophic]\n")
    cfg = rules.load_config(tmp_path)
    assert rules.safety_only(cfg) and not rules.is_off(cfg, "catastrophic")
    why = hook.catastrophic("Bash", {"command": command}, cfg, str(tmp_path))
    assert why and "T3" in why, command
    box = ToolBox(str(tmp_path), "green", cfg=cfg)
    assert box.check_command(command).startswith("REFUSED:"), command


def _ctx(tmp_path, phase: str, mode: str | None) -> dict:
    path = tmp_path / "run" / "guard.json"
    guard_ctx.write_context(path, root=str(tmp_path), phase=phase, ac=None, lane=None, unlocks=[], agent="implementer",
                            thread="t-1", **({"rules_mode": mode} if mode else {}))
    return hook.load_context(str(path))


def test_the_hook_reads_the_flows_mode_from_its_guard_context(tmp_path):
    (tmp_path / "src" / "scores").mkdir(parents=True)
    edit = {"file_path": str(tmp_path / PROD), "old_string": "", "new_string": "X = 1\n"}
    assert hook.decide("Write", edit, _ctx(tmp_path, "red", None))                     # Normal: RED refuses production code
    assert hook.decide("Write", edit, _ctx(tmp_path, "red", rules.SAFETY_ONLY)) is None
    for command in T3:
        assert "T3" in (hook.decide("Bash", {"command": command}, _ctx(tmp_path, "red", rules.SAFETY_ONLY)) or ""), command
    assert hook.decide("Read", {"file_path": str(tmp_path / ".env")}, _ctx(tmp_path, "red", rules.SAFETY_ONLY))


def test_the_toolbox_and_the_guard_context_carry_the_mode(tmp_path):
    box = ToolBox(str(tmp_path), "red", cfg=so(rules.load_config(tmp_path)))
    assert not box.write_file(PROD, "X = 1\n").startswith("REFUSED")
    assert guard_ctx.rules_mode_of(box) == rules.SAFETY_ONLY
    assert guard_ctx.rules_mode_of(ToolBox(str(tmp_path), "red")) == rules.NORMAL


# ---------------------------------------------------------------- the loop guard

def test_the_same_rule_three_times_is_a_loop_another_file_counts_as_the_same_rule(tmp_path):
    box = ToolBox(str(tmp_path), "red")
    box.loop_guard = rules_loop.Counter()
    first = box.write_file("src/scores/a.py", "A = 1\n")
    box.write_file("src/scores/b.py", "B = 1\n")
    assert first.startswith("REFUSED") and box.loop is None
    third = box.write_file("src/scores/c.py", "C = 1\n")
    assert box.loop and box.loop["count"] == 3 and box.loop["lifted"] is True
    assert "keel stopped this step" in third
    assert rules_loop.choices(box.loop) == ["allow", "safety", "stop"]


def test_a_loop_on_t3_offers_no_way_around_it(tmp_path):
    box = ToolBox(str(tmp_path), "green")
    box.loop_guard = rules_loop.Counter()
    for _ in range(3):
        box.check_command("psql -c 'DROP TABLE users'")
    assert box.loop and box.loop["t3"] and box.loop["lifted"] is False
    q = rules_loop.question("work", "do the work", "implementer", box.loop)
    assert q["choices"] == ["retry", "stop"] and "cannot be undone" in q["detail"]


def test_keelbot_has_no_loop_guard(tmp_path):
    box = ToolBox(str(tmp_path), "red")
    for _ in range(5):
        box.write_file("src/scores/a.py", "A = 1\n")
    assert box.loop is None


# ---------------------------------------------------------------- a real flow (the model scripted)

RED_STEP = {"name": "one", "keel_rules": False, "own_branch": False,
            "steps": [{"id": "work", "kind": "agent", "name": "do the work", "agent": "implementer", "phase": "red"}]}
MODEL = {"default": {"provider": "codex", "mode": "subscription", "model": "gpt-5.5"}}


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setenv("KEEL_FAKE", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def write(n: int, path: str = PROD) -> Turn:
    return Turn([{"type": "tool_call", "id": f"c{n}", "name": "Write", "input": {"file_path": path, "content": f"RANK = {n}\n"}}],
                TOOL_USE, Usage(input=100, output=10))


def done(text: str = "Wrote ranks.py.") -> Turn:
    return Turn([{"type": "text", "text": text}], END, Usage(input=100, output=10))


class Script:
    name, model = "openai", "gpt-5.5"

    def __init__(self, turns):
        self.turns = list(turns)

    async def turn(self, req, emit):
        return self.turns.pop(0) if self.turns else done()


def _script(monkeypatch, turns) -> Script:
    s = Script(turns)
    monkeypatch.setattr(providers_mod, "resolve", lambda model, key: s)
    return s


def _waits_for_loop(s: dict) -> dict:
    assert s["status"] == "waiting", s
    q = s.get("waiting") or {}
    assert "blocked" in (q.get("title") or ""), q
    return q


def test_a_flow_blocked_by_a_phase_rule_passes_in_safety_only(client, repo, real, monkeypatch):
    _script(monkeypatch, [write(1), done()])
    tid = start(client, repo, workflow=from_dict(RED_STEP), models=MODEL, keys={"codex": "sk"},
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "rules_mode": "safety-only"})
    s = wait(client, tid)
    assert s["status"] == "done", s
    assert (repo / PROD).read_text() == "RANK = 1\n"
    assert s["rules_mode"] == "safety-only"
    started = client.bus.of(tid, "agent.started")[0]["data"]
    assert started["rules"] == "safety-only"
    lines = [e["data"] for e in client.bus.of(tid, "agent.step")]
    assert lines and all(d.get("rules") == "safety-only" for d in lines if d.get("kind") in ("write", "edit", "guard"))


def test_a_new_flow_runs_in_normal_and_the_phase_rule_holds(client, repo, real, monkeypatch):
    _script(monkeypatch, [write(1), done("RED allows tests only.")])
    tid = start(client, repo, workflow=from_dict(RED_STEP), models=MODEL, keys={"codex": "sk"})
    s = wait(client, tid)
    assert s["rules_mode"] == "normal"
    assert not (repo / PROD).exists()


def test_three_blocks_stop_the_step_and_switching_to_safety_only_lets_it_finish(client, repo, real, monkeypatch):
    script = _script(monkeypatch, [write(1), write(2), write(3), write(4), done()])
    tid = start(client, repo, workflow=from_dict(RED_STEP), models=MODEL, keys={"codex": "sk"})
    q = _waits_for_loop(wait(client, tid))
    assert [c["name"] for c in q.get("choice_info") or []] == ["allow", "safety", "stop"] or q.get("choices") == ["allow", "safety", "stop"]
    assert not (repo / PROD).exists()
    assert client.bus.of(tid, "guard.loop")[0]["data"]["count"] == 3
    s = decide(client, tid, "approve", payload={"choice": "safety"})
    assert s["status"] == "done", s
    assert (repo / PROD).read_text() == "RANK = 4\n"
    assert s["rules_mode"] == "safety-only"
    assert any("rules normal → safety-only" in line for line in s.get("gate_log") or []) or \
        client.bus.of(tid, "thread.rules")[0]["data"]["mode"] == "safety-only"
    assert not script.turns


def test_allow_it_this_once_runs_the_step_once_in_safety_only(client, repo, real, monkeypatch):
    _script(monkeypatch, [write(1), write(2), write(3), write(4), done()])
    tid = start(client, repo, workflow=from_dict(RED_STEP), models=MODEL, keys={"codex": "sk"})
    _waits_for_loop(wait(client, tid))
    s = decide(client, tid, "approve", payload={"choice": "allow"})
    assert s["status"] == "done", s
    assert (repo / PROD).read_text() == "RANK = 4\n"
    assert s["rules_mode"] == "normal"                     # once: the flow's own mode stays


def test_stop_ends_the_flow(client, repo, real, monkeypatch):
    _script(monkeypatch, [write(1), write(2), write(3)])
    tid = start(client, repo, workflow=from_dict(RED_STEP), models=MODEL, keys={"codex": "sk"})
    _waits_for_loop(wait(client, tid))
    s = decide(client, tid, "approve", payload={"choice": "stop"})
    assert s["status"] == "stopped", s


def test_the_person_switches_a_running_flow_and_back(client, repo, real, monkeypatch):
    _script(monkeypatch, [write(1), write(2), write(3)])
    tid = start(client, repo, workflow=from_dict(RED_STEP), models=MODEL, keys={"codex": "sk"})
    _waits_for_loop(wait(client, tid))
    r = client.post(f"/threads/{tid}/rules", json={"mode": "safety-only", "by": "the dashboard"})
    assert r.status_code == 200 and r.json()["rules_mode"] == "safety-only"
    assert client.bus.of(tid, "thread.rules")[-1]["data"] == {"mode": "safety-only", "from": "normal", "by": "the dashboard"}
    assert client.post(f"/threads/{tid}/rules", json={"mode": "normal"}).json()["rules_mode"] == "normal"
    assert client.post(f"/threads/{tid}/rules", json={"mode": "off"}).status_code in (400, 422)


def test_the_safety_history_writes_the_mode_on_each_line():
    from keel_engine.safety.recorder import line_of

    ev = {"type": "agent.step", "thread_id": "t-1", "step": "work", "at": "2026-11-18T10:00:00Z",
          "data": {"kind": "tool", "tool": "Bash", "text": "ls", "ok": True, "rules": "safety-only"}}
    assert line_of(ev, "implementer")["rules"] == "safety-only"
    assert line_of({**ev, "data": {**ev["data"], "rules": None}}, "implementer")["rules"] is None
    assert json.dumps(line_of(ev))


# ---------------------------------------------------------------- MCP: an agent cannot switch to Safety only by itself

class RulesApi:
    """keel's api for keel_rules_mode: one waiting flow, its rules mode, and the switch."""

    def __new__(cls):
        from test_mcp_ux import Api

        class _Api(Api):
            def __init__(self):
                super().__init__()
                self.mode = "normal"
                self.switched: list[dict] = []

            def __call__(self, req):
                import httpx

                if req.url.path == "/api/projects/shop/flows/t-1":
                    return httpx.Response(200, json={"thread": {"thread_id": "t-1", "title": "Prices", "status": "waiting",
                                                                "rules_mode": self.mode}})
                if req.url.path == "/api/threads/t-1/rules" and req.method == "POST":
                    body = json.loads(req.content)
                    self.switched.append(body)
                    self.mode = body["mode"]
                    return httpx.Response(200, json={"thread_id": "t-1", "rules_mode": self.mode})
                return super().__call__(req)
        return _Api()


@pytest.fixture
def here(monkeypatch):
    monkeypatch.setenv("KEEL_PROJECT", "shop")
    monkeypatch.delenv("KEEL_MCP_CONFIRM", raising=False)
    monkeypatch.chdir("/")


async def test_safety_only_from_mcp_waits_for_the_persons_ok_in_keels_inbox(here):
    from test_mcp import call
    from test_mcp_ux import server, token_of

    api = RulesApi()
    srv = server(api, level="write")
    first = await call(srv, "keel_rules_mode", {"mode": "safety-only", "thread_id": "t-1"})
    assert "This needs the person's OK" in first and "keel put the question in its Inbox" in first
    assert api.switched == [] and api.posts[-1][0] == "/api/projects/shop/plugins/ask"
    token = token_of(first)
    early = await call(srv, "keel_rules_mode", {"mode": "safety-only", "thread_id": "t-1", "confirm": token})
    assert "has not approved" in early and api.switched == []                # the token alone does not do it
    api.asks["p_000000000001"] = {"decision": "allow", "why": ""}            # the person approves it in the Inbox
    done = await call(srv, "keel_rules_mode", {"mode": "safety-only", "thread_id": "t-1", "confirm": token})
    assert api.switched == [{"mode": "safety-only"}] and "runs with rules: safety-only" in done


async def test_back_to_normal_from_mcp_asks_nothing_and_reading_changes_nothing(here):
    from test_mcp import call
    from test_mcp_ux import server

    api = RulesApi()
    api.mode = "safety-only"
    srv = server(api, level="write")
    now = await call(srv, "keel_rules_mode", {"thread_id": "t-1"})
    assert "runs with rules: safety-only" in now and api.switched == []
    back = await call(srv, "keel_rules_mode", {"mode": "normal", "thread_id": "t-1"})
    assert api.switched == [{"mode": "normal"}] and "runs with rules: normal" in back
    assert not any(p[0].endswith("/plugins/ask") for p in api.posts)


async def test_the_mcp_tool_is_write_level_and_never_on_an_agents_read_level_server(here):
    from keel_engine import mcp_confirm, mcp_server
    from test_mcp import tool_names
    from test_mcp_ux import server

    assert mcp_server.tool_level("keel_rules_mode") == "write" and mcp_confirm.out_of_band("keel_rules_mode")
    assert "keel_rules_mode" not in await tool_names(server(RulesApi(), level="read"))
